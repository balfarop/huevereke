"""
Administra una instancia de ClaudeSDKClient por sesión de navegador,
para que la conversación (armar el menú, ida y vuelta) mantenga contexto
entre mensajes — igual que una conversación normal en Claude Desktop.

Como el contenedor corre una sola instancia (no hay múltiples réplicas
para 2 usuarios), guardar el estado en un diccionario en memoria es
suficiente. Si algún día escalas a más réplicas, esto tendría que migrar
a almacenamiento externo (Redis, etc.) o session affinity.
"""
import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)

from .agent_logging import log_message
from .config import settings
from .conversation_tools import build_conversation_tools_server
from .menu_cache.sdk_tools import menu_cache_server
from .menu_cache.sync import ensure_fresh
from .web_context_tools import web_context_server

logger = logging.getLogger("menu_semanal")

# Palabras que, en el NOMBRE de una herramienta de Notion (siempre contiene
# "notion" — ver mcp_servers["notion"] en build_options, expuesto como
# mcp__notion__notion-xxx), delatan que es de solo lectura. Es una lista
# blanca a propósito, no una lista negra de "palabras de escritura": frente
# a un nombre de herramienta que no reconocemos, la opción segura en modo
# prueba es bloquear, no permitir — lo contrario dejaría pasar una
# escritura real el día que Notion agregue una herramienta nueva que no
# esté en ninguna lista negra que se nos haya ocurrido escribir a mano.
_NOTION_READ_ONLY_HINTS = ("fetch", "search", "query", "get", "list")


def _is_notion_tool(tool_name: str) -> bool:
    return "notion" in tool_name.lower()


def _is_read_only_notion_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return any(hint in lowered for hint in _NOTION_READ_ONLY_HINTS)

# Texto genérico (sin nombres de herramientas) que se muestra en la línea de
# estado de la web mientras el agente trabaja — ver {"status": ...} en
# drain_messages. Se busca por fragmento del nombre de la herramienta; la
# primera coincidencia gana, así que lo más específico va primero.
_STATUS_POR_HERRAMIENTA = (
    ("mostrar_reporte", "Armando el reporte…"),
    ("cerrar_conversacion", "Terminando…"),
    ("precargar_contexto", "Revisando tu historial de platos y semanas…"),
    ("semanas_recientes", "Revisando las últimas semanas…"),
    ("frecuencia_consumo_familiar", "Revisando lo que consume la familia…"),
    ("receta_insumo", "Calculando los insumos de las recetas…"),
    ("combos_ensalada", "Buscando acompañamientos…"),
    ("platos_principales", "Revisando el catálogo de platos…"),
    ("buscar_platos", "Buscando platos…"),
    ("guardar_notas_de_prueba", "Guardando las notas de la prueba…"),
    ("Skill", "Preparando la propuesta…"),
)


_background_tasks: set = set()


def _run_in_background(coro) -> None:
    """Lanza una corrutina sin esperarla, guardando la referencia para que
    el recolector de basura no la cancele a mitad de camino."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _status_for_tool(tool_name: str) -> Optional[str]:
    """Mensaje genérico para la línea de estado, o None si esta herramienta
    no merece cambiar lo que ya se muestra (ej. ToolSearch, interno)."""
    if tool_name == "ToolSearch":
        return None
    for fragmento, mensaje in _STATUS_POR_HERRAMIENTA:
        if fragmento in tool_name:
            return mensaje
    if _is_notion_tool(tool_name):
        if _is_read_only_notion_tool(tool_name):
            return "Consultando Notion…"
        return "Guardando en Notion…"
    return "Trabajando en tu menú…"


# Una pregunta que habla de revisar/confirmar la propuesta, el reporte o la
# lista de compras exige que el usuario YA haya visto ese contenido (ver
# _violacion_reporte_visible).
_PREGUNTA_SOBRE_REPORTE = re.compile(
    r"reporte|versi[oó]n|lista de compras|propuesta detallada|resumen final",
    re.IGNORECASE,
)
# Largo mínimo de un texto para considerarlo "contenido mostrado" (una
# frase de transición como "Ahora armo el menú" no cuenta).
_MIN_CHARS_CONTENIDO_VISIBLE = 200


def _texto_de_preguntas(input_data: dict) -> str:
    partes = []
    for q in (input_data or {}).get("questions", []) or []:
        partes.append(str(q.get("question", "")))
    return "\n".join(partes)


# Sentinel para señalar "se acabaron los eventos de este turno" en
# event_queue (ver stream_reply). No es un dict real, así que nunca puede
# confundirse con un payload legítimo.
_TURN_DONE = object()

# Cada cuántos segundos sin eventos reales mandamos un heartbeat por SSE.
# Sin esto, una conexión inactiva por varios minutos (ej. el usuario
# tardando en pensar/responder varias preguntas seguidas) puede cortarse
# sola en el camino — navegador, red, lo que sea — y el servidor no se
# entera hasta que intenta escribir en un socket ya cerrado. Un heartbeat
# periódico mantiene la conexión con actividad real y visible.
_HEARTBEAT_SECONDS = 15


@dataclass
class ConversationSession:
    client: Optional[ClaudeSDKClient] = None
    last_used: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    is_new: bool = True
    # Un Future por cada AskUserQuestion pendiente, indexado por su propio
    # question_id (no por sesión) — el modelo puede disparar más de una
    # llamada a AskUserQuestion en el mismo turno cuando necesita más de 4
    # preguntas en total (límite duro de la herramienta), y cada una debe
    # poder responderse de forma independiente. Antes esto era un único
    # Future por sesión: la segunda pregunta pisaba al primero y esa
    # pregunta quedaba huérfana para siempre, sin ninguna forma de
    # resolverse — el agente se quedaba "colgado" esperándola.
    pending_answers: dict[str, asyncio.Future] = field(default_factory=dict)
    # Un Future por cada tool_use_id de AskUserQuestion en curso, para
    # correlacionar can_use_tool (que arma question_id y el payload a
    # mostrar) con drain_messages (que procesa los content blocks del
    # AssistantMessage EN EL ORDEN REAL en que Claude los generó). Antes
    # can_use_tool empujaba la pregunta directo a event_queue en cuanto se
    # disparaba, compitiendo como productor independiente contra el texto
    # que drain_messages iba empujando por su lado — si el SDK dispara el
    # permiso de la herramienta antes de que drain_messages termine de
    # procesar el texto que lo precede en el mismo mensaje, la pregunta
    # podía mostrarse en la web ANTES que el texto (ej. la propuesta de
    # platos) que se supone la antecede. Con esto, quien decide el orden
    # real de aparición en event_queue es siempre drain_messages, nunca
    # can_use_tool directamente. Se usa cualquiera que llegue primero: si
    # can_use_tool corre antes, crea el Future y le pone el resultado; si
    # drain_messages ve el ToolUseBlock primero, crea el Future y espera a
    # que can_use_tool lo resuelva.
    pending_question_payloads: dict[str, asyncio.Future] = field(default_factory=dict)
    # Cola compartida por la que tanto el consumidor de receive_response
    # (texto, preguntas ya reordenadas, uso) como can_use_tool (solo para
    # registrar el payload de una pregunta, ver más arriba) coordinan
    # eventos — así una pregunta puede yieldearse al navegador en el
    # momento en que corresponde, sin pisar ninguna otra ya pendiente.
    event_queue: "asyncio.Queue" = field(default_factory=asyncio.Queue)
    # Tarea que consume session.client.receive_response() para el turno en
    # curso — vive independiente de qué endpoint HTTP esté drenando
    # event_queue en cada momento (ver stream_reply/continue_reply). Así,
    # si la conexión de /chat que arrancó el turno se corta a mitad de
    # camino (ej. el usuario tardó varios minutos en responder varias
    # preguntas y algo en el camino cerró la conexión inactiva), el turno
    # no queda huérfano — /chat/answer puede retomarlo.
    turn_task: Optional[asyncio.Task] = None
    # True mientras alguna conexión HTTP (la de /chat, o una de /chat/answer
    # que haya retomado el turno) está activamente drenando event_queue.
    # Evita que dos respuestas compitan por los mismos eventos si la
    # conexión original seguía viva justo cuando /chat/answer intenta
    # retomarla (ver _drain_events) — sin esto, ambas podrían repartirse
    # los eventos de forma impredecible y una de las dos quedarse sin nada.
    listener_active: bool = False
    # Acumulado de tokens de la sesión completa (ver _start_turn/ResultMessage
    # más abajo). El `usage` de cada ResultMessage cubre solo ese turno (no
    # es acumulativo — ver docs del SDK), así que lo sumamos nosotros acá,
    # turno a turno, mientras dure esta ConversationSession (nunca mandamos
    # /clear ni equivalente, así que no hay reinicios a mitad de sesión que
    # considerar).
    usage_totals: dict = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
    )
    # A diferencia de `usage`, `total_cost_usd` SÍ es el acumulado corrido de
    # toda la sesión (modo "streaming input" del SDK, ver docs) — no lo
    # sumamos nosotros, solo nos quedamos con el último valor recibido.
    # Es una ESTIMACIÓN del lado del cliente (tabla de precios de lista),
    # no el dato real de facturación — se muestra así en la UI.
    latest_cost_usd: Optional[float] = None
    # Conteo PROVISORIO del turno en curso, para que el contador de la UI
    # se mueva en cada intercambio (pregunta/respuesta) y no solo al cerrar
    # toda la propuesta. Se arma con AssistantMessage.usage (disponible
    # apenas llega cada mensaje, a diferencia de ResultMessage.usage que
    # recién existe al terminar TODO el turno — y en este proyecto un
    # "turno" de client.query() puede abarcar varias rondas de
    # AskUserQuestion completas, porque las respuestas se resuelven vía
    # can_use_tool sin abrir un query() nuevo). Solo sumamos input/cache
    # tokens acá: output_tokens por paso es un valor placeholder según la
    # doc del SDK hasta que llega el ResultMessage, así que no lo usamos
    # para no mostrar un número que después "salta" para atrás. Se
    # reinicia a cero en cuanto llega el ResultMessage del turno (ver
    # drain_messages), momento en que su aporte ya quedó reflejado de forma
    # autoritativa en usage_totals/latest_cost_usd de arriba.
    in_flight_usage: dict = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
    )
    # message_id de los AssistantMessage ya contados en in_flight_usage —
    # varios content blocks (ej. texto + llamada a herramienta) comparten
    # el mismo id y el mismo `usage`, así que sin este set los contaríamos
    # doble (ver "Track per-step usage" en la doc de cost-tracking del SDK).
    seen_step_ids: set = field(default_factory=set)
    # Fijado UNA sola vez, al crear la sesión (ver _get_or_create_session) —
    # nunca cambia a mitad de conversación aunque el navegador vuelva a
    # mandar un valor distinto en un mensaje posterior de la misma sesión
    # (el toggle de la web se deshabilita mientras haya una conversación
    # abierta, justamente para que esto no pase). Cuando es True, el
    # can_use_tool de esta sesión (ver _make_web_can_use_tool) bloquea a
    # nivel de código cualquier escritura a Notion — no es una instrucción
    # que el agente pueda "olvidar" seguir, es un permiso real que nunca se
    # concede.
    test_mode: bool = False
    # Lo setea la herramienta mcp__conversation__cerrar_conversacion (ver
    # app/conversation_tools.py) cuando el propio agente decide que esta
    # propuesta (real o de prueba) ya terminó — drain_messages lo revisa al
    # cerrar cada turno (ResultMessage) y, si está seteado, le avisa al
    # navegador para que muestre el aviso de cierre; se limpia después de
    # avisar para no repetirlo en un turno futuro de la misma sesión (si el
    # usuario sigue escribiendo en vez de cerrar).
    close_signal: Optional[dict] = None
    # Garantía de que el usuario VEA el reporte antes de que se le pida
    # revisarlo o confirmarlo. El razonamiento del modelo y los resultados de
    # herramientas no llegan a la web: en una corrida real el agente redactó
    # "la versión ajustada" solo en su razonamiento y preguntó igual "¿todo
    # bien con esta versión?", tres veces seguidas. `shown_since_input` pasa
    # a True cuando se manda a la web un texto largo o un reporte, y vuelve a
    # False con cada mensaje o respuesta del usuario. Si el agente intenta
    # preguntar por el reporte con esto en False, can_use_tool rechaza la
    # pregunta una vez (`denied_since_input`) y le pide mostrarlo primero.
    shown_since_input: bool = False
    denied_since_input: bool = False
    # Un Future por tool_use_id de AskUserQuestion: drain_messages (que ve
    # los bloques en el orden real) decide si la pregunta procede o se
    # rechaza (None = procede, str = mensaje de rechazo) y can_use_tool
    # espera ese veredicto antes de pausar por la respuesta del usuario.
    question_verdicts: dict[str, asyncio.Future] = field(default_factory=dict)


_sessions: dict[str, ConversationSession] = {}

# Le decimos explícitamente a las skills en qué canal están corriendo. Sin
# esto, según su propia documentación, no tienen forma de saber que no son
# Telegram/WhatsApp — así que definimos un cuarto modo a medida para esta
# web: conversacional y breve, con preguntas de opción múltiple reales
# (como en Claude Desktop) en vez de texto plano numerado.
CHANNEL_CONTEXT = (
    "[Contexto de canal — no es parte de mi pregunta, es información sobre "
    "dónde corre esta conversación]\n"
    "Este agente responde a través de una página web de chat privada para "
    "la familia (no Telegram, no WhatsApp). SÍ tienes disponible la "
    "herramienta AskUserQuestion para hacer preguntas de opción múltiple "
    "con botones — como en Claude Desktop. Al usar las skills de menú "
    "semanal:\n"
    "- **Filtro de tema — esta regla aplica en cualquier punto de la "
    "conversación, no solo al primer mensaje.** Esta página existe "
    "únicamente para armar la propuesta de menú semanal de la familia "
    "(platos, acompañamientos, bebidas, lista de compras) y para mantener "
    "al día el catálogo de platos/insumos en Notion. Si un mensaje no "
    "tiene relación con eso — pedirte hablar de otro tema, ayuda con algo "
    "aparte, o charla sin conexión con el menú/catálogo — respóndele en "
    "1-2 frases, sin usar ninguna herramienta (nada de Notion ni del "
    "caché local), aclarando que esta conversación es solo para el menú "
    "semanal de la familia y qué sí puedes hacer acá (armar o ajustar la "
    "propuesta de la semana, o actualizar el catálogo de platos/insumos). "
    "No sigas el tema fuera de lugar ni te disculpes de más — corta y "
    "directo basta, y después queda esperando el siguiente mensaje. Un "
    "saludo simple (\"hola\", \"buenas\") no cuenta como fuera de tema: "
    "respóndelo con normalidad e invita a empezar la propuesta.\n"
    "- No uses el modo \"Telegram\" ni \"WhatsApp\" (nada de asteriscos de "
    "negrita, nada de etiquetas abreviadas tipo \"D1:\").\n"
    "- Tampoco uses el modo \"Detallado\" con tablas markdown completas de "
    "columnas de trabajo interno (Estado, Confianza, etc.) — mantén las "
    "respuestas razonablemente breves y conversacionales, como una charla "
    "de chat normal, sin explayarte de más ni mostrar información de "
    "trabajo interno.\n"
    "- Cuando necesites preguntarme algo con varias opciones para elegir "
    "(bebida, ensalada, confirmar algo, etc.), usa la herramienta "
    "AskUserQuestion en vez de escribir la pregunta como texto plano. "
    "IMPORTANTE — límite duro de esa herramienta: 1 a 4 preguntas por "
    "llamada y 2 a 4 opciones por pregunta (fuera de ese rango la llamada "
    "entera falla). La interfaz web ya muestra, debajo de tus opciones en "
    "cada pregunta, un campo de texto libre siempre visible para que yo "
    "escriba cualquier respuesta que no esté en tu lista — por eso NUNCA "
    "agregues tú una opción tipo \"Otro\" u \"Otro día (dime cuál)\": ese "
    "campo ya existe siempre y no cuenta contra tu límite de opciones. Usa "
    "las 2 a 4 opciones solo para las alternativas reales más probables "
    "(ej. si hay que elegir entre 5 días de la semana, pon como opciones "
    "los 4 días más probables y confía en que el campo de texto libre "
    "cubre cualquier otro caso).\n"
    "- Si necesitas preguntar más de 4 cosas en total, prioriza las 4 más "
    "importantes en una sola llamada a AskUserQuestion y espera mi "
    "respuesta antes de preguntar el resto en tu siguiente turno — evita "
    "lanzar dos llamadas a AskUserQuestion seguidas en el mismo turno. "
    "Esto es una regla dura, no una preferencia: **una sola llamada a "
    "AskUserQuestion por turno, siempre** — si te quedan más cosas por "
    "preguntar, ciérralas en tu próximo turno, después de recibir esta "
    "respuesta, nunca antes.\n"
    "- **Usa siempre AskUserQuestion para pedir confirmación, incluida la "
    "confirmación final de la propuesta** (pasos 6/6b de "
    "propuesta-menu-semanal) — nunca la pidas como pregunta de texto "
    "plano al final de un mensaje, aunque sea una decisión binaria. Para "
    "una confirmación simple, 2-3 opciones alcanzan (ej. \"Sí, "
    "confirmo\" / \"Quiero cambiar algo\") — el campo de texto libre ya "
    "cubre cualquier ajuste puntual que quieran pedir en vez de un "
    "rechazo total.\n"
    "- **El usuario SOLO ve tus mensajes de texto, las tarjetas de "
    "mostrar_reporte y tus preguntas. NO ve tu razonamiento ni los "
    "resultados de herramientas.** Cada vez que la propuesta, la lista de "
    "compras o el reporte final cambie (la primera vez, tras cada ajuste "
    "que pida y antes del reporte final), muéstrale el contenido COMPLETO y "
    "actualizado con mcp__conversation__mostrar_reporte (titulo + contenido "
    "en markdown) o como texto, SIEMPRE antes de la AskUserQuestion que le "
    "pide revisarlo o confirmarlo. Nunca preguntes \"¿todo bien con esta "
    "versión?\" sobre algo que no le mostraste, y nunca digas \"te lo "
    "reenvío\" o \"te lo muestro\" sin que ese contenido vaya en esa "
    "misma respuesta. Una pregunta sobre el reporte o la versión sin "
    "contenido visible previo será rechazada.\n"
    "- Cuando esta propuesta (real o de prueba) llegue a su fin — guardado "
    "en Notion completo (paso 7) o, en modo prueba, apenas el usuario dé "
    "por cerrada la corrida — llama una sola vez a "
    "mcp__conversation__cerrar_conversacion como tu última acción, para "
    "que el navegador pueda liberar el contexto de esta conversación y no "
    "arrastrarlo a la propuesta de la semana siguiente.\n"
    "- **Responde siempre en español, sin mezclar palabras sueltas en "
    "inglés a mitad de una frase** — se ha visto colarse, por ejemplo, la "
    "palabra \"until\" en medio de un texto que debía ser 100% en "
    "español, y eso confunde a quien lee. Antes de enviar tu respuesta, "
    "revisa que no se haya colado ninguna palabra en inglés que no sea un "
    "nombre propio, una marca, o un término técnico sin traducción "
    "establecida — y corrígela si la encuentras.\n"
)

# Se antepone a CHANNEL_CONTEXT solo cuando el toggle "Modo prueba" de la
# web estaba activo al mandar el primer mensaje de la conversación (ver
# stream_reply, ConversationSession.test_mode). Esta instrucción es solo
# una cortesía para que el agente no pierda tiempo/tokens intentando
# escrituras que igual van a fallar — la garantía real es el bloqueo en
# _make_web_can_use_tool, que no depende de que el agente lea o respete
# esto.
TEST_MODE_CONTEXT = (
    "[MODO_PRUEBA activo en esta conversación — no es parte de mi pregunta, "
    "es una condición técnica real, no una preferencia mía que puedas "
    "negociar]\n"
    "Cualquier intento de escritura a Notion (guardar la Semana, crear la "
    "sub-página de notas de conversación, actualizar Platos/Receta_Insumo, "
    "lo que sea) va a ser rechazado automáticamente en esta conversación, "
    "así que NO intentes el paso 7 de propuesta-menu-semanal ni ninguna "
    "otra escritura — sigue el flujo normal de preguntas y arma la "
    "propuesta completa igual, pero al llegar al punto en que normalmente "
    "guardarías, dile al usuario en una frase que esto es una prueba y que "
    "no se guardó nada (no como un error, es intencional). Justo ahí, "
    "antes de cerrar, pregúntale con AskUserQuestion (nunca sin "
    "preguntar) si quiere que igual guardes un resumen de esta prueba en "
    "una página aparte de Notion (fuera de cualquier Semana real) para "
    "que pueda revisarlo — si acepta, llama a "
    "mcp__conversation__guardar_notas_de_prueba con ese resumen (mismo "
    "criterio de contenido que la sub-página real del paso 7: curado y "
    "breve, no el transcript) y pásale la URL que te devuelve tal cual, "
    "en tu mensaje, para que sepa dónde borrarla después. Cierra siempre "
    "con mcp__conversation__cerrar_conversacion, guardado=false (acá "
    "nunca se guarda la propuesta en sí, se haya guardado o no esa "
    "página de notas de prueba).\n"
)


def _make_web_can_use_tool(session: ConversationSession):
    """
    can_use_tool para sesiones de la web: aprueba todo automáticamente,
    salvo AskUserQuestion — ahí pausa (esperando un Future propio para esa
    pregunta puntual, ver ConversationSession.pending_answers) hasta que el
    navegador responda vía POST /chat/answer con el question_id
    correspondiente, y recién entonces deja que el SDK continúe con esa
    respuesta.
    """

    async def handle(tool_name, input_data, context):
        # Bloqueo real de escrituras a Notion en modo prueba — no depende
        # de que el agente recuerde/respete la instrucción de MODO_PRUEBA
        # en el contexto (ver CHANNEL_CONTEXT/stream_reply): aunque lo
        # intente, este permiso nunca se concede mientras session.test_mode
        # sea True. Cubre tanto el guardado real de la Semana como la
        # sub-página de notas de conversación (paso 7) — ambas son
        # herramientas de Notion, ninguna pasa el filtro de solo-lectura.
        if session.test_mode and _is_notion_tool(tool_name) and not _is_read_only_notion_tool(tool_name):
            return PermissionResultDeny(
                message=(
                    "Modo prueba activo en esta conversación: no se permite "
                    "ninguna escritura a Notion (ni guardar la Semana ni "
                    "crear la sub-página de notas). Sigue sin intentarlo de "
                    "nuevo — explícale al usuario que esto es intencional, "
                    "no un error, y sigue el flujo normal de la propuesta."
                )
            )

        if tool_name != "AskUserQuestion":
            return PermissionResultAllow(updated_input=input_data)

        question_id = str(uuid.uuid4())
        loop = asyncio.get_event_loop()
        answer_future = loop.create_future()
        session.pending_answers[question_id] = answer_future

        # No empujamos la pregunta a event_queue desde acá — se la
        # avisamos a drain_messages() (que sí procesa los content blocks
        # del AssistantMessage en el orden real en que Claude los generó)
        # a través de un Future propio por tool_use_id, para que sea
        # SIEMPRE drain_messages quien decida el momento de mostrarla en
        # la web, en el mismo orden en que aparece junto al texto que la
        # precede (ver comentario largo en ConversationSession.pending_question_payloads).
        tool_use_id = getattr(context, "tool_use_id", None)
        payload = {"question": input_data, "question_id": question_id}
        payload_future = session.pending_question_payloads.get(tool_use_id)
        if payload_future is None:
            payload_future = loop.create_future()
            session.pending_question_payloads[tool_use_id] = payload_future
        if not payload_future.done():
            payload_future.set_result(payload)

        # drain_messages decide, con el orden real de los bloques, si esta
        # pregunta procede (ver ConversationSession.shown_since_input).
        verdict_future = session.question_verdicts.get(tool_use_id)
        if verdict_future is None:
            verdict_future = loop.create_future()
            session.question_verdicts[tool_use_id] = verdict_future
        try:
            rechazo = await verdict_future
        finally:
            session.question_verdicts.pop(tool_use_id, None)
        if rechazo:
            session.pending_answers.pop(question_id, None)
            session.pending_question_payloads.pop(tool_use_id, None)
            return PermissionResultDeny(message=rechazo)

        try:
            answers = await answer_future
        finally:
            session.pending_answers.pop(question_id, None)

        updated = dict(input_data)
        updated["answers"] = answers
        return PermissionResultAllow(updated_input=updated)

    return handle


def build_options(can_use_tool, session: "ConversationSession") -> ClaudeAgentOptions:
    kwargs = dict(
        cwd=settings.PROJECT_ROOT,
        mcp_servers={
            # Herramienta mcp__conversation__cerrar_conversacion — el propio
            # agente la llama al terminar una propuesta (real o de prueba)
            # para avisarle al backend que puede cerrar esta sesión y
            # limpiar la pantalla (ver app/conversation_tools.py y
            # drain_messages, más abajo). Construida por sesión (no a nivel
            # de módulo, como notion/menu_cache/web_context) porque necesita
            # una referencia directa a ESTA ConversationSession para dejar
            # la señal donde drain_messages la va a revisar.
            "conversation": build_conversation_tools_server(session),
            "notion": {
                # mcp-remote hace de puente OAuth hacia el servidor MCP oficial
                # de Notion: la primera vez que corras esto te va a abrir el
                # navegador para loguearte con Notion y aprobar el acceso.
                # Después de eso, cachea el token en disco y lo renueva solo.
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "mcp-remote", "https://mcp.notion.com/mcp"],
            },
            # Servidor MCP "en proceso" (Agent SDK, sin subprocess ni red) —
            # ver app/menu_cache/sdk_tools.py. Expone mcp__menu_cache__* como
            # alternativa local a las consultas en vivo de Notion para los
            # pasos 3b/4b/5 de propuesta-menu-semanal (ver SKILL.md de este
            # proyecto, que ahora los pide explícitamente).
            "menu_cache": menu_cache_server,
            # Igual que menu_cache, pero para el precargado de los pasos 1/2
            # (ver app/web_context_tools.py) — el propio agente la llama
            # cuando decide seguir el flujo de propuesta-menu-semanal, en
            # vez de que este archivo se lo adivine de antemano.
            "web_context": web_context_server,
        },
        can_use_tool=can_use_tool,
    )
    if settings.CLAUDE_CLI_PATH:
        kwargs["cli_path"] = settings.CLAUDE_CLI_PATH
    return ClaudeAgentOptions(**kwargs)


async def _get_or_create_session(session_id: str, test_mode: bool = False) -> ConversationSession:
    session = _sessions.get(session_id)
    if session is None:
        session = ConversationSession()
        session.test_mode = test_mode
        _sessions[session_id] = session  # ya visible para submit_answer()
        client = ClaudeSDKClient(
            options=build_options(_make_web_can_use_tool(session), session)
        )
        await client.connect()
        session.client = client
    # Si la sesión YA existía, `test_mode` de este mensaje se ignora a
    # propósito — el modo se fija una sola vez, al crear la conversación
    # (ver ConversationSession.test_mode), y el toggle de la web se
    # deshabilita mientras haya una conversación abierta para que esto
    # nunca sea ambiguo.
    session.last_used = time.time()
    return session


async def close_session(session_id: str) -> None:
    session = _sessions.pop(session_id, None)
    if session:
        if session.turn_task and not session.turn_task.done():
            session.turn_task.cancel()
        if session.client:
            await session.client.disconnect()


async def cleanup_idle_sessions() -> None:
    """Llamar periódicamente (o antes de cada request) para liberar memoria."""
    cutoff = time.time() - settings.SESSION_IDLE_MINUTES * 60
    idle_ids = [
        sid
        for sid, s in _sessions.items()
        if s.last_used < cutoff
        and not s.pending_answers
        and (s.turn_task is None or s.turn_task.done())
    ]
    for sid in idle_ids:
        await close_session(sid)


def submit_answer(session_id: str, question_id: str, answers: dict) -> tuple[bool, bool]:
    """
    Llamado desde POST /chat/answer cuando el usuario responde una pregunta
    de AskUserQuestion puntual (identificada por question_id — puede haber
    más de una pendiente a la vez, ver ConversationSession.pending_answers).

    Devuelve (ok, quedan_pendientes). ok=False si no había ninguna pregunta
    pendiente con ese id (ej. la sesión expiró, o el usuario ya la
    respondió). quedan_pendientes indica si, sin contar la que se acaba de
    resolver, todavía hay otra(s) del mismo turno sin responder — el
    endpoint lo usa para decidir si le corresponde a esta respuesta retomar
    el streaming de la continuación (ver continue_reply) o no, para que no
    compitan dos respuestas a la vez por los mismos eventos.
    """
    session = _sessions.get(session_id)
    if not session:
        return False, False
    future = session.pending_answers.get(question_id)
    if not future or future.done():
        return False, False
    future.set_result(answers)
    session.shown_since_input = False
    session.denied_since_input = False
    remaining = [
        qid
        for qid, fut in session.pending_answers.items()
        if qid != question_id and not fut.done()
    ]
    return True, bool(remaining)


async def _drain_events(session: ConversationSession):
    """
    Generador compartido: drena session.event_queue hasta ver _TURN_DONE,
    con heartbeats periódicos (ver _HEARTBEAT_SECONDS) para que la conexión
    SSE nunca quede completamente inactiva mientras el usuario piensa una
    respuesta. La usan tanto stream_reply (turno nuevo, desde /chat) como
    continue_reply (retomar un turno ya en curso tras responder la última
    pregunta pendiente, desde /chat/answer).

    Solo un consumidor a la vez por sesión (session.listener_active): si la
    conexión original de /chat seguía viva justo cuando /chat/answer
    intenta retomar el turno, esta segunda llamada no compite por los
    mismos eventos — simplemente no yieldea nada y termina, confiando en
    que quien ya está escuchando se encarga. Sin esto, ambas podrían
    repartirse los eventos de forma impredecible y una quedarse sin nada,
    reintroduciendo el mismo síntoma de "se colgó" que esto busca evitar.
    """
    if session.listener_active:
        return
    session.listener_active = True
    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    session.event_queue.get(), timeout=_HEARTBEAT_SECONDS
                )
            except asyncio.TimeoutError:
                yield {"ping": True}
                continue
            if item is _TURN_DONE:
                return
            yield item
    finally:
        session.listener_active = False


def _start_turn(session: ConversationSession, prefix: str) -> None:
    """
    Lanza el consumidor de session.client.receive_response() para el turno
    en curso. Vive en una tarea propia de la sesión (session.turn_task),
    independiente de qué endpoint HTTP esté drenando event_queue en cada
    momento — así, si la conexión de /chat que arrancó el turno se corta a
    mitad de camino, /chat/answer puede retomar los eventos sin perder el
    hilo (ver continue_reply).
    """

    def _usage_payload(*, final: bool) -> dict:
        # `final=False` (cada AssistantMessage nuevo): tokens provisorios de
        # ESTE intercambio, para que el contador de tokens en la web se
        # actualice en vivo. `final=True` (solo al llegar ResultMessage):
        # recién ahí el SDK entrega total_cost_usd real del turno completo,
        # así que el frontend usa esta bandera para saber cuándo el costo
        # aproximado ya es válido y no antes (ver renderUsage() en app.js).
        combined = dict(session.usage_totals)
        for key in session.in_flight_usage:
            combined[key] = combined.get(key, 0) + session.in_flight_usage[key]
        combined["cost_usd"] = session.latest_cost_usd
        combined["final"] = final
        return combined

    async def drain_messages():
        # Todo lo que ve el navegador (texto, preguntas, uso) se decide
        # ACÁ, procesando los mensajes de receive_response() en el orden
        # real en que Claude los generó — can_use_tool (handle(), arriba)
        # ya no empuja directo a event_queue, solo dispara el Future del
        # que este loop lee para saber qué pregunta mostrar y cuándo (ver
        # ConversationSession.pending_question_payloads).
        try:
            async for message in session.client.receive_response():
                log_message(message, prefix=prefix)
                if isinstance(message, AssistantMessage):
                    # Conteo provisorio por intercambio (ver in_flight_usage) —
                    # dedup por message_id porque varios content blocks del
                    # mismo mensaje comparten el mismo `usage`.
                    msg_id = message.message_id
                    if msg_id and msg_id not in session.seen_step_ids:
                        session.seen_step_ids.add(msg_id)
                        step_usage = message.usage or {}
                        for key in session.in_flight_usage:
                            session.in_flight_usage[key] += step_usage.get(key, 0) or 0
                        await session.event_queue.put({"usage": _usage_payload(final=False)})

                    for block in message.content:
                        if isinstance(block, TextBlock):
                            await session.event_queue.put({"text": block.text})
                            if len(block.text.strip()) >= _MIN_CHARS_CONTENIDO_VISIBLE:
                                session.shown_since_input = True
                        elif isinstance(block, ThinkingBlock):
                            # El razonamiento NO se muestra (ni debe), pero
                            # avisamos que el agente sigue trabajando.
                            await session.event_queue.put({"status": "Pensando…"})
                        elif (
                            isinstance(block, ToolUseBlock)
                            and block.name == "mcp__conversation__mostrar_reporte"
                        ):
                            # El reporte viaja en la propia llamada: lo
                            # mostramos acá, en el orden real de los
                            # bloques, y no desde la herramienta.
                            args = block.input or {}
                            await session.event_queue.put(
                                {
                                    "report": {
                                        "titulo": str(args.get("titulo") or "Reporte"),
                                        "contenido": str(args.get("contenido") or ""),
                                    }
                                }
                            )
                            session.shown_since_input = True
                        elif isinstance(block, ToolUseBlock) and block.name != "AskUserQuestion":
                            estado = _status_for_tool(block.name)
                            if estado:
                                await session.event_queue.put({"status": estado})
                        elif isinstance(block, ToolUseBlock) and block.name == "AskUserQuestion":
                            # Veredicto ANTES de mostrar la pregunta: si habla
                            # del reporte/versión/lista y el usuario todavía
                            # no vio ese contenido, se rechaza (una vez) y el
                            # agente debe mostrarlo primero. can_use_tool
                            # espera este veredicto (ver question_verdicts).
                            verdict_future = session.question_verdicts.get(block.id)
                            if verdict_future is None:
                                verdict_future = asyncio.get_event_loop().create_future()
                                session.question_verdicts[block.id] = verdict_future
                            rechazo = None
                            if (
                                not session.shown_since_input
                                and not session.denied_since_input
                                and _PREGUNTA_SOBRE_REPORTE.search(_texto_de_preguntas(block.input))
                            ):
                                session.denied_since_input = True
                                rechazo = (
                                    "Tu pregunta no se mostró: el usuario todavía NO ha visto "
                                    "el contenido sobre el que preguntas — tu razonamiento y "
                                    "tus resultados de herramientas no se ven en pantalla. "
                                    "Primero muéstrale el menú/reporte/lista de compras "
                                    "COMPLETO y actualizado con "
                                    "mcp__conversation__mostrar_reporte (o como texto), y "
                                    "después vuelve a hacer la pregunta."
                                )
                                logger.warning(
                                    "%sAskUserQuestion rechazada: pregunta por el reporte sin "
                                    "contenido visible previo.",
                                    prefix,
                                )
                            if not verdict_future.done():
                                verdict_future.set_result(rechazo)
                            if rechazo:
                                continue
                            # can_use_tool puede dispararse antes o después de
                            # que lleguemos acá — cualquiera que llegue primero
                            # crea el Future, el otro lo completa/espera (ver
                            # pending_question_payloads). Esperar acá (en vez
                            # de que can_use_tool empuje directo) es lo que
                            # garantiza que la pregunta aparezca DESPUÉS del
                            # texto que la precede en este mismo mensaje.
                            payload_future = session.pending_question_payloads.get(block.id)
                            if payload_future is None:
                                payload_future = asyncio.get_event_loop().create_future()
                                session.pending_question_payloads[block.id] = payload_future
                            payload = await payload_future
                            session.pending_question_payloads.pop(block.id, None)
                            await session.event_queue.put(payload)
                elif isinstance(message, ResultMessage):
                    # `usage` cubre solo este turno en modo streaming (no es
                    # acumulativo), así que lo sumamos a mano al total de la
                    # sesión; `total_cost_usd` ya viene acumulado por el SDK
                    # para toda la sesión, así que solo nos quedamos con el
                    # último valor (ver comentario en ConversationSession).
                    turn_usage = message.usage or {}
                    for key in session.usage_totals:
                        session.usage_totals[key] += turn_usage.get(key, 0) or 0
                    if message.total_cost_usd is not None:
                        session.latest_cost_usd = message.total_cost_usd
                    # El turno terminó de verdad — lo provisorio ya quedó
                    # reflejado arriba de forma autoritativa, así que se
                    # reinicia para el próximo turno.
                    session.in_flight_usage = {key: 0 for key in session.in_flight_usage}
                    session.seen_step_ids = set()
                    # Nota: no calculamos acá ningún % contra una cuota —
                    # el consumo del Agent SDK sale de la misma ventana de
                    # 5h/semanal de Claude Code que el uso interactivo (ver
                    # nota larga en config.py), y Anthropic no publica un
                    # equivalente en dólares de esos límites contra el cual
                    # calcular ese %.
                    await session.event_queue.put({"usage": _usage_payload(final=True)})
                    # Si en este turno el agente llamó a
                    # mcp__conversation__cerrar_conversacion (ver
                    # conversation_tools.py), recién ACÁ, al cerrar el turno
                    # de verdad, le avisamos al navegador — nunca antes,
                    # para no cortarle el hilo a texto/preguntas que todavía
                    # pudieran venir en el mismo turno. Se limpia después de
                    # avisar: si el usuario sigue escribiendo en la misma
                    # sesión en vez de cerrarla, un futuro turno no debe
                    # repetir este aviso solo porque el flag quedó pegado.
                    if session.close_signal is not None:
                        if session.close_signal.get("guardado"):
                            # Se acaba de guardar una Semana en Notion: el
                            # caché la necesita para la próxima conversación.
                            _run_in_background(ensure_fresh(force=True))
                        await session.event_queue.put({"close_prompt": session.close_signal})
                        session.close_signal = None
        except Exception as exc:  # noqa: BLE001
            # Un fallo dentro de esta tarea antes terminaba el turno en
            # silencio (la web solo veía el fin del stream, sin nada que
            # mostrar). Ahora queda en el log y llega a la pantalla.
            logger.exception("%sFallo procesando el turno del agente", prefix)
            await session.event_queue.put(
                {"error": f"Se interrumpió la conversación ({type(exc).__name__}: {exc})"}
            )
        finally:
            await session.event_queue.put(_TURN_DONE)

    session.turn_task = asyncio.create_task(drain_messages())


async def stream_reply(session_id: str, user_message: str, test_mode: bool = False):
    """
    Generador async que produce eventos de la respuesta del agente a medida
    que llegan, listos para servirse como Server-Sent Events. Cada evento es
    un dict: {"text": "..."} para texto, {"ping": True} como heartbeat (el
    navegador lo ignora), {"question": {...}, "question_id": "..."} cuando
    el agente llama a AskUserQuestion (el navegador debe responder vía POST
    /chat/answer, incluyendo ese mismo question_id), o {"close_prompt":
    {"guardado": bool, "motivo": "..."}} cuando el agente mismo señala que
    esta propuesta terminó (ver conversation_tools.py) — el navegador debe
    mostrar el aviso de cierre y, recién cuando el usuario lo confirme (o en
    cualquier momento vía "Nueva conversación"), llamar a POST /chat/close.

    `test_mode` solo se usa al CREAR la sesión (primer mensaje) — ver
    ConversationSession.test_mode y _get_or_create_session.
    """
    await cleanup_idle_sessions()
    session = await _get_or_create_session(session_id, test_mode=test_mode)
    prefix = f"[{session_id[:8]}] "

    async with session.lock:
        outgoing_message = user_message
        if session.is_new:
            # Ya NO decidimos acá si conviene precargar — eso llevaba a
            # precargar en base a una heurística de palabras clave sobre el
            # primer mensaje, corriendo antes de que el propio agente
            # aplicara el filtro de tema real. Ahora el precargado es una
            # herramienta (mcp__web_context__precargar_contexto, ver
            # web_context_tools.py) que el agente mismo llama solo cuando
            # decide seguir el flujo de propuesta-menu-semanal — ver esa
            # skill, pasos 1 y 2, para el criterio exacto.
            context = CHANNEL_CONTEXT
            if session.test_mode:
                context = TEST_MODE_CONTEXT + context
            outgoing_message = context + "\nMi mensaje real empieza acá:\n" + user_message
            session.is_new = False
            # Caché local al día para esta conversación, en segundo plano:
            # solo vuelve a sincronizar si pasó más de
            # MENU_CACHE_MAX_AGE_MINUTES desde la última vez.
            _run_in_background(ensure_fresh())

        session.shown_since_input = False
        session.denied_since_input = False
        await session.client.query(outgoing_message)
        _start_turn(session, prefix)

    async for item in _drain_events(session):
        yield item


async def continue_reply(session_id: str):
    """
    Generador async usado por POST /chat/answer cuando la respuesta que
    acaba de llegar era la última pregunta pendiente del turno: retoma el
    mismo turno ya en curso (mismo session.turn_task/event_queue) por si la
    conexión SSE original de /chat ya no está disponible — sin esto, el
    turno queda huérfano y el agente parece "colgado" para siempre aunque
    la pregunta ya se haya respondido bien del lado del backend.
    """
    session = _sessions.get(session_id)
    if not session or not session.turn_task:
        return
    async for item in _drain_events(session):
        yield item

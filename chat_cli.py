"""
Prueba del Agent SDK directo en terminal, SIN pasar todavía por la capa
web (FastAPI, login con Google, streaming SSE). El objetivo de este
script es validar, en aislado, que:

  1. tu CLAUDE_CODE_OAUTH_TOKEN funciona (autenticación por suscripción)
  2. tus 3 skills se cargan correctamente desde .claude/skills
  3. las herramientas de Notion responden bien

Correr desde la RAÍZ del proyecto (donde está este archivo), no desde
dentro de app/:

    python chat_cli.py

Requiere que ya hayas hecho:
  - pip install -r requirements.txt
  - npm install -g @anthropic-ai/claude-code
  - claude setup-token   (y copiado el token a .env como CLAUDE_CODE_OAUTH_TOKEN)
  - copiado tus skills a .claude/skills/
  - configurado NOTION_TOKEN en .env
"""
import asyncio

from claude_agent_sdk import ClaudeSDKClient, PermissionResultAllow

from app.agent_logging import log_message
from app.agent_service import build_options
from app.config import settings

# Igual que CHANNEL_CONTEXT en agent_service.py (usado por la web), pero
# recortado para esta terminal de prueba: le decimos explícitamente al
# modelo en qué canal corre y reforzamos la regla dura de "una sola
# llamada a AskUserQuestion por turno". Sin este aviso, el modelo no tiene
# forma de saber que esta terminal no puede mostrar dos tandas de
# preguntas a la vez (ver el lock en _make_cli_can_use_tool, más abajo,
# que además blinda el caso en que el modelo dispare dos llamadas igual).
CLI_CHANNEL_CONTEXT = (
    "[Contexto de canal — no es parte de mi pregunta, es información sobre "
    "dónde corre esta conversación]\n"
    "Este agente corre en una terminal de prueba (chat_cli.py), no en "
    "Telegram, WhatsApp ni en la web de Huevereke. SÍ tienes disponible "
    "AskUserQuestion (la terminal la muestra como preguntas numeradas), "
    "pero solo puede atender UNA tanda a la vez — necesita tu respuesta "
    "completa a todas las preguntas de una llamada antes de poder mostrar "
    "la siguiente. Por eso, igual que en el canal web: **una sola llamada "
    "a AskUserQuestion por turno, siempre** (1 a 4 preguntas, máx. 4 "
    "opciones cada una). Si te quedan más de 4 preguntas en total, "
    "prioriza las 4 más importantes ahora y deja el resto para tu "
    "siguiente turno, después de recibir esta respuesta — nunca dispares "
    "una segunda llamada a AskUserQuestion en el mismo turno.\n"
)


def _make_cli_can_use_tool():
    """
    Fábrica en vez de función suelta (mismo patrón que
    _make_web_can_use_tool en agent_service.py) para poder cerrar sobre un
    asyncio.Lock propio de esta sesión de CLI.

    El bug reportado: cuando el total de preguntas de una tanda supera el
    límite duro de AskUserQuestion (4 por llamada), el modelo puede
    disparar DOS o más llamadas a AskUserQuestion en el mismo turno, no
    una detrás de otra esperando tu respuesta — el mismo comportamiento
    que ya está documentado en ConversationSession (agent_service.py)
    para la web, que por eso soporta varias preguntas pendientes a la vez
    con un Future por cada una. Esta terminal no tenía nada de eso: el
    SDK invoca can_use_tool para cada tool_use casi al mismo tiempo, así
    que sin lock las dos llamadas entraban juntas a este handler — dos
    `input()` (cada uno en su propio hilo, vía asyncio.to_thread)
    compitiendo por la misma terminal, donde tu respuesta a la primera
    pregunta podía terminar contestando la pregunta equivocada de la
    segunda tanda. De ahí el "no sé a qué pregunta se refiere" y el
    cuelgue al hacer Ctrl+C (quedaba un segundo `input()` pendiente en
    otro hilo, esperando una respuesta que nunca le ibas a mandar).

    Con el lock, si el modelo igual dispara una segunda tanda antes de
    que termines de responder la primera, simplemente espera su turno —
    ves las tandas una detrás de otra, nunca mezcladas. El aviso de canal
    de arriba (CLI_CHANNEL_CONTEXT) busca que esto casi no pase; el lock
    es lo que garantiza que, aunque pase, no se rompa nada.
    """
    lock = asyncio.Lock()

    async def handle(tool_name, input_data, context):
        if tool_name != "AskUserQuestion":
            return PermissionResultAllow(updated_input=input_data)

        async with lock:
            print("\n— Tanda de preguntas del agente —")
            answers = {}
            for q in input_data.get("questions", []):
                print(f"\n❓ {q['question']}")
                options = q.get("options", [])
                for i, opt in enumerate(options, 1):
                    desc = f" — {opt['description']}" if opt.get("description") else ""
                    print(f"  {i}. {opt['label']}{desc}")
                raw = await asyncio.to_thread(input, "Tu respuesta (número, o texto libre): ")
                raw = raw.strip()
                try:
                    answers[q["question"]] = options[int(raw) - 1]["label"]
                except (ValueError, IndexError):
                    answers[q["question"]] = raw

        return PermissionResultAllow(updated_input={**input_data, "answers": answers})

    return handle


async def main():
    if not settings.CLAUDE_CODE_OAUTH_TOKEN:
        print(
            "⚠️  No encontré CLAUDE_CODE_OAUTH_TOKEN en tu .env.\n"
            "   Corre `claude setup-token` primero y pega el resultado en .env"
        )
        return

    print(f"Directorio de trabajo (para descubrir skills): {settings.PROJECT_ROOT}")
    print(f"Buscando skills en: {settings.SKILLS_DIR}\n")

    options = build_options(_make_cli_can_use_tool())

    print("Conectando con el Agent SDK... (escribe 'salir' para terminar)\n")
    first_message = True
    async with ClaudeSDKClient(options=options) as client:
        while True:
            try:
                user_input = input("Tú: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nChau!")
                break

            if not user_input:
                continue
            if user_input.lower() in {"salir", "exit", "quit"}:
                print("Chau!")
                break

            outgoing = user_input
            if first_message:
                outgoing = CLI_CHANNEL_CONTEXT + "\nMi mensaje real empieza acá:\n" + user_input
                first_message = False

            await client.query(outgoing)
            async for message in client.receive_response():
                log_message(message)


if __name__ == "__main__":
    asyncio.run(main())

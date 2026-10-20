"""
Adaptador delgado: expone fetch_context_preload() (notion_direct.py) como
una herramienta del Agent SDK (mcp__web_context__precargar_contexto), en
vez de inyectarla directo en el primer mensaje del usuario como se hacía
antes (ver el historial de agent_service.py — build_options/stream_reply).

Por qué el cambio: precargar siempre en el primer mensaje de cada sesión,
antes de que el propio agente aplicara el filtro de tema de CHANNEL_CONTEXT,
significaba pagar la consulta REST a Notion (y sumar el texto resultante al
contexto de TODA la sesión, no solo de ese turno) incluso para conversaciones
que el agente iba a descartar por no ser sobre el menú. Un primer intento
mitigó esto con una heurística de palabras clave en Python (adivinar de
antemano si el mensaje "parecía" del menú) — funcional, pero un parche: el
propio agente ya sabe aplicar el filtro de tema con criterio real, así que
lo correcto es dejar que sea ÉL quien decida cuándo pedir este dato, en el
momento exacto en que la skill lo necesita (pasos 1 y 2 de
propuesta-menu-semanal), no antes. Esta herramienta reemplaza esa
heurística: ver SKILL.md de propuesta-menu-semanal, pasos 1 y 2, para el
criterio de cuándo llamarla.
"""
from claude_agent_sdk import create_sdk_mcp_server, tool

from .notion_direct import fetch_context_preload


@tool(
    "precargar_contexto",
    (
        "SOLO tiene sentido en el canal web de Huevereke, y SOLO una vez "
        "que ya decidiste que esta conversación sí es sobre armar una "
        "propuesta de menú semanal (es decir, después de aplicar el "
        "filtro de tema del canal) — no la llames para conversaciones "
        "fuera de ese tema. Trae de una sola vez, por REST directo y sin "
        "gastar cuota de 'Query Data Source' de Notion: el catálogo de "
        "platos principales con combo de ensalada y acompañamientos ya "
        "resueltos (paso 1 de propuesta-menu-semanal), las últimas "
        "semanas (paso 2), el vocabulario de combos de Ensalada, y las "
        "URLs ya resueltas de Ensalada/Insumos de desayuno/cada "
        "plato-acompañamiento. Llámala una sola vez por conversación, sin "
        "argumentos — no la repitas más adelante en la misma corrida. Si "
        "falla, sigue con las consultas normales a Notion para esos pasos."
    ),
    {},
)
async def precargar_contexto(args: dict) -> dict:
    try:
        texto = await fetch_context_preload()
    except Exception as exc:  # noqa: BLE001
        texto = (
            f"[No se pudo precargar el catálogo/semanas automáticamente "
            f"({exc}). Consulta Notion normalmente para los pasos 1 y 2 "
            f"de propuesta-menu-semanal.]"
        )
    return {"content": [{"type": "text", "text": texto}]}


web_context_server = create_sdk_mcp_server(
    name="web_context",
    version="0.1.0",
    tools=[precargar_contexto],
)

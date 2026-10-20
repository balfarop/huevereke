"""
Tres herramientas de sesión — a diferencia de web_context_tools.py y
menu_cache/sdk_tools.py (servidores construidos UNA sola vez a nivel de
módulo, sin estado propio de sesión), estas necesitan una
ConversationSession puntual — por eso build_conversation_tools_server(session)
es una fábrica que arma un servidor MCP nuevo por sesión, capturando esa
sesión por clausura (ver agent_service.build_options, que la llama una vez
por ConversationSession nueva):

- cerrar_conversacion: la skill la llama como última acción cuando una
  propuesta (real o de prueba) terminó. Antes de esto, "cerrar la
  conversación" no tenía ninguna señal explícita del lado del agente — la
  única forma de liberar el contexto acumulado (y dejar de pagar tokens
  por él en el siguiente mensaje) era esperar los 60 minutos de
  inactividad (SESSION_IDLE_MINUTES) o que el usuario le diera a "Nueva
  conversación" a mano. Con esta herramienta, la skill misma avisa en el
  momento exacto en que una propuesta terminó — recién ahí drain_messages
  (agent_service.py) le manda al navegador el aviso de cierre, y solo
  cuando el usuario lo confirma (o usa "Nueva conversación" en cualquier
  momento) se cierra de verdad la sesión — ver POST /chat/close en main.py.

- mostrar_reporte: muestra al usuario una tarjeta con el menú/reporte
  completo (el usuario no ve el razonamiento del agente ni los resultados
  de sus herramientas). La tarjeta la emite agent_service.drain_messages
  leyendo la llamada, no esta herramienta, para conservar el orden real.

- guardar_notas_de_prueba: SOLO funciona si session.test_mode es True (lo
  verifica ella misma, no depende de que el agente respete la skill) —
  guarda el resumen curado de una corrida de prueba como sub-página de la
  página fija TEST_NOTES_PARENT_PAGE_ID (ver notion_direct.py), nunca de
  una Semana real ni siquiera momentáneamente. Existe porque el bloqueo
  normal de escrituras a Notion en modo prueba (ver
  agent_service._make_web_can_use_tool) es deliberadamente total — bueno
  para no arriesgar nada por default, pero eso dejaba sin forma de probar
  de punta a punta la escritura de la sub-página de notas (la única pieza
  realmente nueva de esa parte del flujo) antes de confiarla a una semana
  real. Es una herramienta DISTINTA de las de Notion del agente (su nombre
  no contiene "notion"), así que el bloqueo de modo prueba ni la ve — el
  límite acá es el destino fijo hardcodeado, no un permiso genérico.
"""
from claude_agent_sdk import create_sdk_mcp_server, tool

from .notion_direct import create_test_notes_page


def build_conversation_tools_server(session):
    @tool(
        "cerrar_conversacion",
        (
            "Llámala UNA SOLA VEZ, como tu última acción, cuando esta "
            "conversación de propuesta-menu-semanal (real o de prueba) ya "
            "terminó — es decir, justo después de completar el paso 7 "
            "(Semana guardada + sub-página de notas creada) en una corrida "
            "real, o justo después de que el usuario confirme que una "
            "corrida de prueba puede darse por cerrada sin guardar nada. "
            "No la llames a mitad de la propuesta, ni si el usuario todavía "
            "puede querer seguir ajustando algo. `guardado=true` solo si "
            "de verdad se escribió en Notion en esta llamada — si estabas "
            "en modo prueba, o el usuario nunca llegó a confirmar el "
            "guardado, usa `guardado=false`. `motivo` es una frase muy "
            "corta para mostrarle al usuario (ej. 'Semana del 14 de "
            "septiembre guardada' o 'Prueba terminada, no se guardó "
            "nada')."
        ),
        {"guardado": bool, "motivo": str},
    )
    async def cerrar_conversacion(args: dict) -> dict:
        session.close_signal = {
            "guardado": bool(args.get("guardado")),
            "motivo": str(args.get("motivo") or ""),
        }
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Listo, quedó registrado el cierre de esta "
                        "conversación — no hace falta ninguna otra acción "
                        "de tu parte, el navegador se encarga de mostrar el "
                        "aviso correspondiente."
                    ),
                }
            ]
        }

    @tool(
        "guardar_notas_de_prueba",
        (
            "SOLO tiene sentido en modo prueba (si no lo está, no hace "
            "nada y te avisa). Úsala cuando, en una corrida de prueba, el "
            "usuario confirme por AskUserQuestion que quiere ver cómo "
            "habría quedado el resumen de notas de esta conversación — "
            "nunca la llames sin haber preguntado antes, y nunca más de "
            "una vez por conversación. `resumen` es el mismo tipo de "
            "contenido que normalmente iría en la sub-página real del "
            "paso 7 (ver esa sección de la skill): curado y breve, no el "
            "transcript. Devuelve la URL de la página creada — pásasela "
            "al usuario tal cual, en su mensaje, para que sepa dónde "
            "borrarla después; esta página NO es una Semana real ni "
            "cuelga de ninguna, así que borrarla no afecta ningún dato "
            "real."
        ),
        {"resumen": str},
    )
    async def guardar_notas_de_prueba(args: dict) -> dict:
        if not session.test_mode:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Esta herramienta solo funciona en modo prueba — "
                            "esta conversación no lo está, así que no hice "
                            "nada."
                        ),
                    }
                ]
            }
        resumen = str(args.get("resumen") or "").strip()
        try:
            url = await create_test_notes_page(resumen)
        except Exception as exc:  # noqa: BLE001
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"No se pudo guardar la página de notas de "
                            f"prueba ({exc}). Seguí sin guardar nada — "
                            f"avísale al usuario en una frase, sin sonar "
                            f"como si fuera un error grave."
                        ),
                    }
                ]
            }
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Página de notas de prueba creada: {url}",
                }
            ]
        }

    @tool(
        "mostrar_reporte",
        (
            "Muestra al usuario, en pantalla, una tarjeta con el menú, la "
            "lista de compras o el reporte final COMPLETO y actualizado. "
            "El usuario NO ve tu razonamiento ni los resultados de tus "
            "herramientas, solo tus mensajes de texto, esta tarjeta y tus "
            "preguntas: úsala siempre ANTES de una AskUserQuestion que le "
            "pida revisar o confirmar algo (la primera propuesta detallada, "
            "cada versión ajustada tras un cambio suyo, y el reporte final "
            "previo a guardar). Nunca digas 'te lo reenvío' o 'te lo "
            "muestro' sin llamar a esta herramienta o escribir el "
            "contenido en tu mensaje. `titulo` es corto (ej. 'Propuesta "
            "ajustada'); `contenido` va en markdown simple (títulos con "
            "**negrita**, listas con '- ')."
        ),
        {"titulo": str, "contenido": str},
    )
    async def mostrar_reporte(args: dict) -> dict:
        # La tarjeta la manda al navegador drain_messages (agent_service.py)
        # leyendo esta misma llamada, para respetar el orden real de los
        # bloques; acá solo confirmamos.
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Listo, el reporte ya se muestra en la pantalla del "
                        "usuario. Ahora sí puedes hacerle tu pregunta."
                    ),
                }
            ]
        }

    return create_sdk_mcp_server(
        name="conversation",
        version="0.1.0",
        tools=[cerrar_conversacion, guardar_notas_de_prueba, mostrar_reporte],
    )

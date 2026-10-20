"""
Configuración de la app, leída de variables de entorno.
En desarrollo local, colocar estos valores en un archivo .env
(ver .env.example) y se cargan automáticamente.
"""
import os
import sys

from dotenv import load_dotenv

# En Windows, asyncio necesita el ProactorEventLoop para poder lanzar
# subprocesos (el CLI de Claude Code, y npx para el conector de Notion).
# Uvicorn a veces termina usando SelectorEventLoop (que no soporta
# subprocesos en Windows y falla con NotImplementedError). Forzamos la
# política acá, en el módulo que se importa primero, para que quede
# fijada antes de que uvicorn cree su propio loop. No afecta Linux/Mac
# (donde este bloque simplemente no se ejecuta).
if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

load_dotenv()


def _split_emails(raw: str) -> set[str]:
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


class Settings:
    # --- Google OAuth ---
    GOOGLE_CLIENT_ID: str = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    # URL pública completa de tu app + /auth/callback
    # ej: https://menu-semanal.fly.dev/auth/callback
    GOOGLE_REDIRECT_URI: str = os.environ.get("GOOGLE_REDIRECT_URI", "")

    # Lista blanca: únicos correos autorizados a entrar (tú y tu esposa)
    ALLOWED_EMAILS: set[str] = _split_emails(os.environ.get("ALLOWED_EMAILS", ""))

    # --- Sesión / cookies ---
    SESSION_SECRET: str = os.environ.get("SESSION_SECRET", "cambia-esto-en-produccion")

    # --- Claude Agent SDK ---
    # Generado una vez con: claude setup-token
    CLAUDE_CODE_OAUTH_TOKEN: str = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")

    # Ruta explícita al ejecutable nativo de Claude Code. Necesario en Windows:
    # sin esto, distintos procesos (terminal vs. subproceso de uvicorn) pueden
    # resolver el `claude.cmd` de npm en vez del .exe nativo y fallar con
    # "Failed to start Claude Code: [WinError 193]". Déjalo vacío en Linux/Mac
    # (el Dockerfile de despliegue no lo necesita).
    CLAUDE_CLI_PATH: str = os.environ.get("CLAUDE_CLI_PATH", "")

    # --- Skills ---
    # Carpeta raíz del proyecto (donde vive .claude/skills). El Agent SDK
    # descubre skills en <cwd>/.claude/skills, así que este PROJECT_ROOT
    # es lo que se pasa como cwd al conectar el cliente.
    PROJECT_ROOT: str = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )
    SKILLS_DIR: str = os.path.join(PROJECT_ROOT, ".claude", "skills")

    # Minutos de inactividad tras los cuales se descarta la sesión de conversación
    # (libera memoria; no afecta el auto-stop del contenedor, eso lo maneja Fly/Render)
    SESSION_IDLE_MINUTES: int = int(os.environ.get("SESSION_IDLE_MINUTES", "60"))

    # --- Consulta directa a Notion (solo para el sidebar de últimas semanas) ---
    # Un token de integración interna DISTINTO del login OAuth que usa el
    # agente — esto no pasa por el Agent SDK ni por mcp-remote. Crear en
    # https://www.notion.so/my-integrations y compartir la página "Menu
    # Semanal" con esa integración (o al menos las bases Semanas y Platos).
    NOTION_TOKEN: str = os.environ.get("NOTION_TOKEN", "")

    # El UUID (sin el prefijo "collection://") de la fuente de datos de la
    # base "Semanas". Se puede obtener de los logs del agente (aparece como
    # data_source_url al consultar Semanas) o pidiéndoselo una vez al agente.
    SEMANAS_DATA_SOURCE_ID: str = (
        os.environ.get("SEMANAS_DATA_SOURCE_ID") or "cb2d66b6-1ff9-4bc4-8717-1bcd19733aa6"
    )
    PLATOS_DATA_SOURCE_ID: str = (
        os.environ.get("PLATOS_DATA_SOURCE_ID") or "8adf66ab-2faa-4d73-ba28-43f6dda95b36"
    )
    # Fase 6 (proyecto de acompañamientos estructurados): necesario para
    # precargar el vocabulario de combos de Ensalada (Receta_Insumo.Combos).
    RECETA_INSUMO_DATA_SOURCE_ID: str = (
        os.environ.get("RECETA_INSUMO_DATA_SOURCE_ID") or "cd28e58f-a900-49e5-9456-8e5b5d0bb4ac"
    )
    # Faltan por completar (ver .env.example) — solo hacen falta para
    # app.menu_cache.sync, que sí necesita las 5 bases completas (a
    # diferencia de notion_direct.py, que hoy solo usa Semanas, Platos y
    # Receta_Insumo para el precargado fijo).
    INSUMOS_DATA_SOURCE_ID: str = os.environ.get("INSUMOS_DATA_SOURCE_ID", "")
    SEMANA_INSUMO_FAMILIAR_DATA_SOURCE_ID: str = os.environ.get(
        "SEMANA_INSUMO_FAMILIAR_DATA_SOURCE_ID", ""
    )

    # ID (sin guiones o con, cualquiera de los dos formatos funciona con la
    # API de Notion) de una página fija, creada una sola vez a mano en
    # Notion (ej. titulada "🧪 Huevereke — pruebas"), dedicada SOLO a
    # recibir las sub-páginas de notas de conversación de corridas en modo
    # prueba (ver app/conversation_tools.py y el paso 7 de
    # propuesta-menu-semanal) — nunca una Semana real, ni siquiera
    # momentáneamente. Compartila con la MISMA integración interna de
    # NOTION_TOKEN de arriba (no con el login OAuth del agente), porque el
    # guardado de notas de prueba usa ese mismo cliente REST directo, igual
    # que fetch_context_preload. Si queda vacío, la app simplemente no
    # ofrece la opción de guardar notas en modo prueba.
    TEST_NOTES_PARENT_PAGE_ID: str = os.environ.get("TEST_NOTES_PARENT_PAGE_ID", "")

    # --- Caché local del menú (SQLite) — ver app/menu_cache/ ---
    MENU_CACHE_DB_PATH: str = os.environ.get("MENU_CACHE_DB_PATH") or os.path.join(
        PROJECT_ROOT, "menu_cache.sqlite3"
    )

    # NOTA sobre el contador de consumo de la UI (ver agent_service.py):
    # se probó agregar acá una constante de "crédito mensual del Agent SDK"
    # (Pro=$20/mes) para mostrar un % aproximado, basada en un cambio de
    # facturación que Anthropic anunció para el 15/jun/2026 (Agent SDK,
    # `claude -p` y apps de terceros pasarían a un crédito mensual en
    # dólares, aparte de los límites normales). Anthropic pausó ese cambio
    # el mismo día en que debía entrar en vigor ("For now, nothing has
    # changed" — ver su Help Center) y, confirmado además por el propio
    # usuario viendo sus barras de Claude Desktop moverse al usar esta app,
    # hoy el consumo del Agent SDK sigue saliendo de la MISMA ventana de 5
    # horas / límite semanal que el uso interactivo de Claude Code/Desktop
    # — no de un crédito aparte. Por eso se quitó esa constante: no hay hoy
    # ningún equivalente en dólares de esos límites contra el cual calcular
    # un % real. Si Anthropic reactiva el crédito separado en el futuro,
    # recién ahí tendría sentido volver a agregar algo así.


settings = Settings()

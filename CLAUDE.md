# Huevereke (menu-semanal-web-cache)

Chat web (FastAPI + SSE) con un agente que arma la propuesta de menú semanal. Corre sobre el Claude Agent SDK con la suscripción Pro (`CLAUDE_CODE_OAUTH_TOKEN`), y lee/escribe en Notion. Este directorio es un fork paralelo que agrega un caché SQLite local para no agotar la cuota de "Query Data Source" de Notion. Repo: https://github.com/balfarop/huevereke (rama `main`).

Uso actual: solo el dueño, **en local** (`uvicorn`). No hay despliegue activo; `Dockerfile` y `fly.toml` existen pero no se usan por ahora. Ver `README.md` para la configuración completa (tokens, Google OAuth, integración de Notion).

## Comandos (Windows, desde la raíz)

```bash
venv/Scripts/activate                 # entorno virtual ya creado
pip install -r requirements.txt
uvicorn app.main:app --reload         # http://localhost:8000
python chat_cli.py                    # probar el agente sin la web
python build_cache.py [--sample]      # fuerza la resincronización del caché (normalmente ya es automática)
```

No hay tests automatizados. Para verificar cambios: usar el toggle "Modo prueba" de la web (bloquea toda escritura a Notion a nivel de código) o `chat_cli.py`.

## Arquitectura

- `app/main.py`: rutas (`/chat` en streaming SSE, `/chat/answer` para `AskUserQuestion`, `/chat/close`, `/weeks/recent`, `/health`, login Google en `app/auth.py`).
- `app/agent_service.py`: un `ClaudeSDKClient` por sesión de navegador, en memoria. `can_use_tool` aprueba todo salvo `AskUserQuestion`; en modo prueba solo deja pasar herramientas de Notion de solo lectura (lista blanca por nombre).
- `app/notion_direct.py`: llamadas directas a la API REST de Notion (sidebar de semanas y precargado de contexto), con `NOTION_TOKEN` (integración interna). No pasa por el agente ni gasta cuota de suscripción.
- `app/web_context_tools.py`, `app/conversation_tools.py`: herramientas MCP en proceso (precargado de contexto, notas de conversación, `mostrar_reporte`).
- **Qué ve el usuario:** solo texto del agente, tarjetas de `mostrar_reporte`, preguntas y la línea de estado. El razonamiento (`ThinkingBlock`) y los resultados de herramientas no se envían; solo generan eventos `{"status": ...}` con textos genéricos (`_status_for_tool`). Si el agente pregunta por el reporte/versión/lista sin haber mostrado contenido antes, `can_use_tool` rechaza la pregunta una vez y le pide mostrarlo (`shown_since_input`).
- `app/menu_cache/`: caché SQLite (`schema.py`, `sync.py`, `queries.py`) y sus herramientas para el agente (`sdk_tools.py`, expuestas como `mcp__menu_cache__*`). Cada llamada abre su propia conexión, así que no hace falta reiniciar tras `build_cache.py`.
- `.claude/skills/`: `propuesta-menu-semanal`, `notion-interaccion` (esquema de las 5 bases de Notion) y `analisis-plato-insumo`. El agente decide en `propuesta-menu-semanal` cuándo usar el caché o Notion en vivo. `analisis-plato-insumo` se usaba en Claude Desktop para cargar el historial de platos; ya no se usa, pero se conserva como respaldo.
- `app/static/`: frontend de una sola página (`index.html`, `app.js`, `style.css`). El `index.html` de la raíz es una página de presentación aparte, heredada del repo remoto.

## Cosas a tener en cuenta

- **El caché se actualiza solo** (`ensure_fresh()` en `app/menu_cache/sync.py`): al arrancar la app, al empezar una conversación y al guardar una Semana, siempre que tenga más de `MENU_CACHE_MAX_AGE_MINUTES` (30 por defecto). También crea tablas nuevas del esquema. Si Notion falla, sigue con los datos anteriores y lo deja en el log. `python build_cache.py` queda para forzarlo a mano. La sincronización es completa, no incremental. `menu_cache.sqlite3` está en `.gitignore`.
- `.env` tiene secretos y está ignorado por git. Nunca imprimirlo ni commitearlo; `.env.example` es la plantilla.
- Windows: `CLAUDE_CLI_PATH` debe apuntar al ejecutable nativo de Claude (evita `WinError 193`), y `config.py` fuerza `WindowsProactorEventLoopPolicy` para poder lanzar subprocesos.
- El conector de Notion del agente usa `npx mcp-remote` (OAuth por navegador, token cacheado en disco). Es distinto del `NOTION_TOKEN` interno. Si la integración interna da 404 en una base, hay que conectarla directamente a esa base en Notion.
- Las skills y el esquema de Notion están en español; mantener el idioma y el tono de comentarios y mensajes del código (español).
- Cuando una base de Notion cambie de estructura, actualizar `notion-interaccion/references/notion-schema.md` y `app/menu_cache/schema.py` juntos.

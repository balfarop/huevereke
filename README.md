> **Este es un fork paralelo de Huevereke**, para el caché local (SQLite)
> que evita agotar la cuota compartida de "Query Data Source" de Notion
> durante `propuesta-menu-semanal`. Todo lo de abajo (auth, sesión,
> streaming, agente) es una copia sin cambios de la app que ya funciona —
> lo nuevo vive en `app/menu_cache/` y en `build_cache.py`.
>
> **Estado actual: caché conectado al agente.** `app/menu_cache/sdk_tools.py`
> ya expone las herramientas `mcp__menu_cache__*` (`platos_principales`,
> `combos_ensalada`, `receta_insumo_por_platos`, etc.) y `agent_service.py`
> las suma al Agent SDK — la skill `propuesta-menu-semanal` decide cuándo
> usarlas en vez de consultar Notion en vivo (ver sus pasos 3b/4b/5).
>
> **Cómo actualizar el caché** (por ejemplo, después de que
> `analisis-plato-insumo`, `curar-combos-plato`, o cualquier edición manual
> cambien Platos/Insumos/Receta_Insumo/Semanas en Notion — el caché NO se
> refresca solo):
>
> ```bash
> python build_cache.py            # sincroniza y muestra conteos
> python build_cache.py --sample   # además imprime filas de muestra, para verificar
> ```
>
> Corre esto desde la raíz del proyecto (donde está tu `.env`, con
> `NOTION_TOKEN` y las 5 `*_DATA_SOURCE_ID` — necesitas tener completados
> `INSUMOS_DATA_SOURCE_ID` y `SEMANA_INSUMO_FAMILIAR_DATA_SOURCE_ID`, el
> resto ya trae default). Es una sincronización completa (borra e
> reinserta cada tabla), no incremental, así que siempre refleja el estado
> actual de Notion. No hace falta reiniciar la app ni el agente después:
> cada herramienta `mcp__menu_cache__*` abre su propia conexión al archivo
> SQLite en cada llamada, así que la próxima conversación ya lee los datos
> frescos.

# Huevereke — chat web con tu agente

App de una sola página donde tú y tu esposa conversan con el agente de
propuesta de menú semanal, sin Telegram ni WhatsApp de por medio.

Usa el **Claude Agent SDK autenticado con tu suscripción Pro** (no una
API key facturada por token), y **Fly.io con auto-stop** para que no
pagues nada mientras nadie la está usando entre semana.

## 1. Requisitos previos

- Python 3.12+
- Node.js 20+ (necesario porque el Agent SDK corre el CLI de Claude Code
  como subproceso, y porque usamos `npx mcp-remote` para el conector de Notion)
- Una cuenta de Google Cloud (gratis) para el login
- Tu cuenta de Notion con el conector ya usado en Claude Desktop

## 2. Generar el token de tu suscripción Pro

Esto reemplaza la API key de tu arquitectura anterior. Se hace **una
sola vez, desde tu propia compu** (requiere navegador):

```bash
npm install -g @anthropic-ai/claude-code
claude setup-token
```

Te va a abrir el navegador para loguearte con tu cuenta de Claude Pro,
y al final te muestra un token con formato `sk-ant-oat01-...`. Guárdalo
— no se vuelve a mostrar. Ese es tu `CLAUDE_CODE_OAUTH_TOKEN`.

**Solo en Windows:** además del paquete npm, instala la versión nativa
y apunta `CLAUDE_CLI_PATH` en tu `.env` a ella — evita un error de
resolución de PATH (`WinError 193`) entre distintos procesos (terminal
vs. subproceso de uvicorn):

```powershell
irm https://claude.ai/install.ps1 | iex
Get-Command claude | Select-Object Source   # copia esa ruta a CLAUDE_CLI_PATH
```

**Nota de estabilidad:** al día de hoy este modo de autenticación
consume tus límites normales de la suscripción, no tokens de API. Es
una configuración que Anthropic pausó a mitad de año tras haber
anunciado un cambio, y dijeron que van a seguir ajustando el modelo —
o sea que no es algo 100% garantizado a futuro. Conviene revisar cada
cierto tiempo el artículo oficial: https://support.claude.com/en/articles/15036540

## 3. Copiar tus skills existentes

El Agent SDK descubre skills igual que Claude Code/Desktop: en una
carpeta `.claude/skills` dentro del directorio de trabajo. Copia ahí
tus tres skills tal como están hoy:

```bash
cp -r /ruta/a/tus/skills/propuesta-menu-semanal .claude/skills/
cp -r /ruta/a/tus/skills/analisis-plato-insumo .claude/skills/
cp -r /ruta/a/tus/skills/notion-interaccion .claude/skills/
```

No deberían necesitar cambios — el agente las detecta automáticamente
por su descripción, igual que en Desktop.

## 4. Conectar Notion (una sola vez, con navegador)

Tus skills están escritas contra el **conector oficial de Notion**
(las mismas herramientas `notion-fetch`, `notion-search`,
`notion-query-data-sources`, etc. que usas en Claude Desktop) — no
contra un token de integración interna. Para que el Agent SDK tenga
acceso a ese mismo conector, usamos `mcp-remote` como puente OAuth.

No hace falta instalar nada aparte: la primera vez que corras
`chat_cli.py` (o la app), `npx mcp-remote` se descarga solo y te abre
el navegador para loguearte con tu cuenta de Notion y aprobar el
acceso. Después de eso, el token queda cacheado en disco y se renueva
solo — no vuelve a pedir login mientras seas tú corriendo el proceso
en la misma máquina.

**Importante para cuando lleguemos al deploy:** ese cache de token
vive en el sistema de archivos local. En un contenedor que se reinicia
(Fly.io/Render con auto-stop), hay que persistirlo en un volumen o
resolver el login de otra forma — lo vemos en detalle cuando lleguemos
a esa parte.

## 4b. Integración interna de Notion (sidebar + precargado de contexto)

Tanto el sidebar de últimas semanas como el precargado de contexto al
iniciar una conversación (catálogo de platos, semanas, combos de
Ensalada — ver Fase 6) son **consultas fijas y determinísticas** — no
necesitan que un agente razone, así que van directo contra la API REST de
Notion, sin pasar por el Agent SDK ni gastar nada de tu cuota de
suscripción. Esto usa un mecanismo de autenticación *distinto* al del
punto anterior:

1. Ir a https://www.notion.so/my-integrations → "New integration" →
   tipo interna
2. Copiar el "Internal Integration Secret" (empieza con `ntn_` o
   `secret_`) a `NOTION_TOKEN` en tu `.env`
3. Abrir la página "Menu Semanal" en Notion → "..." → "Connections" →
   conectar la integración que acabas de crear (en teoría esto comparte
   automáticamente todas las bases hijas: Platos, Insumos,
   Receta_Insumo, Semanas, Semana_Insumo_Familiar)

Ya viene con un valor por defecto para `SEMANAS_DATA_SOURCE_ID`,
`PLATOS_DATA_SOURCE_ID` y `RECETA_INSUMO_DATA_SOURCE_ID` (los de tu
workspace, confirmados en pruebas) — no deberías necesitar tocarlos salvo
que cambies la estructura de esas bases en Notion. El último se usa desde
la Fase 6 para precargar el vocabulario de combos de Ensalada.

**Troubleshooting — error 404 "Could not find data_source" en el log del
precargado:** en la práctica, compartir la página "Menu Semanal" no
siempre cascadea a **todas** las bases hijas si algunas se agregaron
después de la conexión inicial (le pasó al proyecto real con
`Receta_Insumo`). Si ves este error, comparte esa base **directamente**:
abre `Receta_Insumo` en Notion → "..." → "Connections" → conecta la
integración ahí también, sin depender de la herencia desde la página
padre. El precargado está diseñado para degradarse con gracia — si falla
solo la parte de combos de Ensalada, igual entrega el catálogo de platos
y las semanas (que suelen funcionar desde el principio), así que este
error no bloquea el uso de la app, solo te deja sin ese pedazo del
precargado hasta que lo resuelvas.

## 4c. (Opcional) Página de "zona de pruebas" para el modo prueba

La web tiene un toggle "Modo prueba" que bloquea, a nivel de código, toda
escritura a Notion durante esa conversación — útil para probar el flujo
completo de una propuesta sin arriesgar datos reales. Si además querés
poder guardar (y después borrar a mano) un resumen de esas corridas de
prueba, para revisar cómo habría quedado la sub-página real de notas de
conversación del paso 7:

1. Crear una página cualquiera en Notion (ej. titulada "🧪 Huevereke —
   pruebas") — no hace falta que esté dentro de "Menu Semanal", puede
   vivir en cualquier lado de tu workspace.
2. Compartirla con la MISMA integración interna del punto 4b (no el login
   OAuth del agente): abrirla → "..." → "Connections" → conectar esa
   integración.
3. Copiar su ID (el bloque de 32 caracteres en la URL de la página, antes
   de cualquier `?`) a `TEST_NOTES_PARENT_PAGE_ID` en tu `.env`.

Si dejás esto sin configurar, el modo prueba sigue funcionando igual
(bloqueando escrituras) — simplemente el agente no va a poder ofrecerte
la opción de guardar ese resumen de prueba.

## 5. Configurar el login con Google

1. Ir a https://console.cloud.google.com/apis/credentials
2. Crear un "OAuth Client ID" de tipo "Web application"
3. En "Authorized redirect URIs" agregar: `https://TU-DOMINIO/auth/callback`
   (con el dominio real que te dé Fly.io o Render — puedes empezar con
   `http://localhost:8000/auth/callback` para probar en local)
4. Copiar el Client ID y Client Secret a tu `.env`

## 6. Variables de entorno

```bash
cp .env.example .env
# y completar cada valor
```

`ALLOWED_EMAILS` es la única "base de usuarios" que existe — dos
correos separados por coma, el tuyo y el de tu esposa.

## 7. Probar en local

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Abrir http://localhost:8000 — debería redirigirte a Google, y tras
loguearte con un correo de la lista blanca, ver el chat.

## 8. Desplegar en Fly.io

```bash
curl -L https://fly.io/install.sh | sh
fly launch --no-deploy   # usa el fly.toml incluido, dile que no a Postgres/Redis
fly secrets set \
  GOOGLE_CLIENT_ID=... \
  GOOGLE_CLIENT_SECRET=... \
  GOOGLE_REDIRECT_URI=https://tu-app.fly.dev/auth/callback \
  ALLOWED_EMAILS=tucorreo@gmail.com,esposa@gmail.com \
  SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))") \
  CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
fly deploy
```

`min_machines_running = 0` en el `fly.toml` es lo que hace que no pague
nada mientras nadie entra a la página entre semana — la máquina se
prende sola con la primera visita del sábado y se apaga sola tras un
rato de inactividad.

## Qué falta afinar (según cómo lo pruebes)

- Si el arranque en frío (primeros segundos tras estar dormido) se
  siente muy lento, se puede ajustar `min_machines_running` a 1 en
  fly.toml a cambio de un costo fijo pequeño — evalúalo después de
  probarlo un par de fines de semana.
- Si en algún momento ves errores de token expirado en producción,
  puede ser el problema conocido de refresco de tokens OAuth en
  procesos de larga duración — la solución es volver a correr
  `claude setup-token` y actualizar el secret.
- El cache de `mcp-remote` (donde queda el token de Notion tras el
  login por navegador) vive en el filesystem local — en Fly.io con
  auto-stop, hay que revisar si sobrevive a que la máquina se apague
  y prenda, o si hace falta un volumen persistente. Pendiente de
  probar en el paso de deploy.
- El `can_use_tool` en `agent_service.py` aprueba automáticamente
  cualquier herramienta salvo `AskUserQuestion` (que pausa esperando tu
  respuesta en el navegador). Bien para probar en local; antes de que
  tu esposa use la web, vale la pena revisar si acotar qué se aprueba
  sin preguntar (ej. escrituras en Notion).

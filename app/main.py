import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .agent_service import close_session, continue_reply, stream_reply, submit_answer
from .auth import current_user_email, is_email_allowed, oauth, require_login
from .config import settings
from .menu_cache.sync import ensure_fresh
from .notion_direct import fetch_recent_weeks

logger = logging.getLogger("menu_semanal")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Pone el caché local al día en segundo plano (no retrasa el arranque):
    # crea tablas nuevas del esquema y re-sincroniza si está viejo. Ya no
    # hace falta correr build_cache.py a mano para el uso normal.
    refresh_task = asyncio.create_task(ensure_fresh())
    try:
        yield
    finally:
        refresh_task.cancel()


app = FastAPI(title="Huevereke", lifespan=lifespan)

app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_SECRET)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/login")
async def login(request: Request):
    redirect_uri = settings.GOOGLE_REDIRECT_URI
    # prompt=select_account evita que Google reautentique en silencio con la
    # sesión de Google ya activa en el navegador — te obliga a elegir cuenta
    # de nuevo, que es lo que se espera después de un logout real.
    return await oauth.google.authorize_redirect(
        request, redirect_uri, prompt="select_account"
    )


@app.get("/auth/callback")
async def auth_callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    userinfo = token.get("userinfo") or {}
    email = (userinfo.get("email") or "").lower()

    if not email or not is_email_allowed(email):
        return HTMLResponse(
            "<h1>Acceso no autorizado</h1><p>Este correo no está en la lista permitida.</p>",
            status_code=403,
        )

    request.session["user_email"] = email
    request.session.setdefault("session_id", str(uuid.uuid4()))
    return RedirectResponse(url="/")


@app.get("/logout")
async def logout(request: Request):
    session_id = request.session.get("session_id")
    if session_id:
        await close_session(session_id)
    request.session.clear()
    return RedirectResponse(url="/login")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    email = current_user_email(request)
    if not email:
        return RedirectResponse(url="/login")
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(html.replace("{{USER_EMAIL}}", email))


@app.post("/chat")
async def chat(request: Request, user_email: str = Depends(require_login)):
    body = await request.json()
    message = (body.get("message") or "").strip()
    # Solo importa para el PRIMER mensaje de una sesión nueva — ver
    # ConversationSession.test_mode/_get_or_create_session. El toggle del
    # navegador se deshabilita mientras haya una conversación abierta, así
    # que un valor distinto en un mensaje posterior de la misma sesión ni
    # siquiera debería poder llegar, pero igual se ignora ahí por las dudas.
    test_mode = bool(body.get("test_mode"))
    session_id = request.session.get("session_id")
    if not session_id:
        session_id = str(uuid.uuid4())
        request.session["session_id"] = session_id

    if not message:
        return StreamingResponse(iter([]), media_type="text/event-stream")

    async def event_stream():
        try:
            async for payload in stream_reply(session_id, message, test_mode=test_mode):
                yield f"data: {json.dumps(payload)}\n\n"
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fallo en stream_reply para session_id=%s", session_id)
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        finally:
            yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/chat/close")
async def chat_close(request: Request, user_email: str = Depends(require_login)):
    """
    Cierra (si existe) la conversación actual: descarta el ClaudeSDKClient
    de la sesión (se pierde el historial acumulado a propósito — ver
    ConversationSession) y libera el session_id de la cookie para que el
    próximo mensaje arranque una conversación 100% nueva, sin arrastrar
    nada. La llama tanto el botón manual "Nueva conversación" (en cualquier
    momento) como el aviso de cierre que aparece cuando el propio agente
    señala con mcp__conversation__cerrar_conversacion que una propuesta
    (real o de prueba) ya terminó (ver stream_reply/drain_messages).
    """
    session_id = request.session.pop("session_id", None)
    if session_id:
        await close_session(session_id)
    return {"status": "ok"}


@app.post("/chat/answer")
async def chat_answer(request: Request, user_email: str = Depends(require_login)):
    """
    El navegador llama acá cuando el usuario responde una pregunta de
    AskUserQuestion puntual (identificada por question_id — puede haber
    más de una pendiente a la vez en el mismo turno, ver agent_service.py).

    Si todavía queda otra pregunta de la misma tanda sin responder, esta
    respuesta no abre stream — solo confirma. Recién cuando se responde la
    ÚLTIMA pregunta pendiente de ese turno, esta misma llamada retoma el
    streaming de la continuación (ver continue_reply): así, si la conexión
    original de /chat se cortó mientras el usuario pensaba las respuestas
    (pasa con conexiones inactivas por varios minutos), no se pierde el
    hilo — y evitamos que dos respuestas distintas compitan por los mismos
    eventos si abriéramos stream en cada una.
    """
    body = await request.json()
    answers = body.get("answers") or {}
    question_id = body.get("question_id") or ""
    session_id = request.session.get("session_id")
    if not session_id:
        return JSONResponse({"error": "Sin sesión activa"}, status_code=400)
    if not question_id:
        return JSONResponse({"error": "Falta question_id"}, status_code=400)

    ok, more_pending = submit_answer(session_id, question_id, answers)
    if not ok:
        return JSONResponse(
            {"error": "No hay ninguna pregunta pendiente con ese id"}, status_code=409
        )

    if more_pending:
        return {"status": "ok"}

    async def event_stream():
        try:
            async for payload in continue_reply(session_id):
                yield f"data: {json.dumps(payload)}\n\n"
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fallo en continue_reply para session_id=%s", session_id)
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        finally:
            yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/weeks/recent")
async def weeks_recent(refresh: bool = False, user_email: str = Depends(require_login)):
    try:
        weeks = await fetch_recent_weeks(force_refresh=refresh)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Fallo al obtener las últimas semanas")
        return JSONResponse({"error": str(exc)}, status_code=500)
    return {"weeks": weeks}


@app.get("/favicon.ico")
async def favicon():
    return RedirectResponse(url="/static/img/huevereke.png")


@app.get("/health")
async def health():
    return {"status": "ok"}

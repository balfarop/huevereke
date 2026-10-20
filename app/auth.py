"""
Login con Google, restringido a una lista blanca de correos.

No hay base de datos de usuarios: si el correo que inicia sesión
no está en ALLOWED_EMAILS, se rechaza el acceso. Tras un login
válido, se guarda el correo en la sesión (cookie firmada).
"""
from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request

from .config import settings

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def current_user_email(request: Request) -> str | None:
    return request.session.get("user_email")


def require_login(request: Request) -> str:
    """Dependency de FastAPI: lanza 401 si no hay sesión válida."""
    email = current_user_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="No autenticado")
    return email


def is_email_allowed(email: str) -> bool:
    if not settings.ALLOWED_EMAILS:
        # Si no configuraste la lista blanca, por seguridad no se deja pasar a nadie.
        return False
    return email.lower() in settings.ALLOWED_EMAILS

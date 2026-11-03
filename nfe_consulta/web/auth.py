"""Sessões individuais e autorização dos dados fiscais."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response, status

from nfe_consulta.web.settings import WebSettings


@dataclass(frozen=True)
class WebUser:
    username: str
    display_name: str
    is_admin: bool
    session_token: str = ""
    role: str = "consulta"

    @property
    def authenticated(self) -> bool:
        return bool(self.session_token)

    @property
    def can_export(self) -> bool:
        return self.is_admin or self.role == "fiscal"


PUBLIC_USER = WebUser(
    username="usuario-interno",
    display_name="Usuário",
    is_admin=False,
    role="fiscal",
)


def admin_session_active(request: Request) -> bool:
    settings: WebSettings = request.app.state.settings
    if settings.corporate_auth_enabled:
        return False
    return current_user(request).authenticated


def _corporate_user(request: Request, settings: WebSettings) -> WebUser:
    # Só o gateway autenticado pode atestar identidade e grupos.
    headers = (settings.proxy_secret_header, settings.user_header, settings.name_header, settings.groups_header)
    if any(len(request.headers.getlist(name)) > 1 for name in headers):
        return PUBLIC_USER
    supplied = request.headers.get(settings.proxy_secret_header, "")
    if not settings.proxy_secret or not hmac.compare_digest(supplied.encode(), settings.proxy_secret.encode()):
        return PUBLIC_USER
    username = request.headers.get(settings.user_header, "").strip().casefold()
    groups_raw = request.headers.get(settings.groups_header, "")
    if not username or len(username) > 254 or any(ord(c) < 33 or ord(c) == 127 for c in username) or len(groups_raw) > 16384:
        return PUBLIC_USER
    groups = {g.strip().casefold() for g in groups_raw.split(",") if g.strip()}
    admin = bool(groups & settings.admin_groups)
    if not admin and not groups & settings.allowed_groups:
        return PUBLIC_USER
    name = request.headers.get(settings.name_header, username).strip()[:254] or username
    # Token interno para vincular CSRF à identidade e aos grupos; não é token do IdP.
    identity = hmac.new(settings.csrf_secret.encode(),
        (username + "|" + ",".join(sorted(groups))).encode(), hashlib.sha256).hexdigest()
    return WebUser(username, name, admin, identity, "admin" if admin else "consulta")


def current_user(request: Request) -> WebUser:
    settings: WebSettings = request.app.state.settings
    if settings.corporate_auth_enabled:
        return _corporate_user(request, settings)
    identity = request.app.state.admin_accounts.session_identity(
        request.cookies.get(settings.admin_cookie_name, ""),
        settings.admin_session_minutes * 60,
    )
    if identity:
        username, role = identity
        if role not in {"consulta", "fiscal", "admin"} or (settings.admin_only_auth and role != "admin"):
            return PUBLIC_USER
        return WebUser(
            username=username,
            display_name=username,
            is_admin=role == "admin",
            role=role,
            session_token=request.cookies.get(settings.admin_cookie_name, ""),
        )

    return PUBLIC_USER


def require_admin(request: Request) -> WebUser:
    user = require_user(request)
    if not user.authenticated or not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Entre para acessar a área Admin.",
        )
    return user


def require_user(request: Request) -> WebUser:
    user = current_user(request)
    if not user.authenticated and not request.app.state.settings.admin_only_auth:
        raise HTTPException(401, "Entre para acessar o AtlasDFE.")
    return user


def require_export(request: Request) -> WebUser:
    return require_user(request)


def set_admin_cookie(response: Response, settings: WebSettings, token: str, *, secure: bool = True) -> None:
    response.set_cookie(
        key=settings.admin_cookie_name,
        value=token,
        max_age=settings.admin_session_minutes * 60,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )


def clear_admin_cookie(response: Response, settings: WebSettings, *, secure: bool = True) -> None:
    response.delete_cookie(
        key=settings.admin_cookie_name,
        path="/",
        secure=secure,
        httponly=True,
        samesite="strict",
    )


def csrf_token(user: WebUser, settings: WebSettings) -> str:
    # Login e cadastro usam o token público; formulários autenticados
    # são vinculados à sessão concreta, revogada no logout.
    identidade = user.session_token if user.authenticated else "publico"
    mensagem = f"{user.username.casefold()}|{identidade}|nfe-web-v3".encode("utf-8")
    return hmac.new(
        settings.csrf_secret.encode("utf-8"),
        mensagem,
        hashlib.sha256,
    ).hexdigest()


def validate_csrf(token: str, user: WebUser, settings: WebSettings) -> None:
    esperado = csrf_token(user, settings)
    if not token or not hmac.compare_digest(token, esperado):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token de segurança inválido.",
        )

"""Login do dono do sistema (com verificação em duas etapas opcional)."""

from __future__ import annotations

import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import COOKIE_NAME, current_user
from app.events import record_activity
from app.kv import kv_get, kv_set
from app.models import AuthSession, User
from app.security import box, hash_password, new_token, password_problem, token_hash, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

_failures: dict[str, list[float]] = defaultdict(list)
MAX_FAILURES = 6
WINDOW = 600

SETUP_CODE: dict[str, str | None] = {"code": None}


def setup_code() -> str:
    if SETUP_CODE["code"] is None:
        SETUP_CODE["code"] = secrets.token_hex(4).upper()
    return SETUP_CODE["code"]


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else "") or (request.client.host if request.client else "?")


def _check_rate(key: str) -> None:
    now = time.time()
    _failures[key] = [t for t in _failures[key] if now - t < WINDOW]
    if len(_failures[key]) >= MAX_FAILURES:
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde alguns minutos.")


def _fail(*keys: str) -> None:
    for k in keys:
        _failures[k].append(time.time())


def _set_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def _create_session(db: Session, user: User, request: Request) -> str:
    token = new_token()
    now = datetime.now(timezone.utc)
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=token_hash(token),
            expires_at=now + timedelta(days=get_settings().session_days),
            ip=_client_ip(request)[:64],
            user_agent=request.headers.get("user-agent", "")[:300],
        )
    )
    user.last_login_at = now
    return token


class SetupBody(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=200)
    setup_code: str = Field(max_length=40)


class LoginBody(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=200)
    code: str | None = Field(None, max_length=12)


class PasswordBody(BaseModel):
    current_password: str
    new_password: str


class TotpBody(BaseModel):
    code: str = Field(max_length=12)


class TotpSetupBody(BaseModel):
    password: str


class TotpDisableBody(BaseModel):
    password: str
    code: str


@router.get("/status")
def status(request: Request, db: Session = Depends(get_db)) -> dict:
    from app.deps import user_from_token

    has_user = db.scalar(select(func.count(User.id))) > 0
    user = user_from_token(db, request.cookies.get(COOKIE_NAME)) if has_user else None
    return {
        "needs_setup": not has_user,
        "logged_in": user is not None,
        "email": user.email if user else None,
        "totp_enabled": bool(user.totp_enabled) if user else False,
    }


@router.post("/setup")
def setup(body: SetupBody, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    ip = _client_ip(request)
    _check_rate(f"ip:{ip}")
    if db.scalar(select(func.count(User.id))) > 0:
        raise HTTPException(status_code=409, detail="O sistema já tem dono.")
    if not secrets.compare_digest(body.setup_code.strip().upper(), setup_code()):
        _fail(f"ip:{ip}")
        raise HTTPException(status_code=403, detail="Código de configuração inválido (veja nos logs do backend).")
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="E-mail inválido.")
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    db.flush()
    token = _create_session(db, user, request)
    _set_cookie(response, token)
    SETUP_CODE["code"] = None
    record_activity("system", f"Conta do dono criada ({email})", kind="security", db=db)
    return {"ok": True}


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    ip = _client_ip(request)
    email = body.email.strip().lower()
    _check_rate(f"ip:{ip}")
    _check_rate(f"email:{email}")
    user = db.scalar(select(User).where(User.email == email))
    # mesmo custo para e-mail inexistente (não revela se a conta existe)
    stored = user.password_hash if user else hash_password(secrets.token_hex(8))
    if not verify_password(body.password, stored) or user is None:
        _fail(f"ip:{ip}", f"email:{email}")
        record_activity("system", f"Login recusado para {email} (IP {ip})", kind="security", level="warning")
        raise HTTPException(status_code=401, detail="E-mail ou senha incorretos.")
    if user.totp_enabled:
        if not body.code:
            return {"ok": False, "needs_code": True}
        if not _verify_totp(user, body.code, db):
            _fail(f"ip:{ip}", f"email:{email}")
            raise HTTPException(status_code=401, detail="Código de verificação inválido.")
    token = _create_session(db, user, request)
    _set_cookie(response, token)
    record_activity("system", f"Login de {email} (IP {ip})", kind="security", db=db)
    return {"ok": True}


def _verify_totp(user: User, code: str, db: Session | None = None) -> bool:
    secret = box().decrypt(user.totp_secret_enc or "")
    if not secret:
        return False
    code = code.strip().replace(" ", "")
    last = kv_get(f"totp_last:{user.id}", None, db)
    if last == code:
        return False  # impede reuso do mesmo código
    ok = pyotp.TOTP(secret).verify(code, valid_window=1)
    if ok:
        kv_set(f"totp_last:{user.id}", code, db)
    return ok


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(token)))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return {"email": user.email, "totp_enabled": user.totp_enabled, "created_at": user.created_at.isoformat()}


@router.post("/password")
def change_password(body: PasswordBody, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=403, detail="Senha atual incorreta.")
    problem = password_problem(body.new_password)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    user = db.merge(user)
    user.password_hash = hash_password(body.new_password)
    current = token_hash(request.cookies.get(COOKIE_NAME, ""))
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.token_hash != current))
    record_activity("system", "Senha alterada (outras sessões encerradas)", kind="security", db=db)
    return {"ok": True}


@router.post("/2fa/setup")
def totp_setup(body: TotpSetupBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=403, detail="Senha incorreta.")
    secret = pyotp.random_base32()
    kv_set(f"totp_pending:{user.id}", box().encrypt(secret), db)
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Meta-Bot")
    return {"secret": secret, "uri": uri}


@router.post("/2fa/enable")
def totp_enable(body: TotpBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    pending = kv_get(f"totp_pending:{user.id}", None, db)
    secret = box().decrypt(pending or "")
    if not secret or not pyotp.TOTP(secret).verify(body.code.strip(), valid_window=1):
        raise HTTPException(status_code=400, detail="Código inválido. Confira o horário do celular.")
    user = db.merge(user)
    user.totp_secret_enc = box().encrypt(secret)
    user.totp_enabled = True
    kv_set(f"totp_pending:{user.id}", None, db)
    record_activity("system", "Verificação em duas etapas ativada", kind="security", db=db)
    return {"ok": True}


@router.post("/2fa/disable")
def totp_disable(body: TotpDisableBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if not verify_password(body.password, user.password_hash) or not _verify_totp(user, body.code, db):
        raise HTTPException(status_code=403, detail="Senha ou código inválido.")
    user = db.merge(user)
    user.totp_enabled = False
    user.totp_secret_enc = None
    record_activity("system", "Verificação em duas etapas desativada", kind="security", level="warning", db=db)
    return {"ok": True}


@router.get("/forward")
def forward_auth(request: Request, db: Session = Depends(get_db)) -> Response:
    """Usado pelo nginx (auth_request) para proteger o painel do MT5 em /mt5/."""
    from app.deps import user_from_token

    user = user_from_token(db, request.cookies.get(COOKIE_NAME))
    return Response(status_code=204 if user else 401)


def confirm_password(user: User, password: str | None) -> None:
    """Ações sensíveis (dinheiro real, chaves) pedem a senha de novo."""
    if not password or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=403, detail="Confirme com a sua senha.")


def bootstrap_admin(db: Session) -> str | None:
    """Cria o dono a partir de MB_ADMIN_EMAIL/MB_ADMIN_PASSWORD, ou devolve o código de setup."""
    if db.scalar(select(func.count(User.id))) > 0:
        return None
    settings = get_settings()
    if settings.admin_email and settings.admin_password:
        problem = password_problem(settings.admin_password)
        if problem:
            raise RuntimeError(f"MB_ADMIN_PASSWORD fraca: {problem}")
        db.add(User(email=settings.admin_email.strip().lower(), password_hash=hash_password(settings.admin_password)))
        return None
    return setup_code()

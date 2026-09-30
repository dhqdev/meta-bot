"""Dependências do FastAPI: usuário logado e acesso ao escritório."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, WebSocket
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AuthSession, User
from app.security import token_hash

COOKIE_NAME = "mb_session"


def user_from_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(token)))
    now = datetime.now(timezone.utc)
    if row is None or row.expires_at < now:
        return None
    if now - row.last_seen_at > timedelta(minutes=5):
        row.last_seen_at = now
    return db.get(User, row.user_id)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = user_from_token(db, request.cookies.get(COOKIE_NAME))
    if user is None:
        raise HTTPException(status_code=401, detail="Faça login para continuar.")
    return user


def get_office(request: Request):
    office = getattr(request.app.state, "office", None)
    if office is None:
        raise HTTPException(status_code=503, detail="O escritório ainda está abrindo. Tente em alguns segundos.")
    return office


def ws_office(websocket: WebSocket):
    return getattr(websocket.app.state, "office", None)

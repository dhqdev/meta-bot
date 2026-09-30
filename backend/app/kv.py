"""Chave-valor persistido (estados dos agentes, caches e configurações) e segredos criptografados."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db import session_scope
from app.models import KV, Secret
from app.security import box


def kv_get(key: str, default: Any = None, db: Session | None = None) -> Any:
    if db is not None:
        row = db.get(KV, key)
        return default if row is None else row.value
    with session_scope() as s:
        row = s.get(KV, key)
        return default if row is None else row.value


def kv_set(key: str, value: Any, db: Session | None = None) -> None:
    def _apply(s: Session) -> None:
        row = s.get(KV, key)
        if row is None:
            s.add(KV(key=key, value=value))
        else:
            row.value = value

    if db is not None:
        _apply(db)
        return
    with session_scope() as s:
        _apply(s)


def secret_get(key: str, db: Session | None = None) -> str | None:
    def _read(s: Session) -> str | None:
        row = s.get(Secret, key)
        return None if row is None else box().decrypt(row.value_enc)

    if db is not None:
        return _read(db)
    with session_scope() as s:
        return _read(s)


def secret_set(key: str, value: str | None, db: Session | None = None) -> None:
    def _apply(s: Session) -> None:
        row = s.get(Secret, key)
        if not value:
            if row is not None:
                s.delete(row)
            return
        enc = box().encrypt(value)
        if row is None:
            s.add(Secret(key=key, value_enc=enc))
        else:
            row.value_enc = enc

    if db is not None:
        _apply(db)
        return
    with session_scope() as s:
        _apply(s)

"""Terminais MetaTrader 5 cadastrados (um container mt5 por conta de corretora)."""

from __future__ import annotations

import logging
import threading

from sqlalchemy import select

from app.broker.mt5 import MT5Client
from app.config import Settings
from app.db import session_scope
from app.models import Terminal
from app.security import box

log = logging.getLogger("metabot.terminals")


class TerminalManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._client: MT5Client | None = None
        self._client_key: tuple | None = None

    def ensure_default(self) -> None:
        """Cria o terminal padrão (variáveis MB_MT5_*) se ainda não houver nenhum."""
        with session_scope() as s:
            if s.scalar(select(Terminal.id).limit(1)) is not None:
                return
            token = self.settings.mt5_bridge_token
            s.add(
                Terminal(
                    name="MT5 principal",
                    bridge_url=self.settings.mt5_bridge_url,
                    token_enc=box().encrypt(token) if token else "",
                    active=True,
                )
            )

    def active(self) -> dict | None:
        with session_scope() as s:
            row = s.scalar(select(Terminal).where(Terminal.active.is_(True)).limit(1))
            if row is None:
                row = s.scalar(select(Terminal).order_by(Terminal.id).limit(1))
            if row is None:
                return None
            token = box().decrypt(row.token_enc) or self.settings.mt5_bridge_token
            return {
                "id": row.id,
                "name": row.name,
                "bridge_url": row.bridge_url,
                "token": token or "",
                "login": row.login,
                "server": row.server,
                "password": box().decrypt(row.password_enc) or "",
            }

    def client(self) -> MT5Client | None:
        term = self.active()
        if term is None or not term["token"]:
            return None
        key = (term["id"], term["bridge_url"], term["token"])
        with self._lock:
            if self._client is None or self._client_key != key:
                old = self._client
                self._client = MT5Client(term["bridge_url"], term["token"], self.settings.mt5_timeout_seconds)
                self._client_key = key
                if old is not None:
                    import asyncio

                    try:
                        asyncio.get_running_loop().create_task(old.aclose())
                    except RuntimeError:
                        pass
            return self._client

    def invalidate(self) -> None:
        with self._lock:
            self._client_key = None

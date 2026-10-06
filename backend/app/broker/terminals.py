"""Terminais MetaTrader 5 cadastrados: um bridge por conta de corretora (container, PC ou VPS Windows)."""

from __future__ import annotations

import json
import logging
import threading

from sqlalchemy import select

from app.broker.ctrader import CTraderClient
from app.broker.mt5 import MT5Client
from app.config import Settings
from app.db import session_scope
from app.models import Terminal
from app.security import box

log = logging.getLogger("metabot.terminals")

DEFAULT_NAME = "MT5 principal"
CTRADER_PREFIX = "ctrader://"


def is_ctrader(url: str | None) -> bool:
    """Conta da cTrader (Open API) em vez de um bridge do MT5: o endereço fica "ctrader://live" ou "ctrader://demo"."""
    return (url or "").startswith(CTRADER_PREFIX)


def ctrader_environment(url: str) -> str:
    return "live" if url[len(CTRADER_PREFIX):].startswith("live") else "demo"


def ctrader_endpoint() -> tuple[str | None, int, bool]:
    """Servidor da cTrader. MB_CTRADER_ENDPOINT=host:porta (sem SSL) só para testes com um servidor falso."""
    from app.config import get_settings

    override = (get_settings().ctrader_endpoint or "").strip()
    if override:
        host, _, port = override.rpartition(":")
        return host, int(port), False
    return None, 5035, True


def save_ctrader_tokens(terminal_id: int, access_token: str, refresh_token: str) -> None:
    """Guarda os tokens renovados (criptografados) no terminal."""
    with session_scope() as s:
        row = s.get(Terminal, terminal_id)
        if row is None:
            return
        creds = json.loads(box().decrypt(row.token_enc) or "{}")
        creds.update({"access_token": access_token, "refresh_token": refresh_token})
        row.token_enc = box().encrypt(json.dumps(creds))


class TerminalManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._client: MT5Client | None = None
        self._client_key: tuple | None = None

    def ensure_default(self) -> None:
        """Terminal "MT5 principal" a partir de MB_MT5_BRIDGE_URL / MB_MT5_BRIDGE_TOKEN.

        Ele segue a stack: se as variáveis mudarem, é atualizado na próxima inicialização.
        Sem MB_MT5_BRIDGE_URL nada é criado (os terminais podem ser cadastrados pela tela)."""
        url = (self.settings.mt5_bridge_url or "").strip().rstrip("/")
        if not url:
            return
        token = self.settings.mt5_bridge_token
        with session_scope() as s:
            row = s.scalar(select(Terminal).where(Terminal.name == DEFAULT_NAME).limit(1))
            if row is not None:
                if row.bridge_url != url:
                    log.info("terminal %s: endereço atualizado pela stack (%s)", DEFAULT_NAME, url)
                    row.bridge_url = url
                if token and box().decrypt(row.token_enc) != token:
                    row.token_enc = box().encrypt(token)
                return
            any_active = s.scalar(select(Terminal.id).where(Terminal.active.is_(True)).limit(1)) is not None
            s.add(
                Terminal(
                    name=DEFAULT_NAME,
                    bridge_url=url,
                    token_enc=box().encrypt(token) if token else "",
                    active=not any_active,
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

    def client(self) -> MT5Client | CTraderClient | None:
        term = self.active()
        if term is None or not term["token"]:
            return None
        ctrader = is_ctrader(term["bridge_url"])
        # na cTrader o token muda sozinho (renovação): a conexão aberta continua valendo
        key = (term["id"], term["bridge_url"], term["login"] if ctrader else term["token"])
        with self._lock:
            if self._client is None or self._client_key != key:
                old = self._client
                self._client = self._build(term) if ctrader else MT5Client(term["bridge_url"], term["token"], self.settings.mt5_timeout_seconds)
                self._client_key = key
                if old is not None:
                    import asyncio

                    try:
                        asyncio.get_running_loop().create_task(old.aclose())
                    except RuntimeError:
                        pass
            return self._client

    def _build(self, term: dict) -> CTraderClient | None:
        try:
            creds = json.loads(term["token"])
        except ValueError:
            log.warning("terminal %s: credenciais da cTrader ilegíveis", term["name"])
            return None
        host, port, use_ssl = ctrader_endpoint()
        terminal_id = term["id"]
        return CTraderClient(
            environment=ctrader_environment(term["bridge_url"]),
            client_id=creds.get("client_id", ""),
            client_secret=creds.get("client_secret", ""),
            access_token=creds.get("access_token", ""),
            refresh_token=creds.get("refresh_token", ""),
            account_id=int(term["login"] or 0),
            on_tokens=lambda access, refresh: save_ctrader_tokens(terminal_id, access, refresh),
            host=host,
            port=port,
            use_ssl=use_ssl,
            timeout=self.settings.mt5_timeout_seconds,
        )

    def invalidate(self) -> None:
        with self._lock:
            self._client_key = None

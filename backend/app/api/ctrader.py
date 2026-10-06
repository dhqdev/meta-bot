"""Conectar uma conta da cTrader (Open API): autorização pelo cTrader ID, escolha da conta e cadastro como terminal.

Passo a passo para o dono:
1. Cria um app em openapi.ctrader.com com o endereço de retorno que a tela mostra e espera a aprovação.
2. Informa client id e client secret na tela e entra com o cTrader ID (ou cola um token gerado no Playground).
3. Escolhe a conta (demo ou real) que o Meta-Bot vai operar.
"""

from __future__ import annotations

import json
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.auth import confirm_password
from app.broker.ctrader import AUTH_URL, TOKEN_URL, list_accounts
from app.broker.mt5 import MT5Error, MT5Unavailable
from app.broker.terminals import CTRADER_PREFIX, ctrader_endpoint
from app.config import get_settings
from app.db import session_scope
from app.deps import current_user, get_office
from app.events import record_activity
from app.kv import secret_get, secret_set
from app.models import Terminal, User
from app.security import box

router = APIRouter(prefix="/api/ctrader", tags=["ctrader"])

PENDING_KEY = "ctrader.pending"  # app e tokens até o dono escolher a conta (criptografado)
STATE_TTL = 15 * 60


def _pending() -> dict:
    raw = secret_get(PENDING_KEY)
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def _save_pending(data: dict | None) -> None:
    secret_set(PENDING_KEY, json.dumps(data) if data else None)


def redirect_uri(request: Request) -> str:
    """Endereço de retorno que o dono cadastra no app da cTrader: o endereço público do Meta-Bot + /api/ctrader/callback."""
    base = (get_settings().public_url or "").strip().rstrip("/")
    if not base:
        origin = request.headers.get("origin") or ""
        if origin:
            base = origin.rstrip("/")
        else:
            proto = request.headers.get("x-forwarded-proto") or request.url.scheme
            host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
            base = f"{proto.split(',')[0].strip()}://{host.split(',')[0].strip()}"
    return f"{base}/api/ctrader/callback"


async def exchange_code(code: str, uri: str, client_id: str, client_secret: str) -> dict:
    """Troca o código da autorização pelos tokens (servidor da cTrader)."""
    params = {"grant_type": "authorization_code", "code": code, "redirect_uri": uri, "client_id": client_id, "client_secret": client_secret}
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(TOKEN_URL, params=params, headers={"Accept": "application/json"})
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400 or not data.get("accessToken"):
        raise HTTPException(status_code=400, detail=f"A cTrader recusou a autorização: {data.get('description') or data.get('errorCode') or resp.status_code}")
    return data


@router.get("/status")
def status(request: Request, user: User = Depends(current_user)) -> dict:
    pending = _pending()
    return {
        "redirect_uri": redirect_uri(request),
        "client_id": pending.get("client_id", ""),
        "authorized": bool(pending.get("access_token")),
    }


class StartBody(BaseModel):
    client_id: str = Field(..., min_length=3, max_length=200)
    client_secret: str = Field(..., min_length=3, max_length=300)
    password: str
    # alternativa sem o login pela tela da cTrader: token gerado no Playground do app
    access_token: str | None = Field(None, max_length=500)
    refresh_token: str | None = Field(None, max_length=500)


@router.post("/start")
def start(body: StartBody, request: Request, user: User = Depends(current_user)) -> dict:
    confirm_password(user, body.password)
    uri = redirect_uri(request)
    pending = {"client_id": body.client_id.strip(), "client_secret": body.client_secret.strip(), "redirect_uri": uri}
    if body.access_token:
        pending.update({"access_token": body.access_token.strip(), "refresh_token": (body.refresh_token or "").strip()})
        _save_pending(pending)
        return {"authorized": True, "redirect_uri": uri}
    state = secrets.token_urlsafe(24)
    pending.update({"state": state, "state_at": time.time()})
    _save_pending(pending)
    url = AUTH_URL + "?" + urlencode({"client_id": pending["client_id"], "redirect_uri": uri, "scope": "trading", "product": "web", "state": state})
    return {"authorized": False, "url": url, "redirect_uri": uri}


@router.get("/callback")
async def callback(code: str = "", state: str = "", error: str = "") -> RedirectResponse:
    """A cTrader devolve o dono para cá depois do login; a proteção é o `state` criado pelo dono logado."""
    pending = _pending()
    if error or not code:
        return RedirectResponse("/config?ctrader=recusado", status_code=302)
    if not state or not secrets.compare_digest(state, pending.get("state", "")) or time.time() - float(pending.get("state_at") or 0) > STATE_TTL:
        return RedirectResponse("/config?ctrader=expirado", status_code=302)
    try:
        tokens = await exchange_code(code, pending["redirect_uri"], pending["client_id"], pending["client_secret"])
    except HTTPException:
        return RedirectResponse("/config?ctrader=recusado", status_code=302)
    pending.update({"access_token": tokens["accessToken"], "refresh_token": tokens.get("refreshToken", "")})
    pending.pop("state", None)
    _save_pending(pending)
    return RedirectResponse("/config?ctrader=contas", status_code=302)


@router.get("/accounts")
async def accounts(user: User = Depends(current_user)) -> list[dict]:
    pending = _pending()
    if not pending.get("access_token"):
        raise HTTPException(status_code=400, detail="Autorize o Meta-Bot na cTrader primeiro.")
    host, port, use_ssl = ctrader_endpoint()
    try:
        return await list_accounts(pending["client_id"], pending["client_secret"], pending["access_token"], host=host, port=port, use_ssl=use_ssl)
    except (MT5Error, MT5Unavailable) as exc:
        raise HTTPException(status_code=400, detail=f"Não consegui listar as contas: {exc.message}") from exc


class ConnectBody(BaseModel):
    account_id: int
    live: bool
    login: int | None = None
    broker: str = Field("", max_length=80)
    name: str = Field("", max_length=80)
    active: bool = True
    password: str


@router.post("/connect")
def connect(body: ConnectBody, user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    confirm_password(user, body.password)
    pending = _pending()
    if not pending.get("access_token"):
        raise HTTPException(status_code=400, detail="Autorize o Meta-Bot na cTrader primeiro.")
    creds = {k: pending.get(k, "") for k in ("client_id", "client_secret", "access_token", "refresh_token")}
    env = "live" if body.live else "demo"
    label = body.name.strip() or f"cTrader {body.broker or ''} {'real' if body.live else 'demo'} {body.login or body.account_id}".replace("  ", " ")
    with session_scope() as s:
        if body.active:
            for t in s.scalars(select(Terminal)):
                t.active = False
        row = s.scalar(select(Terminal).where(Terminal.bridge_url == CTRADER_PREFIX + env, Terminal.login == str(body.account_id)).limit(1))
        if row is None:
            row = Terminal(bridge_url=CTRADER_PREFIX + env, login=str(body.account_id))
            s.add(row)
        row.name = label[:80]
        row.token_enc = box().encrypt(json.dumps(creds))
        row.server = f"{body.broker} {body.login or ''}".strip()[:120]
        row.password_enc = ""
        row.active = body.active
        s.flush()
        terminal_id = row.id
    _save_pending(None)
    office.terminals.invalidate()
    office.market.clear_cache()
    office.agent("infra").request("health")
    record_activity("system", f"Conta da cTrader conectada: {label}", kind="settings")
    return {"ok": True, "id": terminal_id, "name": label}

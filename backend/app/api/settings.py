"""Configurações: parâmetros dos agentes, chave da IA e terminais MT5 (corretoras)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.api.auth import confirm_password
from app.broker.mt5 import MT5Client, MT5Error, MT5Unavailable
from app.config import get_settings
from app.db import session_scope
from app.deps import current_user, get_office
from app.events import record_activity
from app.kv import secret_get, secret_set
from app.models import Terminal, User
from app.runtime import AI_MODELS, TIMEFRAMES, RuntimeConfig, get_config, update_config
from app.security import box, mask
from app.services.llm import PRICES

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Campos que exigem senha (mexem em dinheiro real ou no modo de operação)
PROTECTED = {"mode", "system_running"}


@router.get("")
def get_settings_view(user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    cfg = get_config()
    return {
        "config": cfg.model_dump(mode="json"),
        "defaults": RuntimeConfig().model_dump(mode="json"),
        "options": {"timeframes": TIMEFRAMES, "ai_models": AI_MODELS, "ai_prices": PRICES},
        "ai": {
            "key_set": bool(secret_get("anthropic_api_key") or get_settings().anthropic_api_key),
            "key_masked": mask(secret_get("anthropic_api_key") or get_settings().anthropic_api_key),
            "from_env": bool(get_settings().anthropic_api_key and not secret_get("anthropic_api_key")),
            "usage": office.llm.usage_summary(),
        },
    }


@router.put("")
def put_settings(patch: dict[str, Any], user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    blocked = PROTECTED & set(patch)
    if blocked:
        raise HTTPException(status_code=400, detail=f"Use as rotas próprias para: {', '.join(sorted(blocked))}.")
    try:
        cfg = update_config(patch)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", []))
        raise HTTPException(status_code=400, detail=f"{field}: {first.get('msg')}") from exc
    if {"data_source", "server_utc_offset_hours"} & set(patch):
        office.market.clear_cache()
    if {"break_even_r", "trailing_start_r", "trailing_atr_mult", "adaptive_exits"} & set(patch):
        office.invalidate_exit_params()
    if {"watchlist", "timeframes", "enabled_strategies", "rank_by", "min_trades", "min_profit_factor", "oos_fraction"} & set(patch):
        office.agent("strategist").request("ranking")
    record_activity("system", f"Configurações alteradas: {', '.join(sorted(patch))}", kind="settings")
    return {"config": cfg.model_dump(mode="json")}


class AIKeyBody(BaseModel):
    api_key: str | None = Field(None, max_length=300)
    password: str


@router.post("/ai-key")
async def set_ai_key(body: AIKeyBody, user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    confirm_password(user, body.password)
    key = (body.api_key or "").strip()
    if key:
        ok, message = await office.llm.check_key(key)
        if not ok:
            raise HTTPException(status_code=400, detail=message)
        secret_set("anthropic_api_key", key)
        record_activity("system", "Chave da Anthropic (Claude) cadastrada", kind="security")
        return {"ok": True, "message": message, "masked": mask(key)}
    secret_set("anthropic_api_key", None)
    record_activity("system", "Chave da Anthropic removida", kind="security")
    return {"ok": True, "message": "chave removida"}


# ----------------------------------------------------------- terminais MT5
class TerminalBody(BaseModel):
    name: str = Field(max_length=80)
    bridge_url: str = Field(max_length=255)
    token: str | None = Field(None, max_length=300)
    login: str | None = Field(None, max_length=40)
    server: str | None = Field(None, max_length=120)
    broker_password: str | None = Field(None, max_length=200)
    active: bool = False
    password: str


def _terminal_view(t: Terminal) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "bridge_url": t.bridge_url,
        "token_set": bool(t.token_enc),
        "login": t.login,
        "server": t.server,
        "broker_password_set": bool(t.password_enc),
        "active": t.active,
    }


@router.get("/terminals")
def terminals(user: User = Depends(current_user)) -> list[dict]:
    with session_scope() as s:
        return [_terminal_view(t) for t in s.scalars(select(Terminal).order_by(Terminal.id))]


def _validate_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="O endereço do bridge deve começar com http:// ou https://")
    return url


@router.post("/terminals")
def create_terminal(body: TerminalBody, user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    confirm_password(user, body.password)
    with session_scope() as s:
        if body.active:
            for t in s.scalars(select(Terminal)):
                t.active = False
        row = Terminal(
            name=body.name.strip() or "MT5",
            bridge_url=_validate_url(body.bridge_url),
            token_enc=box().encrypt(body.token) if body.token else "",
            login=(body.login or "").strip(),
            server=(body.server or "").strip(),
            password_enc=box().encrypt(body.broker_password) if body.broker_password else "",
            active=body.active,
        )
        s.add(row)
        s.flush()
        view = _terminal_view(row)
    office.terminals.invalidate()
    record_activity("system", f"Terminal MT5 cadastrado: {view['name']}", kind="settings")
    return view


@router.put("/terminals/{terminal_id}")
def update_terminal(terminal_id: int, body: TerminalBody, user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    confirm_password(user, body.password)
    with session_scope() as s:
        row = s.get(Terminal, terminal_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Terminal não encontrado.")
        if body.active:
            for t in s.scalars(select(Terminal)):
                t.active = t.id == terminal_id
        else:
            row.active = False
        row.name = body.name.strip() or row.name
        row.bridge_url = _validate_url(body.bridge_url)
        if body.token:
            row.token_enc = box().encrypt(body.token)
        row.login = (body.login or "").strip()
        row.server = (body.server or "").strip()
        if body.broker_password:
            row.password_enc = box().encrypt(body.broker_password)
        view = _terminal_view(row)
    office.terminals.invalidate()
    office.market.clear_cache()
    office.agent("infra").request("health")
    record_activity("system", f"Terminal MT5 atualizado: {view['name']}", kind="settings")
    return view


class ConfirmBody(BaseModel):
    password: str


@router.delete("/terminals/{terminal_id}")
def delete_terminal(terminal_id: int, body: ConfirmBody, user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    confirm_password(user, body.password)
    with session_scope() as s:
        row = s.get(Terminal, terminal_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Terminal não encontrado.")
        s.delete(row)
    office.terminals.invalidate()
    return {"ok": True}


class TestBody(BaseModel):
    bridge_url: str
    token: str | None = None
    terminal_id: int | None = None


@router.post("/terminals/test")
async def test_terminal(body: TestBody, user: User = Depends(current_user)) -> dict:
    token = body.token
    if not token and body.terminal_id:
        with session_scope() as s:
            row = s.get(Terminal, body.terminal_id)
            token = box().decrypt(row.token_enc) if row else None
    token = token or get_settings().mt5_bridge_token
    if not token:
        raise HTTPException(status_code=400, detail="Informe o token do bridge.")
    client = MT5Client(_validate_url(body.bridge_url), token, timeout=15)
    try:
        health = await client.health()
    except (MT5Error, MT5Unavailable) as exc:
        return {"ok": False, "message": exc.message}
    finally:
        await client.aclose()
    account = health.get("account") or {}
    if not health.get("initialized"):
        return {"ok": False, "message": health.get("error") or "terminal ainda abrindo"}
    return {
        "ok": True,
        "connected": bool(health.get("connected")),
        "message": f"Conectado à conta {account.get('login')} em {account.get('server')} ({account.get('company')})" if account else "Bridge ok, mas o terminal não está logado em nenhuma conta.",
        "account": {k: account.get(k) for k in ("login", "server", "company", "currency", "balance", "leverage")} if account else None,
    }

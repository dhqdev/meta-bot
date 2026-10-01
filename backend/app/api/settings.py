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
from app.runtime import TIMEFRAMES, RuntimeConfig, get_config, update_config
from app.security import box, mask

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Campos que exigem senha (mexem em dinheiro real ou no modo de operação)
PROTECTED = {"mode", "system_running"}

# Nomes em português para as mensagens de erro (os mesmos da tela de Configurações)
FIELD_LABELS = {
    "watchlist": "Ativos", "timeframes": "Tempos gráficos", "enabled_strategies": "Estratégias ligadas",
    "daily_loss_limit": "Limite de perda do dia", "daily_loss_unit": "Unidade do limite de perda",
    "daily_profit_target": "Meta de ganho do dia", "daily_profit_unit": "Unidade da meta de ganho",
    "risk_per_trade_pct": "Risco por operação", "max_drawdown_pct": "Queda máxima desde o pico",
    "max_open_positions": "Posições abertas ao mesmo tempo", "max_positions_per_symbol": "Posições por ativo",
    "max_currency_exposure": "Exposição máxima por moeda", "max_spread_multiplier": "Spread máximo",
    "min_lot_overrisk": "Tolerância do lote mínimo", "rank_by": "Critério do ranking", "min_trades": "Mínimo de operações no teste",
    "min_profit_factor": "Fator de lucro mínimo", "oos_fraction": "Parte reservada para a prova", "ranking_interval_hours": "Refazer o ranking a cada",
    "evolution_interval_hours": "Evoluir a cada", "decision_interval_minutes": "Rever o plano a cada",
    "max_active_setups": "Setups ativos ao mesmo tempo", "min_hour_quality": "Qualidade mínima do horário",
    "news_block_threshold": "Força da notícia que veta", "break_even_r": "Zero a zero a partir de",
    "trailing_start_r": "Trailing a partir de", "trailing_atr_mult": "Distância do trailing", "max_bars_in_trade": "Tempo máximo na operação",
    "b3_close_time": "Fechar day trade da B3 às", "blackout_before_min": "Pausa antes do evento", "blackout_after_min": "Pausa depois do evento",
    "news_interval_minutes": "Ler notícias a cada", "daily_meeting_time": "Horário da daily", "ai_news_interval_minutes": "Notícias pela IA a cada",
    "ai_plan_refresh_minutes": "Validade do plano da IA", "ai_max_calls_per_hour": "Máximo de chamadas de IA por hora",
    "ai_daily_budget_usd": "Orçamento diário de IA", "paper_initial_balance": "Saldo inicial da conta simulada",
    "paper_commission_per_lot": "Comissão por lote", "paper_slippage_points": "Slippage", "magic_number": "Número mágico",
    "deviation_points": "Desvio máximo da ordem", "server_utc_offset_hours": "Fuso do servidor do MT5",
}


def _num(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).replace(".", ",")


def friendly_error(exc: ValidationError) -> str:
    """Primeiro erro de validação em português, com o nome do campo como aparece na tela."""
    first = exc.errors()[0]
    loc = [str(p) for p in first.get("loc", []) if p != "__root__"]
    field = loc[0] if loc else ""
    ctx = first.get("ctx") or {}
    kind = first.get("type", "")
    msg = str(first.get("msg", "")).removeprefix("Value error, ")
    if kind in ("greater_than_equal", "greater_than"):
        msg = f"precisa ser no mínimo {_num(ctx.get('ge', ctx.get('gt')))}"
    elif kind in ("less_than_equal", "less_than"):
        msg = f"precisa ser no máximo {_num(ctx.get('le', ctx.get('lt')))}"
    elif kind == "int_from_float":
        msg = "precisa ser um número inteiro"
    elif kind in ("float_parsing", "int_parsing", "float_type", "int_type"):
        msg = "precisa ser um número"
    elif kind in ("bool_parsing", "bool_type"):
        msg = "precisa ser ligado ou desligado"
    elif kind == "literal_error":
        msg = f"opção inválida ({ctx.get('expected', '')})"
    label = FIELD_LABELS.get(field, field)
    return f"{label}: {msg}" if label else msg


@router.get("")
def get_settings_view(user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    cfg = get_config()
    stored = secret_get("openrouter_api_key")
    env_key = get_settings().openrouter_api_key
    return {
        "config": cfg.model_dump(mode="json"),
        "defaults": RuntimeConfig().model_dump(mode="json"),
        "options": {"timeframes": TIMEFRAMES},
        "ai": {
            **office.llm.status(),
            "key_masked": mask(stored or env_key),
            "from_env": bool(env_key and not stored),
            "usage": office.llm.usage_summary(),
        },
        "mt5": {"panel_url": get_settings().mt5_panel_url},
    }


@router.put("")
def put_settings(patch: dict[str, Any], user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    blocked = PROTECTED & set(patch)
    if blocked:
        raise HTTPException(status_code=400, detail=f"Use as rotas próprias para: {', '.join(sorted(blocked))}.")
    try:
        cfg = update_config(patch)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=friendly_error(exc)) from exc
    if {"data_source", "server_utc_offset_hours"} & set(patch):
        office.market.clear_cache()
        office.sync_data_family()
    if {"break_even_r", "trailing_start_r", "trailing_atr_mult", "adaptive_exits"} & set(patch):
        office.invalidate_exit_params()
    if {"watchlist", "timeframes", "enabled_strategies", "rank_by", "min_trades", "min_profit_factor", "oos_fraction"} & set(patch):
        office.agent("strategist").request("ranking")
    if {"daily_loss_limit", "daily_loss_unit", "daily_profit_target", "daily_profit_unit"} & set(patch):
        office.agent("risk").request("guard")
    record_activity("system", f"Configurações alteradas: {', '.join(sorted(patch))}", kind="settings")
    return {"config": cfg.model_dump(mode="json")}


class AIKeyBody(BaseModel):
    api_key: str | None = Field(None, max_length=300)
    password: str


@router.post("/ai-key")
async def set_ai_key(body: AIKeyBody, user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    """Chave do OpenRouter (a única IA do sistema; cada agente já tem o seu modelo)."""
    confirm_password(user, body.password)
    key = (body.api_key or "").strip()
    if key:
        ok, message = await office.llm.check_key(key)
        if not ok:
            raise HTTPException(status_code=400, detail=message)
        secret_set("openrouter_api_key", key)
        record_activity("system", "Chave do OpenRouter cadastrada", kind="security")
        return {"ok": True, "message": message, "masked": mask(key), "status": office.llm.status()}
    secret_set("openrouter_api_key", None)
    record_activity("system", "Chave do OpenRouter removida", kind="security")
    return {"ok": True, "message": "chave removida", "status": office.llm.status()}


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

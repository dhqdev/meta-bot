"""Estado do sistema: ligar/desligar, modo simulado/real e travas."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import __version__
from app.api.auth import confirm_password
from app.config import get_settings
from app.core.market_hours import fx_status
from app.deps import current_user, get_office
from app.events import bus, record_activity
from app.kv import kv_get, kv_set
from app.models import User
from app.runtime import get_config, update_config

router = APIRouter(prefix="/api", tags=["system"])

PREVIOUS_SOURCE_KEY = "mode.previous_data_source"


@router.get("/health")
def health() -> dict:
    return {"ok": True, "version": __version__, "time": datetime.now(timezone.utc).isoformat()}


@router.get("/system")
async def system(user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    cfg = get_config()
    try:
        account = await office.broker.account()
    except Exception as exc:
        account = {"error": str(exc)}
    return {
        "version": __version__,
        "running": cfg.system_running,
        "mode": cfg.mode,
        "data_source": office.market.source(),
        "data_source_setting": cfg.data_source,
        "ai": office.llm.available(),
        "mt5": office.agent("infra").status,
        "account": account,
        "risk": office.agent("risk").status(),
        "plan": office.agent("manager").active_plan(),
        "plan_rationale": office.agent("manager").rationale,
        "office_break": office.break_info(),
    }


def _local(dt: datetime | str | None) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    tz = ZoneInfo(get_settings().timezone)
    local = dt.astimezone(tz)
    days = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
    return f"{days[local.weekday()]} {local:%H:%M}"


async def _symbol_open(office, symbol: str) -> bool | None:
    try:
        tick = await asyncio.wait_for(office.market.tick(symbol), timeout=6)
        return bool(tick.get("open", True))
    except Exception:
        return None


_DIAG_CACHE: dict = {"at": 0.0, "data": None}
DIAG_TTL = 20.0  # o painel pede a cada minuto; várias telas abertas não refazem tudo (cotação de 10 pares)


def forget_diagnostico() -> None:
    """Algo mudou na mão (ligar/desligar, modo): o painel recalcula na próxima chamada."""
    _DIAG_CACHE.update(at=0.0, data=None)


@router.get("/system/diagnostico")
async def diagnostico(user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    """Por que a equipe está (ou não) operando hoje: mercado, plano, sinais e vetos, em português simples."""
    import time

    if _DIAG_CACHE["data"] is not None and time.monotonic() - _DIAG_CACHE["at"] < DIAG_TTL:
        return _DIAG_CACHE["data"]
    data = await _diagnostico(office)
    _DIAG_CACHE.update(at=time.monotonic(), data=data)
    return data


async def _diagnostico(office) -> dict:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Signal, Trade

    cfg = get_config()
    now = datetime.now(timezone.utc)
    tz = ZoneInfo(get_settings().timezone)
    day_start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    fx = fx_status(now)
    opens = await asyncio.gather(*(_symbol_open(office, sym) for sym in cfg.watchlist))
    symbols = [{"symbol": sym, "open": op} for sym, op in zip(cfg.watchlist, opens)]

    manager = office.agent("manager")
    strategist = office.agent("strategist")
    approved = [p for p in strategist.ranking(only_approved=True, limit=1000) if p["symbol"] in cfg.watchlist and p["timeframe"] in cfg.timeframes]
    approved_by_symbol = Counter(p["symbol"] for p in approved)
    try:
        cands = manager.build_candidates()
    except Exception:
        cands = []
    blocked = Counter(b.split(" (")[0] for c in cands for b in c["blocked"])
    plan = manager.active_plan()
    risk = office.agent("risk").status()
    with session_scope() as s:
        sigs = list(s.scalars(select(Signal).where(Signal.ts >= day_start)))
        sig_status = Counter(x.status for x in sigs)
        vetoes = Counter((x.reason or "").split(" (")[0][:90] for x in sigs if x.status in ("vetado", "falhou", "cancelado", "expirado"))
        trades = s.query(Trade).filter(Trade.entry_time >= day_start, Trade.mode == cfg.mode).count()

    reasons: list[dict] = []

    def add(level: str, text: str) -> None:
        reasons.append({"level": level, "text": text})

    brk = office.break_info()
    if not cfg.system_running:
        if brk and brk.get("kind") == "weekend":
            add("stop", f"Escritório fechado no fim de semana: reabre sozinho {_local(brk['until'])}, junto com o mercado.")
        elif brk:
            add("stop", f"Escritório em pausa depois da daily até {_local(brk['until'])}.")
        else:
            add("stop", "Escritório desligado: ninguém abre operação nova até você ligar (botão no topo).")
    if not fx["open"]:
        add("stop", f"Mercado de câmbio fechado agora. Reabre {_local(fx['next_change'])} (Brasília).")
    elif fx["next_change_in_min"] is not None and fx["next_change_in_min"] <= 180:
        add("info", f"Mercado de câmbio fecha {_local(fx['next_change'])} (Brasília); o Caio encerra as posições na sexta às 17:45 se “fechar antes do fim de semana” estiver ligado.")
    if risk.get("kill_switch"):
        add("stop", f"Trava geral ativa ({risk.get('kill_reason') or 'queda máxima'}): libere em Configurações.")
    if risk.get("day_stop"):
        add("stop", "Meta do dia batida: equipe parada até meia-noite." if risk["day_stop"] == "target" else "Limite de perda do dia atingido: equipe parada até meia-noite.")
    if not approved:
        add("warn", "Nenhuma estratégia aprovada nos pares ainda: a Estela está testando (só entra no plano o que passa no teste e na prova).")
    elif not plan and cands and all(c["blocked"] for c in cands):
        top = ", ".join(f"{k} ({v})" for k, v in blocked.most_common(3))
        add("warn", f"Todos os candidatos estão bloqueados agora: {top}.")
    elif not plan and cfg.system_running:
        add("info", "O Gustavo ainda não montou o plano desta rodada.")
    if plan and not sigs and fx["open"]:
        add("info", "Plano ativo, mas nenhuma estratégia deu sinal de entrada hoje: o mercado não mostrou a oportunidade que elas esperam.")
    n_vetoed = sum(vetoes.values())
    if n_vetoed:
        top = "; ".join(f"{k} ({v}×)" for k, v in vetoes.most_common(3))
        add("warn", f"{n_vetoed} sinal(is) barrado(s) hoje. Principais motivos: {top}.")
    if trades:
        add("ok", f"{trades} operação(ões) aberta(s) hoje.")
    if not reasons:
        add("ok", "Tudo liberado: a equipe está esperando um sinal das estratégias do plano.")

    return {
        "now": now.isoformat(),
        "now_local": _local(now),
        "running": cfg.system_running,
        "fx": {**fx, "next_change_local": _local(fx["next_change"])},
        "symbols": [{**x, "approved": approved_by_symbol.get(x["symbol"], 0)} for x in symbols],
        "funnel": {
            "approved": len(approved),
            "candidates": len(cands),
            "blocked": sum(1 for c in cands if c["blocked"]),
            "plan": len(plan),
            "signals": len(sigs),
            "signals_by_status": dict(sig_status),
            "trades": trades,
        },
        "blocked_reasons": dict(blocked.most_common(5)),
        "veto_reasons": dict(vetoes.most_common(5)),
        "reasons": reasons,
        "schedule": {
            "fx_hours": "domingo 19:00 até sexta 18:00 (Brasília)",
            "weekend_close": "sexta 17:45 (Brasília)" if cfg.close_before_weekend else None,
            "daily": cfg.daily_meeting_time if cfg.daily_meeting_enabled else None,
            "daily_break_minutes": cfg.daily_break_minutes,
            "min_hour_quality": cfg.min_hour_quality,
        },
    }


class RunBody(BaseModel):
    running: bool


@router.post("/system/running")
async def set_running(body: RunBody, user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    office.end_break(reopen=False)  # ligar/desligar na mão vale mais que a pausa da daily e o fim de semana
    if body.running:
        from app.agents.office import WEEKEND_SKIP_KEY
        from app.core.market_hours import all_closed, next_open

        cfg = get_config()
        reopen = next_open(cfg.watchlist) if all_closed(cfg.watchlist) else None
        kv_set(WEEKEND_SKIP_KEY, reopen.isoformat() if reopen else None)
    update_config({"system_running": body.running})
    forget_diagnostico()
    bus.publish({"type": "system", "running": body.running})
    record_activity("system", "Sistema ligado: a equipe chegou ao escritório" if body.running else "Sistema desligado: sem novas entradas (as posições abertas continuam protegidas)", kind="system")
    for agent in office.agents.values():
        agent.request("*")
    return {"running": body.running}


class ModeBody(BaseModel):
    mode: Literal["paper", "live"]
    password: str | None = None
    confirm: bool = False


def apply_mode(office, mode: str, note: str = "") -> None:
    """Troca entre a conta simulada e a da corretora (quem chama já conferiu senha e conexão)."""
    cfg = get_config()
    if mode == "live":
        # guarda a origem dos preços de antes para restaurar ao voltar ao simulado
        if cfg.mode != "live":
            kv_set(PREVIOUS_SOURCE_KEY, cfg.data_source)
        changes = {"mode": "live", "data_source": "mt5"}
    else:
        # o simulado não pode ficar preso ao MT5: se ele cair, a conta simulada ficaria sem preços
        previous = kv_get(PREVIOUS_SOURCE_KEY)
        restore = previous if previous in ("auto", "real", "synthetic", "mt5") and cfg.mode == "live" else None
        if restore is None and cfg.data_source == "mt5" and cfg.mode == "live":
            restore = "auto"
        changes = {"mode": "paper", **({"data_source": restore} if restore else {})}
        kv_set(PREVIOUS_SOURCE_KEY, None)
    update_config(changes)
    forget_diagnostico()
    office.market.clear_cache()
    text = "Modo CONTA DA CORRETORA: as ordens vão para a corretora" if mode == "live" else "Modo simulado: nenhuma ordem vai para a corretora"
    record_activity("system", text + (f" ({note})" if note else ""), kind="system", level="warning" if mode == "live" else "info")


@router.post("/system/mode")
async def set_mode(body: ModeBody, user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    if body.mode == "live":
        confirm_password(user, body.password)
        if not body.confirm:
            raise HTTPException(status_code=400, detail="Confirme que entende que ordens reais serão enviadas à corretora.")
        if not office.market.mt5_ok:
            raise HTTPException(status_code=409, detail="A corretora (MetaTrader 5 ou cTrader) não está conectada. Conecte antes de usar a conta real.")
    apply_mode(office, body.mode)
    return {"mode": body.mode, "data_source": get_config().data_source}


class ConfirmBody(BaseModel):
    password: str


@router.post("/system/kill-switch/reset")
def reset_kill_switch(body: ConfirmBody, user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    confirm_password(user, body.password)
    office.agent("risk").reset_kill_switch()
    return {"ok": True}


@router.post("/system/paper/reset")
def reset_paper(body: ConfirmBody, user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    confirm_password(user, body.password)
    from app.db import session_scope
    from app.models import Trade

    with session_scope() as s:
        if s.query(Trade).filter(Trade.mode == "paper", Trade.status == "open").count():
            raise HTTPException(status_code=409, detail="Feche as posições simuladas abertas antes de zerar a conta.")
    kv_set("paper_reset_at", datetime.now(timezone.utc).isoformat())
    record_activity("system", f"Conta simulada zerada (saldo inicial {get_config().paper_initial_balance:.2f})", kind="system")
    return {"ok": True}

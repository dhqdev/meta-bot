"""Estado do sistema: ligar/desligar, modo simulado/real e travas."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import __version__
from app.api.auth import confirm_password
from app.deps import current_user, get_office
from app.events import record_activity
from app.kv import kv_set
from app.models import User
from app.runtime import get_config, update_config

router = APIRouter(prefix="/api", tags=["system"])


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
        "ai_provider": office.llm.provider() if office.llm.available() else None,
        "mt5": office.agent("infra").status,
        "account": account,
        "risk": office.agent("risk").status(),
        "plan": office.agent("manager").active_plan(),
        "plan_rationale": office.agent("manager").rationale,
    }


class RunBody(BaseModel):
    running: bool


@router.post("/system/running")
async def set_running(body: RunBody, user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    update_config({"system_running": body.running})
    record_activity("system", "Sistema ligado: a equipe chegou ao escritório" if body.running else "Sistema desligado: sem novas entradas (as posições abertas continuam protegidas)", kind="system")
    for agent in office.agents.values():
        agent.request("*")
    return {"running": body.running}


class ModeBody(BaseModel):
    mode: Literal["paper", "live"]
    password: str | None = None
    confirm: bool = False


@router.post("/system/mode")
async def set_mode(body: ModeBody, user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    if body.mode == "live":
        confirm_password(user, body.password)
        if not body.confirm:
            raise HTTPException(status_code=400, detail="Confirme que entende que ordens reais serão enviadas à corretora.")
        if not office.market.mt5_ok:
            raise HTTPException(status_code=409, detail="O MetaTrader 5 não está conectado. Conecte a corretora antes de usar a conta real.")
    update_config({"mode": body.mode, **({"data_source": "mt5"} if body.mode == "live" else {})})
    office.market.clear_cache()
    record_activity("system", "Modo CONTA DA CORRETORA (MT5): as ordens vão para a corretora" if body.mode == "live" else "Modo simulado: nenhuma ordem vai para a corretora", kind="system", level="warning" if body.mode == "live" else "info")
    return {"mode": body.mode}


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

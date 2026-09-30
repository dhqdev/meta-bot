"""Estratégias, ranking, evolução e backtests."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents.strategist import profile_dict, recent_signals
from app.core.strategies import FILTERS, REGISTRY, STRATEGIES
from app.db import session_scope
from app.deps import current_user, get_office
from app.models import BacktestRun, StrategyProfile, User
from app.runtime import TIMEFRAMES, get_config

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


@router.get("")
def catalog(user: User = Depends(current_user)) -> dict:
    enabled = set(get_config().enabled_strategies or [s.key for s in STRATEGIES])
    return {
        "strategies": [{**s.info(), "enabled": s.key in enabled} for s in STRATEGIES],
        "filters": [{"key": f.key, "name": f.name, "description": f.description} for f in FILTERS.values()],
        "sources": sorted({s.source for s in STRATEGIES}),
    }


@router.get("/ranking")
def ranking(
    symbol: str | None = None,
    timeframe: str | None = None,
    only_approved: bool = False,
    limit: int = Query(100, le=500),
    user: User = Depends(current_user),
    office=Depends(get_office),
) -> dict:
    cfg = get_config()
    rows = office.agent("strategist").ranking(symbol, timeframe, limit, only_approved)
    return {"rank_by": cfg.rank_by, "running": office.agent("strategist").ranking_running, "profiles": rows}


@router.get("/profiles/{profile_id}")
def profile(profile_id: int, user: User = Depends(current_user)) -> dict:
    with session_scope() as s:
        row = s.get(StrategyProfile, profile_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Perfil não encontrado.")
        data = profile_dict(row)
        runs = [
            {"id": r.id, "ts": r.ts.isoformat(), "kind": r.kind, "params": r.params, "filters": r.filters, "metrics": r.metrics, "oos_metrics": r.oos_metrics, "approved": r.approved}
            for r in s.scalars(
                select(BacktestRun)
                .where(BacktestRun.symbol == row.symbol, BacktestRun.timeframe == row.timeframe, BacktestRun.strategy == row.strategy)
                .order_by(BacktestRun.ts.desc())
                .limit(30)
            )
        ]
    return {**data, "history": runs}


@router.post("/ranking/run")
def run_ranking(user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    office.agent("strategist").request("ranking")
    return {"ok": True}


@router.post("/evolution/run")
def run_evolution(user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    office.agent("strategist").request("evolution")
    return {"ok": True}


class BacktestBody(BaseModel):
    symbol: str = Field(max_length=40)
    timeframe: str
    strategies: list[str] | None = None
    bars: int | None = Field(None, ge=300, le=20000)


@router.post("/backtest")
async def backtest(body: BacktestBody, user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    if body.timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Tempo gráfico inválido.")
    unknown = [k for k in (body.strategies or []) if k not in REGISTRY]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Estratégias desconhecidas: {', '.join(unknown)}")
    try:
        return await office.agent("strategist").manual_backtest(body.symbol.strip(), body.timeframe, body.strategies, body.bars)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Não foi possível obter os dados: {exc}") from exc


@router.get("/signals")
def signals(limit: int = Query(50, le=200), user: User = Depends(current_user)) -> list[dict]:
    return recent_signals(limit)

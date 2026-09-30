"""Operações, resultado, patrimônio e decisões do gerente."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.agents.cashier import trade_dict
from app.agents.manager import recent_decisions
from app.config import get_settings
from app.core.strategies import REGISTRY
from app.db import session_scope
from app.deps import current_user, get_office
from app.models import EquitySnapshot, Trade, User
from app.runtime import get_config

router = APIRouter(prefix="/api", tags=["trades"])


@router.get("/trades")
def trades(status: str | None = None, mode: str | None = None, limit: int = Query(200, le=1000), user: User = Depends(current_user)) -> list[dict]:
    with session_scope() as s:
        q = select(Trade).order_by(Trade.entry_time.desc()).limit(limit)
        if status:
            q = q.where(Trade.status == status)
        if mode:
            q = q.where(Trade.mode == mode)
        out = []
        for t in s.scalars(q):
            d = trade_dict(t)
            d["strategy_name"] = REGISTRY[t.strategy].name if t.strategy in REGISTRY else t.strategy
            out.append(d)
        return out


@router.get("/trades/open")
async def open_trades(user: User = Depends(current_user), office=Depends(get_office)) -> list[dict]:
    with session_scope() as s:
        rows = [trade_dict(t) for t in s.scalars(select(Trade).where(Trade.status == "open").order_by(Trade.entry_time.desc()))]
    for row in rows:
        try:
            tick = await office.market.tick(row["symbol"])
            spec = await office.market.spec(row["symbol"])
            from app.core.risk import pnl_money

            d = 1 if row["direction"] == "buy" else -1
            px = tick["bid"] if d > 0 else tick["ask"]
            row["price"] = px
            row["open_pnl"] = round(pnl_money(spec, d, row["entry_price"], px, row["volume"]) - row["commission"], 2)
            risk = abs(row["entry_price"] - (row["initial_sl"] or row["entry_price"]))
            row["open_r"] = round((px - row["entry_price"]) * d / risk, 2) if risk else 0.0
        except Exception:
            row["price"] = None
        row["strategy_name"] = REGISTRY[row["strategy"]].name if row["strategy"] in REGISTRY else row["strategy"]
    return rows


@router.post("/trades/{trade_id}/close")
async def close_trade(trade_id: int, user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    ok = await office.agent("cashier").request_close(trade_id, "fechada manualmente")
    if not ok:
        raise HTTPException(status_code=409, detail="Não foi possível fechar (já fechada ou corretora indisponível).")
    return {"ok": True}


@router.get("/trades/summary")
def summary(mode: str | None = None, days: int = Query(90, le=3650), user: User = Depends(current_user)) -> dict:
    mode = mode or get_config().mode
    tz = ZoneInfo(get_settings().timezone)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    with session_scope() as s:
        closed = list(s.scalars(select(Trade).where(Trade.mode == mode, Trade.status == "closed", Trade.exit_time >= since).order_by(Trade.exit_time)))
        eq = [
            {"t": int(r.ts.timestamp()), "equity": round(r.equity, 2), "balance": round(r.balance, 2)}
            for r in s.scalars(select(EquitySnapshot).where(EquitySnapshot.mode == mode, EquitySnapshot.ts >= since).order_by(EquitySnapshot.ts))
        ]
        n = len(closed)
        wins = sum(1 for t in closed if t.pnl > 0)
        gross_win = sum(t.pnl for t in closed if t.pnl > 0)
        gross_loss = -sum(t.pnl for t in closed if t.pnl < 0)
        by_strategy: dict[str, dict] = {}
        by_symbol: dict[str, dict] = {}
        by_day: dict[str, float] = {}
        for t in closed:
            for bucket, key in ((by_strategy, t.strategy or "manual"), (by_symbol, t.symbol)):
                row = bucket.setdefault(key, {"trades": 0, "wins": 0, "pnl": 0.0, "r": 0.0})
                row["trades"] += 1
                row["wins"] += 1 if t.pnl > 0 else 0
                row["pnl"] = round(row["pnl"] + t.pnl, 2)
                row["r"] = round(row["r"] + t.pnl_r, 3)
            day = t.exit_time.astimezone(tz).date().isoformat()
            by_day[day] = round(by_day.get(day, 0.0) + t.pnl, 2)
        today_pnl = round(sum(t.pnl for t in closed if t.exit_time >= today), 2)
    if len(eq) > 400:
        step = len(eq) / 400
        eq = [eq[int(i * step)] for i in range(400)] + [eq[-1]]
    return {
        "mode": mode,
        "trades": n,
        "wins": wins,
        "win_rate": round(wins / n, 4) if n else 0.0,
        "pnl": round(sum(t.pnl for t in closed), 2),
        "today_pnl": today_pnl,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0),
        "avg_r": round(sum(t.pnl_r for t in closed) / n, 3) if n else 0.0,
        "by_strategy": [{"strategy": k, "name": REGISTRY[k].name if k in REGISTRY else k, **v} for k, v in sorted(by_strategy.items(), key=lambda kv: -kv[1]["pnl"])],
        "by_symbol": [{"symbol": k, **v} for k, v in sorted(by_symbol.items(), key=lambda kv: -kv[1]["pnl"])],
        "by_day": [{"day": k, "pnl": v} for k, v in sorted(by_day.items())],
        "equity": eq,
    }


@router.get("/decisions")
def decisions(limit: int = Query(20, le=100), user: User = Depends(current_user)) -> list[dict]:
    return recent_decisions(limit)

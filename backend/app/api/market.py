"""Mercado: ativos, candles, horários, notícias e calendário."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.agents.schedule import sessions_at
from app.broker.market import MarketDataError
from app.db import session_scope
from app.deps import current_user, get_office
from app.models import CalendarEvent, NewsItem, User
from app.runtime import TIMEFRAMES, get_config

router = APIRouter(prefix="/api", tags=["market"])


@router.get("/market/symbols")
async def symbols(q: str = "", user: User = Depends(current_user), office=Depends(get_office)) -> list[dict]:
    try:
        return (await office.market.symbols(q))[:200]
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/market/candles")
async def candles(symbol: str, timeframe: str = "H1", count: int = Query(300, ge=20, le=3000), user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Tempo gráfico inválido.")
    try:
        bars = await office.market.rates(symbol, timeframe, count)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "source": office.market.source(),
        "candles": [
            {"t": int(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c)}
            for t, o, h, l, c in zip(bars.time, bars.open, bars.high, bars.low, bars.close)
        ],
    }


@router.get("/market/overview")
async def overview(user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    cfg = get_config()
    news = office.agent("news")
    schedule = office.agent("schedule")
    rows = []
    for sym in cfg.watchlist:
        item = {"symbol": sym}
        try:
            tick = await office.market.tick(sym)
            day = await office.market.rates(sym, "D1", 2)
            item.update(bid=tick["bid"], ask=tick["ask"], open=tick.get("open", True))
            if day.n >= 2:
                item["change_pct"] = round((tick["bid"] / day.close[-2] - 1) * 100, 3)
        except Exception as exc:
            item["error"] = str(exc)
        item["news"] = news.symbol_score(sym)
        item["hour_quality"] = schedule.hour_quality(sym)
        item["blackout"] = schedule.blackout(sym)
        item["best_hours_local"] = schedule.hour_profile(sym).get("best_hours_local", [])
        rows.append(item)
    return {"source": office.market.source(), "sessions": sessions_at(datetime.now(timezone.utc)), "symbols": rows}


@router.get("/market/hours")
def hours(symbol: str, user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    return office.agent("schedule").hour_profile(symbol) or {"hours": [], "best_hours_utc": [], "best_hours_local": []}


@router.get("/news")
def news(symbol: str | None = None, limit: int = Query(80, le=300), user: User = Depends(current_user)) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=3)
    with session_scope() as s:
        rows = list(s.scalars(select(NewsItem).where(NewsItem.published_at >= since).order_by(NewsItem.published_at.desc()).limit(600)))
        out = []
        for r in rows:
            if symbol and symbol not in (r.symbols or {}):
                continue
            out.append(
                {
                    "id": r.id, "source": r.source, "title": r.title, "url": r.url, "summary_pt": r.summary_pt,
                    "published_at": r.published_at.isoformat(), "impact": r.impact, "category": r.category,
                    "assets": r.assets, "symbols": r.symbols, "ai": r.ai, "outcome": r.outcome,
                }
            )
            if len(out) >= limit:
                break
        return out


@router.get("/calendar")
def calendar(days: int = Query(7, le=14), user: User = Depends(current_user)) -> list[dict]:
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        rows = s.scalars(
            select(CalendarEvent).where(CalendarEvent.ts >= now - timedelta(days=1), CalendarEvent.ts <= now + timedelta(days=days)).order_by(CalendarEvent.ts)
        )
        return [
            {"id": r.id, "title": r.title, "currency": r.currency, "ts": r.ts.isoformat(), "impact": r.impact, "forecast": r.forecast, "previous": r.previous, "actual": r.actual}
            for r in rows
        ]

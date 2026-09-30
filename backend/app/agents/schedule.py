"""Hugo, o analista de horários e calendário (sem IA).

- Mapa de horários: para cada ativo, mede no histórico (candles de 1 h) em que
  horas o preço anda mais do que custa operar (amplitude ÷ spread) e com mais
  direção. Depois mistura com o resultado real das operações em cada hora.
- Calendário econômico (ForexFactory): pausa as entradas antes e depois de
  eventos de alto impacto nas moedas do ativo.
- Sessões: Sydney, Tóquio, Londres, Nova York e B3.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from sqlalchemy import select

from app.agents.base import Agent, AgentProfile
from app.agents.skills import SkillDef
from app.config import get_settings
from app.core.assets import symbol_currencies
from app.db import session_scope
from app.kv import kv_get, kv_set
from app.models import CalendarEvent, Trade
from app.runtime import get_config
from app.services.feeds import fetch_calendar

SESSIONS = [
    # nome, abre (UTC), fecha (UTC)
    ("Sydney", 21, 6),
    ("Tóquio", 0, 9),
    ("Londres", 7, 16),
    ("Nova York", 12, 21),
    ("B3", 12, 21),
]


def sessions_at(ts: datetime) -> list[str]:
    hour = ts.astimezone(timezone.utc).hour
    weekday = ts.astimezone(timezone.utc).weekday()
    out = []
    for name, start, end in SESSIONS:
        if name == "B3" and weekday >= 5:
            continue
        if weekday == 5 or (weekday == 6 and hour < 21) or (weekday == 4 and hour >= 21 and name != "B3"):
            continue
        inside = start <= hour < end if start < end else (hour >= start or hour < end)
        if inside:
            out.append(name)
    return out


class ScheduleAgent(Agent):
    profile = AgentProfile(
        id="schedule",
        name="Hugo",
        role="Horários e Calendário",
        emoji="🕒",
        uses_ai=False,
        description="Mapeia os melhores horários de cada ativo, acompanha o calendário econômico e pausa as entradas perto de notícias de alto impacto.",
    )
    interval = 30.0
    idle_task = "Vigiando o relógio"
    skill_defs = [
        SkillDef("mapa_horarios", "Mapa de horários", "Descobre em que horas cada ativo anda mais que o custo de operar."),
        SkillDef("calendario_economico", "Calendário econômico", "Acompanha os eventos e evita operar em volta deles."),
        SkillDef("sessoes_mercado", "Sessões de mercado", "Sabe quais mercados estão abertos a cada hora."),
    ]

    def __init__(self, office):
        super().__init__(office)
        self._warned: set[str] = set()
        self._profiles: dict[str, dict] = {}

    async def tick(self) -> None:
        if get_settings().network_enabled and self.due("calendar", 3600):
            await self.refresh_calendar()
        if self.due("hours", 6 * 3600):
            await self.refresh_hours()
        if self.due("watch", 60):
            self.watch()

    # --------------------------------------------------------- calendário
    async def refresh_calendar(self) -> None:
        cfg = get_config()
        self.work("Atualizando o calendário econômico", "whiteboard", "🗓️")
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            items = await fetch_calendar(client, cfg.calendar_url)
        if not items:
            self.idle("Vigiando o relógio")
            return
        new = 0
        with session_scope() as s:
            known = set(s.scalars(select(CalendarEvent.uid).where(CalendarEvent.uid.in_([i.uid for i in items]))))
            for it in items:
                if it.uid in known:
                    row = s.scalar(select(CalendarEvent).where(CalendarEvent.uid == it.uid))
                    if row is not None and it.actual and row.actual != it.actual:
                        row.actual = it.actual
                    continue
                s.add(CalendarEvent(uid=it.uid, title=it.title, currency=it.currency, ts=it.ts, impact=it.impact, forecast=it.forecast, previous=it.previous, actual=it.actual))
                new += 1
        high = [i for i in items if i.impact == "High" and i.ts > datetime.now(timezone.utc)]
        if new:
            self.skills.gain("calendario_economico", 3, f"{new} eventos novos no calendário")
            self.log(f"Calendário atualizado: {new} eventos novos, {len(high)} de alto impacto pela frente", kind="calendar")
        self.idle("Vigiando o relógio")

    def upcoming(self, hours: float = 24, impacts: list[str] | None = None) -> list[dict]:
        now = datetime.now(timezone.utc)
        with session_scope() as s:
            q = select(CalendarEvent).where(CalendarEvent.ts >= now - timedelta(hours=1), CalendarEvent.ts <= now + timedelta(hours=hours))
            if impacts:
                q = q.where(CalendarEvent.impact.in_(impacts))
            rows = list(s.scalars(q.order_by(CalendarEvent.ts)))
            return [{"title": r.title, "currency": r.currency, "ts": r.ts.isoformat(), "impact": r.impact, "forecast": r.forecast, "previous": r.previous, "actual": r.actual} for r in rows]

    def blackout(self, symbol: str, at: datetime | None = None) -> dict | None:
        """Evento de alto impacto que pede pausa neste ativo agora (ou None)."""
        cfg = get_config()
        at = at or datetime.now(timezone.utc)
        ccys = symbol_currencies(symbol)
        start = at - timedelta(minutes=cfg.blackout_after_min)
        end = at + timedelta(minutes=cfg.blackout_before_min)
        with session_scope() as s:
            row = s.scalar(
                select(CalendarEvent)
                .where(CalendarEvent.ts >= start, CalendarEvent.ts <= end, CalendarEvent.impact.in_(cfg.blackout_impacts), CalendarEvent.currency.in_(list(ccys)))
                .order_by(CalendarEvent.ts)
                .limit(1)
            )
            if row is None:
                return None
            return {"title": row.title, "currency": row.currency, "ts": row.ts.isoformat(), "impact": row.impact}

    # ---------------------------------------------------------- horários
    async def refresh_hours(self) -> None:
        cfg = get_config()
        self.work("Medindo os melhores horários de cada ativo", "whiteboard", "🕒")
        live = self._live_by_hour()
        done = 0
        for symbol in cfg.watchlist:
            try:
                bars = await self.office.market.rates(symbol, "H1", 2000, closed_only=True, max_age=600)
            except Exception:
                continue
            if bars.n < 200:
                continue
            profile = self._profile(bars, live.get(symbol, {}))
            self._profiles[symbol] = profile
            kv_set(f"hour_profile:{symbol}", profile)
            done += 1
        if done:
            self.skills.gain("mapa_horarios", 2 * done, f"mapa de horários de {done} ativos")
            best = self._profiles.get(cfg.watchlist[0], {}).get("best_hours_local", [])
            if best:
                self.say(f"Melhores horas p/ {cfg.watchlist[0]}: {', '.join(f'{h}h' for h in best[:6])}", "🕒")
            self.log(f"Mapa de horários atualizado para {done} ativos", kind="schedule")
        self.idle("Vigiando o relógio")

    def _live_by_hour(self) -> dict[str, dict[int, list[float]]]:
        since = datetime.now(timezone.utc) - timedelta(days=120)
        out: dict[str, dict[int, list[float]]] = {}
        with session_scope() as s:
            for tr in s.scalars(select(Trade).where(Trade.status == "closed", Trade.entry_time >= since)):
                h = tr.entry_time.astimezone(timezone.utc).hour
                row = out.setdefault(tr.symbol, {}).setdefault(h, [0, 0.0])
                row[0] += 1
                row[1] += tr.pnl_r
        return out

    def _profile(self, bars, live: dict[int, list[float]]) -> dict:
        tz = ZoneInfo(get_settings().timezone)
        hours = (bars.time // 3600) % 24
        rng = (bars.high - bars.low) / bars.close
        spread = np.where(bars.spread > 0, bars.spread / bars.close, np.nan)
        eff = np.where(bars.high > bars.low, np.abs(bars.close - bars.open) / (bars.high - bars.low), 0.0)
        rows = []
        for h in range(24):
            m = hours == h
            n = int(m.sum())
            if n < 10:
                rows.append({"hour_utc": h, "n": n, "range_pct": 0.0, "edge": 0.0, "efficiency": 0.0, "quality": 0.0})
                continue
            r = float(np.nanmean(rng[m]))
            sp = float(np.nanmean(spread[m])) if np.isfinite(spread[m]).any() else r * 0.02
            rows.append({"hour_utc": h, "n": n, "range_pct": round(r * 100, 4), "edge": r / max(sp, 1e-9), "efficiency": float(np.mean(eff[m]))})
        active = [x for x in rows if x["n"] >= 10]
        if active:
            edges = np.array([x["edge"] for x in active])
            effs = np.array([x["efficiency"] for x in active])
            e_rank = edges.argsort().argsort() / max(len(active) - 1, 1)
            f_rank = effs.argsort().argsort() / max(len(active) - 1, 1)
            for x, er, fr in zip(active, e_rank, f_rank):
                hist_q = 0.7 * er + 0.3 * fr
                n_live, sum_r = live.get(x["hour_utc"], [0, 0.0])
                if n_live:
                    live_q = 1 / (1 + math.exp(-2.0 * sum_r / n_live))
                    lam = n_live / (n_live + 10)
                    x["quality"] = round((1 - lam) * hist_q + lam * live_q, 3)
                    x["live_trades"] = n_live
                else:
                    x["quality"] = round(float(hist_q), 3)
        for x in rows:
            x["edge"] = round(x["edge"], 2)
            x["efficiency"] = round(x["efficiency"], 3)
            x["hour_local"] = datetime(2026, 1, 5, x["hour_utc"], tzinfo=timezone.utc).astimezone(tz).hour
        best = sorted([x for x in rows if x["quality"] >= 0.6], key=lambda x: -x["quality"])
        return {
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "hours": rows,
            "best_hours_utc": sorted(x["hour_utc"] for x in best),
            "best_hours_local": sorted(x["hour_local"] for x in best),
        }

    def hour_profile(self, symbol: str) -> dict:
        if symbol not in self._profiles:
            saved = kv_get(f"hour_profile:{symbol}")
            if saved:
                self._profiles[symbol] = saved
        return self._profiles.get(symbol, {})

    def hour_quality(self, symbol: str, at: datetime | None = None) -> float:
        prof = self.hour_profile(symbol)
        if not prof:
            return 0.5
        hour = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).hour
        for row in prof.get("hours", []):
            if row["hour_utc"] == hour:
                return float(row.get("quality", 0.0))
        return 0.5

    def best_hours_utc(self, symbol: str) -> list[int]:
        return list(self.hour_profile(symbol).get("best_hours_utc", []))

    # ------------------------------------------------------------ vigília
    def watch(self) -> None:
        cfg = get_config()
        now = datetime.now(timezone.utc)
        sessions = sessions_at(now)
        events = self.upcoming(2, cfg.blackout_impacts)
        for ev in events:
            ts = datetime.fromisoformat(ev["ts"])
            minutes = (ts - now).total_seconds() / 60
            key = f"{ev['title']}|{ev['ts']}"
            if 0 < minutes <= cfg.blackout_before_min + 5 and key not in self._warned:
                self._warned.add(key)
                affected = [s for s in cfg.watchlist if ev["currency"] in symbol_currencies(s)]
                self.work(f"Alerta: {ev['title']} ({ev['currency']}) em {int(minutes)} min", "agent:manager", "⏰")
                self.say(f"⏰ {ev['title']} ({ev['currency']}) em {int(minutes)} min. Pausa em {', '.join(affected) or 'nenhum ativo'}", "⏰", to="manager")
                self.log(f"Evento de alto impacto em {int(minutes)} min: {ev['title']} ({ev['currency']}). Entradas pausadas em {', '.join(affected) or 'nenhum ativo da lista'}.", kind="calendar", level="warning")
                self.skills.gain("calendario_economico", 2, "alerta de evento")
        if len(self._warned) > 500:
            self._warned.clear()
        self.office.publish_office(sessions=sessions, next_events=self.upcoming(12, ["High", "Medium"])[:6])
        if self.state == "working":
            self.idle("Vigiando o relógio")
        if sessions and self.due("sessions_xp", 6 * 3600):
            self.skills.gain("sessoes_mercado", 1, "sessões acompanhadas")

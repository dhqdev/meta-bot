"""Cada ajuste da tela de Configurações muda de verdade o comportamento da equipe."""

import asyncio
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import BaseModel

from app.db import session_scope
from app.kv import kv_set, secret_set
from app.models import CalendarEvent, StrategyProfile
from app.runtime import update_config
from app.services.llm import LLMService
from tests.test_agents import SYMBOL, activate_plan, make_profile, running  # noqa: F401  (fixture)


def approved(symbol: str, timeframe: str = "H1", strategy: str = "ifr_reversao") -> int:
    with session_scope() as s:
        p = StrategyProfile(
            symbol=symbol, timeframe=timeframe, strategy=strategy, params={}, filters={}, risk={"sl_atr": 1.5, "tp_r": 2.0},
            status="aprovada", score=0.8, metrics={"trades": 80, "win_rate": 0.6, "wilson_lb": 0.5, "expectancy_r": 0.2},
            oos_metrics={"trades": 25, "expectancy_r": 0.1}, tested_at=datetime.now(timezone.utc),
        )
        s.add(p)
        s.flush()
        return p.id


def test_min_hour_quality_blocks_weak_hours(office, running):
    make_profile()
    manager = office.agent("manager")
    # sem mapa de horários a qualidade é 0,5 (neutra)
    update_config({"min_hour_quality": 0.6})
    assert all(any("hora fraca" in b for b in c["blocked"]) for c in manager.build_candidates())
    update_config({"min_hour_quality": 0.3})
    assert not any(any("hora fraca" in b for b in c["blocked"]) for c in manager.build_candidates())


def test_watchlist_and_timeframes_filter_candidates(office, running):
    approved(SYMBOL, "H1")
    approved("EURUSD", "H1")
    approved(SYMBOL, "H4", "supertrend")
    manager = office.agent("manager")
    update_config({"watchlist": [SYMBOL], "timeframes": ["H1"]})
    cands = manager.build_candidates()
    assert {(c["symbol"], c["timeframe"]) for c in cands} == {(SYMBOL, "H1")}
    update_config({"watchlist": [SYMBOL, "EURUSD"], "timeframes": ["H1", "H4"]})
    assert len(manager.build_candidates()) == 3


def test_max_active_setups_limits_the_plan(office, running):
    symbols = [SYMBOL, "ETHUSD", "XAUUSD"]
    for sym in symbols:
        approved(sym)
    update_config({"watchlist": symbols, "min_hour_quality": 0.0})
    manager = office.agent("manager")
    update_config({"max_active_setups": 1})
    assert len(manager.deterministic_plan(manager.build_candidates())) == 1
    update_config({"max_active_setups": 3})
    assert len(manager.deterministic_plan(manager.build_candidates())) == 3


def test_news_filter_and_threshold_veto_signals(office, running, monkeypatch):
    pid = make_profile()
    activate_plan(office, pid)
    manager = office.agent("manager")
    strong_bad_news = {"score": -0.9, "confidence": 0.9, "count": 5, "alerts": ["banco central surpreende"]}
    monkeypatch.setattr(office.agent("news"), "symbol_score", lambda symbol: strong_bad_news)
    update_config({"use_news_filter": True, "news_block_threshold": 0.55})
    ok, reason, _ = manager.review_signal(SYMBOL, pid, "buy")
    assert not ok and "notícias" in reason
    assert manager.review_signal(SYMBOL, pid, "sell")[0]  # a favor da notícia: pode
    update_config({"news_block_threshold": 0.95})  # força 0,81 < 0,95: não veta
    assert manager.review_signal(SYMBOL, pid, "buy")[0]
    update_config({"use_news_filter": False, "news_block_threshold": 0.55})
    assert manager.review_signal(SYMBOL, pid, "buy")[0]


def test_risk_per_trade_sets_the_lot(office, running):
    async def run():
        risk = office.agent("risk")
        tick = await office.market.tick(SYMBOL)
        entry = tick["ask"]
        update_config({"risk_per_trade_pct": 0.5})
        small = await risk.evaluate(SYMBOL, "buy", entry, entry - 1500)
        update_config({"risk_per_trade_pct": 1.0})
        big = await risk.evaluate(SYMBOL, "buy", entry, entry - 1500)
        assert small.ok and big.ok
        assert big.volume == pytest.approx(small.volume * 2, rel=0.35)
        assert big.risk_money > small.risk_money

    asyncio.run(run())


def test_blackout_window_and_impacts(office):
    schedule = office.agent("schedule")
    with session_scope() as s:
        s.add(CalendarEvent(uid="teste-payroll", ts=datetime.now(timezone.utc) + timedelta(minutes=20), currency="USD", title="Payroll", impact="High"))
    update_config({"blackout_before_min": 30, "blackout_impacts": ["High"]})
    assert schedule.blackout("EURUSD")["title"] == "Payroll"
    assert schedule.blackout("EURGBP") is None  # evento do dólar não pausa euro x libra
    update_config({"blackout_before_min": 10})
    assert schedule.blackout("EURUSD") is None  # evento daqui a 20 min: ainda fora da pausa
    update_config({"blackout_before_min": 30, "blackout_impacts": ["Medium"]})
    assert schedule.blackout("EURUSD") is None  # só pausa em impacto médio


def test_exit_settings_reach_the_cashier(office):
    update_config({"break_even_r": 0.0, "trailing_start_r": 2.5, "trailing_atr_mult": 3.0, "adaptive_exits": False})
    office.invalidate_exit_params()
    assert office.exit_params() == {"break_even_r": 0.0, "trailing_start_r": 2.5, "trailing_atr": 3.0}


class Tiny(BaseModel):
    ok: bool


def test_ai_switch_and_hourly_limit():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"model": "x", "choices": [{"finish_reason": "stop", "message": {"content": '{"ok": true}'}}], "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.0001}})

    async def run():
        secret_set("openrouter_api_key", "sk-or-test")
        llm = LLMService(transport=httpx.MockTransport(handler))
        ask = lambda: llm.complete_json(agent="manager", purpose="teste", system="s", user="u", schema_model=Tiny)  # noqa: E731
        update_config({"ai_enabled": False})
        assert llm.available() is False
        res = await ask()
        assert not res.ok and "desligada" in res.error and not calls
        update_config({"ai_enabled": True, "ai_max_calls_per_hour": 1})
        assert (await ask()).ok and len(calls) == 1
        res = await ask()
        assert not res.ok and "por hora" in res.error and len(calls) == 1
        update_config({"ai_max_calls_per_hour": 50, "ai_daily_budget_usd": 0.00005})
        res = await ask()
        assert not res.ok and "orçamento" in res.error

    asyncio.run(run())


def test_settings_api_gives_friendly_errors(client_owner):
    c = client_owner
    r = c.put("/api/settings", json={"daily_meeting_time": "25:00"})
    assert r.status_code == 400 and r.json()["detail"].startswith("Horário da daily:")
    r = c.put("/api/settings", json={"risk_per_trade_pct": 50})
    assert r.status_code == 400 and r.json()["detail"] == "Risco por operação: precisa ser no máximo 5"
    r = c.put("/api/settings", json={"daily_loss_limit": 80, "daily_loss_unit": "percent"})
    assert r.status_code == 400 and "50%" in r.json()["detail"]
    r = c.put("/api/settings", json={"max_open_positions": 2.5})
    assert r.status_code == 400 and "inteiro" in r.json()["detail"]
    r = c.put("/api/settings", json={"daily_meeting_time": "7:5", "daily_profit_target": 150, "daily_profit_unit": "money"})
    assert r.status_code == 200
    assert r.json()["config"]["daily_meeting_time"] == "07:05"
    view = c.get("/api/settings").json()
    assert view["defaults"]["daily_meeting_time"] == "19:00"
    assert set(view["ai"]["models"]) == {"news", "manager", "daily"}
    kv_set("ai_status", {"ok": False, "error": "chave inválida", "at": "agora"})
    assert c.get("/api/settings").json()["ai"]["last"]["error"] == "chave inválida"


def test_ai_plan_reused_while_the_best_candidates_stay_the_same(office):
    """Pontuação que oscila a cada candle não gasta outra chamada de IA; candidato novo ou direção nova, sim."""
    manager = office.agent("manager")

    def cand(pid: int, score: float, direction: str = "both", blocked: list | None = None) -> dict:
        return {"profile_id": pid, "score": score, "suggested_direction": direction, "blocked": blocked or [],
                "symbol": f"S{pid}", "timeframe": "H1", "strategy": "x", "strategy_name": "X", "votes": {}}

    base = [cand(i, 1.0 - i * 0.05) for i in range(1, 11)]
    manager._ai_cache = {"at": time.time(), "fingerprint": manager._fingerprint(base),
                         "picks": [(1, "both", 1.0, "a"), (2, "long", 0.5, "b")], "rationale": "r", "model": "m"}

    wobble = [dict(c, score=c["score"] + (0.04 if c["profile_id"] % 2 else -0.04)) for c in base]
    wobble.sort(key=lambda c: c["score"], reverse=True)
    reused = manager._reuse_ai_plan(wobble)
    assert reused is not None
    assert [(p["profile_id"], p["direction"], p["risk_mult"]) for p in reused[0]] == [(1, "both", 1.0), (2, "long", 0.5)]

    # fora do topo, mexer não muda nada
    assert manager._reuse_ai_plan(base[:-1] + [cand(99, 0.1)]) is not None
    # candidato novo no topo, direção nova, escolhido bloqueado ou plano vencido → chama a IA de novo
    assert manager._reuse_ai_plan([cand(50, 2.0)] + base) is None
    assert manager._reuse_ai_plan([cand(1, 0.95, "short")] + base[1:]) is None
    assert manager._reuse_ai_plan([cand(1, 0.95, blocked=["hora fraca"])] + base[1:]) is None
    dropped = sorted([dict(c, score=0.1) if c["profile_id"] == 2 else c for c in base], key=lambda c: c["score"], reverse=True)
    assert manager._reuse_ai_plan(dropped) is None  # escolhido saiu do topo
    manager._ai_cache["at"] = time.time() - 2 * 3600
    assert manager._reuse_ai_plan(base) is None

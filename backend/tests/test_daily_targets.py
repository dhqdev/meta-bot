"""Metas do dia: ao bater o limite de perda ou a meta de ganho, a equipe para até o dia seguinte."""

import asyncio

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.db import session_scope
from app.models import AgentMessage, Signal, Trade
from app.runtime import RuntimeConfig, update_config
from tests.test_agents import SYMBOL, activate_plan, make_profile, new_signal


@pytest.fixture
def running():
    update_config({"system_running": True, "watchlist": [SYMBOL], "timeframes": ["H1"], "paper_commission_per_lot": 0.0})


def fake_equity(office, monkeypatch, start=10_000.0):
    box = {"equity": start}

    async def account():
        return {"balance": start, "equity": box["equity"], "currency": "USD"}

    monkeypatch.setattr(office.paper, "account", account)
    return box


def test_daily_loss_limit_closes_positions_and_stops_the_team(office, running, monkeypatch):
    async def run():
        update_config({"daily_loss_limit": 2.0, "daily_loss_unit": "percent", "close_on_daily_limit": True})
        pid = make_profile()
        activate_plan(office, pid)
        trade_id = await office.submit_signal(await new_signal(office, pid))
        assert trade_id is not None
        box = fake_equity(office, monkeypatch)
        risk = office.agent("risk")
        await risk.guard()  # início do dia: 10.000
        assert risk.day_stopped() is None
        box["equity"] = 9_790.0  # -2,1%
        await risk.guard()
        assert risk.day_stopped() == "loss"
        assert risk.status()["day_stop"] == "loss" and risk.status()["daily_loss_money"] == pytest.approx(200.0)
        with session_scope() as s:
            tr = s.get(Trade, trade_id)
            assert tr.status == "closed" and tr.exit_reason == "limite do dia"
        # o gerente some com o plano e ninguém mais entra hoje
        assert office.agent("manager").active_plan() == []
        activate_plan(office, pid)
        assert office.agent("manager").active_plan() == []
        sig = await new_signal(office, pid)
        assert await office.submit_signal(sig) is None
        with session_scope() as s:
            assert s.get(Signal, sig).status == "vetado"
            texts = [m.text for m in s.scalars(select(AgentMessage).where(AgentMessage.sender == "risk"))]
        assert any("limite" in t.lower() or "paramos" in t.lower() for t in texts)

    asyncio.run(run())


def test_daily_profit_target_in_money_without_closing(office, running, monkeypatch):
    async def run():
        update_config({"daily_loss_limit": 0, "daily_profit_target": 150.0, "daily_profit_unit": "money", "close_on_daily_limit": False})
        pid = make_profile()
        activate_plan(office, pid)
        trade_id = await office.submit_signal(await new_signal(office, pid))
        box = fake_equity(office, monkeypatch)
        risk = office.agent("risk")
        await risk.guard()
        box["equity"] = 10_100.0
        await risk.guard()
        assert risk.day_stopped() is None and risk.status()["target_progress"] == pytest.approx(100 / 150, rel=1e-3)
        box["equity"] = 10_160.0
        await risk.guard()
        assert risk.day_stopped() == "target"
        with session_scope() as s:
            assert s.get(Trade, trade_id).status == "open"  # não era para encerrar
        verdict = await risk.evaluate(SYMBOL, "buy", 60_000.0, 58_500.0)
        assert not verdict.ok and "meta do dia" in verdict.reason

    asyncio.run(run())


def test_new_day_releases_the_team(office, running, monkeypatch):
    async def run():
        update_config({"daily_loss_limit": 1.0, "daily_loss_unit": "percent"})
        box = fake_equity(office, monkeypatch)
        risk = office.agent("risk")
        await risk.guard()
        box["equity"] = 9_800.0
        await risk.guard()
        assert risk.day_stopped() == "loss"
        st = risk.state_kv()
        st["day_key"] = "paper:2000-01-01"  # simula o dia seguinte
        risk.save_state(st)
        assert risk.day_stopped() is None
        await risk.guard()
        assert risk.day_stopped() is None and risk.status()["day_pnl"] == 0.0

    asyncio.run(run())


def test_daily_target_validation():
    with pytest.raises(ValidationError):
        RuntimeConfig(daily_loss_limit=60, daily_loss_unit="percent")
    ok = RuntimeConfig(daily_loss_limit=500, daily_loss_unit="money", daily_profit_target=1000, daily_profit_unit="money")
    assert ok.daily_loss_limit == 500


def test_old_config_is_migrated():
    from app.kv import kv_set
    from app.runtime import get_config, reset_cache

    kv_set("runtime_config", {"max_daily_loss_pct": 4.5, "timeframes": ["H1", "H4"], "ai_provider": "anthropic"})
    reset_cache()
    cfg = get_config()
    assert cfg.daily_loss_limit == 4.5 and cfg.daily_loss_unit == "percent"
    assert cfg.timeframes == ["M5", "H1", "H4"] and cfg.config_version == 2

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
    # v5: a carteira diária substitui os tempos gráficos antigos
    assert cfg.timeframes == ["D1"] and cfg.market_set == "carteira_diaria" and cfg.config_version == 5


def test_old_watchlist_becomes_the_daily_portfolio_with_the_broker_suffix():
    from app.kv import kv_set
    from app.runtime import CARTEIRA_ATIVOS, get_config, reset_cache

    feeds = [{"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "enabled": True},
             {"name": "Minha fonte", "url": "https://exemplo.com/rss", "enabled": True}]
    kv_set("runtime_config", {"config_version": 2, "watchlist": ["EURUSDm", "XAUUSDm", "BTCUSD"], "news_feeds": feeds,
                              "daily_loss_limit": 1.5, "daily_profit_target": 1})
    reset_cache()
    cfg = get_config()
    assert cfg.watchlist == [p + "m" for p in CARTEIRA_ATIVOS] and cfg.symbol_suffix == "m"
    assert [f.enabled for f in cfg.news_feeds] == [False, True]
    assert (cfg.daily_loss_limit, cfg.daily_profit_target) == (1.5, 1)


def test_v4_watches_six_setups_unless_the_owner_chose_another_number():
    from app.kv import kv_set
    from app.runtime import get_config, reset_cache

    kv_set("runtime_config", {"config_version": 3, "max_active_setups": 3})
    reset_cache()
    cfg = get_config()
    assert (cfg.max_active_setups, cfg.max_setups_per_symbol, cfg.config_version) == (6, 2, 5)
    kv_set("runtime_config", {"config_version": 3, "max_active_setups": 5})
    reset_cache()
    assert get_config().max_active_setups == 5


def test_office_closes_for_the_weekend_and_reopens_with_the_market(office):
    """Câmbio fechado (sexta 18h de Brasília) → escritório fecha sozinho até domingo; religar na mão vale."""
    from datetime import datetime, timezone

    from app.agents.office import WEEKEND_SKIP_KEY
    from app.kv import kv_get, kv_set
    from app.runtime import get_config, update_config

    update_config({"system_running": True})
    friday_open = datetime(2026, 10, 2, 20, 30, tzinfo=timezone.utc)
    assert not office.check_weekend(friday_open)
    assert get_config().system_running

    friday_night = datetime(2026, 10, 2, 21, 5, tzinfo=timezone.utc)
    assert office.check_weekend(friday_night)
    info = office.break_info()
    assert info["kind"] == "weekend" and info["until"].startswith("2026-10-04T22:00")
    assert not get_config().system_running
    assert not office.check_weekend(friday_night)  # já fechado

    # dono religou na mão: não fecha de novo até a reabertura
    office.end_break(reopen=False)
    update_config({"system_running": True})
    kv_set(WEEKEND_SKIP_KEY, "2026-10-04T22:00:00+00:00")
    assert not office.check_weekend(datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc))
    kv_set(WEEKEND_SKIP_KEY, None)

    # escritório desligado pelo dono: o fim de semana não mexe
    update_config({"system_running": False})
    assert not office.check_weekend(friday_night) and office.break_info() is None

    # pausa vencida (domingo à noite) → reabre sozinho
    update_config({"system_running": True})
    office.check_weekend(friday_night)
    kv_set("office.break", {**kv_get("office.break"), "until": "2000-01-01T00:00:00+00:00"})
    assert office.check_break() and get_config().system_running

    # desligado nas configurações: não fecha
    update_config({"weekend_close": False})
    assert not office.check_weekend(friday_night)

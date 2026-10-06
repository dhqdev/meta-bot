"""Fluxo entre os agentes na conta simulada: sinal → gerente → risco → caixa → auditora."""

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models import Signal, StrategyProfile, Trade
from app.runtime import update_config

SYMBOL = "BTCUSD"  # cripto: mercado simulado aberto 24h (testes rodam em qualquer dia)


def make_profile(status="aprovada", win_rate=0.6) -> int:
    with session_scope() as s:
        p = StrategyProfile(
            symbol=SYMBOL, timeframe="H1", strategy="ifr_reversao", params={}, filters={}, risk={"sl_atr": 1.5, "tp_r": 2.0},
            status=status, score=0.7, metrics={"trades": 80, "win_rate": win_rate, "wilson_lb": 0.5, "expectancy_r": 0.2},
            oos_metrics={"trades": 25, "expectancy_r": 0.1}, tested_at=datetime.now(timezone.utc),
        )
        s.add(p)
        s.flush()
        return p.id


def activate_plan(office, profile_id, direction="both"):
    manager = office.agent("manager")
    manager.plan = [{"profile_id": profile_id, "symbol": SYMBOL, "timeframe": "H1", "strategy": "ifr_reversao", "strategy_name": "IFR", "direction": direction, "risk_mult": 1.0, "reason": "teste", "votes": {"strategy": 0.8, "hour": 0.7, "news": 1.0, "live": 0.5}}]
    manager.plan_expires = time.time() + 3600


async def new_signal(office, profile_id, side="buy", entry_type="market", sl_dist=1500.0):
    tick = await office.market.tick(SYMBOL)
    price = tick["ask"] if side == "buy" else tick["bid"]
    d = 1 if side == "buy" else -1
    with session_scope() as s:
        sig = Signal(
            symbol=SYMBOL, timeframe="H1", strategy="ifr_reversao", direction=side, entry_type=entry_type,
            price=price, trigger=price + d * 10 if entry_type == "stop" else None, sl=price - d * sl_dist, tp=price + d * 2 * sl_dist,
            atr=1000.0, profile_id=profile_id, expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        )
        s.add(sig)
        s.flush()
        return sig.id


@pytest.fixture
def running():
    update_config({"system_running": True, "watchlist": [SYMBOL], "timeframes": ["H1"], "paper_commission_per_lot": 0.0})


def test_full_pipeline_open_and_stop(office, running):
    async def run():
        pid = make_profile()
        activate_plan(office, pid)
        sig_id = await new_signal(office, pid)
        trade_id = await office.submit_signal(sig_id)
        assert trade_id is not None
        with session_scope() as s:
            tr = s.get(Trade, trade_id)
            assert tr.mode == "paper" and tr.status == "open" and tr.direction == "buy"
            # 10.000 × 0,5% = 50 de risco; stop de 1.500 com 0,01 USD/tick (1 lote = 1 BTC) → 0,03 lote
            assert tr.volume == pytest.approx(0.03)
            assert tr.risk_money == pytest.approx(45.0, rel=0.01)
            assert s.get(Signal, sig_id).status == "executado"
            tr.sl = tr.entry_price + 5_000  # força o stop
        await office.agent("cashier").monitor()
        with session_scope() as s:
            tr = s.get(Trade, trade_id)
            assert tr.status == "closed" and tr.exit_reason == "sl"
            prof = s.get(StrategyProfile, pid)
            assert prof.live["n"] == 1 and prof.live["wins"] == 0
        risk_state = office.agent("risk").state_kv()
        assert risk_state["consec_losses"] == 1

    asyncio.run(run())


def test_manager_vetoes_signal_outside_plan(office, running):
    async def run():
        pid = make_profile()
        sig_id = await new_signal(office, pid)  # sem plano ativo
        assert await office.submit_signal(sig_id) is None
        with session_scope() as s:
            sig = s.get(Signal, sig_id)
            assert sig.status == "vetado" and sig.reason.startswith("gerente")

    asyncio.run(run())


def test_manager_respects_direction(office, running):
    async def run():
        pid = make_profile()
        activate_plan(office, pid, direction="short")
        sig_id = await new_signal(office, pid, side="buy")
        assert await office.submit_signal(sig_id) is None

    asyncio.run(run())


def test_risk_limits_positions(office, running):
    async def run():
        update_config({"max_open_positions": 1})
        pid = make_profile()
        activate_plan(office, pid)
        first = await office.submit_signal(await new_signal(office, pid))
        assert first is not None
        second_sig = await new_signal(office, pid)
        assert await office.submit_signal(second_sig) is None
        with session_scope() as s:
            assert s.get(Signal, second_sig).reason.startswith("risco")

    asyncio.run(run())


def test_kill_switch_blocks_everything(office, running):
    async def run():
        pid = make_profile()
        activate_plan(office, pid)
        st = office.agent("risk").state_kv()
        st["kill_switch"] = True
        st["kill_reason"] = "teste"
        office.agent("risk").save_state(st)
        assert await office.submit_signal(await new_signal(office, pid)) is None

    asyncio.run(run())


def test_stop_entry_waits_for_trigger(office, running):
    async def run():
        pid = make_profile()
        activate_plan(office, pid)
        sig_id = await new_signal(office, pid, entry_type="stop")
        assert await office.submit_signal(sig_id) is None  # armada, ainda não executada
        cashier = office.agent("cashier")
        assert sig_id in cashier._pending
        cashier._pending[sig_id]["trigger"] = 1.0  # qualquer preço toca o gatilho
        await cashier.process_pending()
        with session_scope() as s:
            assert s.get(Signal, sig_id).status == "executado"
            assert s.scalar(select(Trade).where(Trade.signal_id == sig_id)) is not None

    asyncio.run(run())


def test_manager_decision_without_ai(office, running):
    async def run():
        make_profile()
        await office.agent("manager").decide()
        plan = office.agent("manager").active_plan()
        assert len(plan) <= 3
        # sem mapa de horários a hora vale 0,5 (> mínimo 0,35): o setup entra no plano
        assert plan and plan[0]["symbol"] == SYMBOL

    asyncio.run(run())


def test_strategist_ranking_creates_profiles(office, running):
    async def run():
        update_config({"enabled_strategies": ["supertrend", "donchian_turtle"], "timeframes": ["H4"]})
        total = await office.agent("strategist").run_ranking()
        assert total == 2
        rows = office.agent("strategist").ranking()
        assert {r["strategy"] for r in rows} == {"supertrend", "donchian_turtle"}
        assert all(r["metrics"]["trades"] >= 0 for r in rows)

    asyncio.run(run())


def test_skills_level_up(office):
    book = office.agent("news").skills
    leveled = book.gain("leitura_manchetes", 60, "teste")
    assert leveled
    skill = book.get("leitura_manchetes")
    assert skill["level"] == 2 and skill["xp"] == 60


def test_manager_learns_team_weights(office):
    manager = office.agent("manager")
    before = manager.weights()
    tr = Trade(mode="paper", symbol=SYMBOL, direction="buy", volume=1, entry_price=1, pnl=10, pnl_r=1, context={"votes": {"strategy": 0.9, "hour": 0.2, "news": 0.5, "live": 0.5}})
    for _ in range(5):
        manager.on_trade_closed(tr)
    after = manager.weights()
    assert after["strategy"] > before["strategy"]
    assert after["hour"] < before["hour"]
    assert abs(sum(after.values()) - 1) < 1e-6


def _bars_after(entry_time: datetime, closes: list[float], start_offset_h: int = 0):
    """Candles H1 começando no candle da entrada (fechados), com o fechamento pedido e amplitude pequena."""
    import pandas as pd

    from app.core.bars import Bars

    base = int(entry_time.timestamp()) // 3600 * 3600 + start_offset_h * 3600
    n = 30
    first = closes[0]
    cl = [first] * (n - len(closes)) + closes
    times = [base - (n - len(closes)) * 3600 + i * 3600 for i in range(n)]
    df = pd.DataFrame({"time": times, "open": cl, "high": [c + 50 for c in cl], "low": [c - 50 for c in cl], "close": cl, "spread": [0.0] * n})
    return Bars(df, SYMBOL, "H1", 0.01)


def test_break_even_waits_for_the_candle_close_like_the_backtest(office, running, monkeypatch):
    """Zero a zero decide pelo fechamento do candle (como o backtest); um pico no meio do candle não mexe no stop."""
    async def run():
        update_config({"break_even_r": 1.0, "trailing_start_r": 0.0, "adaptive_exits": False})
        pid = make_profile()
        activate_plan(office, pid)
        trade_id = await office.submit_signal(await new_signal(office, pid))
        cashier = office.agent("cashier")
        with session_scope() as s:
            tr = s.get(Trade, trade_id)
            entry, risk_px, entry_time = tr.entry_price, abs(tr.entry_price - tr.initial_sl), tr.entry_time
        tick = await office.market.tick(SYMBOL)
        spike = {**tick, "bid": entry + 1.5 * risk_px, "ask": entry + 1.5 * risk_px + 1}

        async def fake_tick(symbol):
            return spike

        monkeypatch.setattr(office.market, "tick", fake_tick)
        # 1) o candle da entrada ainda não fechou: o pico não move o stop
        async def no_close_yet(*a, **k):
            return _bars_after(entry_time, [entry], start_offset_h=-1)

        monkeypatch.setattr(office.market, "rates", no_close_yet)
        await cashier.monitor()
        with session_scope() as s:
            assert not (s.get(Trade, trade_id).mgmt or {}).get("be")
        # 2) candle fechou abaixo de 1R: continua sem zero a zero, mesmo com o preço de agora acima
        async def closed_below(*a, **k):
            return _bars_after(entry_time, [entry + 0.5 * risk_px])

        monkeypatch.setattr(office.market, "rates", closed_below)
        await cashier.monitor()
        with session_scope() as s:
            assert not (s.get(Trade, trade_id).mgmt or {}).get("be")
        # 3) candle fechou acima de 1R: stop vai para a entrada (sem somar o spread; comissão zero aqui)
        async def closed_above(*a, **k):
            return _bars_after(entry_time, [entry + 1.2 * risk_px])

        monkeypatch.setattr(office.market, "rates", closed_above)
        await cashier.monitor()
        with session_scope() as s:
            tr = s.get(Trade, trade_id)
            assert tr.mgmt.get("be") and tr.sl == pytest.approx(entry, abs=0.01) and tr.status == "open"

    asyncio.run(run())

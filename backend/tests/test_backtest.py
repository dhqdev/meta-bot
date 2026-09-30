import numpy as np
import pandas as pd
import pytest

from app.core.backtest import CostModel, RiskParams, run_backtest, simulate_exit, split_trades
from app.core.bars import Bars
from app.core.evaluation import Candidate, evaluate, evolve
from app.core.metrics import ApprovalRules, approve, compute_metrics, wilson_lower
from app.core.strategies import SignalSet, get_strategy


def bars_from(ohlc, spread=0.0):
    n = len(ohlc)
    df = pd.DataFrame(ohlc, columns=["open", "high", "low", "close"])
    df["time"] = 1_700_000_000 + np.arange(n) * 3600
    df["volume"] = 100.0
    df["spread"] = spread
    return Bars(df, "TEST", "H1", 0.01)


def flat_prefix(n=60, price=100.0):
    return [[price, price + 0.5, price - 0.5, price] for _ in range(n)]


def signal_at(n, idx, direction=1):
    long_e = np.zeros(n, dtype=bool)
    short_e = np.zeros(n, dtype=bool)
    (long_e if direction > 0 else short_e)[idx] = True
    return SignalSet(long_e, short_e, np.zeros(n, dtype=bool), np.zeros(n, dtype=bool))


def test_long_hits_take_profit():
    rows = flat_prefix() + [[100, 100.2, 99.9, 100.1], [100.1, 103.5, 100.0, 103.0], [103, 103, 103, 103]]
    b = bars_from(rows)
    # ATR ~ 1.0 → stop 1.5, alvo 2R = 3.0
    trades = run_backtest(b, signal_at(len(rows), 59), RiskParams(sl_atr=1.5, tp_r=2.0), CostModel(spread=0.0))
    assert len(trades) == 1
    tr = trades[0]
    assert tr.reason == "tp"
    assert tr.entry == pytest.approx(100.0)
    assert tr.r == pytest.approx(2.0, rel=1e-3)


def test_stop_wins_when_both_inside_same_candle():
    rows = flat_prefix() + [[100, 104, 96, 100], [100, 100, 100, 100]]
    b = bars_from(rows)
    trades = run_backtest(b, signal_at(len(rows), 59), RiskParams(sl_atr=1.5, tp_r=2.0), CostModel(spread=0.0))
    assert trades[0].reason == "sl"
    assert trades[0].r == pytest.approx(-1.0, rel=1e-3)


def test_costs_reduce_result():
    rows = flat_prefix() + [[100, 100.2, 99.9, 100.1], [100.1, 103.5, 100.0, 103.0], [103, 103, 103, 103]]
    b = bars_from(rows)
    clean = run_backtest(b, signal_at(len(rows), 59), RiskParams(), CostModel(spread=0.0))[0]
    costly = run_backtest(b, signal_at(len(rows), 59), RiskParams(), CostModel(spread=0.1, slippage=0.05, commission=0.05))[0]
    assert costly.pnl < clean.pnl
    assert costly.entry == pytest.approx(100.0 + 0.1 + 0.05)


def test_short_trade_uses_ask_for_exit():
    rows = flat_prefix() + [[100, 100.1, 99.8, 99.9], [99.9, 99.9, 96.0, 96.5], [96, 96, 96, 96]]
    b = bars_from(rows, spread=0.0)
    tr = run_backtest(b, signal_at(len(rows), 59, -1), RiskParams(sl_atr=1.5, tp_r=2.0), CostModel(spread=0.2))[0]
    assert tr.direction == -1 and tr.reason == "tp"
    assert tr.r == pytest.approx(2.0, rel=1e-3)


def test_break_even_protects():
    rows = flat_prefix() + [[100, 100.2, 99.9, 100.1], [100.1, 101.8, 100.0, 101.6], [101.6, 101.7, 98.0, 98.2], [98, 98, 98, 98]]
    b = bars_from(rows)
    tr = run_backtest(b, signal_at(len(rows), 59), RiskParams(sl_atr=1.5, tp_r=3.0, break_even_r=1.0), CostModel(spread=0.0))[0]
    assert tr.reason == "be"
    assert tr.r == pytest.approx(0.0, abs=1e-9)


def test_stop_entry_fills_at_trigger():
    n = 64
    rows = flat_prefix() + [[100, 100.5, 99.5, 100], [100, 101.2, 99.9, 101.0], [101, 101.5, 100.8, 101.2], [101.2, 101.3, 101.1, 101.2]]
    b = bars_from(rows)
    long_e = np.zeros(n, dtype=bool)
    long_e[60] = True
    trig = b.high + 0.01
    stop = b.low - 0.01
    sigs = SignalSet(long_e, np.zeros(n, bool), np.zeros(n, bool), np.zeros(n, bool), "stop", trig, None, stop, None, 1)
    trades = run_backtest(b, sigs, RiskParams(sl_atr=1.5, tp_r=2.0), CostModel(spread=0.0))
    assert trades and trades[0].entry == pytest.approx(100.51)


def test_metrics_and_wilson():
    assert wilson_lower(0, 0) == 0.0
    assert 0.3 < wilson_lower(50, 100) < 0.5
    assert wilson_lower(9, 10) < 0.9


def test_real_strategy_evaluation_and_split():
    from app.broker.synthetic import SyntheticMarket

    df = SyntheticMarket().rates("XAUUSD", 14400, 2500, now=1_780_000_000)
    bars = Bars(df, "XAUUSD", "H4", 0.01)
    strat = get_strategy("donchian_turtle")
    ev = evaluate(bars, strat, Candidate(strat.defaults(), {}, {}), CostModel(slippage=0.02, commission=0.07), ApprovalRules(20, 1.0, 0.3))
    ins, oos = split_trades(ev.trades, ev.split_index)
    assert ev.full["trades"] == len(ins) + len(oos)
    assert ev.ins["trades"] == len(ins)
    assert isinstance(ev.approved, bool)
    m = compute_metrics(ev.trades)
    assert m["trades"] == ev.full["trades"]


def test_approval_rules():
    good = {"trades": 60, "profit_factor": 1.5, "expectancy_r": 0.2}
    oos = {"trades": 20, "profit_factor": 1.2, "expectancy_r": 0.1}
    assert approve(good, oos, ApprovalRules(25, 1.1, 0.3)).approved
    bad_oos = {"trades": 20, "profit_factor": 0.8, "expectancy_r": -0.1}
    verdict = approve(good, bad_oos, ApprovalRules(25, 1.1, 0.3))
    assert not verdict.approved and "fora da amostra" in verdict.reasons[0]


def test_evolution_is_validated_out_of_sample():
    import random

    from app.broker.synthetic import SyntheticMarket

    df = SyntheticMarket().rates("EURUSD", 3600, 3000, now=1_780_000_000)
    bars = Bars(df, "EURUSD", "H1", 1e-5)
    strat = get_strategy("keltner_rompimento")
    res = evolve(bars, strat, Candidate(strat.defaults(), {}, {}), CostModel(slippage=2e-5, commission=7e-5), ApprovalRules(20, 1.0, 0.3), "win_rate", random.Random(1), None, 20)
    assert res.tried > 0
    if res.accepted:
        assert res.best.oos["expectancy_r"] > 0
        assert res.best.oos["expectancy_r"] >= 0.7 * res.base.oos["expectancy_r"] or res.base.oos["expectancy_r"] <= 0


def test_simulate_exit_matches_simple_case():
    rows = flat_prefix() + [[100, 100.2, 99.9, 100.1], [100.1, 103.5, 100.0, 103.0]]
    b = bars_from(rows)
    r, reason, _ = simulate_exit(b, 60, 1, 100.0, 98.5, 103.0, RiskParams())
    assert reason == "tp" and r == pytest.approx(2.0)

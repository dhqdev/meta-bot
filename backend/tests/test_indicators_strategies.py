import numpy as np
import pandas as pd
import pytest

from app.broker.synthetic import SyntheticMarket
from app.core import indicators as ind
from app.core.bars import Bars
from app.core.strategies import FILTERS, STRATEGIES, apply_filters


def make_bars(n=1200, symbol="EURUSD", seconds=3600):
    df = SyntheticMarket().rates(symbol, seconds, n, now=1_780_000_000)
    return Bars(df, symbol, "H1", 1e-5)


def test_sma_ema_basics():
    x = np.arange(1, 11, dtype=float)
    assert np.isnan(ind.sma(x, 3)[1])
    assert ind.sma(x, 3)[2] == pytest.approx(2.0)
    assert ind.sma(x, 3)[-1] == pytest.approx(9.0)
    e = ind.ema(x, 3)
    assert np.isnan(e[1]) and np.isfinite(e[2])
    assert e[-1] < x[-1]  # EMA atrasa numa série crescente


def test_rsi_extremes():
    up = np.arange(1, 60, dtype=float)
    assert ind.rsi(up, 14)[-1] == pytest.approx(100.0)
    down = up[::-1].copy()
    assert ind.rsi(down, 14)[-1] == pytest.approx(0.0, abs=1e-9)
    flat = np.ones(40)
    assert ind.rsi(flat, 14)[-1] == pytest.approx(50.0)


def test_atr_constant_range():
    n = 50
    high = np.full(n, 11.0)
    low = np.full(n, 10.0)
    close = np.full(n, 10.5)
    assert ind.atr(high, low, close, 14)[-1] == pytest.approx(1.0)


def test_cross_helpers():
    a = np.array([1, 2, 3, 2, 1], dtype=float)
    assert ind.cross_over(a, 2.5).tolist() == [False, False, True, False, False]
    assert ind.cross_under(a, 2.5).tolist() == [False, False, False, True, False]


@pytest.mark.parametrize("strategy", STRATEGIES, ids=[s.key for s in STRATEGIES])
def test_strategies_have_no_lookahead(strategy):
    """O sinal de uma barra não pode mudar quando chegam barras futuras."""
    full = make_bars(900)
    part = Bars(full.df.iloc[:700], full.symbol, full.timeframe, full.point)
    s_full = strategy.signals(full, strategy.defaults())
    s_part = strategy.signals(part, strategy.defaults())
    for name in ("long_entry", "short_entry", "long_exit", "short_exit"):
        a = getattr(s_full, name)[:700]
        b = getattr(s_part, name)
        assert np.array_equal(a, b), f"{strategy.key}.{name} olha o futuro"


@pytest.mark.parametrize("strategy", STRATEGIES, ids=[s.key for s in STRATEGIES])
def test_strategies_generate_signals(strategy):
    bars = make_bars(3000)
    sigs = strategy.signals(bars, strategy.defaults())
    assert sigs.long_entry.dtype == bool and len(sigs.long_entry) == bars.n
    assert sigs.long_entry.sum() + sigs.short_entry.sum() > 0


def test_param_clean_clamps_and_rounds():
    s = next(x for x in STRATEGIES if x.key == "ifr_reversao")
    p = s.clean({"period": 99, "low": 12, "high": "x", "exit_mid": 0})
    assert p["period"] == 30
    assert p["low"] == 10 or p["low"] == 15
    assert p["high"] == 70
    assert p["exit_mid"] is False


def test_filters_only_remove_entries():
    bars = make_bars(1500)
    s = next(x for x in STRATEGIES if x.key == "cruzamento_medias")
    raw = s.signals(bars, s.defaults())
    filtered = apply_filters(bars, raw, {k: {} for k in FILTERS if k != "horarios"})
    assert filtered.long_entry.sum() <= raw.long_entry.sum()
    assert not (filtered.long_entry & ~raw.long_entry).any()


def test_hours_filter():
    bars = make_bars(1500)
    s = next(x for x in STRATEGIES if x.key == "cruzamento_medias")
    raw = s.signals(bars, s.defaults())
    filtered = apply_filters(bars, raw, {"horarios": {"hours": [13, 14]}})
    hours = (bars.time // 3600) % 24
    assert set(hours[filtered.long_entry | filtered.short_entry]).issubset({13, 14})


def test_bars_cache_reuses_results():
    bars = make_bars(500)
    a = bars.ema(20)
    b = bars.ema(20)
    assert a is b
    assert isinstance(bars.df, pd.DataFrame)

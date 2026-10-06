"""Estratégias de gráfico diário da pesquisa de 06/10/2026: sinais e gestão de saída própria."""

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.agents.strategist import own_exits
from app.core.bars import Bars
from app.core.strategies import get_strategy


def daily_bars(closes: list[float], start: str = "2026-01-01") -> Bars:
    days = pd.bdate_range(start, periods=len(closes), tz="UTC")
    c = np.asarray(closes, dtype=float)
    df = pd.DataFrame({"time": [int(d.timestamp()) for d in days], "open": c, "high": c + 1, "low": c - 1, "close": c, "spread": 0.0})
    return Bars(df, "US500", "D1", 0.01)


def test_rsi2_buys_only_short_dips_in_an_uptrend():
    closes = list(np.linspace(100, 300, 260)) + [290, 280]  # alta longa e duas quedas
    s = get_strategy("rsi2_compra")
    sig = s.signals(daily_bars(closes), s.defaults())
    assert sig.long_entry[-1] and not sig.short_entry.any()
    down = list(np.linspace(300, 100, 262))
    assert not s.signals(daily_bars(down), s.defaults()).long_entry.any()  # abaixo da média de 200: não compra


def test_turn_of_month_signals_the_day_before_the_last_business_day():
    s = get_strategy("virada_mes")
    bars = daily_bars([100.0] * 60, "2026-01-01")
    sig = s.signals(bars, {})
    days = [datetime.fromtimestamp(int(t), timezone.utc).date() for t in bars.time[sig.long_entry]]
    # janeiro/2026 termina numa sexta (30) → sinal na quinta 29; fevereiro termina na sexta 27 → sinal na quinta 26
    assert [d.isoformat() for d in days[:2]] == ["2026-01-29", "2026-02-26"]
    assert not sig.short_entry.any()


def test_daily_strategies_carry_their_own_exits(office):
    strategist = office.agent("strategist")
    mine = strategist.risk_for("rsi2_compra", None, "D1")
    assert mine["break_even_r"] == 0 and mine["trailing_start_r"] == 0 and mine["tp_r"] == 0 and mine["max_bars"] == 10
    team = strategist.risk_for("supertrend", None, "D1")
    assert team["break_even_r"] == office.exit_params()["break_even_r"]
    assert own_exits(get_strategy("virada_mes")) == {"exits": {"break_even_r": 0.0, "trailing_start_r": 0.0}}
    assert own_exits(get_strategy("supertrend")) == {}

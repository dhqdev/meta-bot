import numpy as np
import pytest

from app.broker.synthetic import EPOCH, SyntheticMarket, open_mask
from app.core.assets import symbol_assets, symbol_currencies, symbol_news_score
from app.core.risk import exposure, floor_step, pnl_money, position_size, split_symbol

EURUSD = {"tick_size": 0.00001, "tick_value": 1.0, "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01, "point": 0.00001}


def test_position_size_eurusd():
    # 10.000 de patrimônio, 1% de risco, stop de 20 pips → 0,50 lote
    s = position_size(EURUSD, 10_000, 1.0, 1.1000, 1.0980)
    assert s.ok and s.volume == pytest.approx(0.5)
    assert s.risk_money == pytest.approx(100.0)


def test_position_size_min_lot_rules():
    s = position_size(EURUSD, 100, 0.5, 1.1000, 1.0900)  # risco alvo 0,50; lote mínimo arrisca 10,00
    assert not s.ok and "stop largo" in s.reason
    s2 = position_size(EURUSD, 100, 0.5, 1.1000, 1.0994, min_lot_overrisk=1.5)  # mínimo arrisca 0,60 (até 0,75 aceito)
    assert s2.ok and s2.volume == pytest.approx(0.01)


def test_pnl_and_floor_step():
    assert pnl_money(EURUSD, 1, 1.1, 1.101, 1.0) == pytest.approx(100.0)
    assert pnl_money(EURUSD, -1, 1.1, 1.101, 0.5) == pytest.approx(-50.0)
    assert floor_step(0.1299, 0.01) == pytest.approx(0.12)
    assert floor_step(3.7, 1) == 3


def test_exposure_and_symbols():
    assert split_symbol("EURUSD") == ("EUR", "USD")
    assert split_symbol("US500") == ("US500", None)
    exp = exposure([("EURUSD", 1), ("GBPUSD", 1)])
    assert exp["USD"] == -2 and exp["EUR"] == 1
    assert symbol_assets("WIN$N") == ("IBOV", None)
    assert symbol_assets("WDOV26") == ("USD", "BRL")
    assert symbol_assets("XAUUSD") == ("XAU", "USD")
    assert symbol_currencies("XAUUSD") == {"USD"}
    assert symbol_currencies("WIN$N") == {"BRL", "USD"}
    assert symbol_news_score("EURUSD", {"USD": 0.6}) == pytest.approx(-0.6)
    assert symbol_news_score("NAS100", {"US500": 0.4}) == pytest.approx(0.4)
    assert symbol_news_score("EURUSD", {"JPY": 0.4}) is None


def test_synthetic_is_deterministic_and_respects_sessions():
    now = EPOCH + 400 * 86400 + 5 * 3600
    a = SyntheticMarket().rates("EURUSD", 3600, 500, now=now)
    b = SyntheticMarket().rates("EURUSD", 3600, 500, now=now)
    assert a.equals(b)
    assert (a["high"] >= a[["open", "close"]].max(axis=1) - 1e-12).all()
    dow = ((a["time"] // 86400) + 3) % 7
    assert not (dow == 5).any()  # sábado fechado
    win = SyntheticMarket().rates("WIN$N", 900, 400, now=now)
    minutes = (win["time"] % 86400) // 60
    assert minutes.min() >= 12 * 60 and minutes.max() < 21 * 60 + 25
    assert open_mask("crypto", np.array([EPOCH + 5 * 86400])).all()


def test_synthetic_history_is_stable_over_time():
    m = SyntheticMarket()
    t1 = EPOCH + 300 * 86400
    early = m.rates("GBPUSD", 3600, 200, now=t1)
    later = SyntheticMarket().rates("GBPUSD", 3600, 400, now=t1 + 5 * 86400)
    merged = early.merge(later, on="time", suffixes=("_a", "_b"))
    closed = merged.iloc[:-1]
    assert len(closed) > 150
    assert np.allclose(closed["close_a"], closed["close_b"])


def test_synthetic_tick_has_spread():
    t = SyntheticMarket().tick("EURUSD", now=EPOCH + 100 * 86400 + 12 * 3600)
    assert t["ask"] > t["bid"]

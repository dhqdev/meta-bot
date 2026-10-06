"""Scalper x day trade x posição longa: métricas, variantes da evolução e preferência aprendida."""

import random

from app.core.backtest import BTTrade
from app.core.evaluation import HORIZON_LABELS, Candidate, variants
from app.core.horizons import horizon_of, minutes_from_metrics
from app.core.metrics import compute_metrics
from app.core.strategies import get_strategy
from app.kv import kv_get


def trade(minutes: float, r: float) -> BTTrade:
    return BTTrade(0, 1, 1_000_000, 1_000_000 + int(minutes * 60), 1, 1.0, 1.0 + r, 0.9, None, 0.1, r * 0.1, r, "tp", 1)


def test_horizon_classification():
    assert horizon_of(5) == "scalp" and horizon_of(30) == "scalp"
    assert horizon_of(31) == "day" and horizon_of(8 * 60) == "day"
    assert horizon_of(8 * 60 + 1) == "swing"
    assert minutes_from_metrics({"avg_bars": 4, "trades": 10}, 300) == 20  # perfis antigos: candles × tempo gráfico


def test_metrics_report_holding_time():
    m = compute_metrics([trade(10, 1.0), trade(20, -1.0)])
    assert m["avg_minutes"] == 15.0 and m["horizon"] == "scalp"
    m = compute_metrics([trade(600, 2.0)])
    assert m["horizon"] == "swing"


def test_evolution_always_tries_scalper_and_hold_longer():
    strat = get_strategy("ifr_reversao")
    base = Candidate(strat.defaults(), {}, {"sl_atr": 1.5, "tp_r": 2.0, "max_bars": 48})
    cands = variants(strat, base, random.Random(1))
    by_label = {c.label: c for c in cands}
    scalp = by_label[HORIZON_LABELS[0]]
    longer = by_label[HORIZON_LABELS[1]]
    assert scalp.risk["tp_r"] == 1.0 and scalp.risk["max_bars"] == 6
    assert longer.risk["tp_r"] == 3.0 and longer.risk["max_bars"] == 96


def test_manager_learns_horizon_preference_slowly(office):
    manager = office.agent("manager")
    summary = {
        "horizons": [
            {"key": "scalp", "label": "Scalper", "approved": 3, "bt_oos_expectancy_r": 0.3, "live_trades": 6, "live_r": 3.0},
            {"key": "day", "label": "Day trade", "approved": 2, "bt_oos_expectancy_r": 0.05, "live_trades": 0, "live_r": 0.0},
            {"key": "swing", "label": "Posição longa", "approved": 1, "bt_oos_expectancy_r": -0.2, "live_trades": 4, "live_r": -3.0},
        ]
    }
    notes = manager.learn_horizons(summary)
    w = kv_get("manager.horizon_weights")
    assert w["scalp"] > 1.0 > w["swing"]
    assert 0.75 <= w["swing"] and w["scalp"] <= 1.25
    assert w["scalp"] - 1.0 <= 0.3 * 0.25 + 1e-9  # no máximo 30% do caminho por dia
    assert any("Scalper" in n for n in notes)
    for _ in range(30):
        manager.learn_horizons(summary)
    w = kv_get("manager.horizon_weights")
    assert w["scalp"] <= 1.25 and w["swing"] >= 0.75


def test_horizon_preference_is_relative(office):
    """Se todos os estilos vão bem no backtest, ninguém ganha peso à toa: a preferência é relativa."""
    manager = office.agent("manager")
    rows = [{"key": k, "label": k, "approved": 5, "bt_oos_expectancy_r": 0.1, "live_trades": 0, "live_r": 0.0} for k in ("scalp", "day", "swing")]
    assert manager.learn_horizons({"horizons": rows}) == []
    w = kv_get("manager.horizon_weights")
    assert all(abs(v - 1.0) < 1e-9 for v in w.values())
    before = dict(w)
    rows[0]["bt_oos_expectancy_r"] = 0.25
    notes = manager.learn_horizons({"horizons": rows}, save=False)
    assert notes and kv_get("manager.horizon_weights") == before  # save=False só calcula

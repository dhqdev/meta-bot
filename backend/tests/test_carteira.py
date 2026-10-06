"""Carteira diária (escolha do dono em 06/10/2026): índices e ouro no gráfico diário, plano por regra."""

from app.kv import kv_set
from app.runtime import CARTEIRA_ATIVOS, CARTEIRA_ESTRATEGIAS, TRADING_PAIRS, get_config, market_defaults, reset_cache, update_config
from tests.test_agents import running  # noqa: F401  (fixture)
from tests.test_settings_effects import approved


def test_v5_moves_an_old_forex_config_to_the_daily_portfolio():
    """Ativos, tempo gráfico e estratégias mudam; risco, modo e limites ficam como o dono deixou."""
    kv_set("runtime_config", {"config_version": 4, "watchlist": [p + "m" for p in TRADING_PAIRS], "symbol_suffix": "m",
                              "timeframes": ["M5", "H1"], "risk_per_trade_pct": 0.3, "max_open_positions": 2, "mode": "paper"})
    reset_cache()
    cfg = get_config()
    assert cfg.market_set == "carteira_diaria" and cfg.config_version == 5
    assert cfg.watchlist == [s + "m" for s in CARTEIRA_ATIVOS] and cfg.timeframes == ["D1"]
    assert cfg.enabled_strategies == CARTEIRA_ESTRATEGIAS
    assert (cfg.risk_per_trade_pct, cfg.max_open_positions, cfg.mode) == (0.3, 2, "paper")


def test_switching_the_set_brings_its_timeframes_and_strategies(client_owner):
    r = client_owner.put("/api/settings", json={"market_set": "carteira_diaria"})
    cfg = r.json()["config"]
    assert r.status_code == 200 and cfg["watchlist"] == CARTEIRA_ATIVOS and cfg["timeframes"] == ["D1"]
    r = client_owner.put("/api/settings", json={"symbol_suffix": ".cash"})
    assert r.json()["config"]["watchlist"] == [s + ".cash" for s in CARTEIRA_ATIVOS]
    r = client_owner.put("/api/settings", json={"market_set": "forex"})
    cfg = r.json()["config"]
    assert cfg["watchlist"] == [p + ".cash" for p in TRADING_PAIRS] and cfg["enabled_strategies"] == []
    assert "D1" not in cfg["timeframes"]


def test_daily_setups_ignore_the_hour_filter(office, running):
    """O sinal diário sai no fechamento do dia: hora fraca ou evitada não bloqueia o setup D1."""
    update_config({**market_defaults("carteira_diaria"), "min_hour_quality": 0.99})
    kv_set("team.avoid_hours", {"*": {"hours_utc": list(range(24)), "until": "2999-01-01T00:00:00+00:00"}})
    approved("US500", "D1", "cruzamento_medias")
    cands = office.agent("manager").build_candidates()
    assert cands and not any("hora" in b for c in cands for b in c["blocked"])


def test_daily_portfolio_plan_watches_every_free_setup(office, running):
    """Sem teto de setups no plano: a Rita limita a exposição (uma posição por ativo)."""
    update_config({**market_defaults("carteira_diaria"), "max_active_setups": 2, "max_setups_per_symbol": 1})
    for sym in CARTEIRA_ATIVOS:
        approved(sym, "D1", "cruzamento_medias")
    approved("US500", "D1", "rsi2_compra")
    manager = office.agent("manager")
    plan = manager.deterministic_plan(manager.build_candidates())
    assert len(plan) == len(CARTEIRA_ATIVOS) + 1
    update_config(market_defaults("forex"))
    update_config({"watchlist": CARTEIRA_ATIVOS, "timeframes": ["D1"]})
    assert len(manager.deterministic_plan(manager.build_candidates())) == 2

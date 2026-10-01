"""Horizonte das operações: scalper (minutos), day trade (horas) ou posição longa (dias).

A equipe compara os três pelo tempo médio em posição — no backtest e nas operações reais — e
passa a preferir o que está dando mais resultado (o Gerente aprende isso na daily).
"""

from __future__ import annotations

HORIZONS = {
    "scalp": {"label": "Scalper", "desc": "operações rápidas, de poucos minutos (até 30 min)", "max_minutes": 30},
    "day": {"label": "Day trade", "desc": "operações de algumas horas, encerradas no mesmo dia (até 8 h)", "max_minutes": 8 * 60},
    "swing": {"label": "Posição longa", "desc": "operações que ficam abertas por mais de 8 horas", "max_minutes": None},
}
ORDER = ["scalp", "day", "swing"]

# Variantes de saída que a Estela testa na evolução para comparar os horizontes.
SCALP_RISK = {"sl_atr": 1.0, "tp_r": 1.0, "max_bars": 6}
LONG_TP_R = 3.0


def horizon_of(minutes: float | None) -> str:
    if minutes is None:
        return "day"
    if minutes <= HORIZONS["scalp"]["max_minutes"]:
        return "scalp"
    if minutes <= HORIZONS["day"]["max_minutes"]:
        return "day"
    return "swing"


def label(horizon: str) -> str:
    return HORIZONS.get(horizon, HORIZONS["day"])["label"]


def minutes_from_metrics(metrics: dict | None, timeframe_seconds: int) -> float | None:
    """Tempo médio em posição de um perfil (usa ``avg_minutes``; perfis antigos: candles × tempo gráfico)."""
    metrics = metrics or {}
    if metrics.get("avg_minutes") is not None:
        return float(metrics["avg_minutes"])
    if metrics.get("avg_bars") is not None and metrics.get("trades"):
        return float(metrics["avg_bars"]) * timeframe_seconds / 60
    return None

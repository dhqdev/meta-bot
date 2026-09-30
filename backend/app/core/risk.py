"""Cálculos de risco: tamanho da posição, valor do ponto e exposição por moeda."""

from __future__ import annotations

import math
from dataclasses import dataclass

CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "BRL", "CNY", "MXN", "ZAR", "SEK", "NOK", "TRY", "XAU", "XAG", "BTC", "ETH"}


def value_per_price_unit(spec: dict) -> float:
    """Quanto 1 lote ganha/perde (na moeda da conta) por 1,0 de variação no preço."""
    tick_size = float(spec.get("tick_size") or spec.get("point") or 0)
    tick_value = float(spec.get("tick_value") or 0)
    if tick_size <= 0 or tick_value <= 0:
        return 0.0
    return tick_value / tick_size


def pnl_money(spec: dict, direction: int, entry: float, exit_price: float, volume: float) -> float:
    return (exit_price - entry) * direction * value_per_price_unit(spec) * volume


def floor_step(volume: float, step: float) -> float:
    if step <= 0:
        return volume
    steps = math.floor(volume / step + 1e-9)
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(steps * step, decimals)


@dataclass
class Sizing:
    ok: bool
    volume: float = 0.0
    risk_money: float = 0.0
    reason: str = ""


def position_size(spec: dict, equity: float, risk_pct: float, entry: float, stop: float, min_lot_overrisk: float = 1.5) -> Sizing:
    """Lote para arriscar ``risk_pct``% do patrimônio entre a entrada e o stop."""
    dist = abs(entry - stop)
    vpu = value_per_price_unit(spec)
    if dist <= 0:
        return Sizing(False, reason="stop igual à entrada")
    if vpu <= 0:
        return Sizing(False, reason="a corretora não informou o valor do tick deste ativo")
    target = equity * risk_pct / 100.0
    loss_per_lot = dist * vpu
    vmin = float(spec.get("volume_min") or 0.01)
    vmax = float(spec.get("volume_max") or 100.0)
    step = float(spec.get("volume_step") or vmin)
    volume = min(floor_step(target / loss_per_lot, step), vmax)
    if volume < vmin:
        min_risk = vmin * loss_per_lot
        if min_risk <= target * min_lot_overrisk:
            return Sizing(True, vmin, min_risk, "lote mínimo (risco um pouco acima do alvo)")
        return Sizing(
            False,
            reason=f"stop largo demais para o saldo: o lote mínimo arriscaria {min_risk:.2f} (alvo {target:.2f})",
        )
    return Sizing(True, volume, volume * loss_per_lot, "")


def split_symbol(symbol: str) -> tuple[str, str | None]:
    """EURUSD -> (EUR, USD); XAUUSD -> (XAU, USD); US500 -> (US500, None)."""
    s = "".join(ch for ch in symbol.upper() if ch.isalpha())
    if len(s) >= 6 and s[:3] in CURRENCIES and s[3:6] in CURRENCIES:
        return s[:3], s[3:6]
    return symbol.upper(), None


def exposure(positions: list[tuple[str, int]]) -> dict[str, int]:
    """Exposição líquida por moeda: comprar EURUSD = +EUR e -USD."""
    out: dict[str, int] = {}
    for symbol, direction in positions:
        base, quote = split_symbol(symbol)
        out[base] = out.get(base, 0) + direction
        if quote:
            out[quote] = out.get(quote, 0) - direction
    return out

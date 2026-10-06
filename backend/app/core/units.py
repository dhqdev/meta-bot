"""Números para gente ler: dinheiro sempre com o símbolo da moeda, porcentagem sempre com %, no jeito brasileiro."""

from __future__ import annotations

SYMBOLS = {"BRL": "R$", "USD": "US$", "EUR": "€"}


def num(value: float, decimals: int = 2) -> str:
    return f"{abs(value):,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _sign(value: float, signed: bool) -> str:
    if signed:
        return "+" if value >= 0 else "−"
    return "−" if value < 0 else ""


def money_text(value: float, currency: str = "", signed: bool = False) -> str:
    """Ex.: "R$ 1.234,56", "+US$ 4,16"."""
    symbol = SYMBOLS.get(currency or "", currency or "")
    return f"{_sign(value, signed)}{symbol} {num(value)}".strip()


def pct_text(value: float, signed: bool = False, decimals: int = 2) -> str:
    """`value` já em porcentagem (4.0 → "4,00%")."""
    return f"{_sign(value, signed)}{num(value, decimals)}%"


def limit_text(value: float, unit: str, currency: str = "") -> str:
    """Meta/limite como o dono configurou: "4,00% do patrimônio do início do dia" ou "R$ 4,00 fixos"."""
    if value <= 0:
        return "desligado"
    return f"{pct_text(value)} do patrimônio do início do dia" if unit == "percent" else f"{money_text(value, currency)} fixos"

"""Relação entre ativos da corretora, moedas e códigos usados nas notícias e no calendário."""

from __future__ import annotations

import re

from app.core.risk import CURRENCIES, split_symbol

ASSET_CODES = [
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "CNY", "BRL", "MXN",
    "XAU", "XAG", "OIL", "BTC", "ETH",
    "US500", "US30", "NAS100", "GER40", "UK100", "JP225", "IBOV",
]

_INDEX_ALIASES = [
    (("US500", "SPX", "SP500", "US.500", "USA500", "S&P"), "US500", {"USD"}),
    (("US30", "DJ30", "DOW", "WS30", "USA30"), "US30", {"USD"}),
    (("NAS100", "USTEC", "NDX", "US100", "NQ100", "USATEC"), "NAS100", {"USD"}),
    (("GER40", "DE40", "DAX", "GER30", "DE30"), "GER40", {"EUR"}),
    (("UK100", "FTSE"), "UK100", {"GBP"}),
    (("JP225", "JPN225", "NIKKEI", "N225"), "JP225", {"JPY"}),
]


def _root(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9&.]", "", symbol.upper())


def symbol_assets(symbol: str) -> tuple[str, str | None]:
    """Códigos (base, cotação) do ativo, ex.: EURUSD -> (EUR, USD); WIN$N -> (IBOV, None)."""
    s = _root(symbol)
    if s.startswith(("WIN", "IND", "IBOV", "BOVA")):
        return "IBOV", None
    if s.startswith(("WDO", "DOL")):
        return "USD", "BRL"
    for aliases, code, _ in _INDEX_ALIASES:
        if s.startswith(aliases):
            return code, None
    if s.startswith(("XTI", "WTI", "USOIL", "XBR", "BRENT", "UKOIL", "OIL")):
        return "OIL", "USD"
    base, quote = split_symbol(symbol)
    if quote is not None:
        return base, quote
    return s[:10], None


def symbol_currencies(symbol: str) -> set[str]:
    """Moedas cujo calendário econômico afeta o ativo (para as pausas de notícia)."""
    s = _root(symbol)
    if s.startswith(("WIN", "IND", "WDO", "DOL", "IBOV", "BOVA")):
        return {"BRL", "USD"}
    for aliases, _, ccys in _INDEX_ALIASES:
        if s.startswith(aliases):
            return set(ccys)
    base, quote = symbol_assets(symbol)
    out = {c for c in (base, quote) if c and c in CURRENCIES}
    if base in ("XAU", "XAG", "OIL", "BTC", "ETH"):
        out.add("USD")
    return {c for c in out if c not in ("XAU", "XAG", "BTC", "ETH")} or {"USD"}


def symbol_news_score(symbol: str, asset_scores: dict[str, float]) -> float | None:
    """Sentimento para o ativo: sentimento da base menos o da moeda de cotação."""
    base, quote = symbol_assets(symbol)
    sb = asset_scores.get(base)
    if base in ("US30", "NAS100") and sb is None:
        sb = asset_scores.get("US500")
    sq = asset_scores.get(quote) if quote else None
    if sb is None and sq is None:
        return None
    return max(-1.0, min(1.0, (sb or 0.0) - (sq or 0.0)))

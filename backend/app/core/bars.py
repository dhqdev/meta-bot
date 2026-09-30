"""Candles com cache de indicadores (várias estratégias reaproveitam o mesmo cálculo)."""

from __future__ import annotations

from typing import Any, Callable

import numpy as np
import pandas as pd

from app.core import indicators as ind

COLUMNS = ["time", "open", "high", "low", "close", "volume", "spread"]


class Bars:
    """OHLC em arrays numpy. ``time`` em segundos UTC; ``spread`` em unidades de preço."""

    def __init__(self, df: pd.DataFrame, symbol: str = "", timeframe: str = "", point: float = 0.0):
        missing = [c for c in ("time", "open", "high", "low", "close") if c not in df.columns]
        if missing:
            raise ValueError(f"colunas ausentes: {missing}")
        df = df.reset_index(drop=True)
        self.df = df
        self.symbol = symbol
        self.timeframe = timeframe
        self.point = point
        self.time = df["time"].to_numpy(dtype=np.int64)
        self.open = df["open"].to_numpy(dtype=float)
        self.high = df["high"].to_numpy(dtype=float)
        self.low = df["low"].to_numpy(dtype=float)
        self.close = df["close"].to_numpy(dtype=float)
        self.volume = df["volume"].to_numpy(dtype=float) if "volume" in df else np.zeros(len(df))
        self.spread = df["spread"].to_numpy(dtype=float) if "spread" in df else np.zeros(len(df))
        self.n = len(df)
        self._cache: dict[tuple, Any] = {}

    def __len__(self) -> int:
        return self.n

    def cached(self, key: tuple, fn: Callable[[], Any]) -> Any:
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def tail(self, count: int) -> "Bars":
        return Bars(self.df.iloc[-count:], self.symbol, self.timeframe, self.point)

    # --------------------------------------------------------- indicadores
    def src(self, name: str = "close") -> np.ndarray:
        return getattr(self, name)

    def sma(self, n: int, src: str = "close") -> np.ndarray:
        return self.cached(("sma", int(n), src), lambda: ind.sma(self.src(src), int(n)))

    def ema(self, n: int, src: str = "close") -> np.ndarray:
        return self.cached(("ema", int(n), src), lambda: ind.ema(self.src(src), int(n)))

    def atr(self, n: int = 14) -> np.ndarray:
        return self.cached(("atr", int(n)), lambda: ind.atr(self.high, self.low, self.close, int(n)))

    def rsi(self, n: int = 14) -> np.ndarray:
        return self.cached(("rsi", int(n)), lambda: ind.rsi(self.close, int(n)))

    def macd(self, fast: int, slow: int, signal: int):
        return self.cached(("macd", int(fast), int(slow), int(signal)), lambda: ind.macd(self.close, int(fast), int(slow), int(signal)))

    def bollinger(self, n: int, k: float):
        return self.cached(("bb", int(n), float(k)), lambda: ind.bollinger(self.close, int(n), float(k)))

    def keltner(self, n: int, atr_n: int, mult: float):
        return self.cached(
            ("kc", int(n), int(atr_n), float(mult)),
            lambda: ind.keltner(self.high, self.low, self.close, int(n), int(atr_n), float(mult)),
        )

    def adx(self, n: int = 14):
        return self.cached(("adx", int(n)), lambda: ind.dmi_adx(self.high, self.low, self.close, int(n)))

    def stoch(self, k: int, d: int, smooth: int):
        return self.cached(("stoch", int(k), int(d), int(smooth)), lambda: ind.stochastic(self.high, self.low, self.close, int(k), int(d), int(smooth)))

    def ichimoku(self, t: int, k: int, s: int):
        return self.cached(("ichi", int(t), int(k), int(s)), lambda: ind.ichimoku(self.high, self.low, int(t), int(k), int(s)))

    def supertrend(self, n: int, mult: float):
        return self.cached(("st", int(n), float(mult)), lambda: ind.supertrend(self.high, self.low, self.close, int(n), float(mult)))

    def halftrend(self, amplitude: int):
        return self.cached(("ht", int(amplitude)), lambda: ind.halftrend(self.high, self.low, self.close, int(amplitude)))

    def nrtr(self, n: int, mult: float):
        return self.cached(("nrtr", int(n), float(mult)), lambda: ind.nrtr(self.high, self.low, self.close, int(n), float(mult)))

    def hilo(self, n: int):
        return self.cached(("hilo", int(n)), lambda: ind.hilo(self.high, self.low, self.close, int(n)))

    def highest(self, n: int, src: str = "high") -> np.ndarray:
        return self.cached(("hh", int(n), src), lambda: ind.highest(self.src(src), int(n)))

    def lowest(self, n: int, src: str = "low") -> np.ndarray:
        return self.cached(("ll", int(n), src), lambda: ind.lowest(self.src(src), int(n)))


def bars_from_rows(rows: list[list[float]], symbol: str = "", timeframe: str = "", point: float = 0.0) -> Bars:
    df = pd.DataFrame(rows, columns=COLUMNS[: len(rows[0])] if rows else COLUMNS)
    for col in COLUMNS:
        if col not in df:
            df[col] = 0.0
    return Bars(df, symbol, timeframe, point)

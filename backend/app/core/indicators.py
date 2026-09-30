"""Indicadores técnicos em numpy/pandas.

Todos usam só dados até a barra atual (nada do futuro). Valores sem histórico
suficiente saem como NaN; comparações com NaN dão ``False``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _s(x: np.ndarray) -> pd.Series:
    return pd.Series(np.asarray(x, dtype=float))


def shift(x: np.ndarray, k: int = 1) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    if k == 0:
        return x.copy()
    if k > 0:
        out[k:] = x[:-k]
    else:
        out[:k] = x[-k:]
    return out


def shift_bool(x: np.ndarray, k: int = 1) -> np.ndarray:
    x = np.asarray(x, dtype=bool)
    out = np.zeros_like(x)
    if k > 0:
        out[k:] = x[:-k]
    return out


def cross_over(a: np.ndarray, b: np.ndarray | float) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    b_arr = np.full_like(a, float(b)) if np.isscalar(b) else np.asarray(b, dtype=float)
    with np.errstate(invalid="ignore"):
        return (a > b_arr) & (shift(a) <= shift(b_arr))


def cross_under(a: np.ndarray, b: np.ndarray | float) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    b_arr = np.full_like(a, float(b)) if np.isscalar(b) else np.asarray(b, dtype=float)
    with np.errstate(invalid="ignore"):
        return (a < b_arr) & (shift(a) >= shift(b_arr))


def rising_edge(cond: np.ndarray) -> np.ndarray:
    cond = np.asarray(cond, dtype=bool)
    return cond & ~shift_bool(cond)


def recent_any(cond: np.ndarray, window: int) -> np.ndarray:
    """True se ``cond`` ocorreu em alguma das últimas ``window`` barras (inclui a atual)."""
    cond = np.asarray(cond, dtype=float)
    if window <= 1:
        return cond.astype(bool)
    return _s(cond).rolling(window, min_periods=1).max().to_numpy() > 0


def sma(x: np.ndarray, n: int) -> np.ndarray:
    return _s(x).rolling(int(n), min_periods=int(n)).mean().to_numpy()


def ema(x: np.ndarray, n: int) -> np.ndarray:
    return _s(x).ewm(span=int(n), adjust=False, min_periods=int(n)).mean().to_numpy()


def wilder(x: np.ndarray, n: int) -> np.ndarray:
    return _s(x).ewm(alpha=1.0 / int(n), adjust=False, min_periods=int(n)).mean().to_numpy()


def highest(x: np.ndarray, n: int) -> np.ndarray:
    return _s(x).rolling(int(n), min_periods=int(n)).max().to_numpy()


def lowest(x: np.ndarray, n: int) -> np.ndarray:
    return _s(x).rolling(int(n), min_periods=int(n)).min().to_numpy()


def stdev(x: np.ndarray, n: int) -> np.ndarray:
    return _s(x).rolling(int(n), min_periods=int(n)).std(ddof=0).to_numpy()


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev_close = shift(close)
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    tr[0] = high[0] - low[0]
    return tr


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    return wilder(true_range(high, low, close), n)


def rsi(close: np.ndarray, n: int = 14) -> np.ndarray:
    delta = np.diff(np.asarray(close, dtype=float), prepend=np.nan)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    gain[0] = np.nan
    loss[0] = np.nan
    avg_gain = _s(gain).iloc[1:].ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    avg_loss = _s(loss).iloc[1:].ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    out = np.full(len(close), np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain.to_numpy() / avg_loss.to_numpy()
        values = 100.0 - 100.0 / (1.0 + rs)
    values = np.where((avg_loss.to_numpy() == 0) & (avg_gain.to_numpy() > 0), 100.0, values)
    values = np.where((avg_loss.to_numpy() == 0) & (avg_gain.to_numpy() == 0), 50.0, values)
    out[1:] = values
    return out


def macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    line = ema(close, fast) - ema(close, slow)
    sig = _s(line).ewm(span=int(signal), adjust=False, min_periods=int(signal)).mean().to_numpy()
    return line, sig, line - sig


def bollinger(close: np.ndarray, n: int = 20, k: float = 2.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mid = sma(close, n)
    dev = stdev(close, n)
    return mid + k * dev, mid, mid - k * dev


def keltner(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 20, atr_n: int = 10, mult: float = 2.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mid = ema(close, n)
    a = atr(high, low, close, atr_n)
    return mid + mult * a, mid, mid - mult * a


def dmi_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Devolve (ADX, +DI, -DI) no método de Wilder."""
    up = high - shift(high)
    down = shift(low) - low
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    plus_dm[0] = 0.0
    minus_dm[0] = 0.0
    tr_s = wilder(true_range(high, low, close), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100.0 * wilder(plus_dm, n) / tr_s
        mdi = 100.0 * wilder(minus_dm, n) / tr_s
        dx = 100.0 * np.abs(pdi - mdi) / (pdi + mdi)
    dx = np.where(np.isfinite(dx), dx, 0.0)
    first = int(np.argmax(np.isfinite(pdi))) if np.isfinite(pdi).any() else len(dx)
    adx = np.full(len(dx), np.nan)
    if first < len(dx):
        adx[first:] = wilder(dx[first:], n)
    return adx, pdi, mdi


def stochastic(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, k_n: int = 14, d_n: int = 3, smooth: int = 3
) -> tuple[np.ndarray, np.ndarray]:
    hh = highest(high, k_n)
    ll = lowest(low, k_n)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = 100.0 * (close - ll) / (hh - ll)
    raw = np.where(np.isfinite(raw), raw, 50.0)
    raw[: max(k_n - 1, 0)] = np.nan
    k = sma(raw, smooth) if smooth > 1 else raw
    d = sma(k, d_n)
    return k, d


def ichimoku(
    high: np.ndarray, low: np.ndarray, tenkan_n: int = 9, kijun_n: int = 26, senkou_n: int = 52
) -> dict[str, np.ndarray]:
    """Linhas do Ichimoku. A nuvem na barra i usa spans calculados em i-kijun (deslocamento padrão)."""
    tenkan = (highest(high, tenkan_n) + lowest(low, tenkan_n)) / 2.0
    kijun = (highest(high, kijun_n) + lowest(low, kijun_n)) / 2.0
    span_a_raw = (tenkan + kijun) / 2.0
    span_b_raw = (highest(high, senkou_n) + lowest(low, senkou_n)) / 2.0
    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": shift(span_a_raw, kijun_n),
        "span_b": shift(span_b_raw, kijun_n),
    }


def supertrend(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 10, mult: float = 3.0) -> tuple[np.ndarray, np.ndarray]:
    """Devolve (linha, direção) com direção +1 alta / -1 baixa / 0 sem dados."""
    a = atr(high, low, close, n)
    hl2 = (high + low) / 2.0
    upper_basic = hl2 + mult * a
    lower_basic = hl2 - mult * a
    size = len(close)
    upper = np.full(size, np.nan)
    lower = np.full(size, np.nan)
    line = np.full(size, np.nan)
    direction = np.zeros(size)
    for i in range(size):
        if not np.isfinite(a[i]):
            continue
        if i == 0 or not np.isfinite(upper[i - 1]):
            upper[i], lower[i] = upper_basic[i], lower_basic[i]
            direction[i] = 1.0 if close[i] >= hl2[i] else -1.0
        else:
            upper[i] = upper_basic[i] if (upper_basic[i] < upper[i - 1] or close[i - 1] > upper[i - 1]) else upper[i - 1]
            lower[i] = lower_basic[i] if (lower_basic[i] > lower[i - 1] or close[i - 1] < lower[i - 1]) else lower[i - 1]
            prev = direction[i - 1]
            if prev <= 0 and close[i] > upper[i - 1]:
                direction[i] = 1.0
            elif prev >= 0 and close[i] < lower[i - 1]:
                direction[i] = -1.0
            else:
                direction[i] = prev if prev != 0 else 1.0
        line[i] = lower[i] if direction[i] > 0 else upper[i]
    return line, direction


def halftrend(high: np.ndarray, low: np.ndarray, close: np.ndarray, amplitude: int = 2) -> np.ndarray:
    """HalfTrend (everget/TradingView). Devolve direção +1 alta / -1 baixa / 0 sem dados."""
    size = len(close)
    direction = np.zeros(size)
    if size < amplitude + 2:
        return direction
    high_price = highest(high, amplitude)
    low_price = lowest(low, amplitude)
    high_ma = sma(high, amplitude)
    low_ma = sma(low, amplitude)
    trend = 0
    next_trend = 0
    max_low = low[0]
    min_high = high[0]
    for i in range(1, size):
        if not (np.isfinite(high_price[i]) and np.isfinite(high_ma[i])):
            continue
        if next_trend == 1:
            max_low = max(low_price[i], max_low)
            if high_ma[i] < max_low and close[i] < low[i - 1]:
                trend, next_trend, min_high = 1, 0, high_price[i]
        else:
            min_high = min(high_price[i], min_high)
            if low_ma[i] > min_high and close[i] > high[i - 1]:
                trend, next_trend, max_low = 0, 1, low_price[i]
        direction[i] = 1.0 if trend == 0 else -1.0
    return direction


def nrtr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14, mult: float = 2.5) -> np.ndarray:
    """NRTR (Nick Rypock Trailing Reverse) com distância em ATR. Direção +1/-1/0."""
    a = atr(high, low, close, n)
    size = len(close)
    direction = np.zeros(size)
    trend = 0
    extreme = np.nan
    line = np.nan
    for i in range(size):
        if not np.isfinite(a[i]):
            continue
        dist = mult * a[i]
        if trend == 0:
            trend, extreme = 1, close[i]
            line = extreme - dist
        elif trend == 1:
            if close[i] < line:
                trend, extreme = -1, close[i]
                line = extreme + dist
            else:
                extreme = max(extreme, close[i])
                line = extreme - dist
        else:
            if close[i] > line:
                trend, extreme = 1, close[i]
                line = extreme - dist
            else:
                extreme = min(extreme, close[i])
                line = extreme + dist
        direction[i] = float(trend)
    return direction


def hilo(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 8) -> np.ndarray:
    """HiLo Activator: +1 quando fecha acima da média das máximas anteriores, -1 abaixo da média das mínimas."""
    hi = shift(sma(high, n))
    lo = shift(sma(low, n))
    size = len(close)
    direction = np.zeros(size)
    state = 0.0
    for i in range(size):
        if np.isfinite(hi[i]) and close[i] > hi[i]:
            state = 1.0
        elif np.isfinite(lo[i]) and close[i] < lo[i]:
            state = -1.0
        direction[i] = state
    return direction

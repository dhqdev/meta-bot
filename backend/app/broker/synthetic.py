"""Mercado sintético determinístico (modo demonstração, sem MetaTrader).

Gera preços realistas o bastante para o sistema inteiro funcionar sem
corretora: horários de pregão, volatilidade maior na abertura de Londres e
Nova York, fins de semana fechados, fases de tendência e de lateralidade
(oscilações que revertem à média). É sempre o mesmo histórico para o mesmo
ativo (semente fixa), então os backtests são reproduzíveis.

**Não é mercado real**: serve para testar o sistema e ver os agentes trabalhando.
"""

from __future__ import annotations

import math
import threading
import time as _time
import zlib
from dataclasses import dataclass

import numpy as np
import pandas as pd

EPOCH = 1735689600  # 2025-01-01T00:00:00Z
DAY = 86400
MIN_PER_DAY = 1440
SLOTS = 288  # candles de 5 min por dia
BRL_PER_USD = 5.6  # conversão fixa usada só nos ativos da B3 simulados
OU_TAIL = 600
OU_A = math.exp(-1.0 / 90.0)  # meia-vida ~1 h das oscilações
OU_KERNEL = OU_A ** np.arange(OU_TAIL)


@dataclass(frozen=True)
class Preset:
    price: float
    daily_vol: float  # desvio diário em fração do preço
    digits: int
    spread_points: float
    tick_size: float
    tick_value: float  # em USD por lote
    contract_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    session: str  # fx | index | crypto | b3
    description: str
    currency_base: str
    currency_profit: str

    @property
    def spread_price(self) -> float:
        if self.session == "b3":
            return self.tick_size
        return self.spread_points * 10.0 ** -self.digits


PRESETS: dict[str, Preset] = {
    "EURUSD": Preset(1.0850, 0.0055, 5, 12, 0.00001, 1.0, 100000, 0.01, 0.01, 100, "fx", "Euro x Dólar", "EUR", "USD"),
    "GBPUSD": Preset(1.2700, 0.0060, 5, 15, 0.00001, 1.0, 100000, 0.01, 0.01, 100, "fx", "Libra x Dólar", "GBP", "USD"),
    "USDJPY": Preset(149.50, 0.0060, 3, 14, 0.001, 0.67, 100000, 0.01, 0.01, 100, "fx", "Dólar x Iene", "USD", "JPY"),
    "AUDUSD": Preset(0.6600, 0.0065, 5, 14, 0.00001, 1.0, 100000, 0.01, 0.01, 100, "fx", "Dólar australiano x Dólar", "AUD", "USD"),
    "USDCAD": Preset(1.3600, 0.0045, 5, 18, 0.00001, 0.74, 100000, 0.01, 0.01, 100, "fx", "Dólar x Dólar canadense", "USD", "CAD"),
    "XAUUSD": Preset(2400.0, 0.0110, 2, 25, 0.01, 1.0, 100, 0.01, 0.01, 50, "fx", "Ouro x Dólar", "XAU", "USD"),
    "US500": Preset(5500.0, 0.0095, 1, 5, 0.1, 0.1, 1, 0.1, 0.1, 100, "index", "S&P 500", "US500", "USD"),
    "US30": Preset(39000.0, 0.0090, 1, 20, 0.1, 0.1, 1, 0.1, 0.1, 100, "index", "Dow Jones", "US30", "USD"),
    "NAS100": Preset(19000.0, 0.0120, 1, 15, 0.1, 0.1, 1, 0.1, 0.1, 100, "index", "Nasdaq 100", "NAS100", "USD"),
    "BTCUSD": Preset(65000.0, 0.0300, 2, 1500, 0.01, 0.01, 1, 0.01, 0.01, 10, "crypto", "Bitcoin x Dólar", "BTC", "USD"),
    "ETHUSD": Preset(3200.0, 0.0350, 2, 150, 0.01, 0.01, 1, 0.01, 0.01, 50, "crypto", "Ethereum x Dólar", "ETH", "USD"),
    "WIN$N": Preset(128000.0, 0.0120, 0, 5, 5.0, 1.0 / BRL_PER_USD, 1, 1, 1, 100, "b3", "Mini Índice (contínuo)", "IBOV", "BRL"),
    "WDO$N": Preset(5600.0, 0.0080, 1, 1, 0.5, 5.0 / BRL_PER_USD, 1, 1, 1, 100, "b3", "Mini Dólar (contínuo)", "USD", "BRL"),
}

# Volatilidade relativa por hora UTC em cada tipo de sessão.
_HOURS = {
    "fx": [0.55, 0.55, 0.6, 0.6, 0.6, 0.65, 0.8, 1.2, 1.35, 1.3, 1.2, 1.15, 1.35, 1.6, 1.65, 1.5, 1.3, 1.0, 0.85, 0.75, 0.65, 0.5, 0.45, 0.5],
    "index": [0.5, 0.45, 0.45, 0.45, 0.5, 0.5, 0.55, 0.7, 0.8, 0.8, 0.75, 0.75, 0.85, 1.9, 1.7, 1.4, 1.2, 1.2, 1.3, 1.5, 1.0, 0.4, 0.4, 0.45],
    "crypto": [0.9, 0.85, 0.85, 0.85, 0.85, 0.85, 0.9, 1.0, 1.05, 1.05, 1.05, 1.05, 1.1, 1.3, 1.35, 1.3, 1.2, 1.1, 1.0, 1.0, 0.95, 0.95, 0.9, 0.9],
    "b3": [0.0] * 12 + [1.7, 1.25, 1.05, 0.95, 0.9, 1.15, 1.2, 0.95, 0.8, 0.6, 0.0, 0.0],
}


def open_mask(session: str, times: np.ndarray) -> np.ndarray:
    """True nos instantes (UTC) em que o mercado está aberto."""
    times = np.asarray(times, dtype=np.int64)
    dow = ((times // DAY) + 3) % 7  # 0 = segunda (1970-01-01 foi quinta)
    minute = (times % DAY) // 60
    if session == "crypto":
        return np.ones(times.shape, dtype=bool)
    if session == "b3":
        return (dow <= 4) & (minute >= 12 * 60) & (minute < 21 * 60 + 25)
    is_open = ~(((dow == 4) & (minute >= 21 * 60)) | (dow == 5) | ((dow == 6) & (minute < 22 * 60)))
    if session == "index":
        is_open &= ~((minute >= 21 * 60) & (minute < 22 * 60))
    return is_open


def _generic_preset(symbol: str) -> Preset:
    h = zlib.crc32(symbol.encode())
    price = 10 + (h % 1900) / 10.0
    return Preset(price, 0.015, 2, 10, 0.01, 0.01, 1, 0.01, 0.01, 100, "fx", f"{symbol} (simulado)", symbol[:3], "USD")


def preset_for(symbol: str) -> Preset:
    return PRESETS.get(symbol.upper()) or _generic_preset(symbol.upper())


class SyntheticSymbol:
    """Série de 1 minuto gerada dia a dia desde EPOCH e agregada em candles de 5 minutos."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.p = preset_for(symbol)
        self.seed = zlib.crc32(("meta-bot:" + symbol.upper()).encode())
        self.log_p0 = math.log(self.p.price)
        hour_f = np.array(_HOURS[self.p.session])
        active = hour_f[hour_f > 0]
        # passeio aleatório responde por ~80% da variância diária; o resto é oscilação que reverte
        self.sigma_min = 0.62 * self.p.daily_vol / math.sqrt(60.0 * float((active**2).sum()))
        self.ou_b = 0.16 * self.p.daily_vol * math.sqrt(1 - OU_A**2)
        self.hour_factor = hour_f
        self.lock = threading.Lock()
        self.day_start: list[float] = [self.log_p0]  # nível do passeio aleatório no início de cada dia
        self.days_done = -1
        self._chunks: list[tuple[np.ndarray, ...]] = []
        self._arr: dict[str, np.ndarray] | None = None

    # ------------------------------------------------------------ geração
    def _regime(self, day: int) -> tuple[float, float, float]:
        rng = np.random.default_rng([self.seed, 7, day // 4])
        kind = int(rng.integers(0, 3))  # 0 tendência, 1 lateral, 2 neutro
        scale = (0.5, 0.08, 0.2)[kind]
        drift = rng.normal(0.0, scale) * self.p.daily_vol / (60 * 14)
        phi = (0.15, -0.2, 0.0)[kind]
        volmult = float(np.exp(rng.normal(0.0, 0.2)))
        return drift, phi, volmult

    def _ou(self, day: int) -> np.ndarray:
        cur = np.random.default_rng([self.seed, 11, day]).standard_normal(MIN_PER_DAY)
        prev = np.random.default_rng([self.seed, 11, day - 1]).standard_normal(MIN_PER_DAY)[-OU_TAIL:] if day > 0 else np.zeros(OU_TAIL)
        full = np.convolve(np.concatenate([prev, cur]), OU_KERNEL)[OU_TAIL : OU_TAIL + MIN_PER_DAY]
        return self.ou_b * full

    def _day_path(self, day: int) -> tuple[np.ndarray, np.ndarray]:
        """Devolve (log-preço por minuto, nível do passeio aleatório por minuto)."""
        start = self.day_start[day]
        z = np.random.default_rng([self.seed, day]).standard_normal(MIN_PER_DAY + 1)
        drift, phi, volmult = self._regime(day)
        hours = (np.arange(MIN_PER_DAY) // 60) % 24
        sig = self.sigma_min * volmult * np.maximum(self.hour_factor[hours], 0.35)
        pull = -0.02 * (start - self.log_p0) / MIN_PER_DAY
        rw = start + np.cumsum(drift + pull + sig * (z[1:] + phi * z[:-1]))
        return rw + self._ou(day), rw

    def _build_day(self, day: int) -> None:
        path, rw = self._day_path(day)
        start_price = self.day_start[day] + (self._ou(day - 1)[-1] if day > 0 else 0.0)
        prev = np.concatenate([[start_price], path[:-1]])
        mins = path.reshape(SLOTS, 5)
        opens = prev.reshape(SLOTS, 5)[:, 0]
        closes = mins[:, -1]
        rng = np.random.default_rng([self.seed, day, 3])
        wick = np.abs(rng.standard_normal((SLOTS, 2))) * self.sigma_min * 0.5
        highs = np.maximum(mins.max(axis=1), opens) + wick[:, 0]
        lows = np.minimum(mins.min(axis=1), opens) - wick[:, 1]
        times = EPOCH + day * DAY + np.arange(SLOTS, dtype=np.int64) * 300
        hour_f = self.hour_factor[(np.arange(SLOTS) * 5 // 60) % 24]
        vol = np.round(np.maximum(hour_f, 0.2) * (80 + 40 * np.abs(rng.standard_normal(SLOTS))))
        mask = open_mask(self.p.session, times)
        spread = np.full(SLOTS, self.p.spread_price) * np.where(hour_f < 0.7, 1.6, 1.0)
        self._chunks.append(
            (times[mask], np.exp(opens[mask]), np.exp(highs[mask]), np.exp(lows[mask]), np.exp(closes[mask]), vol[mask], spread[mask])
        )
        if len(self.day_start) == day + 1:
            self.day_start.append(float(rw[-1]))
        self.days_done = day
        self._arr = None

    def _ensure(self, day: int) -> dict[str, np.ndarray]:
        while self.days_done < day:
            self._build_day(self.days_done + 1)
        if self._arr is None:
            cols = list(zip(*self._chunks))
            names = ["time", "open", "high", "low", "close", "volume", "spread"]
            self._arr = {name: np.concatenate(col) for name, col in zip(names, cols)}
        return self._arr

    def _forming(self, arr: dict[str, np.ndarray], end: int, now: float) -> tuple[float, float, float] | None:
        """(close, high, low) do candle em formação cortado no instante ``now``."""
        bar_time = int(arr["time"][end - 1])
        if bar_time + 300 <= now:
            return None
        day = (bar_time - EPOCH) // DAY
        path, _ = self._day_path(day)
        slot = int((bar_time - (EPOCH + day * DAY)) // 300)
        elapsed = max(0, min(4, int((now - bar_time) // 60)))
        seg = path[slot * 5 : slot * 5 + elapsed + 1]
        cur = seg[-1]
        idx = slot * 5 + elapsed + 1
        if idx < len(path):
            cur = seg[-1] + (path[idx] - seg[-1]) * (((now - bar_time) % 60) / 60.0)
        o = float(arr["open"][end - 1])
        price = float(math.exp(cur))
        return price, max(o, price, float(math.exp(seg.max()))), min(o, price, float(math.exp(seg.min())))

    # -------------------------------------------------------------- consulta
    def m5(self, now: float | None = None, count: int | None = None) -> pd.DataFrame:
        now = _time.time() if now is None else now
        with self.lock:
            arr = self._ensure(int((now - EPOCH) // DAY))
            end = int(np.searchsorted(arr["time"], now, side="right"))
            start = 0 if count is None else max(0, end - count)
            df = pd.DataFrame({k: v[start:end].copy() for k, v in arr.items()})
            if end > 0:
                forming = self._forming(arr, end, now)
                if forming is not None:
                    i = len(df) - 1
                    df.loc[i, "close"], df.loc[i, "high"], df.loc[i, "low"] = forming
        return df

    def price(self, now: float | None = None) -> tuple[float, int]:
        now = _time.time() if now is None else now
        with self.lock:
            arr = self._ensure(int((now - EPOCH) // DAY))
            end = int(np.searchsorted(arr["time"], now, side="right"))
            if end == 0:
                return self.p.price, EPOCH
            forming = self._forming(arr, end, now)
            close = forming[0] if forming is not None else float(arr["close"][end - 1])
            return close, int(arr["time"][end - 1])

    def is_open(self, now: float | None = None) -> bool:
        now = _time.time() if now is None else now
        return bool(open_mask(self.p.session, np.array([int(now)]))[0])


def resample(df: pd.DataFrame, seconds: int) -> pd.DataFrame:
    """Agrega candles (tempo de abertura alinhado a múltiplos de ``seconds`` em UTC)."""
    if seconds <= 300 or df.empty:
        return df.reset_index(drop=True)
    keys = (df["time"].to_numpy() // seconds) * seconds
    g = df.groupby(keys, sort=True)
    return pd.DataFrame(
        {
            "time": np.asarray(g["time"].first().index, dtype=np.int64),
            "open": g["open"].first().to_numpy(),
            "high": g["high"].max().to_numpy(),
            "low": g["low"].min().to_numpy(),
            "close": g["close"].last().to_numpy(),
            "volume": g["volume"].sum().to_numpy(),
            "spread": g["spread"].median().to_numpy(),
        }
    )


class SyntheticMarket:
    """Todos os ativos simulados."""

    def __init__(self) -> None:
        self._symbols: dict[str, SyntheticSymbol] = {}
        self._lock = threading.Lock()

    def symbol(self, name: str) -> SyntheticSymbol:
        key = name.upper()
        with self._lock:
            if key not in self._symbols:
                self._symbols[key] = SyntheticSymbol(key)
            return self._symbols[key]

    def rates(self, symbol: str, seconds: int, count: int, now: float | None = None) -> pd.DataFrame:
        s = self.symbol(symbol)
        # candles de 5 min necessários (com folga para mercado fechado)
        need = int(count * max(1, seconds // 300) * (2.6 if s.p.session == "b3" else 1.5)) + 10
        df = resample(s.m5(now, need), seconds)
        return df.iloc[-count:].reset_index(drop=True)

    def tick(self, symbol: str, now: float | None = None) -> dict:
        s = self.symbol(symbol)
        price, bar_time = s.price(now)
        p = s.p
        return {
            "bid": round(price, p.digits),
            "ask": round(price + p.spread_price, p.digits),
            "time": int(now if now is not None else _time.time()),
            "bar_time": bar_time,
            "open": s.is_open(now),
        }

    @staticmethod
    def spec(symbol: str) -> dict:
        p = preset_for(symbol)
        return {
            "name": symbol.upper(),
            "description": p.description,
            "digits": p.digits,
            "point": 10.0 ** -p.digits,
            "tick_size": p.tick_size,
            "tick_value": p.tick_value,
            "contract_size": p.contract_size,
            "volume_min": p.volume_min,
            "volume_max": p.volume_max,
            "volume_step": p.volume_step,
            "stops_level": 0,
            "spread_points": p.spread_points,
            "currency_base": p.currency_base,
            "currency_profit": p.currency_profit,
            "session": p.session,
            "trade_allowed": True,
        }

    @staticmethod
    def list_symbols(q: str = "") -> list[dict]:
        ql = q.lower()
        return [
            {"name": name, "description": p.description, "path": f"Simulado\\{p.session}"}
            for name, p in PRESETS.items()
            if not ql or ql in name.lower() or ql in p.description.lower()
        ]

"""Dados de mercado: MetaTrader 5 (quando conectado), dados reais públicos (Yahoo/Binance) ou o mercado simulado."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import numpy as np
import pandas as pd

from app.broker.mt5 import MT5Error, MT5Unavailable
from app.broker.realdata import RealDataError, RealMarket
from app.broker.synthetic import SyntheticMarket
from app.broker.terminals import TerminalManager
from app.config import get_settings
from app.core.bars import Bars
from app.runtime import TIMEFRAME_SECONDS, get_config

log = logging.getLogger("metabot.market")


class MarketDataError(Exception):
    pass


class MarketService:
    def __init__(self, terminals: TerminalManager, synthetic: SyntheticMarket | None = None, real: RealMarket | None = None):
        self.terminals = terminals
        self.synthetic = synthetic or SyntheticMarket()
        self.real = real or RealMarket()
        self.mt5_ok = False  # atualizado pelo agente de TI
        self.auto_offset_hours: float = 0.0
        self._rates: dict[tuple, tuple[float, pd.DataFrame, float]] = {}
        self._specs: dict[tuple, tuple[float, dict]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ origem
    def source(self) -> str:
        """mt5 | real | synthetic. Automático: MT5 conectado > dados reais públicos > simulado (sem internet)."""
        choice = get_config().data_source
        if choice in ("synthetic", "mt5", "real"):
            return choice
        if self.mt5_ok:
            return "mt5"
        return "real" if get_settings().network_enabled else "synthetic"

    def family(self) -> str:
        """"real" (MT5 ou dados públicos: preços de verdade) ou "simulado"."""
        return "simulado" if self.source() == "synthetic" else "real"

    def data_delay(self, symbol: str) -> int:
        """Atraso dos preços deste ativo em segundos (só alguns dados públicos têm atraso)."""
        return self.real.delay(symbol) if self.source() == "real" else 0

    @property
    def offset_hours(self) -> float:
        cfg = get_config()
        if cfg.server_utc_offset_hours is not None:
            return float(cfg.server_utc_offset_hours)
        return self.auto_offset_hours

    def _client(self):
        client = self.terminals.client()
        if client is None:
            raise MarketDataError("nenhum terminal MT5 configurado (defina o token do bridge)")
        return client

    # ---------------------------------------------------------- consultas
    async def spec(self, symbol: str) -> dict:
        src = self.source()
        key = (src, symbol)
        cached = self._specs.get(key)
        if cached and time.time() - cached[0] < 600:
            return cached[1]
        if src == "synthetic":
            spec = self.synthetic.spec(symbol)
        elif src == "real":
            try:
                spec = await self.real.spec(symbol)
            except RealDataError as exc:
                raise MarketDataError(str(exc)) from exc
        else:
            try:
                info = await self._client().symbol(symbol)
            except (MT5Error, MT5Unavailable) as exc:
                raise MarketDataError(exc.message) from exc
            spec = normalize_spec(info)
        self._specs[key] = (time.time(), spec)
        return spec

    async def rates(self, symbol: str, timeframe: str, count: int = 1000, closed_only: bool = False, max_age: float | None = None) -> Bars:
        src = self.source()
        tf_sec = TIMEFRAME_SECONDS[timeframe]
        key = (src, symbol, timeframe, count)
        ttl = max_age if max_age is not None else min(20.0, tf_sec / 10.0)
        cached = self._rates.get(key)
        now = time.time()
        if cached and now - cached[0] < ttl:
            df, point = cached[1], cached[2]
        else:
            df, point = await self._fetch(src, symbol, timeframe, count)
            self._rates[key] = (now, df, point)
            if len(self._rates) > 400:
                oldest = sorted(self._rates.items(), key=lambda kv: kv[1][0])[:100]
                for k, _ in oldest:
                    self._rates.pop(k, None)
        if closed_only and len(df) and int(df["time"].iloc[-1]) + tf_sec > now:
            df = df.iloc[:-1]
        return Bars(df, symbol, timeframe, point)

    async def _fetch(self, src: str, symbol: str, timeframe: str, count: int) -> tuple[pd.DataFrame, float]:
        if src == "synthetic":
            df = await asyncio.to_thread(self.synthetic.rates, symbol, TIMEFRAME_SECONDS[timeframe], count)
            return df, self.synthetic.spec(symbol)["point"]
        if src == "real":
            try:
                df = await self.real.rates(symbol, TIMEFRAME_SECONDS[timeframe], count)
            except RealDataError as exc:
                raise MarketDataError(str(exc)) from exc
            return df, (await self.spec(symbol))["point"]
        spec = await self.spec(symbol)
        try:
            data = await self._client().rates(symbol, timeframe, count)
        except (MT5Error, MT5Unavailable) as exc:
            raise MarketDataError(exc.message) from exc
        return rows_to_frame(data.get("rows", []), spec["point"], self.offset_hours), spec["point"]

    async def tick(self, symbol: str) -> dict:
        src = self.source()
        if src == "synthetic":
            return await asyncio.to_thread(self.synthetic.tick, symbol)
        if src == "real":
            try:
                return await self.real.tick(symbol)
            except RealDataError as exc:
                raise MarketDataError(str(exc)) from exc
        try:
            t = await self._client().tick(symbol)
        except (MT5Error, MT5Unavailable) as exc:
            raise MarketDataError(exc.message) from exc
        server_time = int(t.get("time") or 0)
        return {
            "bid": float(t.get("bid") or 0),
            "ask": float(t.get("ask") or 0),
            "time": int(server_time - self.offset_hours * 3600) if server_time else int(time.time()),
            "open": True,
        }

    async def symbols(self, q: str = "") -> list[dict]:
        src = self.source()
        if src == "synthetic":
            return self.synthetic.list_symbols(q)
        if src == "real":
            return self.real.list_symbols(q)
        try:
            return await self._client().symbols(q)
        except (MT5Error, MT5Unavailable) as exc:
            raise MarketDataError(exc.message) from exc

    def clear_cache(self) -> None:
        self._rates.clear()
        self._specs.clear()
        self.real.clear()


def rows_to_frame(rows: list[list[Any]], point: float, offset_hours: float) -> pd.DataFrame:
    """Linhas do bridge (hora do servidor, spread em pontos) -> DataFrame em UTC e spread em preço."""
    if not rows:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume", "spread"])
    arr = np.array([[float(x) if x is not None else 0.0 for x in r[:7]] for r in rows])
    return pd.DataFrame(
        {
            "time": (arr[:, 0] - offset_hours * 3600).astype(np.int64),
            "open": arr[:, 1],
            "high": arr[:, 2],
            "low": arr[:, 3],
            "close": arr[:, 4],
            "volume": arr[:, 5],
            "spread": arr[:, 6] * point,
        }
    )


def normalize_spec(info: dict) -> dict:
    point = float(info.get("point") or 10.0 ** -int(info.get("digits") or 5))
    return {
        "name": info.get("name"),
        "description": info.get("description", ""),
        "digits": int(info.get("digits") or 5),
        "point": point,
        "tick_size": float(info.get("trade_tick_size") or point),
        "tick_value": float(info.get("trade_tick_value_loss") or info.get("trade_tick_value") or 0),
        "contract_size": float(info.get("trade_contract_size") or 1),
        "volume_min": float(info.get("volume_min") or 0.01),
        "volume_max": float(info.get("volume_max") or 100),
        "volume_step": float(info.get("volume_step") or 0.01),
        "stops_level": int(info.get("trade_stops_level") or 0),
        "spread_points": float(info.get("spread") or 0),
        "currency_base": info.get("currency_base", ""),
        "currency_profit": info.get("currency_profit", ""),
        "session": "",
        "trade_allowed": int(info.get("trade_mode", 4) or 0) != 0,
    }

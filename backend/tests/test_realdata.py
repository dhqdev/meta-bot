"""Preços reais sem corretora (Yahoo Finance e Binance), com respostas no formato das APIs de verdade."""

import asyncio
import math
import time
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from app.broker.market import MarketService
from app.broker.realdata import RealDataError, RealMarket, parse_yahoo, route_for
from app.db import session_scope
from app.kv import kv_get
from app.models import AgentMessage, Lesson, StrategyProfile, Trade
from app.runtime import update_config

INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "60m": 3600, "1d": 86400}
RANGE_DAYS = {"1d": 1, "5d": 5, "7d": 7, "60d": 60, "730d": 730, "3mo": 90, "10y": 3650}
BASE_PRICE = {"EURUSD=X": 1.10, "USDJPY=X": 150.0, "JPY=X": 150.0, "GC=F": 2400.0, "^GSPC": 5600.0, "BRL=X": 5.4, "BTC-USD": 64000.0, "^BVSP": 130000.0}


def yahoo_payload(ticker: str, interval: str, range_: str, now: float, live_point: bool = True, market_time: float | None = None) -> dict:
    sec = INTERVAL_SECONDS[interval]
    n = min(int(RANGE_DAYS[range_] * 86400 / sec), 20000)
    end = int(now // sec) * sec
    ts = [end - (n - 1 - i) * sec for i in range(n)]
    base = BASE_PRICE.get(ticker, 100.0)
    close = [base * (1 + 0.002 * math.sin(t / 7200.0)) for t in ts]
    opens = [close[i - 1] if i else close[0] for i in range(n)]
    high = [max(o, c) * 1.0004 for o, c in zip(opens, close)]
    low = [min(o, c) * 0.9996 for o, c in zip(opens, close)]
    if n > 3:
        close[2] = None  # o Yahoo devolve buracos (null) de vez em quando
    if live_point:
        ts.append(int(now))  # ponto do preço ao vivo, fora do alinhamento
        opens.append(close[-1]), high.append(close[-1]), low.append(close[-1]), close.append(base * 1.001)
    return {
        "chart": {
            "result": [
                {
                    "meta": {"symbol": ticker, "currency": "USD", "regularMarketPrice": base * 1.001, "regularMarketTime": int(market_time or now), "gmtoffset": 0},
                    "timestamp": ts,
                    "indicators": {"quote": [{"open": opens, "high": high, "low": low, "close": close, "volume": [0] * len(ts)}]},
                }
            ],
            "error": None,
        }
    }


def binance_klines(pair: str, interval: str, limit: int, end_ms: int | None, now: float) -> list:
    sec = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}[interval]
    last = int((end_ms / 1000 if end_ms else now) // sec) * sec
    first_available = int(now) - 5000 * sec  # a corretora "tem" 5000 candles
    rows = []
    for i in range(limit):
        t = last - (limit - 1 - i) * sec
        if t < first_available:
            continue
        p = 64000 + 50 * math.sin(t / 3600)
        rows.append([t * 1000, f"{p:.2f}", f"{p + 20:.2f}", f"{p - 20:.2f}", f"{p + 5:.2f}", "12.5", t * 1000 + sec * 1000 - 1, "0", 10, "0", "0", "0"])
    return rows


class FakeApis:
    """Yahoo + Binance falsos; guarda as chamadas para conferir o cache e os períodos pedidos."""

    def __init__(self, now: float | None = None, binance_status: int = 200, yahoo_status: int = 200, market_time: float | None = None):
        self.now = now or time.time()
        self.calls: list[tuple[str, str, dict]] = []
        self.binance_status = binance_status
        self.yahoo_status = yahoo_status
        self.market_time = market_time

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        host, path = request.url.host, request.url.path
        self.calls.append((host, path, params))
        if "binance" in host:
            if self.binance_status != 200:
                return httpx.Response(self.binance_status, json={"code": 0, "msg": "Service unavailable from a restricted location"})
            if path.endswith("/klines"):
                end = int(params["endTime"]) if "endTime" in params else None
                return httpx.Response(200, json=binance_klines(params["symbol"], params["interval"], int(params["limit"]), end, self.now))
            if path.endswith("/bookTicker"):
                return httpx.Response(200, json={"symbol": params["symbol"], "bidPrice": "64000.00", "bidQty": "1", "askPrice": "64000.01", "askQty": "1"})
        if "kraken" in host:
            pair = params["pair"]
            key = {"EURUSD": "ZEURZUSD", "USDJPY": "ZUSDZJPY"}.get(pair, pair)
            if path.endswith("/OHLC"):
                sec = int(params["interval"]) * 60
                end = int(self.now // sec) * sec
                rows = [[end - (719 - i) * sec, "1.1000", "1.1010", "1.0990", f"{1.1 + 0.001 * math.sin(i / 20):.5f}", "1.1", "10.5", 3] for i in range(720)]
                return httpx.Response(200, json={"error": [], "result": {key: rows, "last": end}})
            if path.endswith("/Ticker"):
                base = 150.0 if pair == "USDJPY" else 1.1
                return httpx.Response(200, json={"error": [], "result": {key: {"a": [f"{base * 1.0001:.5f}", "1", "1.000"], "b": [f"{base:.5f}", "1", "1.000"], "c": [f"{base:.5f}", "0.1"]}}})
        if "yahoo" in host:
            if self.yahoo_status != 200:
                return httpx.Response(self.yahoo_status, text="Too Many Requests")
            ticker = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=yahoo_payload(ticker, params["interval"], params["range"], self.now, market_time=self.market_time))
        return httpx.Response(404)

    def count(self, fragment: str, **params) -> int:
        return sum(1 for host, path, p in self.calls if fragment in host + path and all(p.get(k) == v for k, v in params.items()))


def run(coro):
    return asyncio.run(coro)


def test_routes_for_broker_symbols():
    assert route_for("EURUSD").ticker == "EURUSD=X"
    assert route_for("EURUSDm").ticker == "EURUSD=X"  # sufixo da corretora
    gold = route_for("XAUUSD.a")
    assert gold.ticker == "PAXGUSDT" and gold.backups == (("yahoo", "GC=F"),)  # ouro em tempo real; o futuro é a reserva
    assert route_for("EURUSD").backups == (("kraken", "EURUSD"),)  # forex: a Kraken é a reserva em tempo real
    assert route_for("WDO$N").backups == (("binance", "USDTBRL"),)
    assert route_for("US500").ticker == "^GSPC" and route_for("NAS100").ticker == "^NDX"
    assert route_for("EURUSD").delay == 0 and route_for("WIN$N").delay == 900  # o Ibovespa chega com 15 min de atraso
    btc = route_for("BTCUSD")
    assert btc.provider == "binance" and btc.ticker == "BTCUSDT" and btc.backups == (("yahoo", "BTC-USD"),)
    assert route_for("WIN$N").ticker == "^BVSP"
    wdo = route_for("WDO$N")
    assert wdo.ticker == "BRL=X" and wdo.scale == 1000.0
    assert route_for("PETR4").ticker == "PETR4.SA"


def test_parse_yahoo_aligns_live_point_and_drops_gaps():
    now = 1_760_000_000 + 137  # meio de um candle de 5 min
    df, meta = parse_yahoo(yahoo_payload("EURUSD=X", "5m", "1d", now), "5m")
    assert meta["regularMarketPrice"] > 0
    assert df["time"].is_unique and (df["time"] % 300 == 0).all()
    assert df["close"].notna().all()
    assert df["close"].iloc[-1] == pytest.approx(1.10 * 1.001)  # o preço ao vivo entra no último candle
    assert (df["high"] >= df[["open", "close"]].max(axis=1)).all()


def test_yahoo_history_is_downloaded_once_then_only_the_new_part():
    api = FakeApis()
    market = RealMarket(transport=httpx.MockTransport(api))

    async def go():
        df = await market.rates("EURUSD", 300, 3000)
        assert len(df) == 3000 and df["time"].is_monotonic_increasing
        assert df["spread"].iloc[-1] == pytest.approx(12 * 1e-5)  # spread típico de corretora
        assert api.count("yahoo", interval="5m", range="60d") == 1
        await market.rates("EURUSD", 300, 500)  # ainda fresco: sem nova consulta
        assert api.count("yahoo", interval="5m") == 1
        key = ("yahoo", "EURUSD=X", 300)
        market._frames[key] = (time.time() - 3600, market._frames[key][1])  # envelhece o cache
        df2 = await market.rates("EURUSD", 300, 3000)
        assert api.count("yahoo", interval="5m", range="1d") == 1  # só o trecho novo
        assert len(df2) == 3000 and df2["time"].is_unique

    run(go())


def test_h4_comes_from_hourly_candles_and_daily_works():
    api = FakeApis()
    market = RealMarket(transport=httpx.MockTransport(api))

    async def go():
        h4 = await market.rates("US500", 14400, 500)
        assert len(h4) == 500 and (h4["time"] % 14400 == 0).all()
        assert api.count("yahoo", interval="60m", range="730d") == 1
        d1 = await market.rates("US500", 86400, 300)
        assert len(d1) == 300 and (d1["time"] % 86400 == 0).all()

    run(go())


def test_binance_pages_back_for_long_history_and_gives_the_book():
    api = FakeApis()
    market = RealMarket(transport=httpx.MockTransport(api))

    async def go():
        df = await market.rates("BTCUSD", 300, 2500)
        assert len(df) == 2500 and df["time"].is_unique and (df["time"].diff().dropna() == 300).all()
        assert api.count("binance", interval="5m") == 3  # 1000 + 1000 + 1000
        tick = await market.tick("BTCUSD")
        assert tick["open"] is True and tick["ask"] - tick["bid"] == pytest.approx(15.0)  # spread de corretora, não o da Binance
        assert "Binance" in tick["source"]

    run(go())


def test_crypto_falls_back_to_yahoo_when_binance_is_blocked():
    api = FakeApis(binance_status=451)
    market = RealMarket(transport=httpx.MockTransport(api))

    async def go():
        df = await market.rates("BTCUSD", 3600, 200)
        assert len(df) == 200 and api.count("chart/BTC-USD") >= 1
        tick = await market.tick("BTCUSD")
        assert tick["bid"] == pytest.approx(64000 * 1.001 - 7.5, abs=0.02)
        gold = await market.rates("XAUUSD", 14400, 300)  # reserva do ouro no Yahoo, sem 4 horas: vem do 1 hora
        assert len(gold) == 300 and api.count("chart/GC=F", interval="60m") == 1

    run(go())


def test_stale_quote_means_market_closed_and_jpy_tick_value_in_dollars():
    api = FakeApis(market_time=time.time() - 3 * 3600)  # último preço há 3 horas (feriado/fim de semana)
    market = RealMarket(transport=httpx.MockTransport(api))

    async def go():
        tick = await market.tick("EURUSD")
        assert tick["open"] is False
        spec = await market.spec("USDJPY")
        assert spec["tick_value"] == pytest.approx(0.001 * 100000 / (150.0 * 1.001), rel=1e-4)
        wdo = await market.tick("WDO$N")
        assert (wdo["bid"] + wdo["ask"]) / 2 == pytest.approx(5.4 * 1.001 * 1000, abs=0.6)

    run(go())


def test_rate_limit_backs_off_and_keeps_last_history():
    api = FakeApis()
    market = RealMarket(transport=httpx.MockTransport(api))

    async def go():
        await market.rates("EURUSD", 900, 300)
        api.yahoo_status = 429
        key = ("yahoo", "EURUSD=X", 900)
        market._frames[key] = (time.time() - 3600, market._frames[key][1])
        df = await market.rates("EURUSD", 900, 300)  # usa o último histórico
        assert len(df) == 300
        with pytest.raises(RealDataError):
            await market.rates("US500", 900, 300)  # nunca baixado, sem reserva e o Yahoo bloqueou

    run(go())


def test_forex_falls_back_to_kraken_when_yahoo_refuses():
    api = FakeApis(yahoo_status=429)
    market = RealMarket(transport=httpx.MockTransport(api))

    async def go():
        df = await market.rates("EURUSD", 300, 500)
        assert len(df) == 500 and api.count("kraken", pair="EURUSD", interval="5") == 1
        tick = await market.tick("EURUSD")
        assert "Kraken" in tick["source"] and tick["ask"] - tick["bid"] == pytest.approx(12e-5, abs=1.1e-5)  # spread de corretora, não o da Kraken
        spec = await market.spec("USDJPY")  # valor do tick em dólar pela cotação da Kraken
        assert spec["tick_value"] == pytest.approx(100 / (150 * 1.00005), rel=1e-3)

    run(go())


def test_market_service_picks_real_prices_without_broker(monkeypatch, office):
    svc = office.market
    assert svc.source() == "synthetic"  # testes rodam sem internet
    monkeypatch.setattr("app.broker.market.get_settings", lambda: type("S", (), {"network_enabled": True})())
    assert svc.source() == "real"
    svc.mt5_ok = True
    assert svc.source() == "mt5"
    svc.mt5_ok = False
    update_config({"data_source": "synthetic"})
    assert svc.source() == "synthetic"


def test_switching_to_real_prices_restarts_learning(office):
    api = FakeApis()
    office.market.real = RealMarket(transport=httpx.MockTransport(api))
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        s.add(StrategyProfile(symbol="EURUSD", timeframe="H1", strategy="supertrend", status="aprovada", score=0.8, metrics={"trades": 50}, data_source="synthetic", tested_at=now))
        s.add(Trade(mode="paper", symbol="EURUSD", direction="buy", volume=0.1, entry_price=1.1, entry_time=now, status="open", strategy="supertrend", timeframe="H1", risk_money=10))
        s.add(Lesson(agent="manager", text="lição aprendida no mercado simulado"))
    assert office.sync_data_family() is False  # ainda no simulado
    update_config({"data_source": "real"})
    assert office.sync_data_family() is True
    with session_scope() as s:
        prof = s.scalar(select(StrategyProfile))
        assert prof.status == "nova" and prof.tested_at is None and prof.metrics == {}
        tr = s.scalar(select(Trade))
        assert tr.mode == "paper-sim" and tr.status == "closed" and tr.pnl == 0.0 and tr.exit_reason == "troca de dados"
        assert s.scalar(select(Lesson)).active is False
        assert any("preços reais" in m.text for m in s.scalars(select(AgentMessage).where(AgentMessage.sender == "infra")))
    assert kv_get("market.family") == "real"
    assert office.sync_data_family() is False  # só uma vez
    office._family = None
    update_config({"data_source": "mt5"})  # MT5 também é preço real: nada a reiniciar
    assert office.sync_data_family() is False


def test_real_prices_flow_through_the_office(office):
    """Com preços reais: a Rita calcula o lote e a conta simulada marca a posição a mercado."""
    api = FakeApis()
    office.market.real = RealMarket(transport=httpx.MockTransport(api))
    update_config({"data_source": "real", "watchlist": ["BTCUSD"], "timeframes": ["H1"]})

    async def go():
        assert office.market.source() == "real"
        tick = await office.market.tick("BTCUSD")
        verdict = await office.agent("risk").evaluate("BTCUSD", "buy", tick["ask"], tick["ask"] - 1500)
        assert verdict.ok and verdict.volume > 0
        bars = await office.market.rates("BTCUSD", "H1", 400, closed_only=True)
        assert bars.n >= 300
        res = await office.paper.open("BTCUSD", "buy", verdict.volume, tick["ask"] - 1500, None)
        assert res.ok and res.price >= tick["ask"]
        await office.agent("infra").check_feed()
        assert office.agent("infra").feed["ok"] is True

    run(go())

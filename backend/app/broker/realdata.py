"""Dados reais e gratuitos do mercado, sem corretora (para a conta simulada).

- **Cripto e ouro** (BTC, ETH, SOL..., ouro pelo PAX Gold): API pública da Binance, sem chave.
- **Forex, índices, petróleo e B3**: gráficos públicos do Yahoo Finance (acessados como um
  navegador, porque o Yahoo recusa outros clientes). O mini índice segue o Ibovespa e o mini
  dólar segue o dólar comercial.
- **Reservas**: forex pela Kraken (tempo real, histórico curto que cresce enquanto roda),
  mini dólar pelo USDT/BRL da Binance, cripto e ouro pelo Yahoo.

Os preços são de verdade; as ordens continuam simuladas, com o spread típico de uma
corretora (o da tabela de cada ativo), slippage e comissão da conta simulada. Alguns
tickers do Yahoo chegam com alguns minutos de atraso (veja ``Route.delay``).
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass, replace

import httpx
import numpy as np
import pandas as pd

from app.broker.synthetic import PRESETS, Preset, open_mask, resample

try:  # cliente com "cara" de navegador (TLS igual ao do Chrome): o Yahoo recusa os outros
    from curl_cffi.requests import AsyncSession as BrowserSession
except ImportError:  # pragma: no cover
    BrowserSession = None

log = logging.getLogger("metabot.realdata")

YAHOO_HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
BINANCE_HOSTS = ("https://data-api.binance.vision", "https://api.binance.com")
KRAKEN_HOSTS = ("https://api.kraken.com",)
YAHOO_COOKIE_URL = "https://fc.yahoo.com"
YAHOO_CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
COLUMNS = ["time", "open", "high", "low", "close", "volume", "spread"]

CRYPTO = ("BTC", "ETH", "SOL", "XRP", "LTC", "BNB", "ADA", "DOGE", "DOT", "AVAX", "LINK", "TRX", "BCH", "XLM", "ATOM", "UNI", "NEAR", "TON", "SHIB", "POL")
FIAT = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "BRL", "MXN", "ZAR", "SEK", "NOK", "TRY", "CNH", "SGD", "HKD", "PLN", "DKK"}
# Pares de moedas negociados na Kraken (reserva em tempo real para o forex)
KRAKEN_FX = {"EURUSD", "GBPUSD", "USDJPY", "USDCAD", "USDCHF", "AUDUSD", "EURGBP", "EURJPY", "EURCHF", "EURCAD", "AUDJPY"}
KRAKEN_MINUTES = {60: 1, 300: 5, 900: 15, 1800: 30, 3600: 60, 14400: 240, 86400: 1440}

# (prefixos do ativo na corretora, ticker no Yahoo, sessão, descrição, atraso do Yahoo em segundos)
# Índices americanos: o índice à vista é em tempo real (só no pregão de Nova York); os futuros têm 10 min de atraso.
YAHOO_ALIASES: list[tuple[tuple[str, ...], str, str, str, int]] = [
    (("US500", "SPX", "SP500", "US.500", "USA500", "S&P"), "^GSPC", "index", "S&P 500", 0),
    (("US30", "DJ30", "DOW", "WS30", "USA30"), "^DJI", "index", "Dow Jones", 0),
    (("NAS100", "USTEC", "NDX", "US100", "NQ100", "USATEC"), "^NDX", "index", "Nasdaq 100", 0),
    (("GER40", "DE40", "DAX", "GER30", "DE30"), "^GDAXI", "index", "DAX (Alemanha)", 900),
    (("UK100", "FTSE"), "^FTSE", "index", "FTSE 100 (Reino Unido)", 900),
    (("JP225", "JPN225", "NIKKEI", "N225"), "^N225", "index", "Nikkei 225 (Japão)", 1200),
    (("XAG", "SILVER"), "SI=F", "fx", "Prata (futuro)", 600),
    (("XTI", "WTI", "USOIL", "OIL"), "CL=F", "index", "Petróleo WTI (futuro)", 600),
    (("XBR", "BRENT", "UKOIL"), "BZ=F", "index", "Petróleo Brent (futuro)", 600),
]

# Contratos que o mercado simulado não tem (padrões típicos de CFD nas corretoras)
EXTRA_PRESETS: dict[str, Preset] = {
    "XAGUSD": Preset(30.0, 0.015, 3, 30, 0.001, 5.0, 5000, 0.01, 0.01, 50, "fx", "Prata x Dólar", "XAG", "USD"),
    "USOIL": Preset(75.0, 0.02, 2, 4, 0.01, 10.0, 1000, 0.01, 0.01, 50, "index", "Petróleo WTI", "OIL", "USD"),
    "UKOIL": Preset(80.0, 0.02, 2, 4, 0.01, 10.0, 1000, 0.01, 0.01, 50, "index", "Petróleo Brent", "OIL", "USD"),
    "GER40": Preset(18000.0, 0.01, 1, 15, 0.1, 0.1, 1, 0.1, 0.1, 100, "index", "DAX (Alemanha)", "GER40", "EUR"),
    "UK100": Preset(8000.0, 0.009, 1, 10, 0.1, 0.1, 1, 0.1, 0.1, 100, "index", "FTSE 100", "UK100", "GBP"),
    "JP225": Preset(38000.0, 0.012, 0, 10, 1.0, 1.0, 1, 0.1, 0.1, 100, "index", "Nikkei 225", "JP225", "JPY"),
}
CANONICAL_BY_TICKER = {
    "^GSPC": "US500", "^DJI": "US30", "^NDX": "NAS100", "^GDAXI": "GER40", "^FTSE": "UK100", "^N225": "JP225",
    "PAXGUSDT": "XAUUSD", "GC=F": "XAUUSD", "SI=F": "XAGUSD", "CL=F": "USOIL", "BZ=F": "UKOIL", "^BVSP": "WIN$N", "BRL=X": "WDO$N", "USDTBRL": "WDO$N",
}

# Intervalos: segundos -> (Yahoo, período completo, período da atualização, Binance)
INTERVALS = {
    60: ("1m", "7d", "1d", "1m"),
    300: ("5m", "60d", "1d", "5m"),
    900: ("15m", "60d", "5d", "15m"),
    1800: ("30m", "60d", "5d", "30m"),
    3600: ("60m", "730d", "5d", "1h"),
    86400: ("1d", "10y", "3mo", "1d"),
}
REFRESH_SECONDS = {60: 15, 300: 20, 900: 30, 1800: 45, 3600: 60, 14400: 60, 86400: 600}
TICK_TTL = {"binance": 3.0, "kraken": 5.0, "yahoo": 15.0}
LABELS = {"binance": "Binance", "kraken": "Kraken", "yahoo": "Yahoo Finance"}
MAX_ROWS = 25000
STALE_SECONDS = {"crypto": 15 * 60, "fx": 45 * 60, "index": 45 * 60, "b3": 45 * 60}

# Sugestões para a busca de ativos (Config. → Ativos)
SUGGESTIONS = [
    ("EURUSD", "Euro x Dólar"), ("GBPUSD", "Libra x Dólar"), ("USDJPY", "Dólar x Iene"), ("AUDUSD", "Dólar australiano x Dólar"),
    ("USDCAD", "Dólar x Dólar canadense"), ("USDCHF", "Dólar x Franco suíço"), ("NZDUSD", "Dólar neozelandês x Dólar"),
    ("EURJPY", "Euro x Iene"), ("GBPJPY", "Libra x Iene"), ("EURGBP", "Euro x Libra"), ("USDBRL", "Dólar x Real"),
    ("XAUUSD", "Ouro"), ("XAGUSD", "Prata"), ("USOIL", "Petróleo WTI"), ("UKOIL", "Petróleo Brent"),
    ("US500", "S&P 500"), ("US30", "Dow Jones"), ("NAS100", "Nasdaq 100"), ("GER40", "DAX"), ("UK100", "FTSE 100"), ("JP225", "Nikkei 225"),
    ("BTCUSD", "Bitcoin"), ("ETHUSD", "Ethereum"), ("SOLUSD", "Solana"), ("XRPUSD", "XRP"), ("BNBUSD", "BNB"), ("DOGEUSD", "Dogecoin"),
    ("WIN$N", "Mini Índice (segue o Ibovespa)"), ("WDO$N", "Mini Dólar (segue o dólar comercial)"),
    ("PETR4", "Petrobras PN"), ("VALE3", "Vale ON"), ("ITUB4", "Itaú PN"), ("BBDC4", "Bradesco PN"), ("BOVA11", "ETF Ibovespa"),
]


class RealDataError(Exception):
    pass


@dataclass(frozen=True)
class Route:
    provider: str  # binance | yahoo | kraken
    ticker: str
    session: str  # fx | index | crypto | b3
    kind: str  # fx | crypto | metal | index | oil | b3 | stock | other
    scale: float = 1.0
    backups: tuple[tuple[str, str], ...] = ()  # (fonte, ticker) usadas se a principal falhar
    description: str = ""
    delay: int = 0  # atraso conhecido da fonte (segundos)

    @property
    def label(self) -> str:
        return f"{LABELS.get(self.provider, self.provider)} ({self.ticker})"

    def chain(self) -> list[Route]:
        """A fonte principal e as reservas, nessa ordem."""
        out = [self]
        for provider, ticker in self.backups:
            delay = 600 if ticker.endswith("=F") else self.delay if provider == "yahoo" else 0
            out.append(replace(self, provider=provider, ticker=ticker, backups=(), delay=delay))
        return out


def _root(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9$&.]", "", symbol.upper())


def route_for(symbol: str) -> Route:
    """De onde vêm os preços reais de um ativo (aceita nomes com sufixo da corretora: EURUSDm, XAUUSD.a...)."""
    s = _root(symbol)
    letters = re.sub(r"[^A-Z]", "", s)
    if s.startswith(("WIN", "IND", "IBOV")):
        return Route("yahoo", "^BVSP", "b3", "b3", description="Mini Índice (segue o Ibovespa)", delay=900)
    if s.startswith(("WDO", "DOL")):
        return Route("yahoo", "BRL=X", "b3", "b3", scale=1000.0, backups=(("binance", "USDTBRL"),), description="Mini Dólar (segue o dólar comercial)")
    if s.startswith(("XAU", "GOLD")):
        # PAX Gold (1 token = 1 onça de ouro) na Binance: tempo real; o futuro no Yahoo é a reserva
        return Route("binance", "PAXGUSDT", "fx", "metal", backups=(("yahoo", "GC=F"),), description="Ouro (PAXG, 1 onça)")
    for prefixes, ticker, session, description, delay in YAHOO_ALIASES:
        if s.startswith(prefixes):
            kind = "metal" if ticker == "SI=F" else "oil" if ticker in ("CL=F", "BZ=F") else "index"
            return Route("yahoo", ticker, session, kind, description=description, delay=delay)
    for coin in sorted(CRYPTO, key=len, reverse=True):
        if letters.startswith(coin) and letters[len(coin):len(coin) + 3] in ("USD", ""):
            return Route("binance", f"{coin}USDT", "crypto", "crypto", backups=(("yahoo", f"{coin}-USD"),), description=f"{coin} x Dólar")
    if len(letters) >= 6 and letters[:3] in FIAT and letters[3:6] in FIAT:
        pair = letters[:6]
        backups = (("kraken", pair),) if pair in KRAKEN_FX else ()
        return Route("yahoo", f"{pair}=X", "fx", "fx", backups=backups, description=f"{pair[:3]} x {pair[3:]}")
    if re.fullmatch(r"[A-Z]{4}\d{1,2}F?", s):
        return Route("yahoo", f"{s.rstrip('F')}.SA", "b3", "stock", description=f"{s} (B3)", delay=900)
    return Route("yahoo", s, "index", "other", description=s, delay=900)


def canonical(symbol: str, route: Route) -> str:
    """Nome padrão do ativo (sem o sufixo da corretora): XAUUSDm -> XAUUSD, BTCUSDT -> BTCUSD."""
    if route.ticker in CANONICAL_BY_TICKER:
        return CANONICAL_BY_TICKER[route.ticker]
    letters = re.sub(r"[^A-Z]", "", symbol.upper())
    if route.kind == "fx":
        return letters[:6]
    if route.kind == "crypto":
        return route.ticker[:-4] + "USD"
    return _root(symbol)


def preset_of(symbol: str, route: Route) -> Preset | None:
    name = canonical(symbol, route)
    return PRESETS.get(name) or EXTRA_PRESETS.get(name)


def _fx_digits(quote: str) -> int:
    return 3 if quote in ("JPY", "HUF") else 5


def _price_digits(price: float) -> int:
    if price >= 1000:
        return 2
    if price >= 10:
        return 3
    if price >= 1:
        return 4
    return 6


class RealMarket:
    """Preços reais (Binance e Yahoo Finance) com cache: baixa o histórico uma vez e depois só o trecho novo."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._frames: dict[tuple[str, str, int], tuple[float, pd.DataFrame]] = {}
        self._ticks: dict[str, tuple[float, dict]] = {}
        self._rates_usd: dict[str, tuple[float, float]] = {}
        self._locks: dict[tuple, asyncio.Lock] = {}
        self._sem = asyncio.Semaphore(4)
        self._backoff: dict[str, float] = {}
        self._warned: dict[str, float] = {}
        self._browser = None
        self._crumb = ""
        self._crumb_at = 0.0
        self._crumb_tried = 0.0
        self.last_ok: float | None = None
        self.last_error: str = ""

    # ------------------------------------------------------------ http
    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0), headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, follow_redirects=True, transport=self._transport)
        return self._client

    async def _get_json(self, provider: str, hosts: tuple[str, ...], path: str, params: dict) -> object:
        if time.time() < self._backoff.get(provider, 0):
            raise RealDataError(f"{provider}: muitas consultas, aguardando um minuto")
        last = ""
        async with self._sem:
            for host in hosts:
                try:
                    res = await self._http().get(host + path, params=params)
                except httpx.HTTPError as exc:
                    last = f"{exc.__class__.__name__}"
                    continue
                if res.status_code == 429:
                    self._backoff[provider] = time.time() + 60
                    raise RealDataError(f"{provider}: limite de consultas atingido (tentando de novo em 1 min)")
                if res.status_code >= 400:
                    last = f"HTTP {res.status_code}"
                    continue
                try:
                    data = res.json()
                except ValueError:
                    last = "resposta inválida"
                    continue
                self.last_ok = time.time()
                self.last_error = ""
                return data
        self.last_error = f"{provider}: {last or 'sem resposta'}"
        raise RealDataError(self.last_error)

    def _warn(self, key: str, message: str) -> None:
        if time.time() - self._warned.get(key, 0) > 300:
            self._warned[key] = time.time()
            log.warning(message)

    def _lock(self, key: tuple) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    # --------------------------------------------------------- yahoo
    async def _yahoo_chart(self, ticker: str, interval: str, range_: str) -> tuple[pd.DataFrame, dict]:
        data = await self._yahoo_json(f"/v8/finance/chart/{ticker}", {"interval": interval, "range": range_, "includePrePost": "false"})
        return parse_yahoo(data, interval)

    async def _yahoo_json(self, path: str, params: dict) -> object:
        """Yahoo como um navegador: TLS do Chrome, cookie de sessão e o "crumb" que o site usa."""
        if self._transport is not None or BrowserSession is None:
            return await self._get_json("yahoo", YAHOO_HOSTS, path, params)  # testes (respostas falsas) ou sem curl_cffi
        if time.time() < self._backoff.get("yahoo", 0):
            raise RealDataError("yahoo: muitas consultas, aguardando um minuto")
        last = ""
        for attempt in range(2):
            await self._yahoo_crumb(force=attempt > 0)
            query = dict(params, crumb=self._crumb) if self._crumb else params
            async with self._sem:
                for host in YAHOO_HOSTS:
                    try:
                        res = await self._browser_session().get(host + path, params=query, timeout=20)
                    except Exception as exc:  # erros de rede do curl
                        last = exc.__class__.__name__
                        continue
                    if res.status_code == 429:
                        self._backoff["yahoo"] = time.time() + 60
                        raise RealDataError("yahoo: limite de consultas atingido (tentando de novo em 1 min)")
                    if res.status_code in (401, 403):
                        last = f"HTTP {res.status_code}"
                        break  # cookie/crumb vencido: renova e tenta de novo
                    if res.status_code >= 400:
                        last = f"HTTP {res.status_code}"
                        continue
                    try:
                        data = res.json()
                    except ValueError:
                        last = "resposta inválida"
                        continue
                    self.last_ok = time.time()
                    self.last_error = ""
                    return data
            if not last.startswith("HTTP 40"):
                break
        self.last_error = f"yahoo: {last or 'sem resposta'}"
        raise RealDataError(self.last_error)

    def _browser_session(self):
        if self._browser is None:
            self._browser = BrowserSession(impersonate="chrome", timeout=20)
        return self._browser

    async def _yahoo_crumb(self, force: bool = False) -> None:
        now = time.time()
        if not force and (self._crumb and now - self._crumb_at < 6 * 3600 or now - self._crumb_tried < 300):
            return
        self._crumb_tried = now
        session = self._browser_session()
        try:
            await session.get(YAHOO_COOKIE_URL, timeout=15)  # só cria o cookie de sessão (a resposta é 404 mesmo)
        except Exception:
            pass
        try:
            res = await session.get(YAHOO_CRUMB_URL, timeout=15)
            text = (res.text or "").strip()
            if res.status_code == 200 and 0 < len(text) < 64 and "<" not in text:
                self._crumb, self._crumb_at = text, now
        except Exception:
            pass

    # -------------------------------------------------------- kraken
    async def _kraken(self, path: str, params: dict) -> object:
        data = await self._get_json("kraken", KRAKEN_HOSTS, path, params)
        if not isinstance(data, dict) or data.get("error"):
            raise RealDataError(f"kraken: {(data or {}).get('error') if isinstance(data, dict) else 'resposta inválida'}")
        return data.get("result") or {}

    async def _kraken_ohlc(self, pair: str, seconds: int) -> pd.DataFrame:
        if seconds not in KRAKEN_MINUTES:
            raise RealDataError(f"kraken: tempo gráfico sem suporte ({seconds}s)")
        result = await self._kraken("/0/public/OHLC", {"pair": pair, "interval": KRAKEN_MINUTES[seconds]})
        rows = next((v for k, v in result.items() if k != "last"), [])  # type: ignore[union-attr]
        if not rows:
            raise RealDataError(f"Kraken sem dados de {pair}")
        arr = np.array([[float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[6])] for r in rows])
        return pd.DataFrame({"time": arr[:, 0].astype(np.int64), "open": arr[:, 1], "high": arr[:, 2], "low": arr[:, 3], "close": arr[:, 4], "volume": arr[:, 5]})

    # ------------------------------------------------------- binance
    async def _binance_klines(self, pair: str, interval: str, limit: int, end_ms: int | None = None) -> pd.DataFrame:
        params: dict = {"symbol": pair, "interval": interval, "limit": max(1, min(1000, limit))}
        if end_ms is not None:
            params["endTime"] = end_ms
        data = await self._get_json("binance", BINANCE_HOSTS, "/api/v3/klines", params)
        return parse_binance(data)

    # ---------------------------------------------------------- frames
    async def _frame(self, route: Route, seconds: int, need: int) -> pd.DataFrame:
        provider, ticker = route.provider, route.ticker
        key = (provider, ticker, seconds)
        async with self._lock(key):
            cached = self._frames.get(key)
            now = time.time()
            fresh = cached is not None and now - cached[0] < REFRESH_SECONDS.get(seconds, 60)
            if fresh and (provider != "binance" or len(cached[1]) >= need):
                return cached[1]
            try:
                if provider == "binance":
                    df = await self._load_binance(ticker, seconds, need, cached[1] if cached else None)
                elif provider == "kraken":
                    # a Kraken só devolve os 720 candles mais recentes: o histórico cresce enquanto o sistema roda
                    df = merge_frames(cached[1] if cached else None, await self._kraken_ohlc(ticker, seconds))
                else:
                    df = await self._load_yahoo(ticker, seconds, cached[1] if cached else None)
            except RealDataError as exc:
                if cached is not None:
                    self._warn(f"frame:{ticker}:{seconds}", f"dados reais de {ticker} sem atualizar ({exc}); usando o último histórico")
                    return cached[1]
                raise
            self._frames[key] = (now, df)  # preço original; a escala (mini dólar) é aplicada ao ler
            return df

    async def _load_yahoo(self, ticker: str, seconds: int, cached: pd.DataFrame | None) -> pd.DataFrame:
        interval, full, short, _ = INTERVALS[seconds]
        if cached is None or cached.empty:
            df, _meta = await self._yahoo_chart(ticker, interval, full)
            if df.empty:
                raise RealDataError(f"Yahoo não tem dados de {ticker}")
            return df.iloc[-MAX_ROWS:].reset_index(drop=True)
        new, _meta = await self._yahoo_chart(ticker, interval, short)
        return merge_frames(cached, new)

    async def _load_binance(self, pair: str, seconds: int, need: int, cached: pd.DataFrame | None) -> pd.DataFrame:
        interval = INTERVALS.get(seconds, (None, None, None, None))[3] or {14400: "4h"}.get(seconds)
        if interval is None:
            raise RealDataError(f"tempo gráfico sem suporte: {seconds}s")
        if cached is not None and len(cached) >= need:
            new = await self._binance_klines(pair, interval, 300)
            return merge_frames(cached, new)
        parts: list[pd.DataFrame] = []
        end_ms: int | None = None
        total = 0
        for _ in range(min(10, math.ceil(need / 1000) + 1)):
            part = await self._binance_klines(pair, interval, 1000, end_ms)
            if part.empty:
                break
            parts.append(part)
            total += len(part)
            end_ms = int(part["time"].iloc[0]) * 1000 - 1
            if total >= need or len(part) < 1000:
                break
        if not parts:
            raise RealDataError(f"Binance não tem dados de {pair}")
        df = pd.concat(parts[::-1], ignore_index=True)
        if cached is not None:
            df = merge_frames(df, cached)
        return df.drop_duplicates("time", keep="last").sort_values("time").iloc[-MAX_ROWS:].reset_index(drop=True)

    # ---------------------------------------------------------- público
    async def rates(self, symbol: str, seconds: int, count: int) -> pd.DataFrame:
        route = route_for(symbol)
        errors = []
        for option in route.chain():
            try:
                df = await self._series(option, seconds, count)
                break
            except RealDataError as exc:
                errors.append(str(exc))
        else:
            raise RealDataError("; ".join(errors))
        df = df.copy().reset_index(drop=True)
        if route.scale != 1.0:
            for col in ("open", "high", "low", "close"):
                df[col] = df[col] * route.scale
        spec = await self.spec(symbol)
        df["spread"] = spec["spread_points"] * spec["point"]
        return df[COLUMNS]

    async def _series(self, route: Route, seconds: int, count: int) -> pd.DataFrame:
        base = 3600 if route.provider == "yahoo" and seconds == 14400 else seconds  # o Yahoo não tem 4 horas: junta candles de 1 hora
        if base not in INTERVALS and not (route.provider in ("binance", "kraken") and base == 14400):
            raise RealDataError(f"tempo gráfico sem suporte: {seconds}s")
        df = await self._frame(route, base, count * (seconds // base) + 10)
        if base != seconds:
            df = resample(df.assign(spread=0.0), seconds)
        return df.iloc[-count:]

    @staticmethod
    def delay(symbol: str) -> int:
        """Atraso conhecido da fonte deste ativo (segundos): 0 = tempo real."""
        return route_for(symbol).delay

    async def tick(self, symbol: str) -> dict:
        route = route_for(symbol)
        cached = self._ticks.get(symbol)
        if cached and time.time() - cached[0] < TICK_TTL[route.provider]:
            return cached[1]
        errors = []
        for option in route.chain():
            try:
                bid, ask, last_time = await self._quote(option)
                break
            except (RealDataError, KeyError, TypeError, ValueError, StopIteration) as exc:
                errors.append(str(exc) or exc.__class__.__name__)
        else:
            if cached:
                self._warn(f"tick:{symbol}", f"cotação de {symbol} sem atualizar ({errors})")
                return cached[1]
            raise RealDataError(f"sem cotação real de {symbol}: {'; '.join(errors)}")
        mid = (bid + ask) / 2 * route.scale
        real_spread = (ask - bid) * route.scale if option.provider != "yahoo" else 0.0
        spec = self._static_spec(symbol, route, mid)
        half = max(spec["spread_points"] * spec["point"], real_spread) / 2
        now = time.time()
        is_open = bool(open_mask(route.session, np.array([int(now)]))[0]) and now - last_time <= STALE_SECONDS.get(route.session, 2700) + option.delay
        tick = {
            "bid": round(mid - half, spec["digits"]),
            "ask": round(mid + half, spec["digits"]),
            "time": int(last_time),
            "open": is_open,
            "source": option.label,
        }
        self._ticks[symbol] = (time.time(), tick)
        return tick

    async def _quote(self, route: Route) -> tuple[float, float, float]:
        """(compra, venda, horário do último preço) na fonte indicada."""
        if route.provider == "binance":
            data = await self._get_json("binance", BINANCE_HOSTS, "/api/v3/ticker/bookTicker", {"symbol": route.ticker})
            return float(data["bidPrice"]), float(data["askPrice"]), time.time()  # type: ignore[index]
        if route.provider == "kraken":
            result = await self._kraken("/0/public/Ticker", {"pair": route.ticker})
            row = next(iter(result.values()))  # type: ignore[union-attr]
            return float(row["b"][0]), float(row["a"][0]), time.time()
        price, at = await self._yahoo_price(route.ticker)
        return price, price, at

    async def _yahoo_price(self, ticker: str) -> tuple[float, float]:
        df, meta = await self._yahoo_chart(ticker, "1m", "1d")
        price = meta.get("regularMarketPrice")
        at = meta.get("regularMarketTime")
        if price is None and not df.empty:
            price, at = float(df["close"].iloc[-1]), float(df["time"].iloc[-1])
        if price is None:
            raise RealDataError(f"Yahoo sem cotação de {ticker}")
        return float(price), float(at or time.time())

    async def usd_rate(self, currency: str) -> float | None:
        """Quantas unidades da moeda valem 1 dólar (para converter o valor do tick para USD)."""
        if currency in ("USD", "USDT", ""):
            return 1.0
        cached = self._rates_usd.get(currency)
        if cached and time.time() - cached[0] < 1800:
            return cached[1]
        price = None
        options = [("yahoo", f"{currency}=X", False)]
        if f"USD{currency}" in KRAKEN_FX:
            options.append(("kraken", f"USD{currency}", False))
        elif f"{currency}USD" in KRAKEN_FX:
            options.append(("kraken", f"{currency}USD", True))  # cotação invertida (EURUSD -> euros por dólar)
        if currency == "BRL":
            options.append(("binance", "USDTBRL", False))
        for provider, ticker, invert in options:
            try:
                bid, ask, _ = await self._quote(Route(provider, ticker, "fx", "fx"))
            except (RealDataError, KeyError, TypeError, ValueError, StopIteration):
                continue
            mid = (bid + ask) / 2
            if mid > 0:
                price = 1 / mid if invert else mid
                break
        if price is None:
            return cached[1] if cached else None
        self._rates_usd[currency] = (time.time(), price)
        return price

    async def spec(self, symbol: str) -> dict:
        route = route_for(symbol)
        price = None
        cached = self._ticks.get(symbol)
        if cached:
            price = (cached[1]["bid"] + cached[1]["ask"]) / 2
        elif preset_of(symbol, route) is None and route.kind in ("crypto", "other"):
            try:
                price = (await self.tick(symbol))["bid"]  # casas decimais e spread dependem do preço
            except RealDataError:
                price = None
        spec = self._static_spec(symbol, route, price)
        profit = spec["currency_profit"]
        if profit not in ("USD", ""):
            rate = await self.usd_rate(profit)
            if rate:
                spec["tick_value"] = round(spec["tick_size"] * spec["contract_size"] / rate, 8)
        return spec

    def _static_spec(self, symbol: str, route: Route, price: float | None) -> dict:
        name = _root(symbol)
        preset = preset_of(symbol, route)
        letters = re.sub(r"[^A-Z]", "", name)
        if preset is not None:
            spec = {
                "digits": preset.digits, "point": 10.0 ** -preset.digits, "tick_size": preset.tick_size, "tick_value": preset.tick_value,
                "contract_size": preset.contract_size, "volume_min": preset.volume_min, "volume_max": preset.volume_max,
                "volume_step": preset.volume_step, "spread_points": preset.spread_points, "currency_base": preset.currency_base,
                "currency_profit": preset.currency_profit, "description": preset.description,
            }
            if route.kind == "b3":
                spec["tick_value"] = preset.tick_value * 5.6  # em reais; convertido para USD pela cotação do dia
            elif preset.currency_profit != "USD":
                spec["tick_value"] = preset.tick_size * preset.contract_size  # na moeda de cotação; convertido depois
        elif route.kind == "fx":
            base, quote = letters[:3], letters[3:6]
            digits = _fx_digits(quote)
            spec = {
                "digits": digits, "point": 10.0 ** -digits, "tick_size": 10.0 ** -digits, "tick_value": 10.0 ** -digits * 100000,
                "contract_size": 100000, "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01, "spread_points": 18,
                "currency_base": base, "currency_profit": quote, "description": route.description,
            }
        elif route.kind == "stock":
            spec = {
                "digits": 2, "point": 0.01, "tick_size": 0.01, "tick_value": 0.01, "contract_size": 1, "volume_min": 1, "volume_max": 100000,
                "volume_step": 1, "spread_points": 2, "currency_base": name, "currency_profit": "BRL", "description": route.description,
            }
        else:
            digits = _price_digits(price or 100.0)
            tick = 10.0 ** -digits
            spread = max(1.0, round((price or 100.0) * 0.0004 / tick))
            coin = letters[:-3] if letters.endswith("USD") else letters
            spec = {
                "digits": digits, "point": tick, "tick_size": tick, "tick_value": tick, "contract_size": 1, "volume_min": 0.01,
                "volume_max": 1000, "volume_step": 0.01, "spread_points": spread, "currency_base": coin if route.kind == "crypto" else name,
                "currency_profit": "USD", "description": route.description,
            }
        spec.update({
            "name": symbol.upper(), "stops_level": 0, "session": route.session, "trade_allowed": True,
            "data_source": route.label,
        })
        return spec

    @staticmethod
    def list_symbols(q: str = "") -> list[dict]:
        ql = q.strip().lower()
        rows = [
            {"name": name, "description": desc, "path": route_for(name).label}
            for name, desc in SUGGESTIONS
            if not ql or ql in name.lower() or ql in desc.lower()
        ]
        if ql and len(ql) >= 3 and not any(r["name"].lower() == ql for r in rows):
            sym = q.strip().upper()
            rows.append({"name": sym, "description": "buscar no Yahoo Finance", "path": route_for(sym).label})
        return rows

    def clear(self) -> None:
        self._frames.clear()
        self._ticks.clear()


# ---------------------------------------------------------------- leitura
def parse_yahoo(data: object, interval: str) -> tuple[pd.DataFrame, dict]:
    try:
        chart = data["chart"]  # type: ignore[index]
        if chart.get("error"):
            raise RealDataError(str(chart["error"].get("description") or chart["error"]))
        res = chart["result"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise RealDataError("resposta do Yahoo em formato inesperado") from exc
    meta = res.get("meta") or {}
    ts = res.get("timestamp") or []
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    if not ts:
        return pd.DataFrame(columns=COLUMNS[:-1]), meta
    df = pd.DataFrame(
        {
            "time": np.asarray(ts, dtype=np.int64),
            "open": pd.to_numeric(pd.Series(quote.get("open") or [None] * len(ts)), errors="coerce"),
            "high": pd.to_numeric(pd.Series(quote.get("high") or [None] * len(ts)), errors="coerce"),
            "low": pd.to_numeric(pd.Series(quote.get("low") or [None] * len(ts)), errors="coerce"),
            "close": pd.to_numeric(pd.Series(quote.get("close") or [None] * len(ts)), errors="coerce"),
            "volume": pd.to_numeric(pd.Series(quote.get("volume") or [0] * len(ts)), errors="coerce"),
        }
    )
    df = df.dropna(subset=["close"])
    for col in ("open", "high", "low"):
        df[col] = df[col].fillna(df["close"])
    df["high"] = df[["open", "high", "low", "close"]].max(axis=1)
    df["low"] = df[["open", "high", "low", "close"]].min(axis=1)
    df["volume"] = df["volume"].fillna(0.0)
    seconds = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "60m": 3600, "1d": 86400}[interval]
    if seconds == 86400:
        offset = int(meta.get("gmtoffset") or 0)
        df["time"] = ((df["time"] + offset) // 86400) * 86400  # dia da bolsa, à meia-noite UTC
    else:
        df["time"] = (df["time"] // seconds) * seconds  # o último ponto (preço ao vivo) entra no candle dele
    return aggregate(df), meta


def parse_binance(data: object) -> pd.DataFrame:
    if not isinstance(data, list):
        raise RealDataError("resposta da Binance em formato inesperado")
    if not data:
        return pd.DataFrame(columns=COLUMNS[:-1])
    arr = np.array([[float(r[0]) / 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in data])
    return pd.DataFrame({"time": arr[:, 0].astype(np.int64), "open": arr[:, 1], "high": arr[:, 2], "low": arr[:, 3], "close": arr[:, 4], "volume": arr[:, 5]})


def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """Junta linhas com o mesmo horário de abertura (o Yahoo às vezes repete o candle em formação)."""
    if df.empty or df["time"].is_unique:
        return df.sort_values("time").reset_index(drop=True)
    g = df.sort_values("time", kind="stable").groupby("time", sort=True)
    return pd.DataFrame(
        {
            "time": np.asarray(g["time"].first().index, dtype=np.int64),
            "open": g["open"].first().to_numpy(),
            "high": g["high"].max().to_numpy(),
            "low": g["low"].min().to_numpy(),
            "close": g["close"].last().to_numpy(),
            "volume": g["volume"].sum().to_numpy(),
        }
    )


def merge_frames(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Histórico guardado + trecho novo: os candles novos substituem os antigos com o mesmo horário."""
    if new is None or new.empty:
        return old
    if old is None or old.empty:
        return new.reset_index(drop=True)
    start = int(new["time"].iloc[0])
    merged = pd.concat([old[old["time"] < start], new], ignore_index=True)
    return merged.iloc[-MAX_ROWS:].reset_index(drop=True)

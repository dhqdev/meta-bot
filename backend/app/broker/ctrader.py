"""Corretora pela cTrader Open API (sem MetaTrader e sem Windows).

Fala direto com os servidores da cTrader (protobuf sobre TCP com SSL, porta 5035) e devolve tudo no mesmo
formato do cliente do bridge do MT5 (`MT5Client`): o resto do sistema (cotações, candles, ordens, Tito) não
precisa saber qual das duas está ligada.

Conta: o dono autoriza o app dele (criado em openapi.ctrader.com) a operar a conta pelo login do cTrader ID;
guardamos client id/secret e os tokens criptografados no banco. O token de acesso dura ~30 dias e é renovado
sozinho com o refresh token.

Unidades da cTrader: volume em centésimos de unidade (1 lote de EURUSD = 10.000.000), preços relativos em
1/100000, valores em dinheiro multiplicados por 10^moneyDigits, horários em milissegundos UTC.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import ssl
import struct
import time
from typing import Any, Callable

from app.broker.ctrader_proto import OpenApiCommonMessages_pb2 as common
from app.broker.ctrader_proto import OpenApiMessages_pb2 as oa
from app.broker.mt5 import MT5Error, MT5Unavailable

log = logging.getLogger("metabot.ctrader")

HOSTS = {"live": "live.ctraderapi.com", "demo": "demo.ctraderapi.com"}
PORT = 5035
AUTH_URL = "https://id.ctrader.com/my/settings/openapi/grantingaccess/"
TOKEN_URL = "https://openapi.ctrader.com/apps/token"

HEARTBEAT = 51
ERROR_RES = 50
OA_ERROR_RES = 2142
ORDER_ERROR_EVENT = 2132
EXECUTION_EVENT = 2126
SPOT_EVENT = 2131
TOKEN_INVALIDATED = 2147
CLIENT_DISCONNECT = 2148
ACCOUNT_DISCONNECT = 2164

# tipo de execução (ProtoOAExecutionType)
EX_ACCEPTED, EX_FILLED, EX_REPLACED, EX_CANCELLED, EX_EXPIRED, EX_REJECTED, EX_PARTIAL = 2, 3, 4, 5, 6, 7, 11
BUY, SELL = 1, 2
MARKET = 1

PERIODS = {"M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5, "M10": 6, "M15": 7, "M30": 8, "H1": 9, "H4": 10, "H12": 11, "D1": 12, "W1": 13, "MN1": 14}
PERIOD_SECONDS = {"M1": 60, "M2": 120, "M3": 180, "M4": 240, "M5": 300, "M10": 600, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "H12": 43200, "D1": 86400, "W1": 604800, "MN1": 2592000}

# erros em que vale renovar o token de acesso e tentar de novo
TOKEN_ERRORS = {"CH_ACCESS_TOKEN_INVALID", "OA_AUTH_TOKEN_EXPIRED", "INVALID_ACCESS_TOKEN", "ACCESS_TOKEN_INVALID"}

ERROR_TEXT = {
    "MARKET_CLOSED": "mercado fechado",
    "NOT_ENOUGH_MONEY": "margem insuficiente",
    "TRADING_DISABLED": "negociação desligada para este ativo",
    "TRADING_BAD_VOLUME": "volume inválido para este ativo",
    "TRADING_BAD_STOPS": "stop ou alvo perto demais do preço",
    "POSITION_NOT_FOUND": "posição não encontrada",
    "CH_CLIENT_AUTH_FAILURE": "client id ou client secret do app recusados",
    "CH_ACCESS_TOKEN_INVALID": "autorização da conta expirou (conecte o cTrader de novo em Configurações)",
    "ACCOUNT_NOT_AUTHORIZED": "conta não autorizada para este app",
    "BLOCKED_PAYLOAD_TYPE": "limite de pedidos da cTrader atingido",
    "CANT_ROUTE_REQUEST": "servidor da cTrader indisponível",
}

# payloadType -> classe da mensagem (para decodificar o que chega)
MESSAGE_TYPES: dict[int, Any] = {}
for _mod in (oa, common):
    for _name, _desc in _mod.DESCRIPTOR.message_types_by_name.items():
        _field = _desc.fields_by_name.get("payloadType")
        if _field is not None and _field.has_default_value:
            MESSAGE_TYPES[int(_field.default_value)] = getattr(_mod, _name)


def payload_type(msg: Any) -> int:
    return int(msg.DESCRIPTOR.fields_by_name["payloadType"].default_value)


def error_text(code: str, description: str = "") -> str:
    return ERROR_TEXT.get(code) or description or code or "erro da cTrader"


def norm(name: str) -> str:
    return (name or "").upper().replace("/", "").replace(" ", "")


def money(value: int | float | None, digits: int | None) -> float:
    return float(value or 0) / (10 ** int(digits or 2))


class CTraderError(MT5Error):
    def __init__(self, code: str, description: str = "", status: int = 400):
        super().__init__(error_text(code, description), status, {"error": code, "description": description})
        self.code = code


class CTraderClient:
    """Mesma interface do `MT5Client`, falando com a cTrader Open API."""

    def __init__(
        self,
        *,
        environment: str,
        client_id: str,
        client_secret: str,
        access_token: str,
        refresh_token: str = "",
        account_id: int,
        on_tokens: Callable[[str, str], None] | None = None,
        host: str | None = None,
        port: int = PORT,
        use_ssl: bool = True,
        timeout: float = 20.0,
    ):
        self.environment = environment if environment in HOSTS else "demo"
        self.host = host or HOSTS[self.environment]
        self.port = port
        self.use_ssl = use_ssl
        self.timeout = timeout
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.account_id = int(account_id)
        self.on_tokens = on_tokens
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._tasks: list[asyncio.Task] = []
        self._pending: dict[str, tuple[asyncio.Future, Callable[[int, Any], bool]]] = {}
        self._ids = itertools.count(1)
        self._connect_lock = asyncio.Lock()
        self._ready = False
        self._trader: Any = None
        self._assets: dict[int, str] = {}
        self._symbols: dict[str, Any] = {}  # nome normalizado -> ProtoOALightSymbol
        self._symbol_names: dict[int, str] = {}
        self._details: dict[int, tuple[float, Any]] = {}
        self._spots: dict[int, dict] = {}
        self._spot_waiters: dict[int, asyncio.Event] = {}
        self._subscribed: set[int] = set()
        self._volumes: dict[int, int] = {}  # posição -> volume em centésimos (para fechar)

    # ------------------------------------------------------------ conexão
    @property
    def connected(self) -> bool:
        return self._ready and self._writer is not None and not self._writer.is_closing()

    async def _ensure(self) -> None:
        if self.connected:
            return
        async with self._connect_lock:
            if self.connected:
                return
            await self._close_transport()
            try:
                ctx = ssl.create_default_context() if self.use_ssl else None
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port, ssl=ctx, server_hostname=self.host if ctx else None), self.timeout
                )
            except (OSError, asyncio.TimeoutError) as exc:
                raise MT5Unavailable(f"não foi possível conectar à cTrader ({exc.__class__.__name__})") from exc
            self._tasks = [asyncio.create_task(self._read_loop()), asyncio.create_task(self._heartbeat_loop())]
            try:
                await self._call(oa.ProtoOAApplicationAuthReq(clientId=self.client_id, clientSecret=self.client_secret))
                await self._account_auth()
                await self._load_account()
            except Exception:
                await self._close_transport()
                raise
            self._ready = True
            if self._subscribed:
                ids, self._subscribed = list(self._subscribed), set()
                await self._subscribe(ids)

    async def _account_auth(self) -> None:
        try:
            await self._call(oa.ProtoOAAccountAuthReq(ctidTraderAccountId=self.account_id, accessToken=self.access_token))
        except CTraderError as exc:
            if exc.code not in TOKEN_ERRORS or not self.refresh_token:
                raise
            await self._refresh()
            await self._call(oa.ProtoOAAccountAuthReq(ctidTraderAccountId=self.account_id, accessToken=self.access_token))

    async def _refresh(self) -> None:
        _, res = await self._call(oa.ProtoOARefreshTokenReq(refreshToken=self.refresh_token))
        self.access_token, self.refresh_token = res.accessToken, res.refreshToken
        log.info("token da cTrader renovado")
        if self.on_tokens:
            try:
                self.on_tokens(self.access_token, self.refresh_token)
            except Exception:  # noqa: BLE001  (guardar o token novo não pode derrubar a conexão)
                log.exception("falha ao guardar o token novo da cTrader")

    async def _load_account(self) -> None:
        _, res = await self._call(oa.ProtoOATraderReq(ctidTraderAccountId=self.account_id))
        self._trader = res.trader
        _, assets = await self._call(oa.ProtoOAAssetListReq(ctidTraderAccountId=self.account_id))
        self._assets = {a.assetId: a.name for a in assets.asset}
        _, syms = await self._call(oa.ProtoOASymbolsListReq(ctidTraderAccountId=self.account_id))
        self._symbols = {norm(s.symbolName): s for s in syms.symbol}
        self._symbol_names = {s.symbolId: s.symbolName for s in syms.symbol}

    async def aclose(self) -> None:
        await self._close_transport()

    async def _close_transport(self) -> None:
        self._ready = False
        for task in self._tasks:
            if task is not asyncio.current_task():
                task.cancel()
        self._tasks = []
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
        self._writer = self._reader = None
        self._fail_pending(MT5Unavailable("conexão com a cTrader caiu"))

    def _fail_pending(self, exc: Exception) -> None:
        for fut, _ in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    async def _send(self, msg: Any, client_msg_id: str | None = None) -> None:
        if self._writer is None:
            raise MT5Unavailable("sem conexão com a cTrader")
        wrapper = common.ProtoMessage(payloadType=payload_type(msg), payload=msg.SerializeToString())
        if client_msg_id:
            wrapper.clientMsgId = client_msg_id
        raw = wrapper.SerializeToString()
        self._writer.write(struct.pack(">I", len(raw)) + raw)
        await self._writer.drain()

    async def _call(self, msg: Any, done: Callable[[int, Any], bool] | None = None, timeout: float | None = None) -> tuple[int, Any]:
        """Envia e espera a resposta com o mesmo clientMsgId. `done` decide quando parar (ordens têm vários eventos)."""
        mid = f"mb{next(self._ids)}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[mid] = (fut, done or (lambda _t, _m: True))
        try:
            await self._send(msg, mid)
            return await asyncio.wait_for(fut, timeout or self.timeout)
        except asyncio.TimeoutError as exc:
            raise MT5Unavailable("a cTrader não respondeu a tempo") from exc
        finally:
            self._pending.pop(mid, None)

    async def _read_loop(self) -> None:
        try:
            while self._reader is not None:
                head = await self._reader.readexactly(4)
                body = await self._reader.readexactly(struct.unpack(">I", head)[0])
                wrapper = common.ProtoMessage()
                wrapper.ParseFromString(body)
                self._dispatch(wrapper)
        except (asyncio.IncompleteReadError, ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            if self._ready or self._pending:
                self._ready = False
                self._fail_pending(MT5Unavailable("conexão com a cTrader caiu"))

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(10)
                await self._send(common.ProtoHeartbeatEvent())
        except (asyncio.CancelledError, MT5Unavailable, ConnectionError, OSError):
            pass

    def _dispatch(self, wrapper: Any) -> None:
        ptype = wrapper.payloadType
        if ptype == HEARTBEAT:
            return
        cls = MESSAGE_TYPES.get(ptype)
        msg = None
        if cls is not None:
            msg = cls()
            msg.ParseFromString(wrapper.payload)
        if ptype == SPOT_EVENT and msg is not None:
            self._on_spot(msg)
            return
        if ptype in (TOKEN_INVALIDATED, CLIENT_DISCONNECT, ACCOUNT_DISCONNECT):
            log.warning("cTrader encerrou a sessão (%s)", getattr(msg, "reason", ptype))
            self._ready = False
            return
        if ptype == EXECUTION_EVENT and msg is not None and msg.HasField("position"):
            pos = msg.position
            if pos.positionStatus == 1:  # aberta: guarda o volume para fechar depois
                self._volumes[pos.positionId] = pos.tradeData.volume
            else:
                self._volumes.pop(pos.positionId, None)
        entry = self._pending.get(wrapper.clientMsgId) if wrapper.clientMsgId else None
        if entry is None:
            return
        fut, done = entry
        if fut.done():
            return
        if ptype in (ERROR_RES, OA_ERROR_RES, ORDER_ERROR_EVENT):
            fut.set_exception(CTraderError(getattr(msg, "errorCode", "ERRO"), getattr(msg, "description", "")))
            return
        if done(ptype, msg):
            fut.set_result((ptype, msg))

    # ------------------------------------------------------------ cotações
    def _on_spot(self, ev: Any) -> None:
        spot = self._spots.setdefault(ev.symbolId, {"bid": 0.0, "ask": 0.0, "time": 0})
        if ev.HasField("bid"):
            spot["bid"] = ev.bid / 100000
        if ev.HasField("ask"):
            spot["ask"] = ev.ask / 100000
        spot["time"] = int(ev.timestamp / 1000) if ev.HasField("timestamp") and ev.timestamp else int(time.time())
        if spot["bid"] and spot["ask"]:
            waiter = self._spot_waiters.get(ev.symbolId)
            if waiter is not None:
                waiter.set()

    async def _subscribe(self, ids: list[int]) -> None:
        new = [i for i in ids if i not in self._subscribed]
        if not new:
            return
        for i in new:
            self._spot_waiters.setdefault(i, asyncio.Event())
        await self._call(oa.ProtoOASubscribeSpotsReq(ctidTraderAccountId=self.account_id, symbolId=new))
        self._subscribed.update(new)

    async def _spot(self, symbol_id: int) -> dict:
        await self._subscribe([symbol_id])
        spot = self._spots.get(symbol_id)
        if not spot or not (spot["bid"] and spot["ask"]):
            try:
                await asyncio.wait_for(self._spot_waiters[symbol_id].wait(), 8)
            except asyncio.TimeoutError as exc:
                raise MT5Error(f"sem cotação para {self._symbol_names.get(symbol_id, symbol_id)} (mercado fechado?)", 404) from exc
            spot = self._spots[symbol_id]
        return spot

    def _light(self, name: str) -> Any:
        sym = self._symbols.get(norm(name))
        if sym is None:
            raise MT5Error(f"símbolo não encontrado nesta corretora: {name}", 404)
        return sym

    async def _detail(self, symbol_id: int) -> Any:
        cached = self._details.get(symbol_id)
        if cached and time.time() - cached[0] < 3600:
            return cached[1]
        _, res = await self._call(oa.ProtoOASymbolByIdReq(ctidTraderAccountId=self.account_id, symbolId=[symbol_id]))
        if not res.symbol:
            raise MT5Error("símbolo não encontrado nesta corretora", 404)
        self._details[symbol_id] = (time.time(), res.symbol[0])
        return res.symbol[0]

    async def _quote_to_deposit(self, quote_asset: int) -> float:
        """Quanto vale 1 unidade da moeda de cotação na moeda da conta (pela cadeia de conversão da cTrader)."""
        deposit = self._trader.depositAssetId
        if quote_asset == deposit:
            return 1.0
        _, res = await self._call(oa.ProtoOASymbolsForConversionReq(ctidTraderAccountId=self.account_id, firstAssetId=quote_asset, lastAssetId=deposit))
        rate, current = 1.0, quote_asset
        for sym in res.symbol:
            spot = await self._spot(sym.symbolId)
            mid = (spot["bid"] + spot["ask"]) / 2
            if sym.baseAssetId == current:
                rate, current = rate * mid, sym.quoteAssetId
            elif sym.quoteAssetId == current and mid:
                rate, current = rate / mid, sym.baseAssetId
        if current != deposit:
            log.warning("sem conversão de %s para %s; valor do tick sem conversão", self._assets.get(quote_asset), self._assets.get(deposit))
            return 1.0
        return rate

    # ------------------------------------------------------------ interface do MT5Client
    async def ping(self) -> dict:
        return {"ok": True}

    async def health(self) -> dict:
        await self._ensure()
        account = await self.account()
        now = int(time.time())
        return {
            "ok": True,
            "initialized": True,
            "connected": True,
            "trade_allowed": int(self._trader.accessRights or 0) == 0,
            "account": account,
            "terminal": {"name": "cTrader Open API", "connected": True},
            # a cTrader trabalha em UTC: fuso do servidor = 0
            "server_time_hint": {"symbol": "", "tick_time": now, "utc_now": now},
        }

    async def login(self, login: int, password: str, server: str) -> dict:
        raise MT5Error("no cTrader a conta é escolhida ao conectar (Configurações → Conectar cTrader)", 400)

    async def account(self) -> dict:
        await self._ensure()
        _, res = await self._call(oa.ProtoOATraderReq(ctidTraderAccountId=self.account_id))
        self._trader = trader = res.trader
        digits = trader.moneyDigits if trader.HasField("moneyDigits") else 2
        balance = money(trader.balance, digits)
        profit = 0.0
        try:
            _, pnl = await self._call(oa.ProtoOAGetPositionUnrealizedPnLReq(ctidTraderAccountId=self.account_id))
            profit = sum(money(p.netUnrealizedPnL, pnl.moneyDigits) for p in pnl.positionUnrealizedPnL)
        except CTraderError:
            pass
        broker = trader.brokerName or "cTrader"
        return {
            "login": int(trader.traderLogin or self.account_id),
            "server": f"{broker} ({'real' if self.environment == 'live' else 'demo'})",
            "company": broker,
            "name": "",
            "currency": self._assets.get(trader.depositAssetId, "USD"),
            "balance": round(balance, 2),
            "equity": round(balance + profit, 2),
            "margin_free": round(balance + profit, 2),
            "profit": round(profit, 2),
            "leverage": int((trader.leverageInCents or 0) / 100),
            "trade_allowed": int(trader.accessRights or 0) == 0,
        }

    async def symbols(self, q: str = "", limit: int = 200) -> list[dict]:
        await self._ensure()
        ql = q.lower()
        out = []
        for s in self._symbols.values():
            if ql and ql not in s.symbolName.lower() and ql not in (s.description or "").lower():
                continue
            out.append({
                "name": s.symbolName, "description": s.description, "path": "", "visible": bool(s.enabled), "digits": None,
                "currency_base": self._assets.get(s.baseAssetId, ""), "currency_profit": self._assets.get(s.quoteAssetId, ""),
            })
            if len(out) >= limit:
                break
        return out

    async def symbol(self, name: str) -> dict:
        await self._ensure()
        light = self._light(name)
        info = await self._detail(light.symbolId)
        spot = await self._spot(light.symbolId)
        digits = int(info.digits)
        point = 10.0 ** -digits
        lot = float(info.lotSize or 10_000_000)  # centésimos de unidade por lote
        contract = lot / 100
        rate = await self._quote_to_deposit(light.quoteAssetId)
        stops = 0
        if int(info.distanceSetIn or 1) == 1 and info.slDistance:
            stops = int(info.slDistance)
        return {
            "name": name,
            "description": light.description,
            "digits": digits,
            "point": point,
            "trade_tick_size": point,
            "trade_tick_value": contract * point * rate,
            "trade_contract_size": contract,
            "volume_min": float(info.minVolume or 0) / lot or 0.01,
            "volume_max": float(info.maxVolume or 0) / lot or 100.0,
            "volume_step": float(info.stepVolume or 0) / lot or 0.01,
            "spread": round((spot["ask"] - spot["bid"]) / point),
            "trade_stops_level": stops,
            "currency_base": self._assets.get(light.baseAssetId, ""),
            "currency_profit": self._assets.get(light.quoteAssetId, ""),
            "trade_mode": 4 if int(info.tradingMode or 0) == 0 else 0,
        }

    async def tick(self, symbol: str) -> dict:
        await self._ensure()
        spot = await self._spot(self._light(symbol).symbolId)
        return {"time": spot["time"], "bid": spot["bid"], "ask": spot["ask"], "last": spot["bid"], "volume": 0}

    async def rates(self, symbol: str, timeframe: str, count: int) -> dict:
        """Candles fechados e o atual, do mais antigo para o mais novo, em janelas (a cTrader limita o período por pedido)."""
        await self._ensure()
        tf = timeframe.upper()
        if tf not in PERIODS:
            raise MT5Error(f"timeframe inválido: {timeframe}", 400)
        symbol_id = self._light(symbol).symbolId
        digits = int((await self._detail(symbol_id)).digits)
        # a cTrader não manda o spread de cada candle (são candles do bid): usa o spread de agora, como nos preços públicos,
        # para o backtest não ficar sem esse custo
        try:
            spot = await self._spot(symbol_id)
            spread = max(0, round((spot["ask"] - spot["bid"]) * 10 ** digits))
        except MT5Error:
            spread = 0
        step_ms = PERIOD_SECONDS[tf] * 1000
        window = max(step_ms * 10, min(step_ms * count * 2, 400 * 86400 * 1000))
        to_ts = int(time.time() * 1000)
        bars: dict[int, list] = {}
        empty = 0
        for _ in range(40):
            if len(bars) >= count:
                break
            from_ts = max(0, to_ts - window)
            try:
                _, res = await self._call(
                    oa.ProtoOAGetTrendbarsReq(ctidTraderAccountId=self.account_id, fromTimestamp=from_ts, toTimestamp=to_ts, period=PERIODS[tf], symbolId=symbol_id, count=count),
                    timeout=self.timeout * 2,
                )
            except CTraderError as exc:
                if exc.code in ("INCORRECT_BOUNDARIES", "INVALID_REQUEST") and window > step_ms * 10:
                    window //= 2  # janela grande demais para este tempo gráfico: divide e tenta de novo
                    continue
                raise
            got = 0
            for tb in res.trendbar:
                t = int(tb.utcTimestampInMinutes) * 60
                low = tb.low
                bars[t] = [
                    t,
                    round((low + tb.deltaOpen) / 100000, digits),
                    round((low + tb.deltaHigh) / 100000, digits),
                    round(low / 100000, digits),
                    round((low + tb.deltaClose) / 100000, digits),
                    int(tb.volume), spread, 0,
                ]
                got += 1
            empty = empty + 1 if got == 0 else 0
            if empty >= 3 or from_ts == 0:
                break
            to_ts = from_ts - 1
            await asyncio.sleep(0.2)  # a cTrader aceita no máximo 5 pedidos de histórico por segundo
        rows = [bars[t] for t in sorted(bars)][-count:]
        return {"symbol": symbol, "timeframe": tf, "fields": ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"], "rows": rows}

    def _position_dict(self, pos: Any) -> dict:
        td = pos.tradeData
        info = self._details.get(td.symbolId)
        lot = float(info[1].lotSize) if info and info[1].lotSize else 10_000_000.0
        label = td.label or ""
        magic = int(label[3:]) if label.startswith("MB:") and label[3:].isdigit() else 0
        return {
            "ticket": pos.positionId,
            "symbol": self._symbol_names.get(td.symbolId, str(td.symbolId)),
            "type": 0 if td.tradeSide == BUY else 1,
            "volume": td.volume / lot,
            "price_open": pos.price,
            "sl": pos.stopLoss if pos.HasField("stopLoss") else 0.0,
            "tp": pos.takeProfit if pos.HasField("takeProfit") else 0.0,
            "magic": magic,
            "profit": 0.0,
            "comment": td.comment,
        }

    async def positions(self, magic: int | None = None) -> list[dict]:
        await self._ensure()
        _, res = await self._call(oa.ProtoOAReconcileReq(ctidTraderAccountId=self.account_id))
        out = []
        for pos in res.position:
            self._volumes[pos.positionId] = pos.tradeData.volume
            if pos.tradeData.symbolId not in self._details:
                try:
                    await self._detail(pos.tradeData.symbolId)
                except MT5Error:
                    pass
            out.append(self._position_dict(pos))
        if magic is not None:
            out = [p for p in out if int(p["magic"]) == int(magic)]
        return out

    async def history_deals(self, date_from: int = 0, date_to: int | None = None, position: int | None = None) -> list[dict]:
        """Negócios de uma posição no formato do MT5 (entry 0 = abertura, 1 = saída).

        Na cTrader o saldo só muda no fechamento: o negócio de saída traz lucro, swap e a comissão de ida e volta."""
        if position is None:
            return []
        await self._ensure()
        to_ts = int((date_to or time.time() + 60) * 1000)
        res = None
        for days in (90, 30, 7):
            try:
                _, res = await self._call(oa.ProtoOADealListByPositionIdReq(ctidTraderAccountId=self.account_id, positionId=int(position), fromTimestamp=max(0, to_ts - days * 86400 * 1000), toTimestamp=to_ts))
                break
            except CTraderError as exc:
                if exc.code not in ("INCORRECT_BOUNDARIES", "INVALID_REQUEST"):
                    raise
        out = []
        for d in res.deal if res is not None else []:
            if d.dealStatus not in (2, 3):  # preenchido ou parcial
                continue
            info = self._details.get(d.symbolId)
            lot = float(info[1].lotSize) if info and info[1].lotSize else 10_000_000.0
            closing = d.HasField("closePositionDetail")
            cpd = d.closePositionDetail
            md = (cpd.moneyDigits if cpd.HasField("moneyDigits") else d.moneyDigits) if closing else d.moneyDigits
            out.append({
                "ticket": d.dealId,
                "order": d.orderId,
                "position_id": d.positionId,
                "time": int(d.executionTimestamp / 1000),
                "time_msc": int(d.executionTimestamp),
                "type": 0 if d.tradeSide == BUY else 1,
                "entry": 1 if closing else 0,
                "volume": d.filledVolume / lot,
                "price": d.executionPrice,
                "profit": money(cpd.grossProfit, md) if closing else 0.0,
                "commission": money(cpd.commission, md) if closing else 0.0,
                "swap": money(cpd.swap, md) if closing else 0.0,
                "fee": -money(cpd.pnlConversionFee, md) if closing and cpd.HasField("pnlConversionFee") else 0.0,
                "reason": 0,
                "symbol": self._symbol_names.get(d.symbolId, ""),
            })
        return out

    # ---------------------------------------------------------------- ordens
    @staticmethod
    def _fill_or_reject(ptype: int, msg: Any) -> bool:
        return ptype == EXECUTION_EVENT and msg.executionType in (EX_FILLED, EX_REJECTED, EX_CANCELLED, EX_EXPIRED)

    @staticmethod
    def _result(msg: Any, lot: float) -> dict:
        if msg.executionType != EX_FILLED:
            code = msg.errorCode or "ORDER_REJECTED"
            return {"ok": False, "retcode": code, "comment": error_text(code, "ordem recusada")}
        deal = msg.deal if msg.HasField("deal") else None
        pos = msg.position if msg.HasField("position") else None
        price = (deal.executionPrice if deal is not None and deal.HasField("executionPrice") else 0.0) or (pos.price if pos is not None else 0.0)
        volume = (deal.filledVolume if deal is not None else (pos.tradeData.volume if pos is not None else 0)) / lot
        return {
            "ok": True,
            "retcode": 10009,
            "order": pos.positionId if pos is not None else (deal.positionId if deal is not None else 0),
            "deal": deal.dealId if deal is not None else 0,
            "price": price,
            "volume": volume,
            # stop e alvo que ficaram na corretora (a cTrader ancora o relativo no preço executado)
            "sl": pos.stopLoss if pos is not None and pos.HasField("stopLoss") else 0.0,
            "tp": pos.takeProfit if pos is not None and pos.HasField("takeProfit") else 0.0,
            "comment": "executada",
        }

    async def order_check(self, order: dict) -> dict:
        await self._ensure()
        light = self._light(str(order.get("symbol") or ""))
        await self._detail(light.symbolId)
        return {"ok": True, "retcode": 0, "comment": "ok"}

    async def order_send(self, order: dict) -> dict:
        if str(order.get("action") or "deal") != "deal":
            raise MT5Error("na cTrader só enviamos ordens a mercado", 400)
        await self._ensure()
        side = str(order.get("side") or "").lower()
        if side not in ("buy", "sell"):
            raise MT5Error("side deve ser buy ou sell", 400)
        light = self._light(str(order.get("symbol") or ""))
        info = await self._detail(light.symbolId)
        lot = float(info.lotSize or 10_000_000)
        step = float(info.stepVolume or 1)
        volume = int(round(float(order.get("volume") or 0) * lot / step) * step)
        if volume <= 0:
            raise MT5Error("volume deve ser positivo", 400)
        spot = await self._spot(light.symbolId)
        price = spot["ask"] if side == "buy" else spot["bid"]
        unit = 10 ** max(0, 5 - int(info.digits))  # o relativo precisa respeitar as casas do ativo
        req = oa.ProtoOANewOrderReq(
            ctidTraderAccountId=self.account_id, symbolId=light.symbolId, orderType=MARKET,
            tradeSide=BUY if side == "buy" else SELL, volume=volume,
            label=f"MB:{int(order.get('magic') or 0)}", comment=str(order.get("comment") or "")[:100],
        )
        for key, field in (("sl", "relativeStopLoss"), ("tp", "relativeTakeProfit")):
            if order.get(key):
                rel = int(round(abs(price - float(order[key])) * 100000 / unit)) * unit
                if rel > 0:
                    setattr(req, field, rel)
        _, msg = await self._call(req, self._fill_or_reject, timeout=self.timeout * 2)
        return self._result(msg, lot)

    async def position_close(self, ticket: int, volume: float | None = None, deviation: int = 20, comment: str = "meta-bot") -> dict:
        await self._ensure()
        ticket = int(ticket)
        cents = self._volumes.get(ticket)
        if cents is None:
            await self.positions()
            cents = self._volumes.get(ticket)
        if cents is None:
            raise MT5Error(f"posição {ticket} não encontrada", 404)
        _, msg = await self._call(oa.ProtoOAClosePositionReq(ctidTraderAccountId=self.account_id, positionId=ticket, volume=int(cents)), self._fill_or_reject, timeout=self.timeout * 2)
        lot = 10_000_000.0
        if msg.HasField("position") and msg.position.tradeData.symbolId in self._details:
            lot = float(self._details[msg.position.tradeData.symbolId][1].lotSize or lot)
        return self._result(msg, lot)

    async def position_modify(self, ticket: int, sl: float | None, tp: float | None) -> dict:
        await self._ensure()
        req = oa.ProtoOAAmendPositionSLTPReq(ctidTraderAccountId=self.account_id, positionId=int(ticket))
        if sl:
            req.stopLoss = float(sl)
        if tp:
            req.takeProfit = float(tp)
        try:
            _, msg = await self._call(req, lambda t, m: t == EXECUTION_EVENT and m.executionType != EX_ACCEPTED)
        except CTraderError as exc:
            return {"ok": False, "retcode": exc.code, "comment": exc.message}
        if msg.executionType in (EX_REJECTED, EX_CANCELLED):
            code = msg.errorCode or "ORDER_REJECTED"
            return {"ok": False, "retcode": code, "comment": error_text(code)}
        return {"ok": True, "retcode": 10009, "comment": "ok"}


# ---------------------------------------------------------------- conta (OAuth)
async def list_accounts(client_id: str, client_secret: str, access_token: str, *, host: str | None = None, port: int = PORT, use_ssl: bool = True) -> list[dict]:
    """Contas que o token libera (demo e reais), para o dono escolher qual o Meta-Bot opera."""
    probe = CTraderClient(environment="demo", client_id=client_id, client_secret=client_secret, access_token=access_token, account_id=0, host=host, port=port, use_ssl=use_ssl)
    try:
        try:
            probe._reader, probe._writer = await asyncio.wait_for(
                asyncio.open_connection(probe.host, port, ssl=ssl.create_default_context() if use_ssl else None, server_hostname=probe.host if use_ssl else None), 20
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise MT5Unavailable(f"não foi possível conectar à cTrader ({exc.__class__.__name__})") from exc
        probe._tasks = [asyncio.create_task(probe._read_loop())]
        await probe._call(oa.ProtoOAApplicationAuthReq(clientId=client_id, clientSecret=client_secret))
        _, res = await probe._call(oa.ProtoOAGetAccountListByAccessTokenReq(accessToken=access_token))
        if int(res.permissionScope or 0) == 0 and res.HasField("permissionScope"):
            log.info("token da cTrader só com permissão de leitura")
        return [
            {
                "account_id": int(a.ctidTraderAccountId),
                "login": int(a.traderLogin or 0),
                "live": bool(a.isLive),
                "broker": a.brokerTitleShort or "",
                "can_trade": not (res.HasField("permissionScope") and int(res.permissionScope) == 0),
            }
            for a in res.ctidTraderAccount
        ]
    finally:
        await probe.aclose()


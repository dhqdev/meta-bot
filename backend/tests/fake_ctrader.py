"""Servidor falso da cTrader Open API (protobuf sobre TCP, sem SSL) para testar o cliente sem internet."""

from __future__ import annotations

import asyncio
import struct
import time

from app.broker.ctrader_proto import OpenApiCommonMessages_pb2 as common
from app.broker.ctrader_proto import OpenApiMessages_pb2 as oa
from app.broker.ctrader_proto import OpenApiModelMessages_pb2 as model

ACCOUNT_ID = 4242
CLIENT_ID, CLIENT_SECRET = "app-id", "app-secret"
LOT = 10_000_000  # 1 lote = 100.000 unidades (em centésimos)
EUR, USD, JPY = 1, 2, 3
SYMBOLS = {
    1: {"name": "EURUSD", "base": EUR, "quote": USD, "digits": 5, "bid": 1.10000, "ask": 1.10012},
    2: {"name": "USDJPY", "base": USD, "quote": JPY, "digits": 3, "bid": 150.000, "ask": 150.012},
}


def _type(cls) -> int:
    return int(cls.DESCRIPTOR.fields_by_name["payloadType"].default_value)


TYPES = {}
for _name, _desc in oa.DESCRIPTOR.message_types_by_name.items():
    _f = _desc.fields_by_name.get("payloadType")
    if _f is not None and _f.has_default_value:
        TYPES[int(_f.default_value)] = getattr(oa, _name)


class FakeCTrader:
    def __init__(self):
        self.access_token = "token-1"
        self.refresh_token = "refresh-1"
        self.expired = False  # o próximo AccountAuth com o token atual falha (força a renovação)
        self.positions: dict[int, model.ProtoOAPosition] = {}
        self.deals: list[model.ProtoOADeal] = []
        self.next_id = 5000
        self.slippage = 0.0
        self.reject_amend = False
        self.requests: list[tuple[int, object]] = []
        self.server: asyncio.base_events.Server | None = None
        self.port = 0
        self.writers: list[asyncio.StreamWriter] = []

    async def __aenter__(self) -> "FakeCTrader":
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        for w in self.writers:
            w.close()
        self.server.close()
        await self.server.wait_closed()

    @property
    def endpoint(self) -> str:
        return f"127.0.0.1:{self.port}"

    # ------------------------------------------------------------ helpers
    def _nid(self) -> int:
        self.next_id += 1
        return self.next_id

    async def _send(self, writer, msg, mid: str = "") -> None:
        wrapper = common.ProtoMessage(payloadType=_type(type(msg)), payload=msg.SerializeToString())
        if mid:
            wrapper.clientMsgId = mid
        raw = wrapper.SerializeToString()
        writer.write(struct.pack(">I", len(raw)) + raw)
        await writer.drain()

    async def _error(self, writer, mid: str, code: str, description: str = "") -> None:
        await self._send(writer, oa.ProtoOAErrorRes(ctidTraderAccountId=ACCOUNT_ID, errorCode=code, description=description), mid)

    def close_by_broker(self, position_id: int, price: float) -> None:
        """A corretora fecha a posição (stop ou alvo), como aconteceria sem o Meta-Bot pedir."""
        pos = self.positions.pop(position_id)
        self._closing_deal(pos, price)

    def _closing_deal(self, pos, price: float) -> model.ProtoOADeal:
        d = 1 if pos.tradeData.tradeSide == 1 else -1
        units = pos.tradeData.volume / 100
        gross = round((price - pos.price) * d * units * 100)  # em centavos (moneyDigits = 2)
        now = int(time.time() * 1000)
        deal = model.ProtoOADeal(
            dealId=self._nid(), orderId=self._nid(), positionId=pos.positionId, volume=pos.tradeData.volume, filledVolume=pos.tradeData.volume,
            symbolId=pos.tradeData.symbolId, createTimestamp=now, executionTimestamp=now, executionPrice=price,
            tradeSide=2 if d > 0 else 1, dealStatus=2, commission=-350, moneyDigits=2,
            closePositionDetail=model.ProtoOAClosePositionDetail(entryPrice=pos.price, grossProfit=gross, swap=-40, commission=-700, balance=1_000_000 + gross, moneyDigits=2),
        )
        self.deals.append(deal)
        return deal

    # ------------------------------------------------------------ servidor
    async def _handle(self, reader, writer) -> None:
        self.writers.append(writer)
        try:
            while True:
                head = await reader.readexactly(4)
                body = await reader.readexactly(struct.unpack(">I", head)[0])
                wrapper = common.ProtoMessage()
                wrapper.ParseFromString(body)
                if wrapper.payloadType == 51:
                    continue
                cls = TYPES.get(wrapper.payloadType)
                msg = cls()
                msg.ParseFromString(wrapper.payload)
                self.requests.append((wrapper.payloadType, msg))
                await self._answer(writer, wrapper.clientMsgId, msg)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    async def _answer(self, w, mid: str, msg) -> None:  # noqa: C901  (um caso por mensagem)
        name = type(msg).__name__
        if name == "ProtoOAApplicationAuthReq":
            if (msg.clientId, msg.clientSecret) != (CLIENT_ID, CLIENT_SECRET):
                return await self._error(w, mid, "CH_CLIENT_AUTH_FAILURE")
            return await self._send(w, oa.ProtoOAApplicationAuthRes(), mid)
        if name == "ProtoOAAccountAuthReq":
            if self.expired or msg.accessToken != self.access_token:
                return await self._error(w, mid, "CH_ACCESS_TOKEN_INVALID", "token expirado")
            return await self._send(w, oa.ProtoOAAccountAuthRes(ctidTraderAccountId=ACCOUNT_ID), mid)
        if name == "ProtoOARefreshTokenReq":
            if msg.refreshToken != self.refresh_token:
                return await self._error(w, mid, "CH_ACCESS_TOKEN_INVALID")
            self.access_token, self.refresh_token, self.expired = "token-2", "refresh-2", False
            return await self._send(w, oa.ProtoOARefreshTokenRes(accessToken=self.access_token, tokenType="bearer", expiresIn=2628000, refreshToken=self.refresh_token), mid)
        if name == "ProtoOAGetAccountListByAccessTokenReq":
            accounts = [model.ProtoOACtidTraderAccount(ctidTraderAccountId=ACCOUNT_ID, isLive=False, traderLogin=777001, brokerTitleShort="Pepperstone")]
            return await self._send(w, oa.ProtoOAGetAccountListByAccessTokenRes(accessToken=msg.accessToken, permissionScope=1, ctidTraderAccount=accounts), mid)
        if name == "ProtoOATraderReq":
            trader = model.ProtoOATrader(ctidTraderAccountId=ACCOUNT_ID, balance=1_000_000, depositAssetId=USD, leverageInCents=50000, traderLogin=777001, brokerName="Pepperstone", moneyDigits=2)
            return await self._send(w, oa.ProtoOATraderRes(ctidTraderAccountId=ACCOUNT_ID, trader=trader), mid)
        if name == "ProtoOAAssetListReq":
            assets = [model.ProtoOAAsset(assetId=i, name=n) for i, n in ((EUR, "EUR"), (USD, "USD"), (JPY, "JPY"))]
            return await self._send(w, oa.ProtoOAAssetListRes(ctidTraderAccountId=ACCOUNT_ID, asset=assets), mid)
        if name == "ProtoOASymbolsListReq":
            syms = [model.ProtoOALightSymbol(symbolId=i, symbolName=s["name"], enabled=True, baseAssetId=s["base"], quoteAssetId=s["quote"], description=s["name"]) for i, s in SYMBOLS.items()]
            return await self._send(w, oa.ProtoOASymbolsListRes(ctidTraderAccountId=ACCOUNT_ID, symbol=syms), mid)
        if name == "ProtoOASymbolByIdReq":
            out = [model.ProtoOASymbol(symbolId=i, digits=SYMBOLS[i]["digits"], pipPosition=SYMBOLS[i]["digits"] - 1, lotSize=LOT, minVolume=100_000, maxVolume=1_000_000_000, stepVolume=100_000, slDistance=0) for i in msg.symbolId if i in SYMBOLS]
            return await self._send(w, oa.ProtoOASymbolByIdRes(ctidTraderAccountId=ACCOUNT_ID, symbol=out), mid)
        if name == "ProtoOASymbolsForConversionReq":
            chain = [model.ProtoOALightSymbol(symbolId=2, symbolName="USDJPY", baseAssetId=USD, quoteAssetId=JPY)] if msg.firstAssetId == JPY else []
            return await self._send(w, oa.ProtoOASymbolsForConversionRes(ctidTraderAccountId=ACCOUNT_ID, symbol=chain), mid)
        if name == "ProtoOASubscribeSpotsReq":
            await self._send(w, oa.ProtoOASubscribeSpotsRes(ctidTraderAccountId=ACCOUNT_ID), mid)
            for i in msg.symbolId:
                s = SYMBOLS[i]
                await self._send(w, oa.ProtoOASpotEvent(ctidTraderAccountId=ACCOUNT_ID, symbolId=i, bid=round(s["bid"] * 100000), ask=round(s["ask"] * 100000), timestamp=int(time.time() * 1000)))
            return None
        if name == "ProtoOAGetTrendbarsReq":
            step = {9: 60, 5: 5, 1: 1}.get(msg.period, 60)
            end = msg.toTimestamp // 60000
            bars = []
            minute = end - end % step
            while minute * 60000 >= msg.fromTimestamp and len(bars) < min(msg.count or 500, 500):
                low = 110000 + (minute // step) % 50
                bars.append(model.ProtoOATrendbar(volume=100, period=msg.period, low=low, deltaOpen=3, deltaClose=7, deltaHigh=12, utcTimestampInMinutes=minute))
                minute -= step
            return await self._send(w, oa.ProtoOAGetTrendbarsRes(ctidTraderAccountId=ACCOUNT_ID, period=msg.period, symbolId=msg.symbolId, trendbar=list(reversed(bars))), mid)
        if name == "ProtoOAGetPositionUnrealizedPnLReq":
            pnl = [model.ProtoOAPositionUnrealizedPnL(positionId=p, grossUnrealizedPnL=-150, netUnrealizedPnL=-200) for p in self.positions]
            return await self._send(w, oa.ProtoOAGetPositionUnrealizedPnLRes(ctidTraderAccountId=ACCOUNT_ID, positionUnrealizedPnL=pnl, moneyDigits=2), mid)
        if name == "ProtoOAReconcileReq":
            return await self._send(w, oa.ProtoOAReconcileRes(ctidTraderAccountId=ACCOUNT_ID, position=list(self.positions.values())), mid)
        if name == "ProtoOADealListByPositionIdReq":
            deals = [d for d in self.deals if d.positionId == msg.positionId]
            return await self._send(w, oa.ProtoOADealListByPositionIdRes(ctidTraderAccountId=ACCOUNT_ID, deal=deals, hasMore=False), mid)
        if name == "ProtoOANewOrderReq":
            return await self._new_order(w, mid, msg)
        if name == "ProtoOAClosePositionReq":
            pos = self.positions.get(msg.positionId)
            if pos is None:
                return await self._error(w, mid, "POSITION_NOT_FOUND")
            s = SYMBOLS[pos.tradeData.symbolId]
            price = s["bid"] if pos.tradeData.tradeSide == 1 else s["ask"]
            await self._send(w, oa.ProtoOAExecutionEvent(ctidTraderAccountId=ACCOUNT_ID, executionType=2, position=pos), mid)
            del self.positions[msg.positionId]
            deal = self._closing_deal(pos, price)
            closed = model.ProtoOAPosition()
            closed.CopyFrom(pos)
            closed.positionStatus = 2
            return await self._send(w, oa.ProtoOAExecutionEvent(ctidTraderAccountId=ACCOUNT_ID, executionType=3, position=closed, deal=deal), mid)
        if name == "ProtoOAAmendPositionSLTPReq":
            pos = self.positions.get(msg.positionId)
            if pos is None:
                return await self._error(w, mid, "POSITION_NOT_FOUND")
            if self.reject_amend:
                return await self._send(w, oa.ProtoOAOrderErrorEvent(ctidTraderAccountId=ACCOUNT_ID, errorCode="TRADING_BAD_STOPS", positionId=pos.positionId), mid)
            if msg.HasField("stopLoss"):
                pos.stopLoss = msg.stopLoss
            if msg.HasField("takeProfit"):
                pos.takeProfit = msg.takeProfit
            return await self._send(w, oa.ProtoOAExecutionEvent(ctidTraderAccountId=ACCOUNT_ID, executionType=4, position=pos), mid)
        return await self._error(w, mid, "UNSUPPORTED_MESSAGE")

    async def _new_order(self, w, mid: str, msg) -> None:
        s = SYMBOLS.get(msg.symbolId)
        if s is None:
            return await self._error(w, mid, "SYMBOL_NOT_FOUND")
        if msg.HasField("stopLoss") or msg.HasField("takeProfit"):
            return await self._error(w, mid, "INVALID_REQUEST", "SL/TP absoluto não é aceito em ordem a mercado")
        d = 1 if msg.tradeSide == 1 else -1
        price = round((s["ask"] if d > 0 else s["bid"]) + d * self.slippage, s["digits"])
        pid = self._nid()
        pos = model.ProtoOAPosition(
            positionId=pid, positionStatus=1, swap=0, price=price, moneyDigits=2,
            tradeData=model.ProtoOATradeData(symbolId=msg.symbolId, volume=msg.volume, tradeSide=msg.tradeSide, openTimestamp=int(time.time() * 1000), label=msg.label, comment=msg.comment),
        )
        if msg.HasField("relativeStopLoss"):
            pos.stopLoss = round(price - d * msg.relativeStopLoss / 100000, s["digits"])
        if msg.HasField("relativeTakeProfit"):
            pos.takeProfit = round(price + d * msg.relativeTakeProfit / 100000, s["digits"])
        await self._send(w, oa.ProtoOAExecutionEvent(ctidTraderAccountId=ACCOUNT_ID, executionType=2, position=pos), mid)
        self.positions[pid] = pos
        now = int(time.time() * 1000)
        deal = model.ProtoOADeal(dealId=self._nid(), orderId=self._nid(), positionId=pid, volume=msg.volume, filledVolume=msg.volume, symbolId=msg.symbolId, createTimestamp=now, executionTimestamp=now, executionPrice=price, tradeSide=msg.tradeSide, dealStatus=2, commission=-350, moneyDigits=2)
        self.deals.append(deal)
        await self._send(w, oa.ProtoOAExecutionEvent(ctidTraderAccountId=ACCOUNT_ID, executionType=3, position=pos, deal=deal), mid)

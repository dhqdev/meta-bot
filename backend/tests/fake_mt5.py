"""Imitação do pacote MetaTrader5 para testar o bridge sem Windows/Wine."""

from __future__ import annotations

import time
from collections import namedtuple

import numpy as np

TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_M30 = 1, 5, 15, 30
TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 16385, 16388, 16408
TRADE_ACTION_DEAL, TRADE_ACTION_SLTP, TRADE_ACTION_PENDING, TRADE_ACTION_REMOVE = 1, 6, 5, 8
ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
ORDER_TIME_GTC = 0
POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
TRADE_RETCODE_DONE, TRADE_RETCODE_INVALID_FILL = 10009, 10030

TerminalInfo = namedtuple("TerminalInfo", "connected trade_allowed name build")
AccountInfo = namedtuple("AccountInfo", "login server company currency balance equity margin_free profit leverage trade_allowed")
SymbolInfo = namedtuple(
    "SymbolInfo",
    "name description path visible digits point trade_tick_size trade_tick_value trade_tick_value_loss trade_contract_size volume_min volume_max volume_step spread trade_stops_level filling_mode currency_base currency_profit trade_mode",
)
Tick = namedtuple("Tick", "time bid ask last volume")
Position = namedtuple("Position", "ticket symbol type volume price_open sl tp magic profit comment")
OrderResult = namedtuple("OrderResult", "retcode deal order volume price comment request")
Deal = namedtuple("Deal", "ticket order time time_msc type entry magic position_id reason volume price commission swap profit fee symbol comment")

RATES_DTYPE = np.dtype([("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"), ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")])


class State:
    def __init__(self):
        self.initialized = False
        self.login = 0
        self.positions: dict[int, Position] = {}
        self.deals: list[Deal] = []
        self.next_ticket = 1000
        self.orders_seen: list[dict] = []
        self.reject_fok = True  # simula corretora que não aceita FOK


state = State()


def reset():
    global state
    state = State()


def initialize(**kwargs):
    state.initialized = True
    if kwargs.get("login"):
        state.login = int(kwargs["login"])
    return True


def shutdown():
    state.initialized = False


def last_error():
    return (1, "Success")


def version():
    return (500, 4755, "01 Jan 2026")


def terminal_info():
    if not state.initialized:
        return None
    return TerminalInfo(True, True, "Fake MT5", 4755)


def account_info():
    if not state.initialized or not state.login:
        return None
    return AccountInfo(state.login, "Fake-Demo", "Fake Broker", "USD", 10000.0, 10000.0, 9000.0, 0.0, 100, True)


def login(login, password="", server="", timeout=60000):
    if password == "certa" and server == "Fake-Demo":
        state.login = int(login)
        return True
    return False


_SYMBOLS = {
    "EURUSD": SymbolInfo("EURUSD", "Euro vs Dollar", "Forex\\EURUSD", True, 5, 0.00001, 0.00001, 1.0, 1.0, 100000, 0.01, 100, 0.01, 12, 0, 3, "EUR", "USD", 4),
    "WIN$N": SymbolInfo("WIN$N", "Mini Índice", "BMF\\WIN$N", True, 0, 1.0, 5.0, 1.0, 1.0, 1, 1, 100, 1, 5, 0, 0, "BRL", "BRL", 4),
}


def symbols_get(group=None):
    return list(_SYMBOLS.values())


def symbol_info(name):
    return _SYMBOLS.get(name)


def symbol_select(name, enable=True):
    return name in _SYMBOLS


def symbol_info_tick(name):
    if name not in _SYMBOLS:
        return None
    base = 1.1000 if name == "EURUSD" else 128000.0
    step = 0.00012 if name == "EURUSD" else 5.0
    return Tick(int(time.time()) + 3 * 3600, base, base + step, base, 1)


def copy_rates_from_pos(symbol, timeframe, start, count):
    if symbol not in _SYMBOLS:
        return None
    arr = np.zeros(count, dtype=RATES_DTYPE)
    t0 = 1_700_000_000
    step = {TIMEFRAME_H1: 3600, TIMEFRAME_M15: 900}.get(timeframe, 60)
    price = 1.1 + np.cumsum(np.sin(np.arange(count) / 7.0) * 0.0005)
    arr["time"] = t0 + np.arange(count) * step
    arr["open"] = price
    arr["close"] = price + 0.0002
    arr["high"] = price + 0.0005
    arr["low"] = price - 0.0005
    arr["tick_volume"] = 100
    arr["spread"] = 12
    return arr


def copy_rates_range(symbol, timeframe, date_from, date_to):
    return copy_rates_from_pos(symbol, timeframe, 0, 10)


def positions_get(symbol=None, ticket=None):
    items = list(state.positions.values())
    if ticket is not None:
        items = [p for p in items if p.ticket == ticket]
    if symbol:
        items = [p for p in items if p.symbol == symbol]
    return tuple(items)


def orders_get(symbol=None):
    return ()


def history_deals_get(*args, position=None, **kwargs):
    if position is not None:
        return tuple(d for d in state.deals if d.position_id == position)
    return tuple(state.deals)


def order_check(request):
    return OrderResult(0, 0, 0, request.get("volume", 0), request.get("price", 0), "Done", request)


def order_send(request):
    state.orders_seen.append(dict(request))
    if request.get("type_filling") == ORDER_FILLING_FOK and state.reject_fok:
        return OrderResult(TRADE_RETCODE_INVALID_FILL, 0, 0, 0, 0, "Unsupported filling mode", request)
    action = request["action"]
    if action == TRADE_ACTION_DEAL:
        if request.get("position"):
            pos = state.positions.pop(int(request["position"]))
            profit = (request["price"] - pos.price_open) * (1 if pos.type == POSITION_TYPE_BUY else -1) * 100000 * pos.volume
            state.deals.append(Deal(len(state.deals) + 1, 0, int(time.time()), int(time.time() * 1000), 1, 1, pos.magic, pos.ticket, 3, pos.volume, request["price"], -3.5, 0.0, profit, 0.0, pos.symbol, ""))
            return OrderResult(TRADE_RETCODE_DONE, 1, pos.ticket, pos.volume, request["price"], "Request executed", request)
        state.next_ticket += 1
        ticket = state.next_ticket
        ptype = POSITION_TYPE_BUY if request["type"] == ORDER_TYPE_BUY else POSITION_TYPE_SELL
        state.positions[ticket] = Position(ticket, request["symbol"], ptype, request["volume"], request["price"], request.get("sl", 0.0), request.get("tp", 0.0), request.get("magic", 0), 0.0, request.get("comment", ""))
        state.deals.append(Deal(len(state.deals) + 1, ticket, int(time.time()), int(time.time() * 1000), request["type"], 0, request.get("magic", 0), ticket, 3, request["volume"], request["price"], -3.5, 0.0, 0.0, 0.0, request["symbol"], ""))
        return OrderResult(TRADE_RETCODE_DONE, 1, ticket, request["volume"], request["price"], "Request executed", request)
    if action == TRADE_ACTION_SLTP:
        pos = state.positions[int(request["position"])]
        state.positions[pos.ticket] = pos._replace(sl=request["sl"], tp=request["tp"])
        return OrderResult(TRADE_RETCODE_DONE, 0, 0, 0, 0, "Request executed", request)
    return OrderResult(10013, 0, 0, 0, 0, "Invalid request", request)

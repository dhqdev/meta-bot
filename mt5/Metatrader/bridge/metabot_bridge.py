"""Meta-Bot MT5 Bridge.

API HTTP/JSON sobre o pacote oficial ``MetaTrader5`` do Python. Roda ao lado do
terminal MetaTrader 5, num Windows (``mt5/windows/iniciar-bridge.bat``) ou dentro do
Wine no container ``mt5``, e deixa o backend do Meta-Bot consultar cotações,
histórico e enviar ordens para qualquer corretora em que o terminal estiver logado.

Só usa a biblioteca padrão (compatível com Python 3.9, que é o do Wine) para
não depender de nada além do próprio ``MetaTrader5``.

Segurança: toda rota, exceto ``/ping``, exige ``Authorization: Bearer <token>``
com o valor de ``MT5_BRIDGE_TOKEN``. Sem token configurado, o bridge recusa as
requisições. A porta deve ficar só numa rede privada (rede interna do Docker ou Tailscale).
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

BRIDGE_VERSION = "1.0.0"
MAX_BODY_BYTES = 64 * 1024

log = logging.getLogger("metabot.bridge")

TIMEFRAME_NAMES = [
    "M1", "M2", "M3", "M4", "M5", "M6", "M10", "M12", "M15", "M20", "M30",
    "H1", "H2", "H3", "H4", "H6", "H8", "H12", "D1", "W1", "MN1",
]

# Valores numéricos oficiais, usados quando o módulo não expõe a constante.
_DEFAULT_CONSTANTS = {
    "TRADE_ACTION_DEAL": 1,
    "TRADE_ACTION_PENDING": 5,
    "TRADE_ACTION_SLTP": 6,
    "TRADE_ACTION_MODIFY": 7,
    "TRADE_ACTION_REMOVE": 8,
    "ORDER_TYPE_BUY": 0,
    "ORDER_TYPE_SELL": 1,
    "ORDER_TYPE_BUY_LIMIT": 2,
    "ORDER_TYPE_SELL_LIMIT": 3,
    "ORDER_TYPE_BUY_STOP": 4,
    "ORDER_TYPE_SELL_STOP": 5,
    "ORDER_FILLING_FOK": 0,
    "ORDER_FILLING_IOC": 1,
    "ORDER_FILLING_RETURN": 2,
    "ORDER_TIME_GTC": 0,
    "ORDER_TIME_SPECIFIED": 2,
    "POSITION_TYPE_BUY": 0,
    "POSITION_TYPE_SELL": 1,
    "TRADE_RETCODE_DONE": 10009,
    "TRADE_RETCODE_PLACED": 10008,
    "TRADE_RETCODE_DONE_PARTIAL": 10010,
    "TRADE_RETCODE_INVALID_FILL": 10030,
}

_PENDING_TYPES = {
    "buy_limit": "ORDER_TYPE_BUY_LIMIT",
    "sell_limit": "ORDER_TYPE_SELL_LIMIT",
    "buy_stop": "ORDER_TYPE_BUY_STOP",
    "sell_stop": "ORDER_TYPE_SELL_STOP",
}

_FILLING_NAMES = {
    "fok": "ORDER_FILLING_FOK",
    "ioc": "ORDER_FILLING_IOC",
    "return": "ORDER_FILLING_RETURN",
}


class BridgeError(Exception):
    def __init__(self, status: int, message: str, extra: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra or {}


def to_jsonable(obj: Any) -> Any:
    """Converte namedtuples, arrays estruturados do numpy e escalares em JSON."""
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return obj if obj == obj and obj not in (float("inf"), float("-inf")) else None
    if hasattr(obj, "_asdict"):
        return {k: to_jsonable(v) for k, v in obj._asdict().items()}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    dtype = getattr(obj, "dtype", None)
    if dtype is not None and getattr(dtype, "names", None):
        return [[to_jsonable(v) for v in row] for row in obj.tolist()]
    if hasattr(obj, "item") and not isinstance(obj, (list, tuple)):
        try:
            return to_jsonable(obj.item())
        except Exception:  # pragma: no cover - defensivo
            pass
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, bytes):
        return obj.decode("utf-8", "replace")
    return str(obj)


class Bridge:
    """Envolve o módulo MetaTrader5 com um lock (a lib não é thread-safe)."""

    def __init__(
        self,
        mt5: Any,
        terminal_path: Optional[str] = None,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        init_timeout_ms: int = 60000,
    ):
        self.mt5 = mt5
        self.terminal_path = terminal_path or None
        self.default_login = login
        self.default_password = password
        self.default_server = server
        self.init_timeout_ms = init_timeout_ms
        self.lock = threading.RLock()
        self.initialized = False
        self.last_init_attempt = 0.0
        self.last_error: Optional[Any] = None

    # ------------------------------------------------------------------ util
    def const(self, name: str) -> int:
        value = getattr(self.mt5, name, None)
        if value is None:
            value = _DEFAULT_CONSTANTS[name]
        return int(value)

    def timeframe(self, name: str) -> int:
        key = (name or "").upper()
        if key not in TIMEFRAME_NAMES:
            raise BridgeError(400, "timeframe inválido: %s" % name)
        value = getattr(self.mt5, "TIMEFRAME_" + key, None)
        if value is None:
            raise BridgeError(400, "timeframe não suportado pelo terminal: %s" % name)
        return int(value)

    def _error(self) -> Any:
        try:
            return to_jsonable(self.mt5.last_error())
        except Exception:  # pragma: no cover
            return None

    def _init_kwargs(self, login=None, password=None, server=None, path=None, timeout=None) -> dict:
        kwargs: Dict[str, Any] = {}
        path = path or self.terminal_path
        if path:
            kwargs["path"] = path
        login = login if login is not None else self.default_login
        if login:
            kwargs["login"] = int(login)
            pwd = password if password is not None else self.default_password
            srv = server if server is not None else self.default_server
            if pwd:
                kwargs["password"] = str(pwd)
            if srv:
                kwargs["server"] = str(srv)
        kwargs["timeout"] = int(timeout or self.init_timeout_ms)
        return kwargs

    def ensure(self) -> None:
        """Garante o terminal conectado; reinicializa se preciso."""
        with self.lock:
            if self.initialized:
                try:
                    if self.mt5.terminal_info() is not None:
                        return
                except Exception:
                    pass
                self.initialized = False
            # Evita martelar o terminal enquanto ele instala/abre.
            now = time.time()
            if now - self.last_init_attempt < 3:
                raise BridgeError(503, "terminal MT5 indisponível", {"last_error": self.last_error})
            self.last_init_attempt = now
            ok = bool(self.mt5.initialize(**self._init_kwargs()))
            self.initialized = ok
            if not ok:
                self.last_error = self._error()
                raise BridgeError(503, "falha ao inicializar o MT5", {"last_error": self.last_error})
            self.last_error = None

    # -------------------------------------------------------------- consultas
    def health(self) -> dict:
        with self.lock:
            try:
                self.ensure()
            except BridgeError as exc:
                return {
                    "ok": True,
                    "bridge_version": BRIDGE_VERSION,
                    "initialized": False,
                    "connected": False,
                    "error": exc.message,
                    "last_error": exc.extra.get("last_error"),
                }
            terminal = to_jsonable(self.mt5.terminal_info())
            account = to_jsonable(self.mt5.account_info())
            version = to_jsonable(self.mt5.version())
            return {
                "ok": True,
                "bridge_version": BRIDGE_VERSION,
                "initialized": True,
                "connected": bool(terminal and terminal.get("connected")),
                "trade_allowed": bool(terminal and terminal.get("trade_allowed")),
                "terminal": terminal,
                "account": account,
                "version": version,
                "server_time_hint": self._server_time_hint(),
            }

    def _server_time_hint(self) -> Optional[dict]:
        """Hora do último tick de um símbolo visível, para estimar o fuso do servidor."""
        try:
            symbols = self.mt5.symbols_get() or []
        except Exception:
            return None
        best = None
        for s in symbols[:200]:
            if not getattr(s, "visible", False):
                continue
            tick = self.mt5.symbol_info_tick(s.name)
            if tick is None or not getattr(tick, "time", 0):
                continue
            if best is None or tick.time > best["tick_time"]:
                best = {"symbol": s.name, "tick_time": int(tick.time)}
        if best is not None:
            best["utc_now"] = int(time.time())
        return best

    def initialize(self, body: dict) -> dict:
        with self.lock:
            try:
                self.mt5.shutdown()
            except Exception:
                pass
            self.initialized = False
            kwargs = self._init_kwargs(
                body.get("login"), body.get("password"), body.get("server"),
                body.get("path"), body.get("timeout"),
            )
            ok = bool(self.mt5.initialize(**kwargs))
            self.initialized = ok
            self.last_init_attempt = time.time()
            if not ok:
                self.last_error = self._error()
                raise BridgeError(502, "falha ao inicializar o MT5", {"last_error": self.last_error})
            if body.get("login"):
                self.default_login = int(body["login"])
                self.default_password = body.get("password") or self.default_password
                self.default_server = body.get("server") or self.default_server
            return {"ok": True, "account": to_jsonable(self.mt5.account_info())}

    def login(self, body: dict) -> dict:
        try:
            login = int(body["login"])
        except (KeyError, TypeError, ValueError):
            raise BridgeError(400, "login (número da conta) é obrigatório")
        password = str(body.get("password") or "")
        server = str(body.get("server") or "")
        if not password or not server:
            raise BridgeError(400, "password e server são obrigatórios")
        timeout = int(body.get("timeout") or self.init_timeout_ms)
        with self.lock:
            self.ensure()
            ok = bool(self.mt5.login(login, password=password, server=server, timeout=timeout))
            if not ok:
                raise BridgeError(
                    502,
                    "login recusado pela corretora (confira número, senha e servidor)",
                    {"last_error": self._error()},
                )
            self.default_login, self.default_password, self.default_server = login, password, server
            return {"ok": True, "account": to_jsonable(self.mt5.account_info())}

    def account(self) -> dict:
        with self.lock:
            self.ensure()
            info = self.mt5.account_info()
            if info is None:
                raise BridgeError(409, "terminal sem conta logada", {"last_error": self._error()})
            return to_jsonable(info)

    def terminal(self) -> dict:
        with self.lock:
            self.ensure()
            return to_jsonable(self.mt5.terminal_info())

    def symbols(self, q: str = "", group: str = "", limit: int = 500) -> List[dict]:
        with self.lock:
            self.ensure()
            items = self.mt5.symbols_get(group=group) if group else self.mt5.symbols_get()
            out = []
            ql = q.lower()
            for s in items or []:
                name = getattr(s, "name", "")
                desc = getattr(s, "description", "")
                if ql and ql not in name.lower() and ql not in desc.lower():
                    continue
                out.append({
                    "name": name,
                    "description": desc,
                    "path": getattr(s, "path", ""),
                    "visible": bool(getattr(s, "visible", False)),
                    "digits": getattr(s, "digits", None),
                    "currency_base": getattr(s, "currency_base", ""),
                    "currency_profit": getattr(s, "currency_profit", ""),
                })
                if len(out) >= limit:
                    break
            return out

    def symbol(self, name: str) -> dict:
        if not name:
            raise BridgeError(400, "symbol é obrigatório")
        with self.lock:
            self.ensure()
            self.mt5.symbol_select(name, True)
            info = self.mt5.symbol_info(name)
            if info is None:
                raise BridgeError(404, "símbolo não encontrado nesta corretora: %s" % name)
            return to_jsonable(info)

    def symbol_select(self, body: dict) -> dict:
        name = str(body.get("name") or "")
        enable = bool(body.get("enable", True))
        with self.lock:
            self.ensure()
            ok = bool(self.mt5.symbol_select(name, enable))
            return {"ok": ok}

    def tick(self, symbol: str) -> dict:
        with self.lock:
            self.ensure()
            self.mt5.symbol_select(symbol, True)
            tick = self.mt5.symbol_info_tick(symbol)
            if tick is None:
                raise BridgeError(404, "sem cotação para %s" % symbol, {"last_error": self._error()})
            return to_jsonable(tick)

    def rates(self, symbol: str, timeframe: str, count: int, start: int = 0) -> dict:
        count = max(1, min(int(count), 100000))
        tf = self.timeframe(timeframe)
        with self.lock:
            self.ensure()
            self.mt5.symbol_select(symbol, True)
            data = self.mt5.copy_rates_from_pos(symbol, tf, int(start), count)
            if data is None:
                raise BridgeError(404, "sem histórico para %s %s" % (symbol, timeframe), {"last_error": self._error()})
            return self._rates_payload(symbol, timeframe, data)

    def rates_range(self, symbol: str, timeframe: str, date_from: int, date_to: int) -> dict:
        import datetime as _dt

        tf = self.timeframe(timeframe)
        d_from = _dt.datetime.fromtimestamp(int(date_from), tz=_dt.timezone.utc)
        d_to = _dt.datetime.fromtimestamp(int(date_to), tz=_dt.timezone.utc)
        with self.lock:
            self.ensure()
            self.mt5.symbol_select(symbol, True)
            data = self.mt5.copy_rates_range(symbol, tf, d_from, d_to)
            if data is None:
                raise BridgeError(404, "sem histórico para %s %s" % (symbol, timeframe), {"last_error": self._error()})
            return self._rates_payload(symbol, timeframe, data)

    @staticmethod
    def _rates_payload(symbol: str, timeframe: str, data: Any) -> dict:
        rows = to_jsonable(data)
        if rows and isinstance(rows[0], dict):
            keys = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
            rows = [[r.get(k) for k in keys] for r in rows]
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "fields": ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"],
            "rows": rows or [],
        }

    def positions(self, symbol: str = "", magic: Optional[int] = None) -> List[dict]:
        with self.lock:
            self.ensure()
            items = self.mt5.positions_get(symbol=symbol) if symbol else self.mt5.positions_get()
            out = [to_jsonable(p) for p in (items or [])]
        if magic is not None:
            out = [p for p in out if int(p.get("magic", -1)) == int(magic)]
        return out

    def orders(self, symbol: str = "") -> List[dict]:
        with self.lock:
            self.ensure()
            items = self.mt5.orders_get(symbol=symbol) if symbol else self.mt5.orders_get()
            return [to_jsonable(o) for o in (items or [])]

    def history_deals(self, date_from: int, date_to: int, position: Optional[int] = None) -> List[dict]:
        import datetime as _dt

        with self.lock:
            self.ensure()
            if position:
                items = self.mt5.history_deals_get(position=int(position))
            else:
                d_from = _dt.datetime.fromtimestamp(int(date_from), tz=_dt.timezone.utc)
                d_to = _dt.datetime.fromtimestamp(int(date_to), tz=_dt.timezone.utc)
                items = self.mt5.history_deals_get(d_from, d_to)
            return [to_jsonable(d) for d in (items or [])]

    # ----------------------------------------------------------------- ordens
    def _filling_candidates(self, symbol_info: Any, requested: Optional[str]) -> List[int]:
        if requested and requested != "auto":
            name = _FILLING_NAMES.get(str(requested).lower())
            if not name:
                raise BridgeError(400, "filling inválido: %s" % requested)
            return [self.const(name)]
        flags = int(getattr(symbol_info, "filling_mode", 0) or 0)
        out = []
        if flags & 1:
            out.append(self.const("ORDER_FILLING_FOK"))
        if flags & 2:
            out.append(self.const("ORDER_FILLING_IOC"))
        out.append(self.const("ORDER_FILLING_RETURN"))
        # Sem duplicatas, na ordem de preferência.
        seen: List[int] = []
        for f in out:
            if f not in seen:
                seen.append(f)
        return seen

    def build_request(self, body: dict) -> Tuple[dict, List[int]]:
        action = str(body.get("action") or "deal").lower()
        symbol = str(body.get("symbol") or "")
        if action in ("deal", "pending") and not symbol:
            raise BridgeError(400, "symbol é obrigatório")
        info = self.mt5.symbol_info(symbol) if symbol else None
        if symbol and info is None:
            self.mt5.symbol_select(symbol, True)
            info = self.mt5.symbol_info(symbol)
            if info is None:
                raise BridgeError(404, "símbolo não encontrado nesta corretora: %s" % symbol)
        digits = int(getattr(info, "digits", 5) or 5) if info is not None else 5

        def rnd(value: Any) -> float:
            return round(float(value), digits)

        req: Dict[str, Any] = {}
        fillings: List[int] = []
        if action == "deal":
            side = str(body.get("side") or "").lower()
            if side not in ("buy", "sell"):
                raise BridgeError(400, "side deve ser buy ou sell")
            volume = float(body.get("volume") or 0)
            if volume <= 0:
                raise BridgeError(400, "volume deve ser positivo")
            tick = self.mt5.symbol_info_tick(symbol)
            if tick is None:
                raise BridgeError(409, "sem cotação para %s (mercado fechado?)" % symbol)
            price = tick.ask if side == "buy" else tick.bid
            req = {
                "action": self.const("TRADE_ACTION_DEAL"),
                "symbol": symbol,
                "volume": volume,
                "type": self.const("ORDER_TYPE_BUY" if side == "buy" else "ORDER_TYPE_SELL"),
                "price": rnd(price),
                "deviation": int(body.get("deviation", 20)),
                "magic": int(body.get("magic", 0)),
                "comment": str(body.get("comment", ""))[:31],
                "type_time": self.const("ORDER_TIME_GTC"),
            }
            if body.get("sl"):
                req["sl"] = rnd(body["sl"])
            if body.get("tp"):
                req["tp"] = rnd(body["tp"])
            if body.get("position"):
                req["position"] = int(body["position"])
            fillings = self._filling_candidates(info, body.get("filling"))
        elif action == "pending":
            kind = str(body.get("type") or "").lower()
            if kind not in _PENDING_TYPES:
                raise BridgeError(400, "type deve ser um de %s" % ", ".join(sorted(_PENDING_TYPES)))
            volume = float(body.get("volume") or 0)
            if volume <= 0 or not body.get("price"):
                raise BridgeError(400, "volume e price são obrigatórios")
            req = {
                "action": self.const("TRADE_ACTION_PENDING"),
                "symbol": symbol,
                "volume": volume,
                "type": self.const(_PENDING_TYPES[kind]),
                "price": rnd(body["price"]),
                "magic": int(body.get("magic", 0)),
                "comment": str(body.get("comment", ""))[:31],
                "type_time": self.const("ORDER_TIME_GTC"),
            }
            if body.get("expiration"):
                req["type_time"] = self.const("ORDER_TIME_SPECIFIED")
                req["expiration"] = int(body["expiration"])
            if body.get("sl"):
                req["sl"] = rnd(body["sl"])
            if body.get("tp"):
                req["tp"] = rnd(body["tp"])
            fillings = self._filling_candidates(info, body.get("filling"))
        elif action == "sltp":
            if not body.get("position"):
                raise BridgeError(400, "position (ticket) é obrigatório")
            req = {
                "action": self.const("TRADE_ACTION_SLTP"),
                "position": int(body["position"]),
                "symbol": symbol,
                "sl": rnd(body.get("sl") or 0),
                "tp": rnd(body.get("tp") or 0),
            }
        elif action == "remove":
            if not body.get("order"):
                raise BridgeError(400, "order (ticket) é obrigatório")
            req = {"action": self.const("TRADE_ACTION_REMOVE"), "order": int(body["order"])}
        else:
            raise BridgeError(400, "action inválida: %s" % action)
        return req, fillings

    def _send(self, req: dict, fillings: List[int], check_only: bool) -> dict:
        attempts = fillings or [None]
        last: Optional[dict] = None
        for filling in attempts:
            request = dict(req)
            if filling is not None:
                request["type_filling"] = filling
            fn = self.mt5.order_check if check_only else self.mt5.order_send
            result = fn(request)
            if result is None:
                last = {"ok": False, "retcode": None, "comment": "sem resposta do terminal", "last_error": self._error(), "request": request}
                continue
            data = to_jsonable(result)
            retcode = data.get("retcode")
            if check_only:
                ok = retcode in (0, self.const("TRADE_RETCODE_DONE"))
            else:
                ok = retcode in (
                    self.const("TRADE_RETCODE_DONE"),
                    self.const("TRADE_RETCODE_PLACED"),
                    self.const("TRADE_RETCODE_DONE_PARTIAL"),
                )
            data["ok"] = bool(ok)
            data["request"] = request
            last = data
            if ok or retcode != self.const("TRADE_RETCODE_INVALID_FILL"):
                return data
        return last or {"ok": False, "comment": "nenhuma tentativa"}

    def order_send(self, body: dict, check_only: bool = False) -> dict:
        with self.lock:
            self.ensure()
            req, fillings = self.build_request(body)
            return self._send(req, fillings, check_only)

    def position_close(self, body: dict) -> dict:
        try:
            ticket = int(body["ticket"])
        except (KeyError, TypeError, ValueError):
            raise BridgeError(400, "ticket é obrigatório")
        with self.lock:
            self.ensure()
            items = self.mt5.positions_get(ticket=ticket)
            if not items:
                raise BridgeError(404, "posição %s não encontrada" % ticket)
            pos = items[0]
            volume = float(body.get("volume") or pos.volume)
            side = "sell" if int(pos.type) == self.const("POSITION_TYPE_BUY") else "buy"
            req, fillings = self.build_request({
                "action": "deal",
                "symbol": pos.symbol,
                "side": side,
                "volume": min(volume, float(pos.volume)),
                "position": ticket,
                "deviation": body.get("deviation", 20),
                "magic": getattr(pos, "magic", 0),
                "comment": body.get("comment", "meta-bot close"),
                "filling": body.get("filling"),
            })
            return self._send(req, fillings, False)

    def position_modify(self, body: dict) -> dict:
        try:
            ticket = int(body["ticket"])
        except (KeyError, TypeError, ValueError):
            raise BridgeError(400, "ticket é obrigatório")
        with self.lock:
            self.ensure()
            items = self.mt5.positions_get(ticket=ticket)
            if not items:
                raise BridgeError(404, "posição %s não encontrada" % ticket)
            pos = items[0]
            sl = body.get("sl", pos.sl)
            tp = body.get("tp", pos.tp)
            req, _ = self.build_request({"action": "sltp", "position": ticket, "symbol": pos.symbol, "sl": sl, "tp": tp})
            return self._send(req, [], False)

    def order_cancel(self, body: dict) -> dict:
        with self.lock:
            self.ensure()
            req, _ = self.build_request({"action": "remove", "order": body.get("ticket")})
            return self._send(req, [], False)


# --------------------------------------------------------------------- HTTP
Route = Callable[["Bridge", Dict[str, str], dict], Any]


def _q(query: Dict[str, str], key: str, default: Any = None, cast: Callable = str) -> Any:
    if key not in query or query[key] == "":
        if default is None:
            raise BridgeError(400, "parâmetro obrigatório: %s" % key)
        return default
    try:
        return cast(query[key])
    except (TypeError, ValueError):
        raise BridgeError(400, "parâmetro inválido: %s" % key)


def _opt_int(query: Dict[str, str], key: str) -> Optional[int]:
    value = query.get(key)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        raise BridgeError(400, "parâmetro inválido: %s" % key)


GET_ROUTES: Dict[str, Route] = {
    "/health": lambda b, q, body: b.health(),
    "/account": lambda b, q, body: b.account(),
    "/terminal": lambda b, q, body: b.terminal(),
    "/symbols": lambda b, q, body: b.symbols(q.get("q", ""), q.get("group", ""), _q(q, "limit", 500, int)),
    "/symbol": lambda b, q, body: b.symbol(_q(q, "name")),
    "/tick": lambda b, q, body: b.tick(_q(q, "symbol")),
    "/rates": lambda b, q, body: b.rates(_q(q, "symbol"), _q(q, "timeframe"), _q(q, "count", 1000, int), _q(q, "start", 0, int)),
    "/rates/range": lambda b, q, body: b.rates_range(_q(q, "symbol"), _q(q, "timeframe"), _q(q, "from", cast=int), _q(q, "to", cast=int)),
    "/positions": lambda b, q, body: b.positions(q.get("symbol", ""), _opt_int(q, "magic")),
    "/orders": lambda b, q, body: b.orders(q.get("symbol", "")),
    "/history/deals": lambda b, q, body: b.history_deals(_q(q, "from", 0, int), _q(q, "to", int(time.time()) + 86400, int), _opt_int(q, "position")),
}

POST_ROUTES: Dict[str, Route] = {
    "/initialize": lambda b, q, body: b.initialize(body),
    "/login": lambda b, q, body: b.login(body),
    "/symbol/select": lambda b, q, body: b.symbol_select(body),
    "/order/check": lambda b, q, body: b.order_send(body, check_only=True),
    "/order/send": lambda b, q, body: b.order_send(body),
    "/order/cancel": lambda b, q, body: b.order_cancel(body),
    "/position/close": lambda b, q, body: b.position_close(body),
    "/position/modify": lambda b, q, body: b.position_modify(body),
}


def make_handler(bridge: Bridge, token: str):
    token_bytes = token.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "MetaBotBridge/" + BRIDGE_VERSION
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:  # silencia o log padrão
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _reply(self, status: int, payload: Any) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _authorized(self) -> bool:
            if not token_bytes:
                return False
            header = self.headers.get("Authorization", "")
            supplied = header[7:] if header.lower().startswith("bearer ") else self.headers.get("X-Bridge-Token", "")
            return hmac.compare_digest(supplied.encode("utf-8"), token_bytes)

        def _handle(self, routes: Dict[str, Route], with_body: bool) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path == "/ping":
                self._reply(200, {"ok": True, "bridge_version": BRIDGE_VERSION})
                return
            if not self._authorized():
                self._reply(401, {"ok": False, "error": "token do bridge ausente ou inválido"})
                return
            route = routes.get(path)
            if route is None:
                self._reply(404, {"ok": False, "error": "rota não encontrada"})
                return
            query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
            body: dict = {}
            if with_body:
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY_BYTES:
                    self._reply(413, {"ok": False, "error": "corpo muito grande"})
                    return
                if length:
                    try:
                        body = json.loads(self.rfile.read(length).decode("utf-8"))
                    except ValueError:
                        self._reply(400, {"ok": False, "error": "JSON inválido"})
                        return
                    if not isinstance(body, dict):
                        self._reply(400, {"ok": False, "error": "o corpo deve ser um objeto JSON"})
                        return
            try:
                result = route(bridge, query, body)
            except BridgeError as exc:
                payload = {"ok": False, "error": exc.message}
                payload.update(exc.extra)
                self._reply(exc.status, payload)
                return
            except Exception as exc:  # erro inesperado do terminal
                log.exception("erro na rota %s", path)
                self._reply(500, {"ok": False, "error": "erro interno: %s" % exc.__class__.__name__})
                return
            self._reply(200, result)

        def do_GET(self) -> None:  # noqa: N802
            self._handle(GET_ROUTES, with_body=False)

        def do_POST(self) -> None:  # noqa: N802
            self._handle(POST_ROUTES, with_body=True)

    return Handler


def serve(bridge: Bridge, host: str, port: int, token: str) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(bridge, token))
    server.daemon_threads = True
    return server


def _env_int(name: str) -> Optional[int]:
    value = os.environ.get(name, "").strip()
    return int(value) if value.isdigit() else None


def main() -> int:
    logging.basicConfig(level=os.environ.get("MT5_BRIDGE_LOG_LEVEL", "INFO"), format="%(asctime)s [bridge] %(message)s")
    token = os.environ.get("MT5_BRIDGE_TOKEN", "").strip()
    if len(token) < 16 or "TROQUE_" in token.upper():
        log.error("MT5_BRIDGE_TOKEN ausente, curto (mínimo 16 caracteres) ou ainda com o valor de exemplo. Defina a mesma chave no mt5 e no backend.")
        return 2
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError:
        log.error("pacote MetaTrader5 não instalado neste Python")
        return 3
    bridge = Bridge(
        mt5,
        terminal_path=os.environ.get("MT5_TERMINAL_PATH") or None,
        login=_env_int("MT5_LOGIN"),
        password=os.environ.get("MT5_PASSWORD") or None,
        server=os.environ.get("MT5_SERVER") or None,
        init_timeout_ms=_env_int("MT5_INIT_TIMEOUT_MS") or 60000,
    )
    host = os.environ.get("MT5_BRIDGE_HOST", "0.0.0.0")
    port = _env_int("MT5_BRIDGE_PORT") or 8001
    server = serve(bridge, host, port, token)
    log.info("Meta-Bot MT5 bridge %s ouvindo em %s:%s", BRIDGE_VERSION, host, port)
    try:
        bridge.ensure()
        log.info("terminal MT5 conectado")
    except BridgeError as exc:
        log.warning("terminal ainda não disponível (%s); nova tentativa a cada requisição", exc.message)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Cliente do bridge HTTP do MetaTrader 5 (container ``mt5``)."""

from __future__ import annotations

from typing import Any

import httpx


class MT5Error(Exception):
    def __init__(self, message: str, status: int = 0, data: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.data = data or {}


class MT5Unavailable(MT5Error):
    """Bridge fora do ar, terminal fechado ou sem conexão com a corretora."""


class MT5Client:
    def __init__(self, base_url: str, token: str, timeout: float = 20.0, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> Any:
        try:
            resp = await self._client.request(method, path, params=params, json=body)
        except httpx.TimeoutException as exc:
            raise MT5Unavailable("o bridge do MT5 não respondeu a tempo") from exc
        except httpx.HTTPError as exc:
            raise MT5Unavailable(f"não foi possível falar com o bridge do MT5 ({exc.__class__.__name__})") from exc
        try:
            data = resp.json()
        except ValueError:
            data = {"error": resp.text[:200]}
        if resp.status_code == 401:
            raise MT5Error("token do bridge recusado (confira MB_MT5_BRIDGE_TOKEN e MT5_BRIDGE_TOKEN)", 401, data)
        if resp.status_code == 503:
            raise MT5Unavailable(data.get("error", "terminal MT5 indisponível"), 503, data)
        if resp.status_code >= 400:
            raise MT5Error(data.get("error", f"erro {resp.status_code}"), resp.status_code, data)
        return data

    # ------------------------------------------------------------- consultas
    async def ping(self) -> dict:
        return await self._request("GET", "/ping")

    async def health(self) -> dict:
        return await self._request("GET", "/health")

    async def login(self, login: int, password: str, server: str) -> dict:
        return await self._request("POST", "/login", body={"login": int(login), "password": password, "server": server})

    async def account(self) -> dict:
        return await self._request("GET", "/account")

    async def symbols(self, q: str = "", limit: int = 200) -> list[dict]:
        return await self._request("GET", "/symbols", params={"q": q, "limit": limit})

    async def symbol(self, name: str) -> dict:
        return await self._request("GET", "/symbol", params={"name": name})

    async def tick(self, symbol: str) -> dict:
        return await self._request("GET", "/tick", params={"symbol": symbol})

    async def rates(self, symbol: str, timeframe: str, count: int) -> dict:
        return await self._request("GET", "/rates", params={"symbol": symbol, "timeframe": timeframe, "count": count})

    async def positions(self, magic: int | None = None) -> list[dict]:
        params = {"magic": magic} if magic is not None else None
        return await self._request("GET", "/positions", params=params)

    async def history_deals(self, date_from: int = 0, date_to: int | None = None, position: int | None = None) -> list[dict]:
        params: dict[str, Any] = {"from": date_from}
        if date_to is not None:
            params["to"] = date_to
        if position is not None:
            params["position"] = position
        return await self._request("GET", "/history/deals", params=params)

    # ---------------------------------------------------------------- ordens
    async def order_check(self, order: dict) -> dict:
        return await self._request("POST", "/order/check", body=order)

    async def order_send(self, order: dict) -> dict:
        return await self._request("POST", "/order/send", body=order)

    async def position_close(self, ticket: int, volume: float | None = None, deviation: int = 20, comment: str = "meta-bot") -> dict:
        body: dict[str, Any] = {"ticket": int(ticket), "deviation": deviation, "comment": comment}
        if volume:
            body["volume"] = volume
        return await self._request("POST", "/position/close", body=body)

    async def position_modify(self, ticket: int, sl: float | None, tp: float | None) -> dict:
        return await self._request("POST", "/position/modify", body={"ticket": int(ticket), "sl": sl or 0.0, "tp": tp or 0.0})

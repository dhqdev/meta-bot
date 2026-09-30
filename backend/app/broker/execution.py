"""Execução de ordens: conta simulada (paper) ou conta real/demo da corretora via MT5."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.broker.market import MarketService
from app.broker.mt5 import MT5Error, MT5Unavailable
from app.broker.terminals import TerminalManager
from app.core.risk import pnl_money
from app.db import session_scope
from app.kv import kv_get, kv_set
from app.models import Trade
from app.runtime import get_config


@dataclass
class OrderResult:
    ok: bool
    ticket: str = ""
    price: float = 0.0
    volume: float = 0.0
    commission: float = 0.0
    message: str = ""
    raw: dict = field(default_factory=dict)


def paper_reset_at() -> datetime:
    value = kv_get("paper_reset_at")
    if not value:
        now = datetime.now(timezone.utc)
        kv_set("paper_reset_at", now.isoformat())
        return now
    return datetime.fromisoformat(value)


class PaperBroker:
    """Conta simulada: preços reais (MT5) ou simulados, sem enviar ordens a ninguém."""

    mode = "paper"

    def __init__(self, market: MarketService):
        self.market = market

    async def _mark(self, trade: Trade) -> float:
        try:
            tick = await self.market.tick(trade.symbol)
            spec = await self.market.spec(trade.symbol)
        except Exception:
            return 0.0
        direction = 1 if trade.direction == "buy" else -1
        exit_px = tick["bid"] if direction > 0 else tick["ask"]
        return pnl_money(spec, direction, trade.entry_price, exit_px, trade.volume) - trade.commission

    async def account(self) -> dict:
        cfg = get_config()
        reset = paper_reset_at()
        with session_scope() as s:
            realized = s.scalar(
                select(func.coalesce(func.sum(Trade.pnl), 0.0)).where(
                    Trade.mode == "paper", Trade.status == "closed", Trade.entry_time >= reset
                )
            )
            open_trades = list(s.scalars(select(Trade).where(Trade.mode == "paper", Trade.status == "open")))
        open_pnl = 0.0
        for tr in open_trades:
            open_pnl += await self._mark(tr)
        balance = cfg.paper_initial_balance + float(realized or 0.0)
        return {
            "login": "SIMULADO",
            "server": "Meta-Bot (conta simulada)",
            "company": "Meta-Bot",
            "currency": "USD",
            "balance": round(balance, 2),
            "equity": round(balance + open_pnl, 2),
            "margin_free": round(balance + open_pnl, 2),
            "open_pnl": round(open_pnl, 2),
            "leverage": 100,
            "trade_allowed": True,
        }

    async def open(self, symbol: str, side: str, volume: float, sl: float | None, tp: float | None, comment: str = "") -> OrderResult:
        cfg = get_config()
        tick = await self.market.tick(symbol)
        spec = await self.market.spec(symbol)
        if not tick.get("open", True):
            return OrderResult(False, message="mercado fechado")
        slip = cfg.paper_slippage_points * spec["point"]
        price = tick["ask"] + slip if side == "buy" else tick["bid"] - slip
        return OrderResult(
            True,
            ticket=f"P{secrets.token_hex(5)}",
            price=round(price, spec["digits"]),
            volume=volume,
            commission=round(cfg.paper_commission_per_lot * volume, 2),
        )

    async def close(self, trade: Trade, price: float | None = None) -> OrderResult:
        cfg = get_config()
        spec = await self.market.spec(trade.symbol)
        if price is None:
            tick = await self.market.tick(trade.symbol)
            slip = cfg.paper_slippage_points * spec["point"]
            price = tick["bid"] - slip if trade.direction == "buy" else tick["ask"] + slip
        return OrderResult(True, ticket=trade.ticket, price=round(price, spec["digits"]), volume=trade.volume)

    async def modify(self, trade: Trade, sl: float | None, tp: float | None) -> OrderResult:
        return OrderResult(True, ticket=trade.ticket)


class LiveBroker:
    """Conta da corretora (real ou demo) através do bridge do MT5."""

    mode = "live"

    def __init__(self, terminals: TerminalManager, market: MarketService):
        self.terminals = terminals
        self.market = market

    def _client(self):
        client = self.terminals.client()
        if client is None:
            raise MT5Unavailable("nenhum terminal MT5 configurado")
        return client

    async def account(self) -> dict:
        info = await self._client().account()
        return {
            "login": str(info.get("login", "")),
            "server": info.get("server", ""),
            "company": info.get("company", ""),
            "name": info.get("name", ""),
            "currency": info.get("currency", ""),
            "balance": float(info.get("balance") or 0),
            "equity": float(info.get("equity") or 0),
            "margin_free": float(info.get("margin_free") or 0),
            "open_pnl": float(info.get("profit") or 0),
            "leverage": info.get("leverage"),
            "trade_allowed": bool(info.get("trade_allowed", True)),
        }

    async def open(self, symbol: str, side: str, volume: float, sl: float | None, tp: float | None, comment: str = "") -> OrderResult:
        cfg = get_config()
        try:
            res = await self._client().order_send(
                {
                    "action": "deal",
                    "symbol": symbol,
                    "side": side,
                    "volume": volume,
                    "sl": sl,
                    "tp": tp,
                    "deviation": cfg.deviation_points,
                    "magic": cfg.magic_number,
                    "comment": (comment or "meta-bot")[:31],
                }
            )
        except (MT5Error, MT5Unavailable) as exc:
            return OrderResult(False, message=exc.message)
        if not res.get("ok"):
            return OrderResult(False, message=f"{res.get('comment') or 'ordem recusada'} (código {res.get('retcode')})", raw=res)
        ticket = res.get("order") or res.get("deal")
        return OrderResult(True, ticket=str(ticket), price=float(res.get("price") or 0), volume=float(res.get("volume") or volume), raw=res)

    async def close(self, trade: Trade, price: float | None = None) -> OrderResult:
        cfg = get_config()
        try:
            res = await self._client().position_close(int(trade.ticket), deviation=cfg.deviation_points)
        except (MT5Error, MT5Unavailable) as exc:
            return OrderResult(False, message=exc.message)
        if not res.get("ok"):
            return OrderResult(False, message=f"{res.get('comment') or 'fechamento recusado'} (código {res.get('retcode')})", raw=res)
        return OrderResult(True, ticket=trade.ticket, price=float(res.get("price") or 0), volume=trade.volume, raw=res)

    async def modify(self, trade: Trade, sl: float | None, tp: float | None) -> OrderResult:
        try:
            res = await self._client().position_modify(int(trade.ticket), sl, tp)
        except (MT5Error, MT5Unavailable) as exc:
            return OrderResult(False, message=exc.message)
        return OrderResult(bool(res.get("ok")), ticket=trade.ticket, message=str(res.get("comment") or ""), raw=res)

    async def open_tickets(self) -> set[str]:
        cfg = get_config()
        positions = await self._client().positions(cfg.magic_number)
        return {str(p.get("ticket")) for p in positions}

    async def closed_info(self, ticket: str) -> dict | None:
        """Preço de saída, lucro, comissão e swap de uma posição já fechada."""
        deals = await self._client().history_deals(position=int(ticket))
        if not deals:
            return None
        outs = [d for d in deals if int(d.get("entry", 0)) in (1, 2, 3)]
        if not outs:
            return None
        last = max(outs, key=lambda d: d.get("time_msc") or d.get("time") or 0)
        return {
            "price": float(last.get("price") or 0),
            "profit": sum(float(d.get("profit") or 0) for d in deals),
            "commission": sum(float(d.get("commission") or 0) + float(d.get("fee") or 0) for d in deals),
            "swap": sum(float(d.get("swap") or 0) for d in deals),
            "time": int(last.get("time") or 0) - int(self.market.offset_hours * 3600),
            "reason": int(last.get("reason") or 0),
        }

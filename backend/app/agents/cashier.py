"""Caio, o caixa (sem IA): executa, protege e acompanha as posições.

- Envia a ordem com stop e alvo (conta simulada ou MT5).
- Ordens stop (Setup 9.1): arma e executa quando o preço toca o gatilho.
- Acompanha as posições: break-even, trailing stop, saída por tempo, antes do
  fim de semana e no fim do pregão da B3 (day trade).
- Registra o patrimônio a cada minuto.
- Skill "Gestão de saída": refaz as últimas operações reais com outras regras
  de break-even/trailing e adota a que teria rendido mais (se o ganho for real).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.agents.base import Agent, AgentProfile
from app.agents.skills import SkillDef
from app.broker.execution import LiveBroker, PaperBroker
from app.config import get_settings
from app.core.backtest import RiskParams, simulate_exit
from app.core.risk import pnl_money, value_per_price_unit
from app.db import session_scope
from app.events import bus
from app.models import EquitySnapshot, Signal, Trade
from app.runtime import TIMEFRAME_SECONDS, get_config

DEFAULT_MAX_BARS = {"M5": 48, "M15": 48, "M30": 48, "H1": 72, "H4": 60, "D1": 30}


def trade_dict(t: Trade) -> dict:
    return {
        "id": t.id,
        "mode": t.mode,
        "symbol": t.symbol,
        "direction": t.direction,
        "volume": t.volume,
        "entry_price": t.entry_price,
        "entry_time": t.entry_time.isoformat() if t.entry_time else None,
        "sl": t.sl,
        "tp": t.tp,
        "initial_sl": t.initial_sl,
        "exit_price": t.exit_price,
        "exit_time": t.exit_time.isoformat() if t.exit_time else None,
        "exit_reason": t.exit_reason,
        "pnl": round(t.pnl, 2),
        "pnl_r": round(t.pnl_r, 3),
        "commission": t.commission,
        "risk_money": round(t.risk_money, 2),
        "strategy": t.strategy,
        "timeframe": t.timeframe,
        "ticket": t.ticket,
        "status": t.status,
        "mgmt": t.mgmt or {},
    }


class CashierAgent(Agent):
    profile = AgentProfile(
        id="cashier",
        name="Caio",
        role="Caixa",
        emoji="💰",
        uses_ai=False,
        description="Envia as ordens com stop loss e stop gain, move o stop para o zero a zero, faz trailing e fecha as posições nas regras.",
    )
    interval = 5.0
    idle_task = "Acompanhando as posições"
    always_on = True
    skill_defs = [
        SkillDef("execucao", "Execução de ordens", "Envia ordens com stop e alvo e registra tudo."),
        SkillDef("gestao_saida", "Gestão de saída", "Break-even e trailing aprendidos com as operações reais."),
        SkillDef("protecao", "Proteção de posições", "Garante que nenhuma perda passe do planejado."),
    ]

    def __init__(self, office):
        super().__init__(office)
        self._pending: dict[int, dict] = {}
        self._last_trail_log: dict[int, float] = {}

    async def on_system_change(self, running: bool) -> None:
        if running:
            self.set_state("idle", "desk", "Pronto para executar", "💰")
        else:
            has_open = self._open_trades()
            self.set_state("idle", "vault", "Guardando as posições abertas" if has_open else "Cofre fechado (sistema desligado)", "🔒")

    async def tick(self) -> None:
        if self._pending:
            await self.process_pending()
        await self.monitor()
        if self.due("equity", 60):
            await self.snapshot_equity()
        if self.is_running() and self.due("exits_review", 86400):
            await self.review_exits()

    @staticmethod
    def _open_trades() -> list[int]:
        with session_scope() as s:
            return list(s.scalars(select(Trade.id).where(Trade.status == "open")))

    def broker_for(self, mode: str) -> PaperBroker | LiveBroker:
        return self.office.live if mode == "live" else self.office.paper

    # -------------------------------------------------------------- entrada
    async def execute(self, signal_id: int, volume: float, risk_money: float, risk_pct: float, votes: dict) -> int | None:
        with session_scope() as s:
            sig = s.get(Signal, signal_id)
            if sig is None:
                return None
            info = {
                "id": sig.id, "symbol": sig.symbol, "timeframe": sig.timeframe, "strategy": sig.strategy,
                "direction": sig.direction, "entry_type": sig.entry_type, "price": sig.price, "trigger": sig.trigger,
                "sl": sig.sl, "tp": sig.tp, "profile_id": sig.profile_id, "expires_at": sig.expires_at,
                "volume": volume, "risk_money": risk_money, "risk_pct": risk_pct, "votes": votes,
            }
            if sig.entry_type == "stop":
                sig.status = "aguardando"
        if info["entry_type"] == "stop":
            self._pending[signal_id] = info
            verb = "compra" if info["direction"] == "buy" else "venda"
            self.work(f"Ordem stop de {verb} armada em {info['symbol']} ({info['trigger']:g})", "desk", "🎯")
            self.say(f"🎯 Stop de {verb} armado em {info['symbol']} @ {info['trigger']:g}", "🎯")
            self.log(f"Ordem stop de {verb} armada em {info['symbol']} no gatilho {info['trigger']:g} (vale até {info['expires_at']:%H:%M} UTC)", kind="order")
            return None
        return await self._open(info)

    async def process_pending(self) -> None:
        now = datetime.now(timezone.utc)
        for sid, info in list(self._pending.items()):
            if info["expires_at"] and now > info["expires_at"]:
                self._pending.pop(sid, None)
                self._set_signal(sid, "expirado", "gatilho não foi tocado a tempo")
                self.log(f"Ordem stop em {info['symbol']} expirou sem ser acionada", kind="order")
                continue
            try:
                tick = await self.office.market.tick(info["symbol"])
            except Exception:
                continue
            hit = (info["direction"] == "buy" and tick["ask"] >= info["trigger"]) or (info["direction"] == "sell" and tick["bid"] <= info["trigger"])
            if hit:
                self._pending.pop(sid, None)
                await self._open(info)

    async def _open(self, info: dict) -> int | None:
        cfg = get_config()
        if not self.is_running():
            self._set_signal(info["id"], "cancelado", "sistema desligado")
            return None
        mode = cfg.mode
        broker = self.broker_for(mode)
        symbol, side = info["symbol"], info["direction"]
        d = 1 if side == "buy" else -1
        try:
            spec = await self.office.market.spec(symbol)
            tick = await self.office.market.tick(symbol)
        except Exception as exc:
            self._set_signal(info["id"], "falhou", f"sem cotação: {exc}")
            return None
        ref = info["trigger"] if info["entry_type"] == "stop" and info["trigger"] else info["price"]
        dist_sl = abs(ref - info["sl"])
        dist_tp = abs(info["tp"] - ref) if info["tp"] else None
        est = tick["ask"] if d > 0 else tick["bid"]
        digits = int(spec.get("digits", 5))
        sl_est = round(est - d * dist_sl, digits)
        tp_est = round(est + d * dist_tp, digits) if dist_tp else None
        self.work(f"Enviando {'compra' if d > 0 else 'venda'} de {info['volume']:g} em {symbol}", "vault", "💸")
        res = await broker.open(symbol, side, info["volume"], sl_est, tp_est, comment=f"MB {info['strategy']}"[:31])
        if not res.ok:
            self._set_signal(info["id"], "falhou", res.message)
            self.say(f"❌ Ordem recusada em {symbol}: {res.message[:70]}", "❌")
            self.log(f"Ordem em {symbol} recusada: {res.message}", kind="order", level="warning")
            self.idle("Pronto para executar")
            return None
        fill = res.price or est
        sl = round(fill - d * dist_sl, digits)
        tp = round(fill + d * dist_tp, digits) if dist_tp else None
        if mode == "live" and abs(sl - sl_est) > spec["point"]:
            placeholder = Trade(ticket=res.ticket, symbol=symbol, direction=side, volume=info["volume"], entry_price=fill, mode=mode)
            await broker.modify(placeholder, sl, tp)
        risk_money = info["volume"] * dist_sl * value_per_price_unit(spec)
        ctx = {"votes": info["votes"], "risk_pct": info["risk_pct"]}
        with session_scope() as s:
            tr = Trade(
                mode=mode, terminal_id=(self.office.terminals.active() or {}).get("id") if mode == "live" else None,
                symbol=symbol, direction=side, volume=res.volume or info["volume"], entry_price=fill,
                sl=sl, tp=tp, initial_sl=sl, strategy=info["strategy"], timeframe=info["timeframe"],
                profile_id=info["profile_id"], signal_id=info["id"], ticket=res.ticket, status="open",
                commission=res.commission, risk_money=risk_money, context=ctx, mgmt={},
            )
            s.add(tr)
            s.flush()
            trade_id = tr.id
            data = trade_dict(tr)
            sig = s.get(Signal, info["id"])
            if sig is not None:
                sig.status = "executado"
                sig.trade_id = trade_id
        verb = "Comprei" if d > 0 else "Vendi"
        bus.publish({"type": "trade", "event": "opened", "trade": data})
        self.say(f"✅ {verb} {data['volume']:g} {symbol} @ {fill:g} · stop {sl:g}" + (f" · alvo {tp:g}" if tp else ""), "💸")
        self.log(f"{verb} {data['volume']:g} de {symbol} a {fill:g} ({'simulado' if mode == 'paper' else 'conta MT5'}). Stop {sl:g}, alvo {tp if tp else '—'}. Risco {risk_money:.2f}.", kind="trade")
        self.skills.gain("execucao", 3, f"ordem em {symbol}")
        self.idle("Acompanhando as posições")
        return trade_id

    @staticmethod
    def _set_signal(signal_id: int, status: str, reason: str) -> None:
        with session_scope() as s:
            sig = s.get(Signal, signal_id)
            if sig is not None:
                sig.status = status
                sig.reason = reason[:500]

    # ------------------------------------------------------------ vigilância
    async def monitor(self) -> None:
        with session_scope() as s:
            trades = list(s.scalars(select(Trade).where(Trade.status == "open")))
            s.expunge_all()
        if not trades:
            return
        live_tickets: set[str] | None = None
        if any(t.mode == "live" for t in trades):
            try:
                live_tickets = await self.office.live.open_tickets()
            except Exception:
                live_tickets = None
        for tr in trades:
            try:
                if tr.mode == "live":
                    if live_tickets is None:
                        continue
                    if tr.ticket not in live_tickets:
                        await self._sync_closed_live(tr)
                        continue
                await self._manage(tr)
            except Exception as exc:
                self.log(f"Falha ao acompanhar a operação #{tr.id} ({tr.symbol}): {exc}", kind="order", level="warning")

    async def _manage(self, tr: Trade) -> None:
        cfg = get_config()
        spec = await self.office.market.spec(tr.symbol)
        tick = await self.office.market.tick(tr.symbol)
        d = 1 if tr.direction == "buy" else -1
        price = tick["bid"] if d > 0 else tick["ask"]
        # conta simulada: o stop e o alvo são conferidos aqui (na corretora, o MT5 faz isso)
        if tr.mode == "paper":
            if tr.sl is not None and (price - tr.sl) * d <= 0:
                reason = "sl"
                mg = tr.mgmt or {}
                if mg.get("trailing"):
                    reason = "trailing"
                elif mg.get("be"):
                    reason = "be"
                await self._close(tr, reason, price=min(tr.sl, price) if d > 0 else max(tr.sl, price))
                return
            if tr.tp is not None and (price - tr.tp) * d >= 0:
                await self._close(tr, "tp", price=tr.tp)
                return
        if not tick.get("open", True):
            return
        exits = self.office.exit_params()
        risk_px = abs(tr.entry_price - (tr.initial_sl or tr.entry_price))
        fav = (price - tr.entry_price) * d
        mg = dict(tr.mgmt or {})
        new_sl = None
        if risk_px > 0 and exits["break_even_r"] > 0 and not mg.get("be") and fav >= exits["break_even_r"] * risk_px:
            buffer = (tick["ask"] - tick["bid"]) + tr.commission / max(value_per_price_unit(spec) * tr.volume, 1e-12)
            cand = tr.entry_price + d * buffer
            if tr.sl is None or (cand - tr.sl) * d > 0:
                new_sl = cand
            mg["be"] = True
        if risk_px > 0 and exits["trailing_start_r"] > 0 and fav >= exits["trailing_start_r"] * risk_px and tr.timeframe:
            try:
                bars = await self.office.market.rates(tr.symbol, tr.timeframe, 100, closed_only=True, max_age=60)
                atr = float(bars.atr(14)[-1])
            except Exception:
                atr = 0.0
            if atr > 0:
                cand = price - d * exits["trailing_atr"] * atr
                ref = new_sl if new_sl is not None else tr.sl
                if ref is None or (cand - ref) * d > 0:
                    new_sl = cand
                    mg["trailing"] = True
        if new_sl is not None:
            new_sl = round(new_sl, int(spec.get("digits", 5)))
            res = await self.broker_for(tr.mode).modify(tr, new_sl, tr.tp)
            if res.ok:
                with session_scope() as s:
                    row = s.get(Trade, tr.id)
                    if row is not None:
                        old = row.sl
                        row.sl = new_sl
                        row.mgmt = mg
                first_be = mg.get("be") and not (tr.mgmt or {}).get("be")
                last_log = self._last_trail_log.get(tr.id, 0)
                if first_be:
                    self.say(f"🔒 Stop no zero a zero em {tr.symbol}", "🔒")
                    self.log(f"Stop de {tr.symbol} movido para o zero a zero ({new_sl:g})", kind="order")
                    self.skills.gain("protecao", 2, "break-even")
                elif time.time() - last_log > 600:
                    self._last_trail_log[tr.id] = time.time()
                    self.log(f"Trailing em {tr.symbol}: stop {old:g} → {new_sl:g}", kind="order")
                bus.publish({"type": "trade", "event": "updated", "trade": {**trade_dict(tr), "sl": new_sl, "mgmt": mg}})
        # saídas por regra de tempo
        now = datetime.now(timezone.utc)
        max_bars = cfg.max_bars_in_trade or DEFAULT_MAX_BARS.get(tr.timeframe, 60)
        tf_sec = TIMEFRAME_SECONDS.get(tr.timeframe, 3600)
        if tr.timeframe and (now - tr.entry_time).total_seconds() >= max_bars * tf_sec:
            await self._close(tr, "tempo")
            return
        if cfg.close_before_weekend and spec.get("session", "fx") != "crypto" and not self._is_b3(tr.symbol):
            if now.weekday() == 4 and (now.hour, now.minute) >= (20, 45):
                await self._close(tr, "fim de semana")
                return
        if self._is_b3(tr.symbol):
            local = now.astimezone(ZoneInfo(get_settings().timezone))
            hh, mm = (int(x) for x in cfg.b3_close_time.split(":"))
            if (local.hour, local.minute) >= (hh, mm):
                await self._close(tr, "fim do pregão")

    @staticmethod
    def _is_b3(symbol: str) -> bool:
        cfg = get_config()
        return symbol.upper().startswith(tuple(p.upper() for p in cfg.b3_prefixes))

    async def request_close(self, trade_id: int, reason: str) -> bool:
        with session_scope() as s:
            tr = s.get(Trade, trade_id)
            if tr is None or tr.status != "open":
                return False
            s.expunge(tr)
        return await self._close(tr, reason)

    async def _close(self, tr: Trade, reason: str, price: float | None = None) -> bool:
        broker = self.broker_for(tr.mode)
        self.work(f"Fechando {tr.symbol} ({reason})", "vault", "🧾")
        res = await broker.close(tr, price)
        if not res.ok:
            self.log(f"Não consegui fechar {tr.symbol}: {res.message}", kind="order", level="warning")
            self.idle("Acompanhando as posições")
            return False
        if tr.mode == "live":
            info = None
            try:
                info = await self.office.live.closed_info(tr.ticket)
            except Exception:
                info = None
            if info:
                await self._finalize(tr.id, info["price"], reason, profit=info["profit"], commission=info["commission"], swap=info["swap"], exit_ts=info["time"])
                return True
        await self._finalize(tr.id, res.price, reason)
        return True

    async def _sync_closed_live(self, tr: Trade) -> None:
        info = await self.office.live.closed_info(tr.ticket)
        if info is None:
            return
        reason = {4: "sl", 5: "tp", 6: "stop out"}.get(info["reason"], "fechada na corretora")
        if reason == "sl" and (tr.mgmt or {}).get("trailing"):
            reason = "trailing"
        elif reason == "sl" and (tr.mgmt or {}).get("be"):
            reason = "be"
        await self._finalize(tr.id, info["price"], reason, profit=info["profit"], commission=info["commission"], swap=info["swap"], exit_ts=info["time"])

    async def _finalize(self, trade_id: int, exit_price: float, reason: str, profit: float | None = None, commission: float | None = None, swap: float | None = None, exit_ts: int | None = None) -> None:
        with session_scope() as s:
            tr = s.get(Trade, trade_id)
            if tr is None or tr.status != "open":
                return
            d = 1 if tr.direction == "buy" else -1
            if profit is not None:
                pnl = float(profit) + float(commission or 0) + float(swap or 0)
                tr.commission = abs(float(commission or 0))
                tr.swap = float(swap or 0)
            else:
                spec = await self.office.market.spec(tr.symbol)
                pnl = pnl_money(spec, d, tr.entry_price, exit_price, tr.volume) - tr.commission
            tr.exit_price = exit_price
            tr.exit_time = datetime.fromtimestamp(exit_ts, tz=timezone.utc) if exit_ts else datetime.now(timezone.utc)
            tr.exit_reason = reason
            tr.pnl = round(pnl, 2)
            tr.pnl_r = round(pnl / tr.risk_money, 3) if tr.risk_money > 0 else 0.0
            tr.status = "closed"
            data = trade_dict(tr)
            s.flush()
            s.expunge(tr)
        bus.publish({"type": "trade", "event": "closed", "trade": data})
        emoji = "🟢" if data["pnl"] > 0 else "🔴"
        self.say(f"{emoji} {data['symbol']} fechado ({reason}): {data['pnl']:+.2f} ({data['pnl_r']:+.2f}R)".replace(".", ","), emoji)
        self.log(f"Operação #{trade_id} em {data['symbol']} encerrada por {reason}: {data['pnl']:+.2f} ({data['pnl_r']:+.2f}R)".replace(".", ","), kind="trade")
        if reason in ("be", "trailing") or (reason == "sl" and data["pnl_r"] >= -1.2):
            self.skills.gain("protecao", 3, "perda contida no planejado")
        if reason in ("be", "trailing") and data["pnl_r"] > -0.1:
            self.skills.gain("gestao_saida", 4, f"saída protegida em {data['symbol']}")
        self.idle("Acompanhando as posições")
        self.office.on_trade_closed(tr)

    # ----------------------------------------------------------- patrimônio
    async def snapshot_equity(self) -> None:
        cfg = get_config()
        try:
            acc = await self.office.broker.account()
        except Exception:
            return
        with session_scope() as s:
            s.add(EquitySnapshot(mode=cfg.mode, balance=float(acc["balance"]), equity=float(acc["equity"])))
            since = datetime.now(timezone.utc) - timedelta(days=7)
            pts = [
                {"t": int(r.ts.timestamp()), "equity": round(r.equity, 2)}
                for r in s.scalars(select(EquitySnapshot).where(EquitySnapshot.mode == cfg.mode, EquitySnapshot.ts >= since).order_by(EquitySnapshot.ts))
            ]
        if len(pts) > 120:
            step = len(pts) / 120
            pts = [pts[int(i * step)] for i in range(120)] + [pts[-1]]
        bus.publish({"type": "account", "mode": cfg.mode, **acc})
        self.office.publish_office(account={"mode": cfg.mode, **acc}, equity=pts)

    # ------------------------------------------------------- aprendizado
    async def review_exits(self) -> None:
        """Refaz as últimas operações com outras regras de saída e adota a melhor, se valer a pena."""
        cfg = get_config()
        with session_scope() as s:
            trades = list(s.scalars(select(Trade).where(Trade.status == "closed", Trade.timeframe != "").order_by(Trade.exit_time.desc()).limit(40)))
            s.expunge_all()
        if len(trades) < 15:
            return
        self.work(f"Revendo a gestão de {len(trades)} operações", "library", "📚")
        configs = [(be, ts, ta) for be in (0.0, 0.75, 1.0, 1.5) for ts in (0.0, 1.0, 1.5, 2.0) for ta in (1.5, 2.0, 3.0)]
        results = {c: [] for c in configs}
        for tr in trades:
            try:
                bars = await self.office.market.rates(tr.symbol, tr.timeframe, 1500, max_age=900)
            except Exception:
                continue
            start = int(bars.time.searchsorted(int(tr.entry_time.timestamp()), side="right")) - 1
            if start < 20 or start >= bars.n - 1:
                continue
            d = 1 if tr.direction == "buy" else -1
            risk_px = abs(tr.entry_price - (tr.initial_sl or tr.entry_price))
            if risk_px <= 0:
                continue
            target = tr.tp
            max_bars = cfg.max_bars_in_trade or DEFAULT_MAX_BARS.get(tr.timeframe, 60)
            for be, ts, ta in configs:
                r, _, _ = simulate_exit(bars, start, d, tr.entry_price, tr.initial_sl, target, RiskParams(break_even_r=be, trailing_start_r=ts, trailing_atr=ta, max_bars=max_bars))
                results[(be, ts, ta)].append(r)
        scored = {c: sum(v) / len(v) for c, v in results.items() if len(v) >= 15}
        if not scored:
            self.idle("Acompanhando as posições")
            return
        cur = self.office.exit_params()
        cur_key = min(scored, key=lambda c: abs(c[0] - cur["break_even_r"]) + abs(c[1] - cur["trailing_start_r"]) + abs(c[2] - cur["trailing_atr"]))
        best = max(scored, key=scored.get)
        n = len(results[best])
        if scored[best] >= scored[cur_key] + 0.05:
            learned = {"break_even_r": best[0], "trailing_start_r": best[1], "trailing_atr": best[2]}
            self.skills.update("gestao_saida", params={"learned": learned}, stats={"n": n, "avg_r_before": round(scored[cur_key], 3), "avg_r_after": round(scored[best], 3)})
            self.skills.gain("gestao_saida", 20, "nova regra de saída adotada")
            self.office.invalidate_exit_params()
            self.log(
                f"Aprendi uma gestão de saída melhor com {n} operações reais: break-even {best[0]:g}R, trailing a partir de {best[1]:g}R com {best[2]:g} ATR "
                f"(média {scored[cur_key]:+.2f}R → {scored[best]:+.2f}R)".replace(".", ","),
                kind="learning",
            )
            self.say("📚 Aprendi uma gestão de saída melhor!", "📚")
        else:
            self.skills.update("gestao_saida", stats={"n": n, "avg_r_current": round(scored[cur_key], 3), "reviewed_at": datetime.now(timezone.utc).isoformat()})
            self.skills.gain("gestao_saida", 3, "gestão atual confirmada")
        self.idle("Acompanhando as posições")

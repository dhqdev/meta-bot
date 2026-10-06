"""Rita, a gerente de risco (sem IA): tamanho da posição, metas do dia e trava geral.

- Lote pelo risco: arrisca X% do patrimônio entre a entrada e o stop, com o
  valor do tick informado pela corretora.
- Metas do dia: ao bater o limite de perda ou a meta de ganho (em % ou em
  dinheiro), encerra as posições e a equipe para de operar até o dia seguinte.
- Limites: queda máxima desde o pico (trava geral), posições simultâneas, por
  ativo e exposição por moeda, e spread.
- Risco adaptativo (skill): reduz o risco depois de perdas seguidas e volta
  aos poucos depois de ganhos.
"""

from __future__ import annotations

import json

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.agents.base import Agent, AgentProfile
from app.agents.skills import SkillDef
from app.config import get_settings
from app.core.risk import exposure, position_size
from app.core.units import limit_text, money_text, pct_text
from app.db import session_scope
from app.kv import kv_get, kv_set
from app.models import Trade
from app.runtime import get_config


class Verdict:
    def __init__(self, ok: bool, reason: str = "", volume: float = 0.0, risk_money: float = 0.0, risk_pct: float = 0.0):
        self.ok = ok
        self.reason = reason
        self.volume = volume
        self.risk_money = risk_money
        self.risk_pct = risk_pct


class RiskAgent(Agent):
    profile = AgentProfile(
        id="risk",
        name="Rita",
        role="Risco",
        emoji="🛡️",
        uses_ai=False,
        description="Calcula o lote pelo risco, controla perda diária, queda máxima, exposição por moeda e pode vetar qualquer operação.",
    )
    interval = 20.0
    idle_task = "Vigiando o risco"
    skill_defs = [
        SkillDef("dimensionamento", "Dimensionamento de posição", "Calcula o lote certo para o risco escolhido."),
        SkillDef("controle_drawdown", "Controle de drawdown", "Mantém as perdas dentro dos limites do dia e da conta."),
        SkillDef("exposicao", "Controle de exposição", "Evita apostar duas vezes na mesma moeda."),
        SkillDef("risco_adaptativo", "Risco adaptativo", "Reduz o risco nas sequências de perda e recupera nos ganhos."),
    ]

    def __init__(self, office):
        super().__init__(office)
        self._status: dict = {}

    def state_kv(self) -> dict:
        return kv_get("risk_state") or {"kill_switch": False, "adaptive_mult": 1.0, "consec_losses": 0, "peak_equity": None}

    def save_state(self, st: dict) -> None:
        kv_set("risk_state", st)

    async def tick(self) -> None:
        if self.due("guard", 30):
            await self.guard()

    @staticmethod
    def _to_money(value: float, unit: str, base: float) -> float:
        """Meta/limite em dinheiro (0 = desligado)."""
        if value <= 0:
            return 0.0
        return value if unit == "money" else base * value / 100.0

    async def guard(self) -> None:
        cfg = get_config()
        st = self.state_kv()
        try:
            acc = await self.office.broker.account()
        except Exception:
            return
        equity = float(acc.get("equity") or 0)
        currency = acc.get("currency") or ""
        mode = cfg.mode
        today = self._today_start()
        key = f"{mode}:{today.date().isoformat()}"
        if st.get("day_key") != key:
            st["day_key"] = key
            st["day_start_equity"] = equity
            if st.get("day_blocked"):
                self.log("Novo dia: metas zeradas, a equipe pode operar de novo", kind="risk")
            st["day_blocked"] = False
            st["day_stop"] = None
            st["day_stop_pnl"] = None
        peak_key = f"peak_{mode}"
        st[peak_key] = max(float(st.get(peak_key) or 0), equity)
        day_start = float(st.get("day_start_equity") or equity) or equity
        day_pnl = equity - day_start
        day_pct = day_pnl / day_start * 100 if day_start else 0.0
        dd_pct = (st[peak_key] - equity) / st[peak_key] * 100 if st[peak_key] else 0.0
        loss_money = self._to_money(cfg.daily_loss_limit, cfg.daily_loss_unit, day_start)
        target_money = self._to_money(cfg.daily_profit_target, cfg.daily_profit_unit, day_start)
        if not st.get("day_blocked"):
            if loss_money > 0 and day_pnl <= -loss_money:
                await self._stop_day(st, "loss", day_pnl, currency)
            elif target_money > 0 and day_pnl >= target_money:
                await self._stop_day(st, "target", day_pnl, currency)
        if not st.get("kill_switch") and dd_pct >= cfg.max_drawdown_pct:
            st["kill_switch"] = True
            st["kill_reason"] = f"queda de {dd_pct:.1f}% desde o pico"
            self.set_state("alert", "desk", "TRAVA GERAL: queda máxima atingida", "🚨")
            self.tell("all", "🚨 Trava geral acionada: queda máxima atingida! Ninguém entra até o dono liberar.", kind="alerta")
            self.log(f"Trava geral: queda de {dd_pct:.2f}% desde o pico (limite {cfg.max_drawdown_pct}%). Nenhuma entrada até você liberar em Configurações.".replace(".", ","), kind="risk", level="error")
        self.save_state(st)
        open_pos = self._open_positions(mode)
        room_money = max(0.0, loss_money + min(day_pnl, 0.0)) if loss_money > 0 else None
        self._status = {
            "mode": mode,
            "currency": currency,
            "equity": round(equity, 2),
            "day_start_equity": round(day_start, 2),
            "day_pnl": round(day_pnl, 2),
            "day_pct": round(day_pct, 3),
            "daily_loss_money": round(loss_money, 2),
            "daily_target_money": round(target_money, 2),
            "daily_loss_limit": cfg.daily_loss_limit,
            "daily_loss_unit": cfg.daily_loss_unit,
            "daily_profit_target": cfg.daily_profit_target,
            "daily_profit_unit": cfg.daily_profit_unit,
            "daily_room_money": round(room_money, 2) if room_money is not None else None,
            "daily_room_pct": round(room_money / day_start * 100, 3) if room_money is not None and day_start else None,
            "target_progress": round(max(0.0, day_pnl) / target_money, 3) if target_money > 0 else None,
            "day_stop": st.get("day_stop"),
            "day_stop_pnl": st.get("day_stop_pnl"),
            "drawdown_pct": round(dd_pct, 3),
            "max_drawdown_pct": cfg.max_drawdown_pct,
            "kill_switch": bool(st.get("kill_switch")),
            "kill_reason": st.get("kill_reason", ""),
            "day_blocked": bool(st.get("day_blocked")),
            "adaptive_mult": round(float(st.get("adaptive_mult", 1.0)), 3),
            "open_positions": len(open_pos),
            "max_positions": cfg.max_open_positions,
            "exposure": exposure([(sym, 1 if d == "buy" else -1) for sym, d in open_pos]),
        }
        self.office.publish_office(risk=self._status)
        if self.state not in ("alert",) or not (st.get("kill_switch") or st.get("day_blocked")):
            if self.state != "idle":
                self.idle("Vigiando o risco")
        if self.due("dd_xp", 86400) and not st.get("kill_switch") and dd_pct < cfg.max_drawdown_pct / 2:
            self.skills.gain("controle_drawdown", 5, "dia dentro dos limites")

    async def _stop_day(self, st: dict, kind: str, day_pnl: float, currency: str) -> None:
        """Bateu a meta ou o limite do dia: encerra as posições (se configurado) e a equipe para."""
        cfg = get_config()
        st["day_blocked"] = True
        st["day_stop"] = kind
        st["day_stop_pnl"] = round(day_pnl, 2)
        self.save_state(st)
        pnl_txt = money_text(day_pnl, currency, signed=True)
        if kind == "target":
            self.set_state("alert", "desk", f"Meta do dia batida ({pnl_txt}): equipe parada até amanhã", "🎯")
            self.tell("all", self.line("target", pnl=pnl_txt), kind="comemoracao", data={"pnl": day_pnl})
            self.log(f"Meta de ganho do dia atingida ({pnl_txt}). A equipe para de operar até amanhã.", kind="risk")
            self.skills.gain("controle_drawdown", 10, "meta do dia batida")
        else:
            self.set_state("alert", "desk", f"Limite de perda do dia ({pnl_txt}): equipe parada até amanhã", "🛑")
            self.tell("all", self.line("loss", pnl=pnl_txt), kind="alerta", data={"pnl": day_pnl})
            self.log(f"Limite de perda do dia atingido ({pnl_txt}). A equipe para de operar até amanhã.", kind="risk", level="warning")
        cashier = self.office.agent("cashier")
        cashier.cancel_pending("dia encerrado pela Rita")
        if cfg.close_on_daily_limit and self._open_positions(cfg.mode):
            self.tell("cashier", self.line("close_all"), kind="pedido")
            await cashier.close_all("meta do dia" if kind == "target" else "limite do dia")
        manager = self.office.agent("manager")
        manager.end_day(kind)

    def day_stopped(self) -> str | None:
        """"target" ou "loss" quando a equipe já encerrou o dia; None se ainda pode operar."""
        st = self.state_kv()
        if not st.get("day_blocked"):
            return None
        if st.get("day_key", "").split(":")[-1] != self._today_start().date().isoformat():
            return None  # virou o dia e o guarda ainda não rodou
        return st.get("day_stop") or "loss"

    def summary_for_ai(self) -> str:
        """Risco do dia em texto, com a unidade escrita em cada número: a IA confundia R$ 4,16 com 4,16%."""
        st = self.status()
        cfg = get_config()
        cur = st.get("currency") or ""
        if "day_pnl" not in st:
            return f"Ainda sem números do dia. Limite de perda configurado: {limit_text(cfg.daily_loss_limit, cfg.daily_loss_unit, cur)}."
        lines = [
            f"- Resultado do dia: {money_text(st['day_pnl'], cur, signed=True)} ({pct_text(st['day_pct'], signed=True)} do patrimônio do início do dia, {money_text(st['day_start_equity'], cur)}).",
        ]
        if st.get("daily_target_money"):
            lines.append(
                f"- Meta de ganho do dia: {money_text(st['daily_target_money'], cur)} ({limit_text(cfg.daily_profit_target, cfg.daily_profit_unit, cur)}); "
                f"já feito: {pct_text((st.get('target_progress') or 0) * 100)} da meta."
            )
        else:
            lines.append("- Meta de ganho do dia: desligada.")
        if st.get("daily_loss_money"):
            room = st.get("daily_room_money")
            lines.append(f"- Limite de perda do dia: {money_text(st['daily_loss_money'], cur)} ({limit_text(cfg.daily_loss_limit, cfg.daily_loss_unit, cur)}); ainda cabe perder {money_text(room or 0, cur)}.")
        lines.append(f"- Posições abertas: {st.get('open_positions', 0)} de no máximo {st.get('max_positions')}. Multiplicador de risco atual: {st.get('adaptive_mult', 1)}.")
        lines.append(f"- Queda desde o pico: {pct_text(st.get('drawdown_pct') or 0)} (trava geral em {pct_text(cfg.max_drawdown_pct)}).")
        if st.get("kill_switch"):
            lines.append("- TRAVA GERAL ATIVA: nenhuma entrada.")
        if st.get("day_blocked"):
            lines.append("- DIA ENCERRADO pela Rita (meta ou limite batido).")
        else:
            lines.append("- O dia está ABERTO: quem encerra o dia pela meta ou pelo limite é a Rita, pela regra; não encerre o dia por conta própria.")
        lines.append(f"- Exposição por moeda: {json.dumps(st.get('exposure') or {}, ensure_ascii=False)}.")
        return "\n".join(lines)

    def status(self) -> dict:
        if not self._status:
            cfg = get_config()
            return {"daily_loss_limit": cfg.daily_loss_limit, "daily_loss_unit": cfg.daily_loss_unit, "open_positions": 0, "max_positions": cfg.max_open_positions, "kill_switch": False}
        return self._status

    def _today_start(self) -> datetime:
        tz = ZoneInfo(get_settings().timezone)
        now = datetime.now(tz)
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def _open_positions(mode: str) -> list[tuple[str, str]]:
        with session_scope() as s:
            return [(t.symbol, t.direction) for t in s.scalars(select(Trade).where(Trade.status == "open", Trade.mode == mode))]

    # ---------------------------------------------------------- avaliação
    async def evaluate(self, symbol: str, direction: str, entry: float, stop: float, risk_mult: float = 1.0) -> Verdict:
        cfg = get_config()
        st = self.state_kv()
        if st.get("kill_switch"):
            return Verdict(False, f"trava geral ativa ({st.get('kill_reason', '')})")
        if st.get("day_blocked"):
            return Verdict(False, "meta do dia batida: equipe parada até amanhã" if st.get("day_stop") == "target" else "limite de perda do dia atingido")
        open_pos = self._open_positions(cfg.mode)
        if len(open_pos) >= cfg.max_open_positions:
            return Verdict(False, f"já há {len(open_pos)} posições abertas (limite {cfg.max_open_positions})")
        if sum(1 for sym, _ in open_pos if sym == symbol) >= cfg.max_positions_per_symbol:
            return Verdict(False, f"já há posição em {symbol}")
        d = 1 if direction == "buy" else -1
        exp = exposure([(sym, 1 if dd == "buy" else -1) for sym, dd in open_pos] + [(symbol, d)])
        worst = max(exp.items(), key=lambda kv: abs(kv[1]), default=("", 0))
        if abs(worst[1]) > cfg.max_currency_exposure:
            return Verdict(False, f"exposição demais em {worst[0]} ({worst[1]:+d})")
        try:
            spec = await self.office.market.spec(symbol)
            tick = await self.office.market.tick(symbol)
            acc = await self.office.broker.account()
        except Exception as exc:
            return Verdict(False, f"sem dados para calcular o risco: {exc}")
        if not tick.get("open", True):
            return Verdict(False, "mercado fechado")
        spread = float(tick["ask"]) - float(tick["bid"])
        typical = await self._typical_spread(symbol)
        if typical > 0 and spread > cfg.max_spread_multiplier * typical:
            return Verdict(False, f"spread alto agora ({spread:g}; o normal é {typical:g})")
        equity = float(acc.get("equity") or 0)
        if equity <= 0:
            return Verdict(False, "patrimônio zerado")
        adaptive = float(st.get("adaptive_mult", 1.0)) if cfg.adaptive_risk else 1.0
        risk_pct = cfg.risk_per_trade_pct * adaptive * max(0.25, min(1.0, risk_mult))
        sizing = position_size(spec, equity, risk_pct, entry, stop, cfg.min_lot_overrisk)
        if not sizing.ok:
            return Verdict(False, sizing.reason)
        self.skills.gain("dimensionamento", 2, f"lote de {symbol}")
        self.skills.gain("exposicao", 1, "exposição conferida")
        return Verdict(True, sizing.reason, sizing.volume, sizing.risk_money, risk_pct)

    async def _typical_spread(self, symbol: str) -> float:
        """Spread mediano dos últimos candles de 15 min (0 se não houver dado)."""
        try:
            bars = await self.office.market.rates(symbol, "M15", 200, max_age=900)
        except Exception:
            return 0.0
        values = [v for v in bars.spread[-200:] if v > 0]
        if not values:
            return 0.0
        values.sort()
        return float(values[len(values) // 2])

    # --------------------------------------------------------- aprendizado
    def on_trade_closed(self, trade: Trade) -> None:
        st = self.state_kv()
        mult = float(st.get("adaptive_mult", 1.0))
        if trade.pnl < 0:
            st["consec_losses"] = int(st.get("consec_losses", 0)) + 1
            if st["consec_losses"] >= 2:
                mult = max(0.25, mult * 0.75)
        else:
            if st.get("consec_losses", 0) >= 2:
                self.skills.gain("risco_adaptativo", 6, "sequência de perdas contida")
            st["consec_losses"] = 0
            mult = min(1.0, mult * 1.25)
        if mult != st.get("adaptive_mult"):
            self.log(f"Risco adaptativo agora em {mult:.0%} do normal".replace(".", ","), kind="risk")
            if mult < float(st.get("adaptive_mult", 1.0)):
                self.tell("manager", "🛡️ " + self.line("adaptive", pct=f"{mult:.0%}"), kind="info")
        st["adaptive_mult"] = round(mult, 4)
        self.save_state(st)
        if trade.risk_money > 0 and abs(trade.pnl) <= trade.risk_money * 1.3:
            self.skills.gain("dimensionamento", 3, "perda/ganho dentro do risco planejado")

    def reset_kill_switch(self) -> None:
        st = self.state_kv()
        st["kill_switch"] = False
        st["kill_reason"] = ""
        st[f"peak_{get_config().mode}"] = None
        self.save_state(st)
        self.log("Trava geral liberada pelo dono do sistema", kind="risk")
        self.idle("Vigiando o risco")



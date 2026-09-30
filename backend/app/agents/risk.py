"""Rita, a gerente de risco (sem IA): tamanho da posição, limites e trava geral.

- Lote pelo risco: arrisca X% do patrimônio entre a entrada e o stop, com o
  valor do tick informado pela corretora.
- Limites: perda diária máxima, queda máxima desde o pico (trava geral),
  posições simultâneas, por ativo e exposição por moeda, e spread.
- Risco adaptativo (skill): reduz o risco depois de perdas seguidas e volta
  aos poucos depois de ganhos.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.agents.base import Agent, AgentProfile
from app.agents.skills import SkillDef
from app.config import get_settings
from app.core.risk import exposure, position_size
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

    async def guard(self) -> None:
        cfg = get_config()
        st = self.state_kv()
        try:
            acc = await self.office.broker.account()
        except Exception:
            return
        equity = float(acc.get("equity") or 0)
        mode = cfg.mode
        today = self._today_start()
        key = f"{mode}:{today.date().isoformat()}"
        if st.get("day_key") != key:
            st["day_key"] = key
            st["day_start_equity"] = equity
            if st.get("day_blocked"):
                self.log("Novo dia: limite de perda diária liberado", kind="risk")
            st["day_blocked"] = False
        peak_key = f"peak_{mode}"
        st[peak_key] = max(float(st.get(peak_key) or 0), equity)
        day_start = float(st.get("day_start_equity") or equity) or equity
        day_pnl = equity - day_start
        day_pct = day_pnl / day_start * 100 if day_start else 0.0
        dd_pct = (st[peak_key] - equity) / st[peak_key] * 100 if st[peak_key] else 0.0
        if not st.get("day_blocked") and day_pct <= -cfg.max_daily_loss_pct:
            st["day_blocked"] = True
            self.set_state("alert", "desk", "Perda diária no limite: novas entradas bloqueadas até amanhã", "🛑")
            self.say(f"🛑 Perda do dia {day_pct:.1f}%: parei as entradas até amanhã.".replace(".", ","), "🛑")
            self.log(f"Limite de perda diária atingido ({day_pct:.2f}%). Entradas bloqueadas até o próximo dia.".replace(".", ","), kind="risk", level="warning")
        if not st.get("kill_switch") and dd_pct >= cfg.max_drawdown_pct:
            st["kill_switch"] = True
            st["kill_reason"] = f"queda de {dd_pct:.1f}% desde o pico"
            self.set_state("alert", "desk", "TRAVA GERAL: queda máxima atingida", "🚨")
            self.say("🚨 Trava geral acionada: queda máxima atingida!", "🚨")
            self.log(f"Trava geral: queda de {dd_pct:.2f}% desde o pico (limite {cfg.max_drawdown_pct}%). Nenhuma entrada até você liberar em Configurações.".replace(".", ","), kind="risk", level="error")
        self.save_state(st)
        open_pos = self._open_positions(mode)
        self._status = {
            "mode": mode,
            "equity": round(equity, 2),
            "day_pnl": round(day_pnl, 2),
            "day_pct": round(day_pct, 3),
            "daily_limit_pct": cfg.max_daily_loss_pct,
            "daily_room_pct": round(max(0.0, cfg.max_daily_loss_pct + min(day_pct, 0.0)), 3),
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

    def status(self) -> dict:
        if not self._status:
            cfg = get_config()
            return {"daily_room_pct": cfg.max_daily_loss_pct, "open_positions": 0, "max_positions": cfg.max_open_positions, "kill_switch": False}
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
            return Verdict(False, "limite de perda diária atingido")
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


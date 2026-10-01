"""O escritório: cria os agentes, liga tudo e conduz o fluxo sinal → gerente → risco → caixa."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, update

from app.agents.auditor import AuditorAgent
from app.agents.base import Agent
from app.agents.cashier import CashierAgent
from app.agents.daily import DAILY_SKILL, DailyMeeting
from app.agents.infra import InfraAgent
from app.agents.manager import ManagerAgent
from app.agents.news import NewsAgent
from app.agents.risk import RiskAgent
from app.agents.schedule import ScheduleAgent
from app.agents.strategist import StrategistAgent
from app.broker.execution import LiveBroker, PaperBroker
from app.broker.market import MarketService
from app.broker.terminals import TerminalManager
from app.config import Settings
from app.db import session_scope
from app.events import bus, record_activity
from app.kv import kv_get, kv_set
from app.models import KV, EquitySnapshot, Lesson, Signal, StrategyProfile, Trade
from app.runtime import get_config, update_config
from app.services.llm import LLMService

log = logging.getLogger("metabot.office")

BREAK_KEY = "office.break"

AGENT_CLASSES = [InfraAgent, NewsAgent, ScheduleAgent, StrategistAgent, ManagerAgent, RiskAgent, CashierAgent, AuditorAgent]


class Office:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.terminals = TerminalManager(settings)
        self.market = MarketService(self.terminals)
        self.paper = PaperBroker(self.market)
        self.live = LiveBroker(self.terminals, self.market)
        self.llm = LLMService()
        self.agents: dict[str, Agent] = {}
        for cls in AGENT_CLASSES:
            agent = cls(self)
            self.agents[agent.id] = agent
        self.daily = DailyMeeting(self)
        self.tasks: list[asyncio.Task] = []
        self.office_info: dict = {}
        self._exit_cache: tuple[float, dict] | None = None
        self._pipeline_lock = asyncio.Lock()

    # -------------------------------------------------------------- vida
    def ensure_setup(self) -> None:
        self.terminals.ensure_default()
        for agent in self.agents.values():
            agent.skills.ensure([*agent.skill_defs, DAILY_SKILL])

    async def start(self) -> None:
        bus.bind_loop(asyncio.get_running_loop())
        self.ensure_setup()
        self.sync_data_family()
        for agent in self.agents.values():
            self.tasks.append(asyncio.create_task(agent.run_forever(), name=f"agent-{agent.id}"))
        self.tasks.append(asyncio.create_task(self.daily_loop(), name="daily"))
        if not self.check_break():
            self.publish_office(office_break=self.break_info() or {})
        log.info("escritório aberto com %d agentes", len(self.agents))

    # ------------------------------------------------- origem dos preços
    def sync_data_family(self) -> bool:
        """Preços simulados x preços reais (MT5 ou Yahoo/Binance).

        Ao trocar de família, o que foi aprendido com a outra não vale: a conta simulada
        recomeça, posições abertas são anuladas (sem lucro nem prejuízo), as estratégias são
        retestadas com o histórico novo e as lições, horários evitados e pesos voltam ao início.
        Devolve True quando houve a troca."""
        family = self.market.family()
        if getattr(self, "_family", None) == family:
            return False
        self._family = family
        previous = kv_get("market.family")
        if previous is None:
            with session_scope() as s:
                old_source = s.scalar(select(StrategyProfile.data_source).where(StrategyProfile.data_source != "").limit(1))
            previous = None if old_source is None else ("simulado" if old_source == "synthetic" else "real")
        kv_set("market.family", family)
        if previous is None or previous == family:
            return False
        self._restart_learning(previous, family)
        return True

    def _restart_learning(self, previous: str, family: str) -> None:
        now = datetime.now(timezone.utc)
        archived = "paper-sim" if previous == "simulado" else "paper-real"
        with session_scope() as s:
            for tr in s.scalars(select(Trade).where(Trade.mode == "paper", Trade.status == "open")):
                tr.status, tr.exit_price, tr.exit_time, tr.exit_reason, tr.pnl, tr.pnl_r = "closed", tr.entry_price, now, "troca de dados", 0.0, 0.0
            s.execute(update(Trade).where(Trade.mode == "paper").values(mode=archived))
            s.execute(update(EquitySnapshot).where(EquitySnapshot.mode == "paper").values(mode=archived))
            s.execute(update(Signal).where(Signal.status.in_(("proposto", "aprovado", "aguardando"))).values(status="cancelado", reason="troca da origem dos preços"))
            s.execute(
                update(StrategyProfile).values(
                    params={}, filters={}, risk={}, version=1, status="nova", score=0.0, metrics={}, is_metrics={}, oos_metrics={},
                    hour_stats={}, live={}, data_source="", tested_at=None, evolved_at=None,
                )
            )
            s.execute(update(Lesson).where(Lesson.active.is_(True)).values(active=False))
            s.execute(delete(KV).where(KV.key.like("hour_profile:%")))
        self.agents["cashier"].cancel_pending("troca da origem dos preços")
        kv_set("paper_reset_at", now.isoformat())
        for key in ("team.avoid_hours", "manager.weights", "manager.horizon_weights", "risk_state"):
            kv_set(key, None)
        self.market.clear_cache()
        self.invalidate_exit_params()
        manager = self.agents["manager"]
        manager.plan = []
        self.agents["schedule"]._profiles.clear()
        self.publish_office(plan=[])
        infra = self.agents["infra"]
        if family == "real":
            text = "📡 Agora estamos com preços reais do mercado! A conta simulada recomeça do zero e a Estela refaz todos os testes com o histórico de verdade."
        else:
            text = "🧪 Voltamos para o mercado simulado (sem preços reais). A conta simulada recomeça e as estratégias serão retestadas."
        infra.tell("all", text, kind="alerta")
        record_activity("infra", text + " Posições abertas foram anuladas sem lucro nem prejuízo; lições e horários evitados recomeçam.", kind="system", level="warning")
        for agent_id, job in (("strategist", "ranking"), ("schedule", "hours"), ("infra", "warm")):
            self.agents[agent_id].request(job)
        log.info("origem dos preços: %s -> %s (aprendizado reiniciado)", previous, family)

    async def daily_loop(self) -> None:
        """Confere a cada 30 s se chegou a hora da daily e se a pausa depois dela já acabou."""
        while True:
            await asyncio.sleep(30)
            try:
                self.check_break()
                if self.daily.due():
                    await self.daily.run()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("falha na daily")

    # ------------------------------------------------- pausa depois da daily
    @staticmethod
    def break_info() -> dict | None:
        info = kv_get(BREAK_KEY)
        return info if isinstance(info, dict) and info.get("until") else None

    def start_break(self, minutes: int) -> None:
        """Depois da daily automática: escritório fecha por ``minutes`` e reabre sozinho.

        Ninguém abre posição nova na pausa (plano vazio e ordens stop armadas canceladas); o Caio
        continua protegendo as posições abertas (stop, alvo e trailing)."""
        now = datetime.now(timezone.utc)
        until = now + timedelta(minutes=minutes)
        info = {"until": until.isoformat(), "started": now.isoformat(), "minutes": int(minutes)}
        kv_set(BREAK_KEY, info)
        update_config({"system_running": False})
        bus.publish({"type": "system", "running": False})
        self.agents["cashier"].cancel_pending("pausa depois da daily")
        self.publish_office(office_break=info)
        back = until.astimezone(ZoneInfo(self.settings.timezone)).strftime("%H:%M")
        text = f"🌙 Daily feita! Escritório fechado por {minutes} min para a equipe descansar; voltamos às {back}. O Caio segue de olho nas posições abertas."
        self.agents["manager"].tell("all", text, kind="daily", data={"break_until": info["until"]})
        record_activity("manager", f"Pausa depois da daily: escritório fechado até {back} (reabre sozinho)", kind="system")
        self._wake_all()

    def check_break(self) -> bool:
        """Pausa vencida → reabre o escritório. Devolve True se reabriu agora."""
        info = self.break_info()
        if info is None:
            return False
        if datetime.now(timezone.utc).isoformat() < info["until"]:
            return False
        self.end_break(reopen=True)
        return True

    def end_break(self, reopen: bool) -> None:
        """Encerra a pausa. ``reopen=False`` quando o dono ligou/desligou na mão (vale a escolha dele)."""
        if self.break_info() is None:
            return
        kv_set(BREAK_KEY, None)
        self.publish_office(office_break={})
        if not reopen:
            return
        update_config({"system_running": True})
        bus.publish({"type": "system", "running": True})
        self.agents["manager"].tell("all", "☀️ Fim da pausa! Escritório aberto de novo: cada um na sua mesa, aplicando o que combinamos na daily.", kind="daily")
        record_activity("manager", "Pausa encerrada: escritório aberto de novo", kind="system")
        self._wake_all()

    def _wake_all(self) -> None:
        """Acorda os agentes para perceberem na hora que o escritório fechou/abriu (sem forçar tarefas)."""
        for agent in self.agents.values():
            agent._wake.set()

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

    def agent(self, agent_id: str):
        return self.agents[agent_id]

    @property
    def broker(self) -> PaperBroker | LiveBroker:
        return self.live if get_config().mode == "live" else self.paper

    # ---------------------------------------------------------- escritório
    def publish_office(self, **info) -> None:
        """Dados que decoram o escritório (TV de notícias, quadro, telão, cofre...)."""
        clean = {k: v for k, v in info.items() if v is not None}
        if not clean:
            return
        self.office_info.update(clean)
        bus.publish({"type": "office", "data": clean})

    def meeting(self, host: str, participants: list[str], lines: list[dict], title: str = "Reunião") -> None:
        bus.publish({"type": "meeting", "id": uuid.uuid4().hex[:8], "host": host, "participants": participants, "lines": lines, "title": title})

    def snapshot(self) -> dict:
        cfg = get_config()
        infra = self.agents["infra"]
        return {
            "type": "snapshot",
            "agents": [a.snapshot() for a in self.agents.values()],
            "system": {
                "running": cfg.system_running,
                "mode": cfg.mode,
                "data_source": self.market.source(),
                "ai": self.llm.available(),
                "mt5": getattr(infra, "status", {}),
            },
            "office": self.office_info,
        }

    # ------------------------------------------------------ gestão de saída
    def exit_params(self) -> dict:
        now = time.time()
        if self._exit_cache and now - self._exit_cache[0] < 60:
            return self._exit_cache[1]
        cfg = get_config()
        params = {"break_even_r": cfg.break_even_r, "trailing_start_r": cfg.trailing_start_r, "trailing_atr": cfg.trailing_atr_mult}
        if cfg.adaptive_exits:
            learned = self.agents["cashier"].skills.get("gestao_saida").get("params", {}).get("learned")
            if learned:
                params.update({k: float(v) for k, v in learned.items() if k in params})
        self._exit_cache = (now, params)
        return params

    def invalidate_exit_params(self) -> None:
        self._exit_cache = None

    # ------------------------------------------------------------- fluxo
    async def submit_signal(self, signal_id: int) -> int | None:
        """Sinal da Estrategista → aprovação do Gerente → risco da Rita → execução do Caio."""
        async with self._pipeline_lock:
            return await self._pipeline(signal_id)

    async def _pipeline(self, signal_id: int) -> int | None:
        manager = self.agents["manager"]
        risk = self.agents["risk"]
        cashier = self.agents["cashier"]
        with session_scope() as s:
            sig = s.get(Signal, signal_id)
            if sig is None:
                return None
            data = {
                "symbol": sig.symbol, "direction": sig.direction, "profile_id": sig.profile_id,
                "entry": sig.trigger if sig.entry_type == "stop" and sig.trigger else sig.price, "sl": sig.sl,
                "strategy": sig.strategy, "timeframe": sig.timeframe,
            }
        ok, reason, risk_mult = manager.review_signal(data["symbol"], data["profile_id"], data["direction"])
        side = "compra" if data["direction"] == "buy" else "venda"
        if not ok:
            self._reject(signal_id, "gerente", reason)
            manager.tell("strategist", "✋ " + manager.line("veto", symbol=data["symbol"], reason=reason[:80]), kind="resposta")
            return None
        manager.work(f"Aprovado: {data['symbol']}. Rita, calcula o risco?", "agent:risk", "✅")
        manager.tell("risk", "✅ " + manager.line("approve", symbol=data["symbol"], side=side), kind="pedido")
        verdict = await risk.evaluate(data["symbol"], data["direction"], data["entry"], data["sl"], risk_mult)
        manager.idle("Acompanhando o plano")
        if not verdict.ok:
            self._reject(signal_id, "risco", verdict.reason)
            risk.tell("manager", "❌ " + risk.line("veto", reason=verdict.reason[:90]), kind="resposta")
            return None
        risk.work(f"Lote {verdict.volume:g} ({verdict.risk_pct:.2f}% de risco)".replace(".", ","), "agent:cashier", "🛡️")
        risk.tell("cashier", "🛡️ " + risk.line("lot", volume=f"{verdict.volume:g}", symbol=data["symbol"], risk=f"{verdict.risk_money:.2f}".replace(".", ",")), kind="pedido")
        with session_scope() as s:
            sig = s.get(Signal, signal_id)
            if sig is not None:
                sig.status = "aprovado"
                sig.context = {**(sig.context or {}), "volume": verdict.volume, "risk_money": verdict.risk_money}
        setup = next((p for p in manager.active_plan() if p["profile_id"] == data["profile_id"]), {})
        trade_id = await cashier.execute(signal_id, verdict.volume, verdict.risk_money, verdict.risk_pct, setup.get("votes", {}))
        risk.idle("Vigiando o risco")
        return trade_id

    @staticmethod
    def _reject(signal_id: int, who: str, reason: str) -> None:
        with session_scope() as s:
            sig = s.get(Signal, signal_id)
            if sig is None:
                return
            sig.status = "vetado"
            sig.reason = f"{who}: {reason}"[:500]
            text = f"Sinal de {'compra' if sig.direction == 'buy' else 'venda'} em {sig.symbol} vetado pelo {who}: {reason}"
            agent = "manager" if who == "gerente" else "risk"
        record_activity(agent, text, kind="signal")

    def on_trade_closed(self, trade: Trade) -> None:
        try:
            self.agents["auditor"].on_trade_closed(trade)
        except Exception:
            log.exception("falha ao auditar a operação %s", trade.id)

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
from app.core.market_hours import all_closed, next_open
from app.core.units import money_text
from app.models import KV, EquitySnapshot, Lesson, Signal, StrategyProfile, Trade
from app.runtime import get_config, update_config
from app.services.llm import LLMService

log = logging.getLogger("metabot.office")

BREAK_KEY = "office.break"
WEEKEND_SKIP_KEY = "office.weekend_skip"  # dono religou na mão no fim de semana: não fecha de novo até essa hora

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
        self.sync_paper_currency()
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

    def sync_paper_currency(self) -> bool:
        """Trocou a moeda da conta simulada (real ↔ dólar): a conta recomeça do saldo inicial na moeda nova.

        Resultados em moedas diferentes não se somam, então as operações simuladas antigas vão para o
        arquivo (``paper-usd``/``paper-brl``) e as abertas são anuladas sem lucro nem prejuízo. O que a
        equipe aprendeu fica, porque é medido em R (múltiplos do risco), não em dinheiro."""
        currency = get_config().paper_currency
        previous = kv_get("paper.currency")
        if previous == currency:
            return False
        kv_set("paper.currency", currency)
        if previous is None:
            with session_scope() as s:
                if s.scalar(select(Trade.id).where(Trade.mode == "paper").limit(1)) is None:
                    return False
            previous = "USD"  # antes desta opção a conta simulada era sempre em dólar
        now = datetime.now(timezone.utc)
        archived = f"paper-{previous.lower()}"
        with session_scope() as s:
            for tr in s.scalars(select(Trade).where(Trade.mode == "paper", Trade.status == "open")):
                tr.status, tr.exit_price, tr.exit_time, tr.exit_reason, tr.pnl, tr.pnl_r = "closed", tr.entry_price, now, "troca de moeda", 0.0, 0.0
            s.execute(update(Trade).where(Trade.mode == "paper").values(mode=archived))
            s.execute(update(EquitySnapshot).where(EquitySnapshot.mode == "paper").values(mode=archived))
        self.agents["cashier"].cancel_pending("troca da moeda da conta simulada")
        kv_set("paper_reset_at", now.isoformat())
        kv_set("risk_state", None)
        self.market.clear_cache()
        name = "reais (R$)" if currency == "BRL" else "dólares (US$)"
        cfg = get_config()
        text = f"💱 A conta simulada agora é em {name}. Ela recomeça com {cfg.paper_initial_balance:,.2f} e os lotes passam a ser calculados nessa moeda.".replace(",", "X").replace(".", ",").replace("X", ".")
        self.agents["risk"].tell("all", text, kind="alerta")
        record_activity("risk", text + " Posições simuladas abertas foram anuladas sem lucro nem prejuízo.", kind="system", level="warning")
        log.info("moeda da conta simulada: %s -> %s", previous, currency)
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
                self.check_weekend()
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

    def start_break(self, minutes: int, until: datetime | None = None, kind: str = "daily") -> None:
        """Escritório fecha e reabre sozinho: depois da daily automática (``minutes``) ou no fim de
        semana, com o mercado fechado (``until`` = reabertura do mercado, ``kind="weekend"``).

        Ninguém abre posição nova na pausa (plano vazio e ordens stop armadas canceladas); o Caio
        continua protegendo as posições abertas (stop, alvo e trailing)."""
        now = datetime.now(timezone.utc)
        until = until or now + timedelta(minutes=minutes)
        minutes = int((until - now).total_seconds() // 60)
        info = {"until": until.isoformat(), "started": now.isoformat(), "minutes": minutes, "kind": kind}
        kv_set(BREAK_KEY, info)
        update_config({"system_running": False})
        bus.publish({"type": "system", "running": False})
        self.agents["cashier"].cancel_pending("mercado fechado (fim de semana)" if kind == "weekend" else "pausa depois da daily")
        self.publish_office(office_break=info)
        local = until.astimezone(ZoneInfo(self.settings.timezone))
        back = local.strftime("%H:%M")
        if kind == "weekend":
            day = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"][local.weekday()]
            text = f"🏖️ Mercado fechado: escritório fechado no fim de semana. Voltamos {day} às {back}. O Caio segue de olho nas posições abertas."
            self.agents["manager"].tell("all", text, kind="info", data={"break_until": info["until"]})
            record_activity("manager", f"Fim de semana: escritório fechado até {day} {back} (reabre sozinho com o mercado)", kind="system")
        else:
            text = f"🌙 Daily feita! Escritório fechado por {minutes} min para a equipe descansar; voltamos às {back}. O Caio segue de olho nas posições abertas."
            self.agents["manager"].tell("all", text, kind="daily", data={"break_until": info["until"]})
            record_activity("manager", f"Pausa depois da daily: escritório fechado até {back} (reabre sozinho)", kind="system")
        self._wake_all()

    def check_weekend(self, now: datetime | None = None) -> bool:
        """Mercado de todos os ativos fechado → fecha o escritório até a reabertura. Devolve True se fechou agora.

        Não fecha se o dono desligou o escritório (vale a escolha dele) nem se ele religou na mão
        durante o fim de semana (``office.weekend_skip``)."""
        cfg = get_config()
        now = now or datetime.now(timezone.utc)
        if not cfg.weekend_close or not all_closed(cfg.watchlist, now):
            return False
        info = self.break_info()
        if info and info.get("kind") == "weekend":
            return False
        if not cfg.system_running and info is None:
            return False
        if (kv_get(WEEKEND_SKIP_KEY) or "") > now.isoformat():
            return False
        until = next_open(cfg.watchlist, now)
        if until is None:
            return False
        self.start_break(0, until=until, kind="weekend")
        return True

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
        info = self.break_info()
        if info is None:
            return
        kv_set(BREAK_KEY, None)
        self.publish_office(office_break={})
        if not reopen:
            return
        update_config({"system_running": True})
        bus.publish({"type": "system", "running": True})
        if info.get("kind") == "weekend":
            self.agents["manager"].tell("all", "☀️ Mercado aberto! Começa a semana: cada um na sua mesa.", kind="info")
        else:
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
        risk.tell("cashier", "🛡️ " + risk.line("lot", volume=f"{verdict.volume:g}", symbol=data["symbol"], risk=money_text(verdict.risk_money, self.market.account_currency())), kind="pedido")
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

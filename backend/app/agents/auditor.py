"""Aurora, a auditora (IA opcional): confere o resultado real e distribui o aprendizado.

- A cada operação fechada: atualiza o resultado real da estratégia, compara com
  o backtest e, se o real estiver claramente abaixo do prometido, coloca a
  estratégia "em observação" para a Estela revalidar.
- Dá XP para quem acertou (estratégia, gerente, notícias, risco, caixa).
- Todo dia às 22h (horário de Brasília): diário de trading. Com IA,
  escreve a análise e registra lições que entram no prompt do Gerente e da Nina.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import select

from app.agents.base import Agent, AgentProfile
from app.agents.skills import SkillDef, add_lesson, playbook
from app.config import get_settings
from app.core.metrics import wilson_upper
from app.core.strategies import REGISTRY
from app.db import session_scope
from app.kv import kv_get, kv_set
from app.models import Lesson, StrategyProfile, Trade
from app.services.llm import to_json


class AILesson(BaseModel):
    agent: str
    text: str


class AIJournal(BaseModel):
    summary: str
    lessons: list[AILesson]
    mood: str


class AuditorAgent(Agent):
    profile = AgentProfile(
        id="auditor",
        name="Aurora",
        role="Auditoria",
        emoji="🎓",
        uses_ai=True,
        description="Compara o resultado real com o backtest, distribui XP para a equipe, escreve o diário do dia e registra lições que os outros agentes passam a seguir.",
    )
    interval = 60.0
    idle_task = "Auditando as operações"
    skill_defs = [
        SkillDef("auditoria", "Auditoria de operações", "Confere cada operação e compara com o prometido no backtest."),
        SkillDef("diario", "Diário de trading", "Escreve o resumo do dia."),
        SkillDef("licoes", "Lições aprendidas", "Transforma erros e acertos em regras para a equipe."),
    ]

    async def tick(self) -> None:
        tz = ZoneInfo(get_settings().timezone)
        now = datetime.now(tz)
        today = now.date().isoformat()
        if now.hour >= 22 and kv_get("auditor.last_journal") != today:
            await self.daily_journal()
            kv_set("auditor.last_journal", today)
        if self.due("prune", 7 * 86400):
            self.prune_lessons()

    # ------------------------------------------------------- por operação
    def on_trade_closed(self, trade: Trade) -> None:
        win = trade.pnl > 0
        self.skills.gain("auditoria", 2, f"operação #{trade.id} auditada")
        if trade.profile_id:
            with session_scope() as s:
                prof = s.get(StrategyProfile, trade.profile_id)
                if prof is not None:
                    live = dict(prof.live or {})
                    live["n"] = int(live.get("n", 0)) + 1
                    live["wins"] = int(live.get("wins", 0)) + (1 if win else 0)
                    live["sum_r"] = round(float(live.get("sum_r", 0.0)) + trade.pnl_r, 4)
                    live["last"] = datetime.now(timezone.utc).isoformat()
                    prof.live = live
                    expected = float((prof.metrics or {}).get("win_rate", 0.0))
                    n, wins = live["n"], live["wins"]
                    # real claramente abaixo do backtest: nem o melhor cenário estatístico alcança o prometido
                    if n >= 10 and prof.status == "aprovada" and wilson_upper(wins, n) < expected - 0.05 and live["sum_r"] < 0:
                        prof.status = "observacao"
                        name = REGISTRY[prof.strategy].name if prof.strategy in REGISTRY else prof.strategy
                        msg = f"{name} em {prof.symbol} {prof.timeframe}: acerto real {wins}/{n} bem abaixo dos {expected:.0%} do backtest. Coloquei em observação para a Estela revalidar."
                        self.log(msg.replace(".", ",", 1), kind="audit", level="warning")
                        add_lesson("strategist", f"Resultado real abaixo do backtest em {name} {prof.symbol} {prof.timeframe}; revalidar antes de voltar a usar.", {"n": n, "wins": wins, "expected": expected})
                        self.skills.gain("licoes", 5, "desvio entre real e backtest detectado")
        strategist = self.office.agent("strategist")
        if trade.strategy in REGISTRY:
            strategist.skills.gain(trade.strategy, 10 if win else 1, "resultado real")
        if win:
            news_score = self.office.agent("news").symbol_score(trade.symbol)["score"]
            d = 1 if trade.direction == "buy" else -1
            if news_score * d > 0.2:
                self.office.agent("news").skills.gain("sentimento_ativos", 3, "notícias a favor de operação vencedora")
        self.office.agent("manager").on_trade_closed(trade)
        self.office.agent("risk").on_trade_closed(trade)

    # -------------------------------------------------------------- diário
    async def daily_journal(self) -> None:
        tz = ZoneInfo(get_settings().timezone)
        start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        with session_scope() as s:
            trades = list(s.scalars(select(Trade).where(Trade.status == "closed", Trade.exit_time >= start)))
            rows = [
                {
                    "symbol": t.symbol, "strategy": t.strategy, "timeframe": t.timeframe, "direction": t.direction,
                    "pnl": round(t.pnl, 2), "r": round(t.pnl_r, 2), "exit": t.exit_reason,
                    "entry_hour_local": t.entry_time.astimezone(tz).hour, "mode": t.mode,
                }
                for t in trades
            ]
        self.work("Escrevendo o diário do dia", "desk", "📝")
        total = sum(r["pnl"] for r in rows)
        wins = sum(1 for r in rows if r["pnl"] > 0)
        base = f"Dia com {len(rows)} operações, {wins} vencedoras, resultado {total:+.2f}.".replace(".", ",", 1) if rows else "Dia sem operações."
        summary = base
        if rows and self.office.llm.available():
            res = await self.office.llm.complete_json(
                agent=self.id,
                purpose="diário do dia",
                tier="auditor",
                system=playbook("auditor"),
                user=f"Operações de hoje:\n{to_json(rows)}\nRisco: {to_json(self.office.agent('risk').status())}",
                schema_model=AIJournal,
                effort="medium",
                max_tokens=8000,
            )
            if res.ok and res.data is not None:
                summary = f"{base} {res.data.summary}"
                valid = {"manager", "strategist", "risk", "cashier", "news", "schedule", "all"}
                for lesson in res.data.lessons[:3]:
                    if lesson.agent in valid and lesson.text.strip():
                        add_lesson(lesson.agent, lesson.text, {"day": start.date().isoformat()}, source="ai")
                        self.skills.gain("licoes", 5, "lição registrada")
        if rows:
            losers = [r for r in rows if r["pnl"] <= 0]
            by_hour: dict[int, list[float]] = {}
            for r in losers:
                by_hour.setdefault(r["entry_hour_local"], []).append(r["r"])
            for hour, rs in by_hour.items():
                if len(rs) >= 3:
                    add_lesson("schedule", f"{len(rs)} perdas hoje com entrada às {hour}h (horário de Brasília): observar esse horário.", {"hour": hour, "r": rs})
        self.log(f"📝 Diário do dia: {summary}", kind="journal")
        self.say("📝 Diário do dia publicado", "📝")
        self.skills.gain("diario", 5, "diário publicado")
        self.idle("Auditando as operações")

    def prune_lessons(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=45)
        with session_scope() as s:
            for lesson in s.scalars(select(Lesson).where(Lesson.active.is_(True), Lesson.ts < cutoff)):
                lesson.score -= 1.0
                if lesson.score <= 0:
                    lesson.active = False

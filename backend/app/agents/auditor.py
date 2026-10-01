"""Aurora, a auditora (IA opcional): confere o resultado real e distribui o aprendizado.

- A cada operação fechada: atualiza o resultado real da estratégia, compara com
  o backtest e, se o real estiver claramente abaixo do prometido, coloca a
  estratégia "em observação" para a Estela revalidar.
- Dá XP para quem acertou (estratégia, gerente, notícias, risco, caixa).
- Na daily das 19h, fecha a reunião com as lições do dia (veja ``daily.py``);
  as lições entram no prompt dos agentes com IA no dia seguinte.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.agents.base import Agent, AgentProfile
from app.agents.skills import SkillDef, add_lesson
from app.core.metrics import wilson_upper
from app.core.strategies import REGISTRY
from app.db import session_scope
from app.models import Lesson, StrategyProfile, Trade


class AuditorAgent(Agent):
    profile = AgentProfile(
        id="auditor",
        name="Aurora",
        role="Auditoria",
        emoji="🎓",
        uses_ai=True,
        description="Compara o resultado real com o backtest, distribui XP para a equipe e fecha a daily das 19h com as lições que todos passam a seguir.",
    )
    interval = 60.0
    idle_task = "Auditando as operações"
    skill_defs = [
        SkillDef("auditoria", "Auditoria de operações", "Confere cada operação e compara com o prometido no backtest."),
        SkillDef("diario", "Diário de trading", "Escreve o resumo do dia."),
        SkillDef("licoes", "Lições aprendidas", "Transforma erros e acertos em regras para a equipe."),
    ]

    async def tick(self) -> None:
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
                        # 24 h fora do plano antes da revalidação (senão o mesmo backtest aprovaria de novo na hora)
                        live["revalidate_after"] = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
                        live["flagged_by"] = "auditor"
                        prof.live = dict(live)
                        name = REGISTRY[prof.strategy].name if prof.strategy in REGISTRY else prof.strategy
                        msg = f"{name} em {prof.symbol} {prof.timeframe}: acerto real {wins}/{n} bem abaixo dos {expected:.0%} do backtest. Coloquei em observação para a Estela revalidar."
                        self.log(msg.replace(".", ",", 1), kind="audit", level="warning")
                        self.tell("strategist", "🔎 " + self.line("observation", name=name, symbol=prof.symbol, timeframe=prof.timeframe), kind="pedido")
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

    def prune_lessons(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=45)
        with session_scope() as s:
            for lesson in s.scalars(select(Lesson).where(Lesson.active.is_(True), Lesson.ts < cutoff)):
                lesson.score -= 1.0
                if lesson.score <= 0:
                    lesson.active = False

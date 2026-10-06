"""Gustavo, o gerente (usa IA): reúne a equipe e decide horário, ativo e estratégia.

Monta os candidatos a partir dos setups aprovados pela Estrategista, pontua
cada um com a evidência do backtest, a qualidade da hora (Hugo), as notícias
(Nina) e o resultado real (Auditora), e pede à IA o plano final — que só
pode escolher entre os candidatos. Sem IA, vale a pontuação.

A skill "Calibração da equipe" é aprendizado de verdade: depois de cada
operação, quem "votou" certo ganha peso na pontuação e quem errou perde
(pesos multiplicativos).
"""

from __future__ import annotations

import math
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import select

from app.agents.base import Agent, AgentProfile
from app.agents.personas import persona_prompt
from app.agents.review import CHECK_BARS, PositionContext, counterfactual, decide as review_decide, learn_patience
from app.agents.skills import SkillDef, active_lessons, playbook
from app.config import get_settings
from app.core.assets import symbol_currencies
from app.core.horizons import minutes_from_metrics
from app.db import session_scope
from app.kv import kv_get, kv_set
from app.models import Decision, StrategyProfile, Trade
from app.runtime import TIMEFRAME_SECONDS, get_config
from app.services.llm import to_json

DEFAULT_WEIGHTS = {"strategy": 0.45, "hour": 0.2, "news": 0.15, "live": 0.2}
DEFAULT_HORIZON_WEIGHTS = {"scalp": 1.0, "day": 1.0, "swing": 1.0}
# dias úteis em 30 dias corridos (o backtest conta operações por mês corrido)
TRADING_DAYS_PER_MONTH = 21.4


def activity_factor(signals_per_day: float) -> float:
    """Peso da frequência na ordem do plano: um setup que dá ~1 entrada por dia vale inteiro; um que
    dá uma por semana perde até 25%. Sem isso o plano enchia de setups lentos (H4) e o dia passava
    sem nenhum sinal, enquanto as aprovadas mais ativas ficavam de fora."""
    return 0.75 + 0.25 * min(1.0, max(0.0, signals_per_day))


class AIPick(BaseModel):
    candidate_id: int
    direction: str
    risk_mult: float
    reason: str


class AIPlan(BaseModel):
    picks: list[AIPick]
    rationale: str


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _local_day() -> str:
    return datetime.now(ZoneInfo(get_settings().timezone)).date().isoformat()


class ManagerAgent(Agent):
    profile = AgentProfile(
        id="manager",
        name="Gustavo",
        role="Gerente",
        emoji="👔",
        uses_ai=True,
        description="Reúne notícias, horários, backtests e risco e decide com a IA quais setups (ativo, tempo gráfico e estratégia) ficam ativos.",
    )
    interval = 15.0
    idle_task = "Acompanhando o plano"
    skill_defs = [
        SkillDef("tomada_decisao", "Tomada de decisão", "Escolhe setups que dão resultado real."),
        SkillDef("leitura_contexto", "Leitura de contexto", "Combina notícias, horários e calendário na decisão."),
        SkillDef("confianca_equipe", "Calibração da equipe", "Aprende quanto confiar em cada colega a partir dos resultados."),
        SkillDef("gestao_posicoes", "Revisão de posições", "Revisa as posições abertas com o mercado de agora e aprende com o que teria acontecido."),
    ]

    def __init__(self, office):
        super().__init__(office)
        self.plan: list[dict] = []
        self.plan_expires: float = 0.0
        self.decision_id: int | None = None
        self.last_meeting_at = 0.0
        self.rationale = ""
        # Economia: o plano da IA é reaproveitado enquanto os candidatos não mudarem.
        self._ai_cache: dict | None = None
        self._ended_day: str | None = None

    async def tick(self) -> None:
        cfg = get_config()
        # revisão das posições abertas (de hora em hora; o botão na tela pede uma agora)
        if (cfg.position_review_minutes > 0 or "review" in self._forced) and self.due("review", max(1, cfg.position_review_minutes) * 60):
            await self.review_positions()
        stopped = self.office.agent("risk").day_stopped()
        if stopped:
            if self._ended_day != _local_day():
                self.end_day(stopped)
            return
        self.morning_focus()
        if self.due("decide", cfg.decision_interval_minutes * 60):
            await self.decide()
        if self.plan and time.time() > self.plan_expires:
            self.plan = []
            self.office.publish_office(plan=[])

    async def on_system_change(self, running: bool) -> None:
        await super().on_system_change(running)
        if not running:
            self.plan = []

    def weights(self) -> dict[str, float]:
        stored = kv_get("manager.weights") or {}
        w = {k: float(stored.get(k, v)) for k, v in DEFAULT_WEIGHTS.items()}
        total = sum(w.values()) or 1.0
        return {k: v / total for k, v in w.items()}

    def morning_focus(self) -> None:
        """Primeira coisa do dia: lembra a equipe do foco combinado na daily de ontem."""
        today = _local_day()
        if kv_get("daily.focus_told") == today:
            return
        kv_set("daily.focus_told", today)
        report = self.office.daily.last_report(before=today)
        if report and report.get("focus"):
            self.tell("all", "☀️ " + self.line("focus", focus="; ".join(report["focus"][:3])), kind="daily", data={"day": report["day"]})

    def horizon_weights(self) -> dict[str, float]:
        stored = kv_get("manager.horizon_weights") or {}
        return {k: float(stored.get(k, v)) for k, v in DEFAULT_HORIZON_WEIGHTS.items()}

    def learn_horizons(self, summary: dict, save: bool = True) -> list[str]:
        """Scalper x day trade x posição longa: ajusta a preferência com o backtest e o resultado real.

        Peso entre 0,75 e 1,25, mudando no máximo 30% do caminho por dia (aprende devagar). A preferência é
        relativa: os alvos são centrados em 1 (se todos vão bem, ninguém ganha peso à toa).
        ``save=False`` só calcula (prévia da daily)."""
        old = self.horizon_weights()
        targets: dict[str, float] = {}
        labels: dict[str, str] = {}
        for row in summary.get("horizons", []):
            h = row["key"]
            labels[h] = row.get("label", h)
            bt = max(-1.0, min(1.0, float(row.get("bt_oos_expectancy_r") or 0.0) * 4))
            n = int(row.get("live_trades") or 0)
            live = max(-1.0, min(1.0, float(row.get("live_r") or 0.0) / n * 2)) if n >= 3 else 0.0
            has_bt = bool(row.get("approved"))
            targets[h] = 1.0 + 0.25 * ((0.6 * bt if has_bt else 0.0) + 0.4 * live)
        if len(targets) > 1:
            shift = sum(targets.values()) / len(targets) - 1.0
            targets = {h: t - shift for h, t in targets.items()}
        new: dict[str, float] = {}
        notes = []
        for h, target in targets.items():
            value = round(min(1.25, max(0.75, 0.7 * old.get(h, 1.0) + 0.3 * target)), 3)
            new[h] = value
            if abs(value - old.get(h, 1.0)) >= 0.01:
                notes.append(f"{labels[h]}: peso {old.get(h, 1.0):.2f} → {value:.2f}".replace(".", ","))
        if save:
            kv_set("manager.horizon_weights", new)
            self.skills.update("leitura_contexto", params={"horizon_weights": new})
        return notes

    def active_plan(self) -> list[dict]:
        if not self.plan or time.time() > self.plan_expires:
            return []
        if self.office.agent("risk").day_stopped():
            return []
        return self.plan

    def end_day(self, kind: str) -> None:
        """A Rita encerrou o dia (meta ou limite): plano vazio até amanhã."""
        self._ended_day = _local_day()
        self.plan = []
        self.plan_expires = 0.0
        self._ai_cache = None
        target = kind == "target"
        self.rationale = "Dia encerrado: meta de ganho batida." if target else "Dia encerrado: limite de perda atingido."
        self.office.publish_office(plan=[], plan_rationale=self.rationale)
        self.tell("all", self.line("day_stop_target" if target else "day_stop_loss"), kind="comemoracao" if target else "alerta")
        self.set_state("idle", "manager", "Dia encerrado com a meta batida 🎯" if target else "Dia encerrado no limite de perda", "🎯" if target else "🛑")

    # --------------------------------------------------------- candidatos
    def build_candidates(self) -> list[dict]:
        cfg = get_config()
        strategist = self.office.agent("strategist")
        news = self.office.agent("news")
        schedule = self.office.agent("schedule")
        w = self.weights()
        hw = self.horizon_weights()
        avoid = kv_get("team.avoid_hours") or {}
        hour_now = datetime.now(timezone.utc).hour
        now_iso = datetime.now(timezone.utc).isoformat()

        def avoided(symbol: str) -> bool:
            """Hora que a daily mandou evitar (no ativo ou em todos: chave "*"), válida até amanhã à noite."""
            for key in (symbol, "*"):
                entry = avoid.get(key) or {}
                if hour_now in entry.get("hours_utc", []) and entry.get("until", "") >= now_iso:
                    return True
            return False

        cands = []
        for prof in strategist.ranking(only_approved=True, limit=300):
            if prof["symbol"] not in cfg.watchlist or prof["timeframe"] not in cfg.timeframes:
                continue
            ns = news.symbol_score(prof["symbol"])
            hour_q = schedule.hour_quality(prof["symbol"])
            blackout = schedule.blackout(prof["symbol"])
            live = prof.get("live") or {}
            n_live = int(live.get("n", 0))
            live_s = 0.5 if n_live < 3 else _sigmoid(2.0 * float(live.get("sum_r", 0.0)) / n_live)
            news_strength = abs(ns["score"]) * ns["confidence"]
            news_s = 1.0 - 0.5 * news_strength
            strat_s = float(prof["score"])
            total = w["strategy"] * strat_s + w["hour"] * hour_q + w["news"] * news_s + w["live"] * live_s
            horizon = prof.get("horizon") or "day"
            total *= hw.get(horizon, 1.0)
            blocked = []
            if avoided(prof["symbol"]):
                blocked.append(f"hora evitada pela daily ({hour_now}h UTC)")
            if blackout:
                blocked.append(f"evento {blackout['title']} ({blackout['currency']})")
            if hour_q < cfg.min_hour_quality:
                blocked.append(f"hora fraca ({hour_q:.2f})")
            direction = "both"
            if cfg.use_news_filter and news_strength >= 0.35:
                direction = "long" if ns["score"] > 0 else "short"
            m = prof["metrics"] or {}
            oos = prof["oos_metrics"] or {}
            per_day = float(m.get("trades_per_month") or 0.0) / TRADING_DAYS_PER_MONTH
            cands.append(
                {
                    "id": len(cands) + 1,
                    "profile_id": prof["id"],
                    "symbol": prof["symbol"],
                    "timeframe": prof["timeframe"],
                    "strategy": prof["strategy"],
                    "strategy_name": prof["strategy_name"],
                    "horizon": horizon,
                    "avg_minutes": prof.get("avg_minutes"),
                    "score": round(total, 3),
                    "signals_per_day": round(per_day, 2),
                    "priority": round(total * activity_factor(per_day), 3),
                    "votes": {"strategy": round(strat_s, 3), "hour": round(hour_q, 3), "news": round(news_s, 3), "live": round(live_s, 3)},
                    "suggested_direction": direction,
                    "news": {"score": ns["score"], "confidence": ns["confidence"], "alerts": ns["alerts"][:2]},
                    "hour_quality": round(hour_q, 3),
                    "backtest": {
                        "trades": m.get("trades"), "win_rate": m.get("win_rate"), "wilson_lb": m.get("wilson_lb"),
                        "expectancy_r": m.get("expectancy_r"), "profit_factor": m.get("profit_factor"), "max_dd_pct": m.get("max_dd_pct"),
                        "oos_trades": oos.get("trades"), "oos_win_rate": oos.get("win_rate"), "oos_expectancy_r": oos.get("expectancy_r"),
                    },
                    "live": {"trades": n_live, "sum_r": round(float(live.get("sum_r", 0.0)), 2), "wins": int(live.get("wins", 0))},
                    "blocked": blocked,
                }
            )
        cands.sort(key=lambda c: c["priority"], reverse=True)
        for i, c in enumerate(cands, 1):
            c["id"] = i
        return cands

    def deterministic_plan(self, cands: list[dict]) -> list[dict]:
        cfg = get_config()
        plan: list[dict] = []
        per_symbol: Counter[str] = Counter()
        for c in cands:
            if len(plan) >= cfg.max_active_setups:
                break
            if c["blocked"] or per_symbol[c["symbol"]] >= cfg.max_setups_per_symbol or c["score"] < 0.45:
                continue
            per_symbol[c["symbol"]] += 1
            plan.append(self._setup(c, c["suggested_direction"], 1.0, "maior pontuação da equipe"))
        return plan

    @staticmethod
    def _setup(c: dict, direction: str, risk_mult: float, reason: str) -> dict:
        return {
            "profile_id": c["profile_id"],
            "symbol": c["symbol"],
            "timeframe": c["timeframe"],
            "strategy": c["strategy"],
            "strategy_name": c["strategy_name"],
            "direction": direction if direction in ("both", "long", "short") else "both",
            "risk_mult": round(min(1.0, max(0.25, float(risk_mult))), 2),
            "reason": reason[:240],
            "votes": c["votes"],
            "score": c["score"],
            "horizon": c.get("horizon", "day"),
        }

    async def ai_plan(self, cands: list[dict]) -> tuple[list[dict] | None, str, str]:
        cfg = get_config()
        risk = self.office.agent("risk").status()
        schedule = self.office.agent("schedule")
        # todas as lições valem aqui: a Estela, a Rita e o Hugo não usam IA, quem aplica é o Gustavo
        lessons = "\n".join(f"- ({l['agent']}) {l['text']}" for l in active_lessons(None, 12)) or "- (nenhuma ainda)"
        system = (
            playbook("manager")
            + "\n\n" + playbook("equipe")
            + "\n\n## Sua personalidade\n" + persona_prompt(["manager"])
            + "\n\n## Lições da equipe (daily e Auditora)\n" + lessons
        )
        last_daily = self.office.daily.last_report(before=_local_day())
        focus = "; ".join((last_daily or {}).get("focus", [])[:3]) or "nenhum"
        top = cands[:15]
        user = (
            f"Agora (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}. "
            f"Limite de setups ativos: {cfg.max_active_setups} (no máximo {cfg.max_setups_per_symbol} por ativo). Ordenação preferida pelo dono: {cfg.rank_by}.\n"
            f"Sessões abertas: {', '.join(self.office.office_info.get('sessions') or []) or 'nenhuma'}.\n"
            f"Eventos de alto impacto nas próximas 6 h: {to_json(schedule.upcoming(6, ['High']))}\n"
            f"Risco: {to_json(risk)}\n"
            f"Foco combinado na daily de ontem: {focus}.\n"
            f"Candidatos (já filtrados pela Estrategista; 'blocked' não pode ser escolhido):\n{to_json(top)}"
        )
        res = await self.office.llm.complete_json(
            agent=self.id,
            purpose="plano do gerente",
            tier="manager",
            system=system,
            user=user,
            schema_model=AIPlan,
            effort="high",
            max_tokens=16000,
        )
        if not res.ok or res.data is None:
            return None, res.error, res.model
        by_id = {c["id"]: c for c in top}
        plan: list[dict] = []
        used: Counter[str] = Counter()
        for pick in res.data.picks:
            c = by_id.get(pick.candidate_id)
            if c is None or c["blocked"] or used[c["symbol"]] >= cfg.max_setups_per_symbol or len(plan) >= cfg.max_active_setups:
                continue
            if any(p["profile_id"] == c["profile_id"] for p in plan):
                continue
            used[c["symbol"]] += 1
            plan.append(self._setup(c, pick.direction, pick.risk_mult, pick.reason))
        return plan, res.data.rationale[:800], res.model

    @staticmethod
    def _fingerprint(cands: list[dict]) -> dict[int, str]:
        """Candidatos livres que a IA viu (os 15 primeiros) e a direção sugerida de cada um."""
        return {c["profile_id"]: c["suggested_direction"] for c in [c for c in cands if not c["blocked"]][:15]}

    def _reuse_ai_plan(self, cands: list[dict]) -> tuple[list[dict], str, str] | None:
        """Plano da IA ainda válido → não gasta outra chamada.

        A pontuação oscila a cada candle e a ordem dos candidatos muda junto; isso sozinho não justifica
        perguntar de novo. Chama a IA outra vez só se o plano venceu, se um escolhido saiu do topo, ficou
        bloqueado ou mudou de direção, ou se apareceu no topo um candidato que a IA ainda não tinha visto.
        """
        cache = self._ai_cache
        cfg = get_config()
        if not cache or time.time() - cache["at"] > cfg.ai_plan_refresh_minutes * 60:
            return None
        seen = cache["fingerprint"]
        free = [c for c in cands if not c["blocked"]]
        if any(seen.get(c["profile_id"]) != c["suggested_direction"] for c in free[: cfg.max_active_setups]):
            return None
        top = {c["profile_id"] for c in free[: max(6, 2 * cfg.max_active_setups)]}
        if any(pid not in top for pid, *_ in cache["picks"]):
            return None
        by_profile = {c["profile_id"]: c for c in cands}
        plan = []
        for pid, direction, risk_mult, reason in cache["picks"]:
            c = by_profile.get(pid)
            if c is None or c["blocked"] or seen.get(pid) != c["suggested_direction"]:
                return None
            plan.append(self._setup(c, direction, risk_mult, reason))
        return plan, cache["rationale"], cache["model"]

    # ------------------------------------------------------------ decisão
    async def decide(self) -> None:
        cfg = get_config()
        self.work("Montando o plano com a equipe", "manager", "🧠")
        cands = self.build_candidates()
        if not cands:
            if self.plan:
                self.log("Nenhum setup aprovado no momento: plano vazio", kind="decision")
            self.plan = []
            self.office.publish_office(plan=[])
            self.idle("Aguardando setups aprovados da Estela")
            return
        ai_used = False
        model = ""
        plan = None
        rationale = ""
        if self.office.llm.available():
            reused = self._reuse_ai_plan(cands)
            if reused is not None:
                plan, rationale, model = reused
                ai_used = True
            else:
                plan, rationale, model = await self.ai_plan(cands)
                if plan is None:
                    self.log(f"IA indisponível para o plano ({rationale}); usei a pontuação da equipe", kind="decision", level="warning")
                    rationale = ""
                    self._ai_cache = None
                else:
                    ai_used = True
                    self._ai_cache = {
                        "at": time.time(),
                        "fingerprint": self._fingerprint(cands),
                        "picks": [(p["profile_id"], p["direction"], p["risk_mult"], p["reason"]) for p in plan],
                        "rationale": rationale,
                        "model": model,
                    }
        if plan is None:
            plan = self.deterministic_plan(cands)
            rationale = rationale or (
                "Plano pela pontuação da equipe (backtest, hora, notícias e resultado real)." if plan else "Nenhum candidato passou nos filtros de hora, calendário e pontuação."
            )
        changed = [(p["profile_id"], p["direction"]) for p in plan] != [(p["profile_id"], p["direction"]) for p in self.plan]
        with session_scope() as s:
            row = Decision(plan=plan, candidates=cands[:20], rationale=rationale, ai=ai_used, model=model, context={"weights": self.weights()})
            s.add(row)
            s.flush()
            self.decision_id = row.id
        for p in plan:
            p["decision_id"] = self.decision_id
        self.plan = plan
        self.plan_expires = time.time() + max(2 * cfg.decision_interval_minutes * 60, 1800)
        self.rationale = rationale
        self.skills.gain("leitura_contexto", 2, "plano montado")
        if changed or time.time() - self.last_meeting_at > 3600:
            self.meeting(cands, plan, rationale)
        self.office.publish_office(plan=plan, plan_rationale=rationale)
        summary = ", ".join(f"{p['symbol']} {p['timeframe']} {p['strategy_name']}" + ("" if p["direction"] == "both" else f" (só {'compra' if p['direction'] == 'long' else 'venda'})") for p in plan) or "ficar de fora"
        if changed:
            self.log(f"Novo plano{' (IA)' if ai_used else ''}: {summary}. {rationale}", kind="decision")
            short = ", ".join(f"{p['symbol']} {p['timeframe']}" for p in plan)
            self.tell("all", "📋 " + (self.line("plan_new", summary=short) if plan else self.line("plan_empty")), kind="info", data={"plan": [p["profile_id"] for p in plan]})
        self.idle("Acompanhando o plano")

    def meeting(self, cands: list[dict], plan: list[dict], rationale: str) -> None:
        self.last_meeting_at = time.time()
        news = self.office.office_info.get("news_mood") or {}
        sessions = self.office.office_info.get("sessions") or []
        events = self.office.agent("schedule").upcoming(6, ["High"])
        risk = self.office.agent("risk").status()
        top = cands[0] if cands else None
        lines = [
            {"agent": "news", "text": f"Humor das notícias: {news.get('label', 'neutro')}."},
            {"agent": "schedule", "text": (f"Abertos: {', '.join(sessions)}. " if sessions else "Mercados calmos. ") + (f"Atenção: {events[0]['title']} ({events[0]['currency']})." if events else "Sem evento forte nas próximas horas.")},
        ]
        if top:
            bt = top["backtest"]
            lines.append({"agent": "strategist", "text": f"Melhor setup: {top['strategy_name']} em {top['symbol']} {top['timeframe']}, {(bt.get('win_rate') or 0):.0%} de acerto em {bt.get('trades')} operações.".replace(".", ",", 1)})
        lines.append({"agent": "risk", "text": f"Risco livre hoje: {risk.get('daily_room_pct', 0):.1f}% · posições {risk.get('open_positions', 0)}/{risk.get('max_positions', 0)}.".replace(".", ",")})
        verdict = "Plano: " + "; ".join(f"{p['symbol']} {p['timeframe']} {p['strategy_name']}" for p in plan) if plan else "Hoje ficamos de fora."
        lines.append({"agent": "manager", "text": verdict[:170]})
        self.office.meeting(host=self.id, participants=["news", "schedule", "strategist", "risk"], lines=lines, title="Reunião de planejamento")

    # ------------------------------------------------------------ sinais
    def review_signal(self, symbol: str, profile_id: int | None, direction: str) -> tuple[bool, str, float]:
        cfg = get_config()
        setup = next((p for p in self.active_plan() if p["profile_id"] == profile_id), None)
        if setup is None:
            return False, "setup fora do plano atual", 1.0
        want = "long" if direction == "buy" else "short"
        if setup["direction"] not in ("both", want):
            return False, f"plano só permite {setup['direction']}", 1.0
        blackout = self.office.agent("schedule").blackout(symbol)
        if blackout:
            return False, f"pausa por evento: {blackout['title']} ({blackout['currency']})", 1.0
        ns = self.office.agent("news").symbol_score(symbol)
        strength = ns["score"] * ns["confidence"]
        if cfg.use_news_filter and abs(strength) >= cfg.news_block_threshold and (strength > 0) != (direction == "buy"):
            return False, f"notícias fortes contra a operação ({ns['score']:+.2f})", 1.0
        return True, setup.get("reason", ""), float(setup.get("risk_mult", 1.0))

    # ------------------------------------------------- revisão das posições
    def patience(self) -> float:
        return float(kv_get("manager.review_patience") or 0.0)

    def _count_review(self, action: str) -> None:
        day = _local_day()
        stats = kv_get("manager.review_day") or {}
        if stats.get("day") != day:
            stats = {"day": day, "close": 0, "sl": 0, "tp": 0, "hold": 0}
        stats[action] = int(stats.get(action, 0)) + 1
        kv_set("manager.review_day", stats)

    async def _position_context(self, tr: Trade) -> PositionContext | None:
        cfg = get_config()
        market = self.office.market
        spec = await market.spec(tr.symbol)
        tick = await market.tick(tr.symbol)
        if not tick.get("open", True):
            return None
        d = 1 if tr.direction == "buy" else -1
        price = tick["bid"] if d > 0 else tick["ask"]
        risk = abs(tr.entry_price - (tr.initial_sl or tr.entry_price))
        tf = tr.timeframe or "H1"
        tf_sec = TIMEFRAME_SECONDS.get(tf, 3600)
        bars = await market.rates(tr.symbol, tf, 300, closed_only=True, max_age=60)
        if bars.n < 60:
            return None
        atr = bars.atr(14)
        entry_ts = tr.entry_time.replace(tzinfo=tr.entry_time.tzinfo or timezone.utc).timestamp()
        # melhor momento da operação: só candles que abriram depois da entrada (a máxima do candle
        # da entrada pode ter sido antes dela) + o preço de agora
        after = [i for i in range(bars.n) if bars.time[i] >= entry_ts]
        i0 = after[0] if after else bars.n
        if d > 0:
            best = max([float(x) for x in bars.high[i0:]] + [price])
        else:
            best = min([float(x) for x in bars.low[i0:]] + [price])
        mfe_r = max(0.0, (best - tr.entry_price) * d / risk) if risk > 0 else 0.0
        ema_f, ema_s = bars.ema(20), bars.ema(50)
        adx = float(bars.adx(14)[0][-1])
        gap = (ema_f[-1] - ema_s[-1]) * d
        slope = (ema_f[-1] - ema_f[-4]) * d
        trend = "with" if gap > 0 and slope > 0 else "against" if gap < 0 and slope < 0 else "flat"
        from app.agents.cashier import DEFAULT_MAX_BARS

        max_bars = cfg.max_bars_in_trade or int((tr.context or {}).get("max_bars") or 0) or DEFAULT_MAX_BARS.get(tf, 60)
        age_s = (datetime.now(timezone.utc) - tr.entry_time.replace(tzinfo=tr.entry_time.tzinfo or timezone.utc)).total_seconds()
        hours = age_s / 3600
        age_text = f"{age_s / 60:.0f} min" if hours < 2 else f"{hours:.0f} h"
        # evento forte antes da próxima revisão (ou dentro da pausa antes do evento)
        window = max(cfg.position_review_minutes, cfg.blackout_before_min)
        ccys = symbol_currencies(tr.symbol)
        now = datetime.now(timezone.utc)
        event, event_min = None, None
        for ev in self.office.agent("schedule").upcoming(window / 60 + 0.01, cfg.blackout_impacts):
            ts = datetime.fromisoformat(ev["ts"])
            ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            minutes = (ts - now).total_seconds() / 60
            if ev["currency"] in ccys and 0 <= minutes <= window:
                event, event_min = ev, minutes
                break
        ns = self.office.agent("news").symbol_score(tr.symbol)
        strength = float(ns["score"]) * float(ns["confidence"])
        strategy_ok = True
        typical = None
        if tr.profile_id:
            with session_scope() as s:
                prof = s.get(StrategyProfile, tr.profile_id)
                strategy_ok = prof is None or prof.status == "aprovada"
                avg_min = minutes_from_metrics(prof.metrics, tf_sec) if prof is not None else None
            if avg_min and avg_min > 0:
                typical = age_s / (avg_min * 60)
        return PositionContext(
            direction=d, entry=tr.entry_price, price=price, sl=tr.sl, tp=tr.tp, risk=risk,
            spread=float(tick["ask"] - tick["bid"]), point=float(spec.get("point") or 0.0),
            atr_now=float(atr[-1]), atr_entry=float(atr[min(bars.n - 1, max(0, i0 - 1))]), mfe_r=mfe_r, trend=trend, adx=adx,
            age_frac=age_s / (max_bars * tf_sec) if max_bars else 0.0, age_text=age_text, typical_frac=typical,
            event=event, event_minutes=event_min,
            news_against=cfg.use_news_filter and strength * d <= -cfg.news_block_threshold,
            strategy_ok=strategy_ok, patience=self.patience(),
        )

    async def review_positions(self) -> list[dict]:
        """De hora em hora: olha cada posição aberta com o mercado de agora e decide com o Caio.

        Fechar, apertar o stop (nunca afrouxar) ou mudar o alvo — ver ``app/agents/review.py``.
        Também confere as saídas antigas da revisão para aprender (paciência)."""
        cfg = get_config()
        await self.check_review_outcomes()
        with session_scope() as s:
            trades = list(s.scalars(select(Trade).where(Trade.status == "open", Trade.mode == cfg.mode)))
            s.expunge_all()
        if not trades:
            return []
        cashier = self.office.agent("cashier")
        self.work(f"Revisando {len(trades)} posição(ões) aberta(s) com o Caio", "agent:cashier", "🔍")
        results = []
        now = datetime.now(timezone.utc)
        for tr in trades:
            tf_sec = TIMEFRAME_SECONDS.get(tr.timeframe or "H1", 3600)
            age = (now - tr.entry_time.replace(tzinfo=tr.entry_time.tzinfo or timezone.utc)).total_seconds()
            if age < max(900, min(tf_sec, 3600)):
                continue  # acabou de entrar: deixa a operação respirar
            try:
                ctx = await self._position_context(tr)
            except Exception as exc:
                self.log(f"Não consegui revisar {tr.symbol}: {exc}", kind="decision", level="warning")
                continue
            if ctx is None:
                continue
            dec = review_decide(ctx)
            r_txt = f"{ctx.r_now:+.1f}R".replace(".", ",")
            entry = {"at": now.isoformat(), "action": dec.action, "text": dec.why[:240], "r": round(ctx.r_now, 2), "trend": ctx.trend}
            results.append({"trade_id": tr.id, "symbol": tr.symbol, **entry})
            if dec.action == "close":
                self.tell("cashier", "🔍 " + self.line("review_close", symbol=tr.symbol, r=r_txt, why=dec.why[:120]), kind="pedido", data={"trade_id": tr.id})
                extra = {"review_close": {"sl": tr.sl, "tp": tr.tp, "price": ctx.price, "r": round(ctx.r_now, 3), "at": now.isoformat()}}
                await cashier.note_review(tr.id, entry, extra)
                ok = await cashier.request_close(tr.id, "revisao")
                cashier.tell("manager", self._done_line(cashier, tr.symbol, ok, "posição encerrada", "não consegui fechar agora"), kind="resposta")
                self.log(f"Revisão: encerrei {tr.symbol} com {r_txt} — {dec.why}", kind="decision")
                self._count_review("close")
                self.skills.gain("gestao_posicoes", 2, f"revisão de {tr.symbol}")
            elif dec.action == "adjust":
                parts = []
                if dec.sl is not None:
                    parts.append(self.line("review_sl", symbol=tr.symbol, sl=f"{dec.sl:g}", why=dec.why[:120]))
                if dec.tp is not None and dec.sl is None:
                    parts.append(self.line("review_tp", symbol=tr.symbol, tp=f"{dec.tp:g}", why=dec.why[:120]))
                self.tell("cashier", "🔍 " + parts[0], kind="pedido", data={"trade_id": tr.id})
                ok, what = await cashier.adjust(tr.id, sl=dec.sl, tp=dec.tp, why=dec.why, entry=entry)
                cashier.tell("manager", self._done_line(cashier, tr.symbol, ok, what, what), kind="resposta")
                if ok:
                    if dec.sl is not None:
                        self._count_review("sl")
                    if dec.tp is not None:
                        self._count_review("tp")
                    self.skills.gain("gestao_posicoes", 2, f"ajuste em {tr.symbol}")
                else:
                    await cashier.note_review(tr.id, {**entry, "action": "hold", "text": f"ajuste não aplicado ({what})"})
            else:
                await cashier.note_review(tr.id, entry)
                self.say("🔍 " + self.line("review_hold", symbol=tr.symbol, r=r_txt, why=dec.why[:100]))
                self._count_review("hold")
        self.idle("Acompanhando o plano")
        return results

    def _done_line(self, cashier, symbol: str, ok: bool, what: str, why: str) -> str:
        if ok:
            return "✅ " + cashier.line("review_done", what=what, symbol=symbol)
        return "⚠️ " + cashier.line("review_fail", symbol=symbol, why=why[:80])

    async def check_review_outcomes(self) -> list[dict]:
        """Confere as saídas da revisão: e se tivesse segurado? Ajusta a paciência do Gustavo."""
        now = datetime.now(timezone.utc)
        out = []
        with session_scope() as s:
            rows = list(s.scalars(select(Trade).where(Trade.status == "closed", Trade.exit_reason == "revisao", Trade.exit_time >= now - timedelta(days=10))))
            pending = [
                (t.id, t.symbol, t.timeframe or "H1", t.direction, t.entry_price, t.initial_sl, t.exit_price, t.exit_time, dict(t.mgmt or {}))
                for t in rows
                if (t.mgmt or {}).get("review_close") and not (t.mgmt or {}).get("review_check")
            ]
        for tid, symbol, tf, direction, entry, initial_sl, exit_price, exit_time, mg in pending:
            tf_sec = TIMEFRAME_SECONDS.get(tf, 3600)
            exit_dt = exit_time.replace(tzinfo=exit_time.tzinfo or timezone.utc)
            if (now - exit_dt).total_seconds() < CHECK_BARS * tf_sec:
                continue
            try:
                bars = await self.office.market.rates(symbol, tf, 400, closed_only=True, max_age=300)
            except Exception:
                continue
            after = [i for i in range(bars.n) if bars.time[i] >= exit_dt.timestamp()][:CHECK_BARS]
            if len(after) < CHECK_BARS // 2:
                continue
            d = 1 if direction == "buy" else -1
            risk = abs(entry - (initial_sl or entry))
            rc = mg["review_close"]
            cf = counterfactual(d, entry, risk, rc.get("sl"), rc.get("tp"), bars.high[after], bars.low[after], bars.close[after])
            if cf is None:
                continue
            actual = (exit_price - entry) * d / risk if risk > 0 else 0.0
            diff = round(cf - actual, 2)
            patience, verdict = learn_patience(self.patience(), diff)
            kv_set("manager.review_patience", patience)
            check = {"cf_r": round(cf, 2), "r": round(actual, 2), "diff": diff, "verdict": verdict, "at": now.isoformat()}
            with session_scope() as s:
                row = s.get(Trade, tid)
                if row is not None:
                    row.mgmt = {**(row.mgmt or {}), "review_check": check}
            stats = kv_get("manager.review_stats") or {"checked": 0, "good": 0, "early": 0}
            stats["checked"] = int(stats.get("checked", 0)) + 1
            if verdict == "acertou":
                stats["good"] = int(stats.get("good", 0)) + 1
                self.skills.gain("gestao_posicoes", 6, f"saída de {symbol} evitou {abs(diff):.1f}R de piora".replace(".", ","))
            elif verdict == "cedo":
                stats["early"] = int(stats.get("early", 0)) + 1
            kv_set("manager.review_stats", stats)
            self.skills.update("gestao_posicoes", params={"patience": patience, **stats})
            fmt = lambda v: f"{v:+.1f}R".replace(".", ",")  # noqa: E731
            if verdict == "cedo":
                text = f"Conferi minha saída de {symbol}: segurando teria feito {fmt(cf)} (saí com {fmt(actual)}). Vou ser um pouco mais paciente."
            elif verdict == "acertou":
                text = f"Conferi minha saída de {symbol}: segurando teria feito {fmt(cf)} (saí com {fmt(actual)}). Boa decisão."
            else:
                text = f"Conferi minha saída de {symbol}: segurando daria quase o mesmo ({fmt(cf)} x {fmt(actual)})."
            self.log(text, kind="decision", data=check)
            out.append({"trade_id": tid, **check})
        return out

    # --------------------------------------------------------- aprendizado
    def on_trade_closed(self, trade: Trade) -> None:
        votes = (trade.context or {}).get("votes") or {}
        if not votes:
            return
        win = trade.pnl > 0
        eta = 0.08
        w = self.weights()
        for k in w:
            v = float(votes.get(k, 0.5))
            signal = (v - 0.5) * 2  # -1..1: quanto o colega "apoiou" o setup
            w[k] *= math.exp(eta * signal * (1 if win else -1))
        w = {k: min(0.6, max(0.05, v)) for k, v in w.items()}
        total = sum(w.values())
        w = {k: round(v / total, 4) for k, v in w.items()}
        kv_set("manager.weights", w)
        self.skills.update("confianca_equipe", params={"weights": w})
        self.skills.gain("confianca_equipe", 3, "pesos da equipe recalibrados")
        if win:
            self.skills.gain("tomada_decisao", 8, f"operação vencedora em {trade.symbol}")


def recent_decisions(limit: int = 20) -> list[dict]:
    with session_scope() as s:
        rows = list(s.scalars(select(Decision).order_by(Decision.ts.desc()).limit(limit)))
        return [{"id": r.id, "ts": r.ts.isoformat(), "plan": r.plan, "rationale": r.rationale, "ai": r.ai, "model": r.model, "candidates": r.candidates[:10]} for r in rows]


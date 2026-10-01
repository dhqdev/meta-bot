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
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import select

from app.agents.base import Agent, AgentProfile
from app.agents.personas import persona_prompt
from app.agents.skills import SkillDef, active_lessons, playbook
from app.config import get_settings
from app.db import session_scope
from app.kv import kv_get, kv_set
from app.models import Decision, Trade
from app.runtime import get_config
from app.services.llm import to_json

DEFAULT_WEIGHTS = {"strategy": 0.45, "hour": 0.2, "news": 0.15, "live": 0.2}
DEFAULT_HORIZON_WEIGHTS = {"scalp": 1.0, "day": 1.0, "swing": 1.0}


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

    def learn_horizons(self, summary: dict) -> list[str]:
        """Scalper x day trade x posição longa: ajusta a preferência com o backtest e o resultado real.

        Peso entre 0,75 e 1,25, mudando no máximo 30% do caminho por dia (aprende devagar)."""
        old = self.horizon_weights()
        new: dict[str, float] = {}
        notes = []
        for row in summary.get("horizons", []):
            h = row["key"]
            bt = max(-1.0, min(1.0, float(row.get("bt_oos_expectancy_r") or 0.0) * 4))
            n = int(row.get("live_trades") or 0)
            live = max(-1.0, min(1.0, float(row.get("live_r") or 0.0) / n * 2)) if n >= 3 else 0.0
            has_bt = bool(row.get("approved"))
            target = 1.0 + 0.25 * ((0.6 * bt if has_bt else 0.0) + 0.4 * live)
            value = round(min(1.25, max(0.75, 0.7 * old.get(h, 1.0) + 0.3 * target)), 3)
            new[h] = value
            if abs(value - old.get(h, 1.0)) >= 0.01:
                notes.append(f"{row.get('label', h)}: peso {old.get(h, 1.0):.2f} → {value:.2f}".replace(".", ","))
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
        cands.sort(key=lambda c: c["score"], reverse=True)
        for i, c in enumerate(cands, 1):
            c["id"] = i
        return cands

    def deterministic_plan(self, cands: list[dict]) -> list[dict]:
        cfg = get_config()
        plan: list[dict] = []
        used_symbols: set[str] = set()
        for c in cands:
            if len(plan) >= cfg.max_active_setups:
                break
            if c["blocked"] or c["symbol"] in used_symbols or c["score"] < 0.45:
                continue
            used_symbols.add(c["symbol"])
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
        lessons = "\n".join(f"- ({l['agent']}) {l['text']}" for l in active_lessons("manager", 10)) or "- (nenhuma ainda)"
        system = (
            playbook("manager")
            + "\n\n" + playbook("equipe")
            + "\n\n## Sua personalidade\n" + persona_prompt(["manager"])
            + "\n\n## Lições registradas pela Auditora\n" + lessons
        )
        last_daily = self.office.daily.last_report(before=_local_day())
        focus = "; ".join((last_daily or {}).get("focus", [])[:3]) or "nenhum"
        top = cands[:15]
        user = (
            f"Agora (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}. "
            f"Limite de setups ativos: {cfg.max_active_setups}. Ordenação preferida pelo dono: {cfg.rank_by}.\n"
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
        used: set[str] = set()
        for pick in res.data.picks:
            c = by_id.get(pick.candidate_id)
            if c is None or c["blocked"] or c["symbol"] in used or len(plan) >= cfg.max_active_setups:
                continue
            used.add(c["symbol"])
            plan.append(self._setup(c, pick.direction, pick.risk_mult, pick.reason))
        return plan, res.data.rationale[:800], res.model

    @staticmethod
    def _fingerprint(cands: list[dict]) -> list:
        return [(c["profile_id"], c["suggested_direction"], c["blocked"], round(c["score"], 1)) for c in cands[:15]]

    def _reuse_ai_plan(self, cands: list[dict]) -> tuple[list[dict], str, str] | None:
        """Plano da IA ainda válido (mesmos candidatos e dentro da validade) → não gasta outra chamada."""
        cache = self._ai_cache
        cfg = get_config()
        if not cache or time.time() - cache["at"] > cfg.ai_plan_refresh_minutes * 60:
            return None
        if cache["fingerprint"] != self._fingerprint(cands):
            return None
        by_profile = {c["profile_id"]: c for c in cands}
        plan = []
        for pid, direction, risk_mult, reason in cache["picks"]:
            c = by_profile.get(pid)
            if c is None or c["blocked"]:
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


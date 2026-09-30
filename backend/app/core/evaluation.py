"""Avaliação completa de uma estratégia (backtest + métricas + aprovação) e evolução de parâmetros."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.core.backtest import BTTrade, CostModel, RiskParams, run_backtest, split_trades
from app.core.bars import Bars
from app.core.metrics import ApprovalRules, approve, compute_metrics, hour_stats, objective, score
from app.core.strategies import FILTERS, Strategy, apply_filters


@dataclass
class Candidate:
    params: dict
    filters: dict = field(default_factory=dict)
    risk: dict = field(default_factory=dict)
    label: str = "atual"

    def key(self) -> str:
        import json

        return json.dumps([self.params, self.filters, self.risk], sort_keys=True, default=str)


@dataclass
class Evaluation:
    candidate: Candidate
    trades: list[BTTrade]
    full: dict
    ins: dict
    oos: dict
    score: float
    approved: bool
    reasons: list[str]
    hours: dict
    split_index: int


def evaluate(
    bars: Bars,
    strategy: Strategy,
    cand: Candidate,
    costs: CostModel,
    rules: ApprovalRules,
    rank_by: str = "win_rate",
    risk_pct: float = 1.0,
) -> Evaluation:
    params = strategy.clean(cand.params)
    risk = RiskParams.from_dict({**strategy.risk, **(cand.risk or {})})
    sigs = apply_filters(bars, strategy.signals(bars, params), cand.filters)
    trades = run_backtest(bars, sigs, risk, costs)
    split = int(bars.n * (1 - rules.oos_fraction))
    ins, oos = split_trades(trades, split)
    span_full = float(bars.time[-1] - bars.time[0]) if bars.n > 1 else None
    span_is = float(bars.time[min(split, bars.n - 1)] - bars.time[0]) if bars.n > 1 else None
    span_oos = float(bars.time[-1] - bars.time[min(split, bars.n - 1)]) if bars.n > 1 else None
    m_full = compute_metrics(trades, risk_pct, span_full)
    m_is = compute_metrics(ins, risk_pct, span_is)
    m_oos = compute_metrics(oos, risk_pct, span_oos)
    verdict = approve(m_full, m_oos, rules)
    return Evaluation(
        candidate=Candidate(params, cand.filters or {}, risk.to_dict(), cand.label),
        trades=trades,
        full=m_full,
        ins=m_is,
        oos=m_oos,
        score=score(m_full, rank_by) * (1.0 if verdict.approved else 0.6),
        approved=verdict.approved,
        reasons=verdict.reasons,
        hours=hour_stats(trades),
        split_index=split,
    )


def _fmt(v) -> str:
    return str(v).replace(".", ",") if isinstance(v, float) else str(v)


def variants(strategy: Strategy, base: Candidate, rng: random.Random, best_hours: list[int] | None = None, n_random: int = 8) -> list[Candidate]:
    """Mutações que o Estrategista testa: um parâmetro por vez, sorteios, filtros e risco."""
    out: list[Candidate] = []
    params = strategy.clean(base.params)
    labels = {p.name: p.label for p in strategy.params}
    for p in strategy.params:
        if p.kind == "bool":
            out.append(Candidate({**params, p.name: not params[p.name]}, dict(base.filters), dict(base.risk), f"{labels[p.name]}: {'não' if params[p.name] else 'sim'}"))
            continue
        for delta in (-p.step, p.step):
            value = p.clean(params[p.name] + delta)
            if value != params[p.name]:
                out.append(Candidate({**params, p.name: value}, dict(base.filters), dict(base.risk), f"{labels[p.name]}: {_fmt(params[p.name])} → {_fmt(value)}"))
    for _ in range(n_random):
        sample = {p.name: rng.choice(p.values()) for p in strategy.params}
        out.append(Candidate(strategy.clean(sample), dict(base.filters), dict(base.risk), "combinação sorteada"))
    for key, fdef in FILTERS.items():
        filters = dict(base.filters)
        if key in filters:
            filters.pop(key)
            out.append(Candidate(dict(params), filters, dict(base.risk), f"sem filtro {fdef.name}"))
        else:
            if key == "horarios":
                if not best_hours:
                    continue
                filters[key] = {"hours": sorted(best_hours)}
            else:
                filters[key] = {}
            out.append(Candidate(dict(params), filters, dict(base.risk), f"com filtro {fdef.name}"))
    risk = {**strategy.risk, **(base.risk or {})}
    sl = float(risk.get("sl_atr", 1.5))
    for new_sl in (sl - 0.5, sl + 0.5):
        if 0.75 <= new_sl <= 4.0:
            out.append(Candidate(dict(params), dict(base.filters), {**risk, "sl_atr": round(new_sl, 2)}, f"stop {_fmt(sl)} → {_fmt(round(new_sl, 2))} ATR"))
    tp = float(risk.get("tp_r", 2.0))
    for new_tp in (1.0, 1.5, 2.0, 3.0):
        if abs(new_tp - tp) > 1e-9:
            out.append(Candidate(dict(params), dict(base.filters), {**risk, "tp_r": new_tp}, f"alvo {_fmt(tp)}R → {_fmt(new_tp)}R"))
    # sem duplicatas
    seen = {base.key()}
    unique = []
    for c in out:
        k = c.key()
        if k not in seen:
            seen.add(k)
            unique.append(c)
    return unique


@dataclass
class EvolutionResult:
    base: Evaluation
    best: Evaluation | None
    tried: int
    accepted: bool
    summary: str


def evolve(
    bars: Bars,
    strategy: Strategy,
    base: Candidate,
    costs: CostModel,
    rules: ApprovalRules,
    rank_by: str,
    rng: random.Random,
    best_hours: list[int] | None = None,
    budget: int = 40,
) -> EvolutionResult:
    """Escolhe só com a parte antiga (dentro da amostra) e confirma na parte recente (fora da amostra)."""
    base_eval = evaluate(bars, strategy, base, costs, rules, rank_by)
    cands = variants(strategy, base, rng, best_hours)
    rng.shuffle(cands)
    cands = cands[:budget]
    min_is = max(5, int(rules.min_trades * (1 - rules.oos_fraction) * 0.6))
    evals = []
    for cand in cands:
        ev = evaluate(bars, strategy, cand, costs, rules, rank_by)
        if ev.ins["trades"] >= min_is:
            evals.append(ev)
    base_is = objective(base_eval.ins, rank_by)
    base_oos = objective(base_eval.oos, rank_by)
    evals.sort(key=lambda e: objective(e.ins, rank_by), reverse=True)
    best: Evaluation | None = None
    for ev in evals[:3]:
        is_obj = objective(ev.ins, rank_by)
        oos_obj = objective(ev.oos, rank_by)
        margin = abs(base_oos) * 0.05 + 1e-6
        # Buscar acerto não pode custar a expectativa: no máximo 30% a menos que a atual.
        base_exp = base_eval.oos["expectancy_r"]
        keeps_edge = ev.oos["expectancy_r"] >= (0.7 * base_exp if base_exp > 0 else 0.0)
        if (
            is_obj > base_is
            and oos_obj > base_oos + margin
            and keeps_edge
            and ev.oos["trades"] >= rules.min_oos_trades
            and ev.oos["profit_factor"] >= max(1.0, rules.min_profit_factor - 0.1)
            and ev.oos["expectancy_r"] > 0
        ):
            if best is None or oos_obj > objective(best.oos, rank_by):
                best = ev
    if best is not None:
        summary = (
            f"{best.candidate.label}: acerto fora da amostra {base_eval.oos['win_rate']:.0%} → {best.oos['win_rate']:.0%}, "
            f"expectativa {base_eval.oos['expectancy_r']:+.2f}R → {best.oos['expectancy_r']:+.2f}R"
        ).replace(".", ",")
    else:
        summary = f"testou {len(cands)} variações; nenhuma superou a atual nas duas partes do histórico"
    return EvolutionResult(base=base_eval, best=best, tried=len(cands), accepted=best is not None, summary=summary)

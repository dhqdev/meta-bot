"""Estela, a estrategista (sem IA): backtests, ranking e evolução das estratégias.

- **Ranking:** testa todas as estratégias em cada ativo e tempo gráfico, com
  custos reais, e aprova só as que têm amostra suficiente, fator de lucro
  mínimo e que se sustentam no período recente que não foi usado para escolher.
- **Evolução (as skills melhoram):** varia um parâmetro por vez, sorteia
  combinações, liga/desliga filtros (tendência, ADX, volatilidade, horários do
  Hugo...) e ajusta stop/alvo. Escolhe só com a parte antiga do histórico e só
  adota se melhorar também na parte recente. Cada evolução confirmada dá XP.
- **Sinais ao vivo:** para os setups que o Gerente escolheu, confere cada
  candle fechado e leva o sinal ao Gerente.
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.agents.base import Agent, AgentProfile
from app.agents.skills import SkillDef
from app.core.backtest import CostModel, RiskParams
from app.core.bars import Bars
from app.core.evaluation import Candidate, evaluate, evolve
from app.core.horizons import HORIZONS, ORDER, horizon_of, label as horizon_label, minutes_from_metrics
from app.core.metrics import ApprovalRules, RANK_LABELS, equity_curve
from app.core.risk import value_per_price_unit
from app.core.strategies import REGISTRY, STRATEGIES, apply_filters, get_strategy
from app.db import session_scope
from app.models import BacktestRun, Signal, StrategyProfile, Trade
from app.runtime import TIMEFRAME_SECONDS, get_config

BARS_BY_TF = {"M5": 6000, "M15": 5000, "M30": 4000, "H1": 4000, "H4": 3000, "D1": 1500}
DEFAULT_MAX_BARS = {"M5": 48, "M15": 48, "M30": 48, "H1": 72, "H4": 60, "D1": 30}
SORT_KEY = {"win_rate": "wilson_lb", "expectancy": "expectancy_r", "profit_factor": "profit_factor", "net": "return_pct"}


def profile_dict(p: StrategyProfile) -> dict:
    strat = REGISTRY.get(p.strategy)
    minutes = minutes_from_metrics(p.metrics, TIMEFRAME_SECONDS.get(p.timeframe, 3600))
    return {
        "id": p.id,
        "symbol": p.symbol,
        "timeframe": p.timeframe,
        "strategy": p.strategy,
        "strategy_name": strat.name if strat else p.strategy,
        "source": strat.source if strat else "",
        "style": strat.style if strat else "",
        "params": p.params,
        "filters": p.filters,
        "risk": p.risk,
        "version": p.version,
        "status": p.status,
        "score": p.score,
        "metrics": p.metrics,
        "is_metrics": p.is_metrics,
        "oos_metrics": p.oos_metrics,
        "live": p.live,
        "avg_minutes": round(minutes, 1) if minutes is not None else None,
        "horizon": horizon_of(minutes),
        "hour_stats": p.hour_stats,
        "data_source": p.data_source,
        "tested_at": p.tested_at.isoformat() if p.tested_at else None,
        "evolved_at": p.evolved_at.isoformat() if p.evolved_at else None,
    }


def _profile_risk(risk: dict, timeframe: str) -> dict:
    out = {"sl_atr": risk.get("sl_atr"), "tp_r": risk.get("tp_r")}
    max_bars = int(risk.get("max_bars") or 0)
    if max_bars and max_bars != DEFAULT_MAX_BARS.get(timeframe, 60):
        out["max_bars"] = max_bars
    return out


class StrategistAgent(Agent):
    profile = AgentProfile(
        id="strategist",
        name="Estela",
        role="Estrategista",
        emoji="📈",
        uses_ai=False,
        description="Roda os backtests de todas as estratégias nos ativos escolhidos, ranqueia pela taxa de acerto e evolui os parâmetros com validação fora da amostra.",
    )
    interval = 15.0
    idle_task = "Acompanhando os setups"
    skill_defs = [SkillDef(s.key, s.name, s.description) for s in STRATEGIES] + [
        SkillDef("backtesting", "Backtesting", "Testa estratégias no histórico com custos reais."),
        SkillDef("otimizacao", "Otimização walk-forward", "Procura parâmetros melhores sem viciar no histórico."),
        SkillDef("evolucao", "Evolução de estratégias", "Adota só melhorias confirmadas fora da amostra."),
    ]

    def __init__(self, office):
        super().__init__(office)
        self._last_bar: dict[tuple, int] = {}
        self._exit_bar: dict[int, int] = {}
        self._rng = random.Random(20260930)
        self.ranking_running = False

    async def tick(self) -> None:
        cfg = get_config()
        if self.due("ranking", cfg.ranking_interval_hours * 3600):
            await self.run_ranking()
        if cfg.evolution_enabled and self.due("evolution", cfg.evolution_interval_hours * 3600) and self._has_profiles():
            await self.run_evolution()
        await self.watch_signals()
        await self.watch_exits()
        if self.due("revalidate", 1800):
            await self.revalidate_flagged()

    def _has_profiles(self) -> bool:
        with session_scope() as s:
            return s.scalar(select(StrategyProfile.id).where(StrategyProfile.tested_at.is_not(None)).limit(1)) is not None

    # ------------------------------------------------------------ helpers
    def rules(self) -> ApprovalRules:
        cfg = get_config()
        return ApprovalRules(cfg.min_trades, cfg.min_profit_factor, cfg.oos_fraction)

    def costs(self, spec: dict) -> CostModel:
        cfg = get_config()
        vpu = value_per_price_unit(spec)
        commission = cfg.paper_commission_per_lot / vpu if vpu > 0 else 0.0
        return CostModel(spread=None, slippage=cfg.paper_slippage_points * spec["point"], commission=commission)

    def risk_for(self, strategy_key: str, profile_risk: dict | None, timeframe: str) -> dict:
        cfg = get_config()
        exits = self.office.exit_params()
        base = {**get_strategy(strategy_key).risk, **(profile_risk or {})}
        return {
            "sl_atr": base.get("sl_atr", 1.5),
            "tp_r": base.get("tp_r", 2.0),
            "break_even_r": exits["break_even_r"],
            "trailing_start_r": exits["trailing_start_r"],
            "trailing_atr": exits["trailing_atr"],
            "max_bars": cfg.max_bars_in_trade or int(base.get("max_bars") or 0) or DEFAULT_MAX_BARS.get(timeframe, 60),
        }

    def enabled_strategies(self) -> list[str]:
        cfg = get_config()
        keys = [k for k in cfg.enabled_strategies if k in REGISTRY]
        return keys or [s.key for s in STRATEGIES]

    def _load_profiles(self, symbol: str, timeframe: str) -> dict[str, dict]:
        with session_scope() as s:
            rows = s.scalars(select(StrategyProfile).where(StrategyProfile.symbol == symbol, StrategyProfile.timeframe == timeframe))
            return {r.strategy: {"params": dict(r.params or {}), "filters": dict(r.filters or {}), "risk": dict(r.risk or {}), "status": r.status} for r in rows}

    # ------------------------------------------------------------ ranking
    async def run_ranking(self, symbols: list[str] | None = None, timeframes: list[str] | None = None) -> int:
        if self.ranking_running:
            return 0
        self.ranking_running = True
        try:
            return await self._run_ranking(symbols, timeframes)
        finally:
            self.ranking_running = False

    async def _run_ranking(self, symbols: list[str] | None, timeframes: list[str] | None) -> int:
        cfg = get_config()
        symbols = symbols or cfg.watchlist
        timeframes = timeframes or cfg.timeframes
        keys = self.enabled_strategies()
        total = approved = 0
        rules = self.rules()
        started = time.perf_counter()
        source = self.office.market.source()
        for symbol in symbols:
            try:
                spec = await self.office.market.spec(symbol)
            except Exception as exc:
                self.log(f"Não consegui os dados de {symbol}: {exc}", kind="strategy", level="warning")
                continue
            costs = self.costs(spec)
            for tf in timeframes:
                try:
                    bars = await self.office.market.rates(symbol, tf, BARS_BY_TF[tf], closed_only=True, max_age=600)
                except Exception as exc:
                    self.log(f"Sem histórico de {symbol} {tf}: {exc}", kind="strategy", level="warning")
                    continue
                if bars.n < 300:
                    continue
                self.work(f"Testando {len(keys)} estratégias em {symbol} {tf}", "desk", "📊")
                profiles = self._load_profiles(symbol, tf)
                results = await asyncio.to_thread(self._rank_block, bars, keys, profiles, costs, rules, cfg.rank_by, tf)
                approved += self._save_ranking(symbol, tf, bars, results, source)
                total += len(results)
                await asyncio.sleep(0)
        elapsed = time.perf_counter() - started
        if total:
            self.skills.gain("backtesting", min(40, total // 5 + 1), f"{total} backtests")
            top = self.ranking(limit=3, only_approved=True)
            self.work("Publicando o ranking no quadro", "whiteboard", "🏆")
            if top:
                best = top[0]
                m = best["metrics"]
                self.tell("manager", "🏆 " + self.line("ranking_top", name=best["strategy_name"], symbol=best["symbol"], timeframe=best["timeframe"], win_rate=f"{m['win_rate']:.0%}"), kind="info")
            else:
                self.say("🤔 " + self.line("ranking_none"), "🤔")
            self.log(
                f"Ranking atualizado: {total} backtests em {elapsed:.0f}s, {approved} aprovados (ordenado por {RANK_LABELS.get(cfg.rank_by, cfg.rank_by)})",
                kind="strategy",
            )
            self.office.publish_office(ranking=[{k: p[k] for k in ("symbol", "timeframe", "strategy_name", "metrics", "status")} for p in self.ranking(limit=5)])
            self.office.agent("manager").request("decide")
        self.idle("Acompanhando os setups")
        return total

    def _rank_block(self, bars: Bars, keys: list[str], profiles: dict, costs: CostModel, rules: ApprovalRules, rank_by: str, tf: str) -> list[dict]:
        out = []
        for key in keys:
            strat = get_strategy(key)
            prof = profiles.get(key, {})
            cand = Candidate(prof.get("params") or strat.defaults(), prof.get("filters") or {}, self.risk_for(key, prof.get("risk"), tf))
            ev = evaluate(bars, strat, cand, costs, rules, rank_by)
            out.append({"key": key, "eval": ev})
        return out

    def _save_ranking(self, symbol: str, tf: str, bars: Bars, results: list[dict], source: str) -> int:
        now = datetime.now(timezone.utc)
        approved = 0
        with session_scope() as s:
            for item in results:
                ev = item["eval"]
                key = item["key"]
                row = s.scalar(select(StrategyProfile).where(StrategyProfile.symbol == symbol, StrategyProfile.timeframe == tf, StrategyProfile.strategy == key))
                if row is None:
                    row = StrategyProfile(symbol=symbol, timeframe=tf, strategy=key)
                    s.add(row)
                row.params = ev.candidate.params
                row.filters = ev.candidate.filters
                row.risk = _profile_risk(ev.candidate.risk, tf)
                if row.status != "observacao":
                    row.status = "aprovada" if ev.approved else "reprovada"
                row.score = ev.score
                row.metrics = {**ev.full, "reasons": ev.reasons}
                row.is_metrics = ev.ins
                row.oos_metrics = ev.oos
                row.hour_stats = ev.hours
                row.data_source = source
                row.tested_at = now
                s.add(
                    BacktestRun(
                        kind="ranking", symbol=symbol, timeframe=tf, strategy=key,
                        params=ev.candidate.params, filters=ev.candidate.filters, risk=ev.candidate.risk,
                        bars=bars.n, start_ts=int(bars.time[0]), end_ts=int(bars.time[-1]),
                        metrics=ev.full, is_metrics=ev.ins, oos_metrics=ev.oos, score=ev.score,
                        approved=ev.approved, data_source=source,
                    )
                )
                if ev.approved:
                    approved += 1
        for item in results:
            if item["eval"].approved:
                self.skills.gain(item["key"], 3, f"aprovada em {symbol} {tf}")
        return approved

    def ranking(self, symbol: str | None = None, timeframe: str | None = None, limit: int = 50, only_approved: bool = False) -> list[dict]:
        cfg = get_config()
        with session_scope() as s:
            q = select(StrategyProfile).where(StrategyProfile.tested_at.is_not(None))
            if symbol:
                q = q.where(StrategyProfile.symbol == symbol)
            if timeframe:
                q = q.where(StrategyProfile.timeframe == timeframe)
            if only_approved:
                q = q.where(StrategyProfile.status == "aprovada")
            rows = [profile_dict(p) for p in s.scalars(q)]
        key = SORT_KEY.get(cfg.rank_by, "wilson_lb")
        rows.sort(key=lambda p: (p["status"] == "aprovada", (p["metrics"] or {}).get(key, 0), p["score"]), reverse=True)
        return rows[:limit]

    def horizon_summary(self, days: int = 30) -> dict:
        """Scalper x day trade x posição longa: o que os backtests e as operações reais mostram."""
        out = {h: {"key": h, **HORIZONS[h], "profiles": 0, "approved": 0, "bt_win_rate": 0.0, "bt_expectancy_r": 0.0, "bt_oos_expectancy_r": 0.0,
                   "live_trades": 0, "live_wins": 0, "live_r": 0.0, "live_pnl": 0.0, "best": None} for h in ORDER}
        acc: dict[str, list] = {h: [] for h in ORDER}
        for p in self.ranking(limit=1000):
            h = p["horizon"]
            if h not in out or not (p["metrics"] or {}).get("trades"):
                continue
            out[h]["profiles"] += 1
            if p["status"] == "aprovada":
                out[h]["approved"] += 1
                acc[h].append(p)
                if out[h]["best"] is None:
                    out[h]["best"] = {"strategy_name": p["strategy_name"], "symbol": p["symbol"], "timeframe": p["timeframe"], "win_rate": p["metrics"].get("win_rate")}
        for h, rows in acc.items():
            if rows:
                out[h]["bt_win_rate"] = round(sum((r["metrics"] or {}).get("win_rate", 0) for r in rows) / len(rows), 4)
                out[h]["bt_expectancy_r"] = round(sum((r["metrics"] or {}).get("expectancy_r", 0) for r in rows) / len(rows), 4)
                out[h]["bt_oos_expectancy_r"] = round(sum((r["oos_metrics"] or {}).get("expectancy_r", 0) for r in rows) / len(rows), 4)
        since = datetime.now(timezone.utc) - timedelta(days=days)
        with session_scope() as s:
            for t in s.scalars(select(Trade).where(Trade.status == "closed", Trade.exit_time >= since)):
                if t.exit_time is None or t.entry_time is None:
                    continue
                h = horizon_of((t.exit_time - t.entry_time).total_seconds() / 60)
                row = out[h]
                row["live_trades"] += 1
                row["live_wins"] += int(t.pnl > 0)
                row["live_r"] = round(row["live_r"] + t.pnl_r, 3)
                row["live_pnl"] = round(row["live_pnl"] + t.pnl, 2)
        for row in out.values():
            row["label"] = horizon_label(row["key"])
            row["live_win_rate"] = round(row["live_wins"] / row["live_trades"], 4) if row["live_trades"] else None
        return {"horizons": [out[h] for h in ORDER], "days": days}

    # ----------------------------------------------------------- evolução
    async def run_evolution(self, limit: int = 6) -> int:
        cfg = get_config()
        rules = self.rules()
        with session_scope() as s:
            approved = list(s.scalars(select(StrategyProfile).where(StrategyProfile.status == "aprovada").order_by(StrategyProfile.score.desc()).limit(4)))
            others = list(
                s.scalars(
                    select(StrategyProfile)
                    .where(StrategyProfile.status == "reprovada", StrategyProfile.tested_at.is_not(None))
                    .order_by(StrategyProfile.score.desc())
                    .limit(12)
                )
            )
            picks = [(p.id, p.symbol, p.timeframe, p.strategy, dict(p.params or {}), dict(p.filters or {}), dict(p.risk or {}), p.version) for p in approved]
            for p in others:
                if len(picks) >= limit:
                    break
                if (p.metrics or {}).get("trades", 0) >= cfg.min_trades // 2:
                    picks.append((p.id, p.symbol, p.timeframe, p.strategy, dict(p.params or {}), dict(p.filters or {}), dict(p.risk or {}), p.version))
        if not picks:
            return 0
        self.work(f"Estudando melhorias em {len(picks)} estratégias", "library", "📚")
        adopted = 0
        for pid, symbol, tf, key, params, filters, risk, version in picks:
            strat = get_strategy(key)
            try:
                spec = await self.office.market.spec(symbol)
                bars = await self.office.market.rates(symbol, tf, BARS_BY_TF[tf], closed_only=True, max_age=900)
            except Exception:
                continue
            if bars.n < 300:
                continue
            best_hours = self.office.agent("schedule").best_hours_utc(symbol)
            full_risk = self.risk_for(key, risk, tf)
            self.set_state("working", "library", f"Evoluindo {strat.name} em {symbol} {tf} (v{version})", "🧪")
            res = await asyncio.to_thread(
                evolve, bars, strat, Candidate(params, filters, full_risk), self.costs(spec), rules, cfg.rank_by, self._rng, best_hours, 40
            )
            self.skills.gain("otimizacao", 3, f"{res.tried} variações testadas")
            self.skills.gain(key, 1, "estudo")
            if not res.accepted or res.best is None:
                continue
            best = res.best
            now = datetime.now(timezone.utc)
            with session_scope() as s:
                row = s.get(StrategyProfile, pid)
                if row is None:
                    continue
                row.params = best.candidate.params
                row.filters = best.candidate.filters
                row.risk = _profile_risk(best.candidate.risk, tf)
                row.version = (row.version or 1) + 1
                row.status = "aprovada" if best.approved else row.status
                row.score = best.score
                row.metrics = {**best.full, "reasons": best.reasons}
                row.is_metrics = best.ins
                row.oos_metrics = best.oos
                row.hour_stats = best.hours
                row.evolved_at = now
                row.tested_at = now
                new_version = row.version
                s.add(
                    BacktestRun(
                        kind="evolucao", symbol=symbol, timeframe=tf, strategy=key, params=best.candidate.params,
                        filters=best.candidate.filters, risk=best.candidate.risk, bars=bars.n,
                        start_ts=int(bars.time[0]), end_ts=int(bars.time[-1]), metrics=best.full,
                        is_metrics=best.ins, oos_metrics=best.oos, score=best.score, approved=best.approved,
                        data_source=self.office.market.source(),
                    )
                )
            adopted += 1
            text = f"🧬 {strat.name} em {symbol} {tf} evoluiu para a v{new_version}: {res.summary}"
            self.skills.event(key, "evolved", text, {"symbol": symbol, "timeframe": tf, "version": new_version, "label": best.candidate.label})
            self.skills.gain(key, 25, "evolução confirmada fora da amostra")
            self.skills.gain("evolucao", 15, "evolução adotada")
            self.log(text, kind="evolution")
            self.tell("all", "🧬 " + self.line("evolved", name=strat.name, symbol=symbol, timeframe=tf, version=new_version), kind="comemoracao")
        if adopted == 0:
            self.log(f"Estudei {len(picks)} estratégias: nenhuma variação superou a atual nas duas partes do histórico", kind="evolution")
        self.idle("Acompanhando os setups")
        return adopted

    async def revalidate_flagged(self) -> None:
        with session_scope() as s:
            flagged = [(p.id, p.symbol, p.timeframe, p.strategy) for p in s.scalars(select(StrategyProfile).where(StrategyProfile.status == "observacao"))]
        for pid, symbol, tf, key in flagged:
            try:
                spec = await self.office.market.spec(symbol)
                bars = await self.office.market.rates(symbol, tf, BARS_BY_TF[tf], closed_only=True, max_age=300)
            except Exception:
                continue
            with session_scope() as s:
                row = s.get(StrategyProfile, pid)
                cand = Candidate(dict(row.params or {}), dict(row.filters or {}), self.risk_for(key, row.risk, tf))
            ev = await asyncio.to_thread(evaluate, bars, get_strategy(key), cand, self.costs(spec), self.rules(), get_config().rank_by)
            with session_scope() as s:
                row = s.get(StrategyProfile, pid)
                row.status = "aprovada" if ev.approved else "reprovada"
                row.metrics = {**ev.full, "reasons": ev.reasons}
                row.oos_metrics = ev.oos
                row.tested_at = datetime.now(timezone.utc)
            verdict = "continua aprovada" if ev.approved else "foi reprovada"
            self.log(f"Revalidei {get_strategy(key).name} em {symbol} {tf} depois do alerta da Auditora: {verdict}", kind="strategy")
            if not ev.approved:
                self.skills.event(key, "demoted", f"reprovada em {symbol} {tf} após resultado real abaixo do esperado")

    # ------------------------------------------------------------- sinais
    async def watch_signals(self) -> None:
        manager = self.office.agent("manager")
        plan = manager.active_plan()
        if not plan:
            return
        now = time.time()
        for setup in plan:
            key = (setup["symbol"], setup["timeframe"], setup["strategy"])
            tf_sec = TIMEFRAME_SECONDS[setup["timeframe"]]
            last = self._last_bar.get(key)
            if last is not None and now < last + 2 * tf_sec:
                continue  # ainda não fechou um candle novo
            try:
                bars = await self.office.market.rates(setup["symbol"], setup["timeframe"], 400, closed_only=True, max_age=3)
            except Exception:
                continue
            if bars.n < 250:
                continue
            last_t = int(bars.time[-1])
            if last is not None and last_t <= last:
                continue
            self._last_bar[key] = last_t
            # candle fechou há muito tempo (ex.: sistema acabou de ligar); preços públicos podem chegar com atraso
            if now - (last_t + tf_sec) > 0.25 * tf_sec + self.office.market.data_delay(setup["symbol"]) + 60:
                continue
            await self._check_setup(setup, bars)

    async def _check_setup(self, setup: dict, bars: Bars) -> None:
        strat = get_strategy(setup["strategy"])
        with session_scope() as s:
            prof = s.get(StrategyProfile, setup["profile_id"])
            if prof is None or prof.status != "aprovada":
                return
            params, filters, prisk = dict(prof.params or {}), dict(prof.filters or {}), dict(prof.risk or {})
        sigs = apply_filters(bars, strat.signals(bars, params), filters)
        i = bars.n - 1
        direction = 1 if sigs.long_entry[i] else -1 if sigs.short_entry[i] else 0
        if direction == 0:
            return
        bias = setup.get("direction", "both")
        if (bias == "long" and direction < 0) or (bias == "short" and direction > 0):
            self.log(f"Sinal de {'compra' if direction > 0 else 'venda'} em {setup['symbol']} ignorado: o Gerente só quer {bias}", kind="signal")
            return
        risk = RiskParams.from_dict(self.risk_for(strat.key, prisk, setup["timeframe"]))
        atr = float(bars.atr(risk.atr_period)[i])
        price = float(bars.close[i])
        entry_type = sigs.entry_type
        trigger = None
        if entry_type == "stop":
            trig = sigs.long_trigger if direction > 0 else sigs.short_trigger
            stp = sigs.long_stop if direction > 0 else sigs.short_stop
            trigger = float(trig[i])
            dist = abs(trigger - float(stp[i])) if stp is not None else risk.sl_atr * atr
            dist = min(max(dist, risk.min_sl_atr * atr), risk.max_sl_atr * atr)
            ref = trigger
        else:
            dist = risk.sl_atr * atr
            ref = price
        sl = ref - direction * dist
        tp = ref + direction * risk.tp_r * dist if risk.tp_r > 0 else None
        tf_sec = TIMEFRAME_SECONDS[setup["timeframe"]]
        expires = datetime.fromtimestamp(int(bars.time[i]) + tf_sec * (1 + max(1, sigs.valid_bars)), tz=timezone.utc)
        side = "buy" if direction > 0 else "sell"
        with session_scope() as s:
            sig = Signal(
                symbol=setup["symbol"], timeframe=setup["timeframe"], strategy=strat.key, direction=side,
                entry_type=entry_type, price=price, trigger=trigger, sl=sl, tp=tp, atr=atr,
                bar_time=int(bars.time[i]), profile_id=setup["profile_id"], decision_id=setup.get("decision_id"),
                expires_at=expires, context={"risk_mult": setup.get("risk_mult", 1.0), "tp_r": risk.tp_r, "max_bars": risk.max_bars},
            )
            s.add(sig)
            s.flush()
            sig_id = sig.id
        verb = "COMPRA" if direction > 0 else "VENDA"
        self.work(f"Levando sinal de {verb} em {setup['symbol']} ao Gerente", "agent:manager", "📈")
        self.tell("manager", "📈 " + self.line("signal", side=verb.lower(), symbol=setup["symbol"], timeframe=setup["timeframe"], name=strat.name), kind="pedido")
        self.log(f"Sinal de {verb.lower()} em {setup['symbol']} {setup['timeframe']} pela {strat.name} (preço {price:g}, stop {sl:g})", kind="signal")
        self.skills.gain(strat.key, 1, "sinal ao vivo")
        await self.office.submit_signal(sig_id)
        self.idle("Acompanhando os setups")

    async def watch_exits(self) -> None:
        with session_scope() as s:
            trades = [
                (t.id, t.symbol, t.timeframe, t.strategy, t.direction, t.profile_id, t.entry_time)
                for t in s.scalars(select(Trade).where(Trade.status == "open", Trade.strategy != ""))
            ]
        now = time.time()
        for tid, symbol, tf, key, direction, profile_id, entry_time in trades:
            if not tf or key not in REGISTRY:
                continue
            tf_sec = TIMEFRAME_SECONDS.get(tf, 3600)
            last = self._exit_bar.get(tid)
            if last is not None and now < last + 2 * tf_sec:
                continue
            try:
                bars = await self.office.market.rates(symbol, tf, 400, closed_only=True, max_age=3)
            except Exception:
                continue
            if bars.n < 250:
                continue
            last_t = int(bars.time[-1])
            if (last is not None and last_t <= last) or last_t <= int(entry_time.timestamp()):
                self._exit_bar[tid] = max(last_t, last or 0)
                continue
            self._exit_bar[tid] = last_t
            with session_scope() as s:
                prof = s.get(StrategyProfile, profile_id) if profile_id else None
                params = dict(prof.params or {}) if prof else {}
            sigs = get_strategy(key).signals(bars, params)
            i = bars.n - 1
            is_long = direction == "buy"
            if (is_long and (sigs.long_exit[i] or sigs.short_entry[i])) or (not is_long and (sigs.short_exit[i] or sigs.long_entry[i])):
                self.tell("cashier", f"🚪 Caio, a estratégia pediu saída em {symbol}.", kind="pedido")
                await self.office.agent("cashier").request_close(tid, "sinal de saída da estratégia")

    # ----------------------------------------------------- backtest manual
    async def manual_backtest(self, symbol: str, timeframe: str, strategies: list[str] | None = None, bars_count: int | None = None) -> dict:
        cfg = get_config()
        keys = [k for k in (strategies or self.enabled_strategies()) if k in REGISTRY]
        spec = await self.office.market.spec(symbol)
        bars = await self.office.market.rates(symbol, timeframe, bars_count or BARS_BY_TF[timeframe], closed_only=True, max_age=600)
        if bars.n < 300:
            raise ValueError(f"histórico insuficiente para {symbol} {timeframe} ({bars.n} candles)")
        profiles = self._load_profiles(symbol, timeframe)
        costs = self.costs(spec)
        rules = self.rules()

        def run() -> list[dict]:
            out = []
            for key in keys:
                strat = get_strategy(key)
                prof = profiles.get(key, {})
                cand = Candidate(prof.get("params") or strat.defaults(), prof.get("filters") or {}, self.risk_for(key, prof.get("risk"), timeframe))
                ev = evaluate(bars, strat, cand, costs, rules, cfg.rank_by)
                out.append(
                    {
                        "strategy": key,
                        "name": strat.name,
                        "source": strat.source,
                        "style": strat.style,
                        "params": ev.candidate.params,
                        "filters": ev.candidate.filters,
                        "risk": ev.candidate.risk,
                        "metrics": ev.full,
                        "is_metrics": ev.ins,
                        "oos_metrics": ev.oos,
                        "approved": ev.approved,
                        "reasons": ev.reasons,
                        "score": ev.score,
                        "equity": equity_curve(ev.trades),
                        "trades": [t.to_dict() for t in ev.trades[-200:]],
                        "hours": ev.hours,
                    }
                )
            key_name = SORT_KEY.get(cfg.rank_by, "wilson_lb")
            out.sort(key=lambda r: (r["approved"], r["metrics"].get(key_name, 0)), reverse=True)
            return out

        self.work(f"Backtest pedido na tela: {symbol} {timeframe}", "desk", "📊")
        results = await asyncio.to_thread(run)
        self.skills.gain("backtesting", max(1, len(results) // 3), "backtest manual")
        self.idle("Acompanhando os setups")
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "bars": bars.n,
            "start": int(bars.time[0]),
            "end": int(bars.time[-1]),
            "data_source": self.office.market.source(),
            "rank_by": cfg.rank_by,
            "split_time": int(bars.time[int(bars.n * (1 - cfg.oos_fraction))]),
            "results": results,
        }


def recent_signals(limit: int = 50) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=14)
    with session_scope() as s:
        rows = list(s.scalars(select(Signal).where(Signal.ts >= since).order_by(Signal.ts.desc()).limit(limit)))
        return [
            {
                "id": r.id, "ts": r.ts.isoformat(), "symbol": r.symbol, "timeframe": r.timeframe, "strategy": r.strategy,
                "strategy_name": REGISTRY[r.strategy].name if r.strategy in REGISTRY else r.strategy,
                "direction": r.direction, "entry_type": r.entry_type, "price": r.price, "trigger": r.trigger,
                "sl": r.sl, "tp": r.tp, "status": r.status, "reason": r.reason, "trade_id": r.trade_id,
            }
            for r in rows
        ]

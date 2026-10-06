"""Simulação honesta de vários dias com preços reais (precisa de internet): o plano atual contra planos mais abertos.

A diferença para o diagnosticar_entradas.py: aqui a Estela aprova as estratégias só com o histórico de ANTES
de cada bloco de dias (como seria na vida real) e os dias do bloco são o teste. A cada bloco ela refaz o ranking
com os dados até o começo dele, como faria ao longo das semanas.

Cada regra de plano mostra as entradas que o backtest teria feito nos setups que ela estaria vigiando hora a hora
(plano por pontuação, sem IA e sem notícias). Não aplica a meta nem o limite de perda do dia da Rita, nem o teto de
posições abertas ao mesmo tempo.

Uso (na pasta backend):  python scripts/simular_fora_da_amostra.py --dia 2026-10-05 --dias 60 --blocos 3
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp(prefix="metabot-simulacao-")
os.environ.setdefault("MB_DATA_DIR", _TMP)
os.environ.setdefault("MB_DATABASE_URL", f"sqlite:///{_TMP}/simulacao.db")
os.environ.setdefault("MB_SECRET_KEY", "simulacao-local-0123456789abcdef0123456789abcdef")
os.environ.setdefault("MB_NETWORK_ENABLED", "true")
os.environ["MB_AGENTS_ENABLED"] = "true"

from app.config import get_settings  # noqa: E402
from app.db import configure, init_db  # noqa: E402
from app.security import SecretBox, set_secret_box  # noqa: E402

BRT = ZoneInfo("America/Sao_Paulo")


def line(text: str = "") -> None:
    print(text, flush=True)


def plan_with(cands: list[dict], limit: int, per_symbol: int, min_score: float) -> list[dict]:
    """Mesma regra do plano do Gustavo sem IA (manager.deterministic_plan), com limites diferentes."""
    out, used = [], Counter()
    for c in cands:
        if len(out) >= limit:
            break
        if c["blocked"] or used[c["symbol"]] >= per_symbol or c["score"] < min_score:
            continue
        used[c["symbol"]] += 1
        out.append(c)
    return out


POLICIES = {
    "atual (6 setups, 2 por par)": lambda cands: plan_with(cands, 6, 2, 0.45),
    "aberto (10 setups, 3 por par)": lambda cands: plan_with(cands, 10, 3, 0.45),
    "mais aberto (10, 3 por par, nota 0,35)": lambda cands: plan_with(cands, 10, 3, 0.35),
    "todas as livres": lambda cands: [c for c in cands if not c["blocked"]],
}


CARTEIRA_ATIVOS = ["US500", "NAS100", "US30", "GER40", "XAUUSD"]
CARTEIRA_ESTRATEGIAS = [
    "cruzamento_medias", "supertrend", "adx_dmi", "nrtr", "ichimoku_4regras", "squeeze_rompimento", "setup_91",
    "price_action_engolfo", "donchian_turtle", "macd_histograma", "hilo_activator", "rsi2_compra", "virada_mes", "correcao_tendencia",
]


def drawdown(values: list[float]) -> float:
    peak = acc = worst = 0.0
    for v in values:
        acc += v
        peak = max(peak, acc)
        worst = min(worst, acc - peak)
    return worst


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dia", default="", help="último dia, AAAA-MM-DD (padrão: ontem em Brasília)")
    parser.add_argument("--dias", type=int, default=60, help="quantos dias úteis simular, voltando a partir de --dia")
    parser.add_argument("--blocos", type=int, default=3, help="em quantos blocos a Estela refaz o ranking")
    parser.add_argument("--carteira", action="store_true", help="índices e ouro no gráfico diário, com as estratégias da carteira")
    parser.add_argument("--proposta", action="store_true", help="aprovação com 15+ operações fora da amostra e ranking por expectativa")
    args = parser.parse_args()
    if args.proposta:
        from app.core import metrics

        metrics.ApprovalRules.min_oos_trades = property(lambda self: max(15, int(self.min_trades * self.oos_fraction * 0.6)))
    last = date.fromisoformat(args.dia) if args.dia else (datetime.now(BRT).date() - timedelta(days=1))
    days: list[date] = []
    d = last
    while len(days) < max(1, args.dias):
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    size = -(-len(days) // max(1, args.blocos))
    blocks = [days[i : i + size] for i in range(0, len(days), size)]

    def day_start(day: date) -> datetime:
        return datetime(day.year, day.month, day.day, tzinfo=BRT).astimezone(timezone.utc)

    settings = get_settings()
    set_secret_box(SecretBox(settings.secret_key))
    configure(settings.resolved_database_url)
    init_db()
    from app.agents import manager as manager_mod
    from app.agents.office import Office
    from app.agents.strategist import BARS_BY_TF
    from app.core.bars import Bars
    from app.core.backtest import RiskParams, run_backtest
    from app.core.strategies import apply_filters, get_strategy
    from app.runtime import TIMEFRAME_SECONDS, get_config

    office = Office(settings)
    office.ensure_setup()
    if args.proposta:
        from app.runtime import update_config

        update_config({"rank_by": "expectancy"})
    if args.carteira:
        from app.runtime import update_config

        update_config({"watchlist": CARTEIRA_ATIVOS, "timeframes": ["D1"], "enabled_strategies": CARTEIRA_ESTRATEGIAS})
    cfg = get_config()
    strategist = office.agent("strategist")
    schedule = office.agent("schedule")
    manager = office.agent("manager")
    news = office.agent("news")
    news.symbol_score = lambda symbol: {"score": 0.0, "confidence": 0.0, "alerts": []}  # dias passados: sem notícias

    # o histórico que a Estela e o Hugo enxergam termina no corte (começo do bloco)
    real_rates = office.market.rates
    cut = {"ts": None}

    async def rates(symbol, timeframe, count=1000, closed_only=False, max_age=None):
        bars = await real_rates(symbol, timeframe, count, closed_only=closed_only, max_age=3600)
        if cut["ts"] is None:
            return bars
        keep = bars.df[bars.df["time"].to_numpy() + TIMEFRAME_SECONDS[timeframe] <= cut["ts"]]
        return Bars(keep, bars.symbol, bars.timeframe, bars.point)

    office.market.rates = rates

    line(f"== {len(days)} dias úteis (Brasília) de {days[0]:%d/%m} a {days[-1]:%d/%m}, em {len(blocks)} blocos ==")
    line(f"   origem dos preços: {office.market.source()} · pares: {', '.join(cfg.watchlist)} · tempos gráficos: {', '.join(cfg.timeframes)}")
    line(f"   ranking por {cfg.rank_by} · risco por operação {cfg.risk_per_trade_pct}% do patrimônio (1R)")

    results: dict[str, dict[date, list[float]]] = {name: {day: [] for day in days} for name in POLICIES}
    signals_seen: dict[str, int] = Counter()
    real_hour = schedule.hour_quality
    blocked_why: Counter = Counter()
    for block in blocks:
        start, end = day_start(block[0]), day_start(block[-1]) + timedelta(days=1)
        cut["ts"] = start.timestamp()
        t0 = time.time()
        await schedule.refresh_hours()
        total = await strategist.run_ranking()
        approved = strategist.ranking(only_approved=True, limit=1000)
        by_tf = Counter(p["timeframe"] for p in approved)
        line("")
        line(f"== Bloco {block[0]:%d/%m} a {block[-1]:%d/%m}: ranking com dados até {block[0]:%d/%m} ==")
        line(f"   {total} backtests em {time.time() - t0:.0f}s, {len(approved)} aprovados ({', '.join(f'{k} {v}' for k, v in sorted(by_tf.items()))})")
        cut["ts"] = None

        # entradas que o backtest faria em cada setup aprovado durante o bloco (preços de verdade, depois do corte)
        trades_by_profile: dict[int, list[tuple[int, float]]] = {}
        for p in approved:
            strat = get_strategy(p["strategy"])
            tf_sec = TIMEFRAME_SECONDS[p["timeframe"]]
            try:
                bars = await office.market.rates(p["symbol"], p["timeframe"], BARS_BY_TF[p["timeframe"]], closed_only=True)
            except Exception as exc:  # noqa: BLE001
                line(f"   ! {p['symbol']} {p['timeframe']}: {exc}")
                continue
            sigs = apply_filters(bars, strat.signals(bars, p["params"]), p["filters"])
            risk = RiskParams.from_dict(strategist.risk_for(p["strategy"], p["risk"], p["timeframe"]))
            costs = strategist.costs(await office.market.spec(p["symbol"]))
            index = {int(t): i for i, t in enumerate(bars.time)}
            out = []
            for t in run_backtest(bars, sigs, risk, costs):
                i = index.get(int(t.entry_time))
                if i is None or i == 0:
                    continue
                signal_close = int(bars.time[i - 1]) + tf_sec  # o sinal fecha no candle anterior à entrada
                if start.timestamp() <= signal_close < end.timestamp():
                    out.append((signal_close, t.r))
            trades_by_profile[p["id"]] = out

        for day in block:
            for h in range(24):
                at = day_start(day) + timedelta(hours=h)
                schedule.hour_quality = lambda symbol, at=None, _t=at: real_hour(symbol, _t)

                class _Fixed(datetime):
                    @classmethod
                    def now(cls, tz=None, _t=at):
                        return _t if tz is None else _t.astimezone(tz)

                manager_mod.datetime = _Fixed
                try:
                    cands = manager.build_candidates()
                finally:
                    manager_mod.datetime = datetime
                for c in cands:
                    for why in c["blocked"]:
                        blocked_why[why.split(" (")[0]] += 1
                for name, rule in POLICIES.items():
                    ids = {c["profile_id"] for c in rule(cands)}
                    for pid in ids:
                        for ts, r in trades_by_profile.get(pid, []):
                            if at.timestamp() <= ts < at.timestamp() + 3600:
                                results[name][day].append(r)
                                signals_seen[name] += 1
        schedule.hour_quality = real_hour

        for name in POLICIES:
            rs = [r for day in block for r in results[name][day]]
            wins = sum(1 for r in rs if r > 0)
            line(f"   {name:40s} {len(rs):3d} entradas ({len(rs) / len(block):.1f}/dia) · acerto {wins / len(rs) if rs else 0:.0%} · soma {sum(rs):+.2f}R")

    line("")
    line(f"   candidatos bloqueados (hora a hora): {dict(blocked_why.most_common(5))}")
    line(f"== Total dos {len(days)} dias (tudo fora da amostra) ==")
    for name in POLICIES:
        per_day = [sum(results[name][day]) for day in days]
        rs = [r for day in days for r in results[name][day]]
        wins = sum(1 for r in rs if r > 0)
        green = sum(1 for v in per_day if v > 0)
        red = sum(1 for v in per_day if v < 0)
        line(
            f"   {name:40s} {len(rs):3d} entradas ({len(rs) / len(days):.1f}/dia) · acerto {wins / len(rs) if rs else 0:.0%} · "
            f"soma {sum(rs):+.2f}R ({sum(rs) * cfg.risk_per_trade_pct:+.1f}%) · dias no lucro {green}, no prejuízo {red} · "
            f"pior dia {min(per_day):+.2f}R · maior queda {drawdown(per_day):+.2f}R"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Por que a equipe não entrou? Refaz um dia inteiro com preços reais (precisa de internet).

1. A Estela roda o ranking de verdade (10 pares, tempos gráficos da configuração).
2. O Hugo mede a qualidade de cada hora.
3. Para o dia escolhido (padrão: ontem, no horário de Brasília), conta os sinais que cada
   estratégia aprovada deu candle a candle e quantos deles caíram num setup que o plano
   do Gustavo estaria vigiando naquela hora (plano por pontuação, sem IA).

Uso (na pasta backend):  python scripts/diagnosticar_entradas.py --dia 2026-10-05
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp(prefix="metabot-diagnostico-")
os.environ.setdefault("MB_DATA_DIR", _TMP)
os.environ.setdefault("MB_DATABASE_URL", f"sqlite:///{_TMP}/diagnostico.db")
os.environ.setdefault("MB_SECRET_KEY", "diagnostico-local-0123456789abcdef0123456789abcdef")
os.environ.setdefault("MB_NETWORK_ENABLED", "true")
os.environ["MB_AGENTS_ENABLED"] = "true"

from app.config import get_settings  # noqa: E402
from app.db import configure, init_db  # noqa: E402
from app.security import SecretBox, set_secret_box  # noqa: E402

BRT = ZoneInfo("America/Sao_Paulo")


def line(text: str = "") -> None:
    print(text, flush=True)


def plan_at(manager, cands: list[dict]) -> list[dict]:
    """Plano por pontuação (o mesmo que o Gustavo monta sem IA)."""
    return manager.deterministic_plan(cands)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dia", default="", help="AAAA-MM-DD (padrão: ontem em Brasília)")
    args = parser.parse_args()
    day = date.fromisoformat(args.dia) if args.dia else (datetime.now(BRT).date() - timedelta(days=1))
    start = datetime(day.year, day.month, day.day, tzinfo=BRT).astimezone(timezone.utc)
    end = start + timedelta(days=1)

    settings = get_settings()
    set_secret_box(SecretBox(settings.secret_key))
    configure(settings.resolved_database_url)
    init_db()
    from app.agents import manager as manager_mod
    from app.agents.office import Office
    from app.agents.strategist import BARS_BY_TF
    from app.core.backtest import RiskParams, run_backtest
    from app.core.strategies import apply_filters, get_strategy
    from app.runtime import TIMEFRAME_SECONDS, get_config

    office = Office(settings)
    office.ensure_setup()
    cfg = get_config()
    strategist = office.agent("strategist")
    schedule = office.agent("schedule")
    manager = office.agent("manager")

    line(f"== Dia analisado: {day} (Brasília) = {start:%Y-%m-%d %H:%M} a {end:%Y-%m-%d %H:%M} UTC ==")
    line(f"   pares: {', '.join(cfg.watchlist)} · tempos gráficos: {', '.join(cfg.timeframes)}")
    t0 = time.time()
    await schedule.refresh_hours()
    total = await strategist.run_ranking()
    approved = strategist.ranking(only_approved=True, limit=1000)
    line(f"   ranking: {total} backtests em {time.time() - t0:.0f}s, {len(approved)} aprovados")
    reasons = Counter()
    for p in strategist.ranking(limit=2000):
        if p["status"] != "aprovada":
            for r in (p["metrics"] or {}).get("reasons", []):
                reasons[r.split(" (")[0].split(" <")[0]] += 1
    line(f"   motivos das reprovações: {dict(reasons.most_common(6))}")
    by_tf = Counter(p["timeframe"] for p in approved)
    line(f"   aprovadas por tempo gráfico: {dict(by_tf)}")

    # sinais do dia em cada setup aprovado
    line("")
    line("== Sinais do dia em cada estratégia aprovada ==")
    signals: dict[int, list[tuple[int, int, float | None]]] = {}
    for p in approved:
        strat = get_strategy(p["strategy"])
        tf_sec = TIMEFRAME_SECONDS[p["timeframe"]]
        try:
            bars = await office.market.rates(p["symbol"], p["timeframe"], BARS_BY_TF[p["timeframe"]], closed_only=True, max_age=900)
        except Exception as exc:  # noqa: BLE001
            line(f"   ! {p['symbol']} {p['timeframe']}: {exc}")
            continue
        sigs = apply_filters(bars, strat.signals(bars, p["params"]), p["filters"])
        risk = RiskParams.from_dict(strategist.risk_for(p["strategy"], p["risk"], p["timeframe"]))
        trades = {int(t.entry_time): t.r for t in run_backtest(bars, sigs, risk, strategist.costs(await office.market.spec(p["symbol"])))}
        out = []
        for i in range(bars.n):
            closes = int(bars.time[i]) + tf_sec
            if not (start.timestamp() <= closes < end.timestamp()):
                continue
            d = 1 if sigs.long_entry[i] else -1 if sigs.short_entry[i] else 0
            if d:
                nxt = int(bars.time[i + 1]) if i + 1 < bars.n else None
                out.append((closes, d, trades.get(nxt) if nxt else None))
        signals[p["id"]] = out
        m = p["metrics"] or {}
        line(
            f"   {p['symbol']:7s} {p['timeframe']:3s} {p['strategy_name'][:34]:34s} acerto {m.get('win_rate', 0):.0%} "
            f"{m.get('trades_per_month', 0):5.1f}/mês · pontuação {p['score']:.3f} · sinais no dia: {len(out)}"
            + (f" (R: {', '.join('?' if r is None else f'{r:+.2f}' for _, _, r in out)})" if out else "")
        )
    all_sigs = sum(len(v) for v in signals.values())

    # o que o plano estaria vigiando a cada hora
    line("")
    line("== Plano por pontuação, hora a hora (sem IA) ==")
    news = office.agent("news")
    news.symbol_score = lambda symbol: {"score": 0.0, "confidence": 0.0, "alerts": []}  # dia passado: sem notícias
    watched_hits = []
    seen_plans = Counter()
    real_hour = schedule.hour_quality
    for h in range(24):
        at = start + timedelta(hours=h)
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
        plan = plan_at(manager, cands)
        free = sum(1 for c in cands if not c["blocked"])
        key = tuple(f"{p['symbol']} {p['timeframe']} {p['strategy']}" for p in plan)
        seen_plans[key] += 1
        ids = {p["profile_id"] for p in plan}
        hits = [(pid, s) for pid, lst in signals.items() if pid in ids for s in lst if at.timestamp() <= s[0] < at.timestamp() + 3600]
        watched_hits += hits
        line(f"   {at.astimezone(BRT):%H}h BRT: {free:2d} livres de {len(cands)} · plano: {', '.join(key) or '(vazio)'} · sinais vigiados: {len(hits)}")

    line("")
    line("== Resumo ==")
    line(f"   sinais de TODAS as aprovadas no dia: {all_sigs}")
    line(f"   sinais que caíram no plano vigiado:  {len(watched_hits)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

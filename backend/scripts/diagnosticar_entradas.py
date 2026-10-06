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


def policy(cands: list[dict], limit: int, per_symbol: int, min_score: float = 0.45, activity: bool = False) -> list[dict]:
    """Plano por pontuação com regras ajustáveis (para comparar alternativas)."""
    def key(c):
        if not activity:
            return c["score"]
        spd = float(c.get("tpm") or 0) / 21.4
        return c["score"] * (0.75 + 0.25 * min(1.0, spd))
    out, used = [], Counter()
    for c in sorted(cands, key=key, reverse=True):
        if len(out) >= limit:
            break
        if c["blocked"] or used[c["symbol"]] >= per_symbol or c["score"] < min_score:
            continue
        used[c["symbol"]] += 1
        out.append(c)
    return out


POLICIES = {
    "atual (3 setups, 1 por par)": dict(limit=3, per_symbol=1),
    "6 setups, 1 por par": dict(limit=6, per_symbol=1),
    "6 setups, até 2 por par": dict(limit=6, per_symbol=2),
    "6 setups, até 2 por par + frequência": dict(limit=6, per_symbol=2, activity=True),
    "10 setups, até 3 por par + frequência": dict(limit=10, per_symbol=3, activity=True),
    "todas as livres (teto)": dict(limit=999, per_symbol=999, min_score=0.0),
}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dia", default="", help="último dia, AAAA-MM-DD (padrão: ontem em Brasília)")
    parser.add_argument("--dias", type=int, default=1, help="quantos dias úteis analisar, voltando a partir de --dia")
    args = parser.parse_args()
    last = date.fromisoformat(args.dia) if args.dia else (datetime.now(BRT).date() - timedelta(days=1))
    days: list[date] = []
    d = last
    while len(days) < max(1, args.dias):
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    windows = {
        day: (
            datetime(day.year, day.month, day.day, tzinfo=BRT).astimezone(timezone.utc),
            datetime(day.year, day.month, day.day, tzinfo=BRT).astimezone(timezone.utc) + timedelta(days=1),
        )
        for day in days
    }
    first_ts, last_ts = windows[days[0]][0].timestamp(), windows[days[-1]][1].timestamp()

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

    line(f"== Dias analisados (Brasília): {', '.join(str(x) for x in days)} ==")
    line(f"   origem dos preços: {office.market.source()} · pares: {', '.join(cfg.watchlist)} · tempos gráficos: {', '.join(cfg.timeframes)}")
    t0 = time.time()
    await schedule.refresh_hours()
    total = await strategist.run_ranking()
    approved = strategist.ranking(only_approved=True, limit=1000)
    line(f"   ranking: {total} backtests em {time.time() - t0:.0f}s, {len(approved)} aprovados")
    reasons = Counter()
    by_tf_all = Counter()
    for p in strategist.ranking(limit=2000):
        by_tf_all[p["timeframe"]] += 1
        if p["status"] != "aprovada":
            for r in (p["metrics"] or {}).get("reasons", []):
                reasons[r.split(" (")[0].split(" <")[0].split(" 0")[0].split(" 1")[0]] += 1
    line(f"   motivos das reprovações: {dict(reasons.most_common(6))}")
    by_tf = Counter(p["timeframe"] for p in approved)
    line(f"   aprovadas por tempo gráfico: {', '.join(f'{tf} {by_tf.get(tf, 0)}/{n}' for tf, n in by_tf_all.items())}")

    # sinais de cada setup aprovado nos dias analisados
    line("")
    line("== Sinais das estratégias aprovadas no período ==")
    signals: dict[int, list[tuple[int, int, float | None]]] = {}
    tpm = {p["id"]: float((p["metrics"] or {}).get("trades_per_month") or 0) for p in approved}
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
            if not (first_ts <= closes < last_ts):
                continue
            direction = 1 if sigs.long_entry[i] else -1 if sigs.short_entry[i] else 0
            if direction:
                nxt = int(bars.time[i + 1]) if i + 1 < bars.n else None
                out.append((closes, direction, trades.get(nxt) if nxt else None))
        signals[p["id"]] = out
        m = p["metrics"] or {}
        known = [r for _, _, r in out if r is not None]
        line(
            f"   {p['symbol']:7s} {p['timeframe']:3s} {p['strategy_name'][:34]:34s} acerto {m.get('win_rate', 0):.0%} "
            f"{m.get('trades_per_month', 0):5.1f}/mês · pontuação {p['score']:.3f} · sinais: {len(out)}"
            + (f" (soma {sum(known):+.2f}R em {len(known)} fechadas)" if known else "")
        )

    # o que cada regra de plano estaria vigiando, hora a hora
    news = office.agent("news")
    news.symbol_score = lambda symbol: {"score": 0.0, "confidence": 0.0, "alerts": []}  # dias passados: sem notícias
    real_hour = schedule.hour_quality
    hits: dict[str, list] = {name: [] for name in POLICIES}
    per_day: dict[str, Counter] = {name: Counter() for name in POLICIES}
    for day in days:
        start = windows[day][0]
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
            for c in cands:
                c["tpm"] = tpm.get(c["profile_id"], 0.0)
            for name, rules in POLICIES.items():
                ids = {c["profile_id"] for c in policy(cands, **rules)}
                got = [s for pid, lst in signals.items() if pid in ids for s in lst if at.timestamp() <= s[0] < at.timestamp() + 3600]
                hits[name] += got
                per_day[name][day] += len(got)

    line("")
    line("== Regras de plano comparadas (sinais que o plano estaria vigiando) ==")
    for name in POLICIES:
        known = [r for _, _, r in hits[name] if r is not None]
        wins = sum(1 for r in known if r > 0)
        by_day = " ".join(f"{d:%d/%m}:{per_day[name][d]}" for d in days)
        line(
            f"   {name:40s} sinais {len(hits[name]):3d} · fechadas {len(known):3d} · acerto {wins / len(known) if known else 0:.0%} · "
            f"soma {sum(known):+.2f}R · por dia {by_day}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

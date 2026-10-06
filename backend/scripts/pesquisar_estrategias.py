"""Pesquisa de estratégias com preços reais (precisa de internet): o que se sustenta sem ajuste fino.

Cada estratégia roda com parâmetros FIXOS (os padrões, ou os da literatura para as novas), sem a evolução
da Estela escolher nada olhando o resultado. Assim o histórico inteiro vale como teste.

1. Período de escolha: tudo antes dos últimos 6 meses. Uma estratégia "passa" se, somando todos os ativos
   do grupo, tem expectativa positiva depois dos custos, ganha na maioria dos ativos e na maioria dos 4
   pedaços de tempo.
2. Últimos 6 meses (guardados): conferimos se quem passou continua ganhando em dados que não ajudaram a escolher.

Uso (na pasta backend):  python scripts/pesquisar_estrategias.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp(prefix="metabot-pesquisa-")
os.environ.setdefault("MB_DATA_DIR", _TMP)
os.environ.setdefault("MB_DATABASE_URL", f"sqlite:///{_TMP}/pesquisa.db")
os.environ.setdefault("MB_SECRET_KEY", "pesquisa-local-0123456789abcdef0123456789abcdef")
os.environ.setdefault("MB_NETWORK_ENABLED", "true")

from app.config import get_settings  # noqa: E402
from app.core import indicators as ind  # noqa: E402
from app.core.bars import Bars  # noqa: E402
from app.core.backtest import RiskParams, run_backtest  # noqa: E402
from app.core.strategies import STRATEGIES, SignalSet  # noqa: E402
from app.db import configure, init_db  # noqa: E402
from app.security import SecretBox, set_secret_box  # noqa: E402

FX = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "EURJPY", "GBPJPY", "EURGBP"]
IDX = ["US500", "NAS100", "US30", "GER40"]
GOLD = ["XAUUSD"]
COUNT = {"H1": 13000, "H4": 3300, "D1": 2600}
HOLDOUT_DAYS = 182


def line(text: str = "") -> None:
    print(text, flush=True)


# --------------------------------------------------------------------------- estratégias novas (literatura)
def _hours(b: Bars) -> np.ndarray:
    return (b.time // 3600) % 24  # hora UTC de abertura do candle


def _days(b: Bars) -> np.ndarray:
    return b.time // 86400


def rsi2(b: Bars, p: dict) -> SignalSet:
    """Connors RSI(2): compra queda curta dentro da tendência de alta (acima da média de 200), sai acima da média de 5."""
    c = b.close
    r = ind.rsi(c, 2)
    s200, s5 = b.sma(200), b.sma(5)
    with np.errstate(invalid="ignore"):
        return SignalSet((c > s200) & (r < 10), (c < s200) & (r > 90), c > s5, c < s5)


def ibs(b: Bars, p: dict) -> SignalSet:
    """Internal Bar Strength: fechou perto da mínima do dia (IBS < 0,2) em tendência de alta → compra; sai ao fechar acima da máxima de ontem."""
    c, h, l = b.close, b.high, b.low
    with np.errstate(invalid="ignore", divide="ignore"):
        v = (c - l) / (h - l)
        s200 = b.sma(200)
        return SignalSet((v < 0.2) & (c > s200), (v > 0.8) & (c < s200), c > ind.shift(h), c < ind.shift(l))


def donchian_tendencia(b: Bars, p: dict) -> SignalSet:
    """Tartarugas sem alvo: entra no rompimento de 55 candles e só sai no rompimento contrário de 20 (deixa o lucro correr)."""
    c = b.close
    with np.errstate(invalid="ignore"):
        return SignalSet(
            c > ind.shift(b.highest(55)), c < ind.shift(b.lowest(55)), c < ind.shift(b.lowest(20)), c > ind.shift(b.highest(20))
        )


def medias_longas(b: Bars, p: dict) -> SignalSet:
    """Seguidor de tendência lento: média de 20 cruza a de 100; sai no cruzamento contrário, sem alvo."""
    f, s = b.ema(20), b.ema(100)
    up, dn = ind.cross_over(f, s), ind.cross_under(f, s)
    return SignalSet(up, dn, dn, up)


def london_breakout(b: Bars, p: dict) -> SignalSet:
    """Rompimento da faixa asiática (00h–07h UTC) entre 07h e 10h UTC; uma entrada por dia por lado."""
    hr, day = _hours(b), _days(b)
    n = b.n
    rh = np.full(n, np.nan)
    rl = np.full(n, np.nan)
    cur_day, hi, lo = -1, -np.inf, np.inf
    for i in range(n):
        if day[i] != cur_day:
            cur_day, hi, lo = day[i], -np.inf, np.inf
        if hr[i] < 7:
            hi, lo = max(hi, b.high[i]), min(lo, b.low[i])
        elif hi > -np.inf:
            rh[i], rl[i] = hi, lo
    window = (hr >= 7) & (hr <= 10)
    c = b.close
    with np.errstate(invalid="ignore"):
        up = window & (c > rh)
        dn = window & (c < rl)
    long_entry = up & ~_seen_today(up, day)
    short_entry = dn & ~_seen_today(dn, day)
    z = np.zeros(n, dtype=bool)
    return SignalSet(long_entry, short_entry, z, z)


def _seen_today(cond: np.ndarray, day: np.ndarray) -> np.ndarray:
    out = np.zeros(len(cond), dtype=bool)
    seen, cur = False, -1
    for i in range(len(cond)):
        if day[i] != cur:
            cur, seen = day[i], False
        out[i] = seen
        seen = seen or bool(cond[i])
    return out


def asia_reversao(b: Bars, p: dict) -> SignalSet:
    """Volta à média na madrugada (21h–05h UTC, mercado parado): fecha fora da Bollinger(20,2) → aposta na volta ao meio."""
    hr = _hours(b)
    up, mid, lo = b.bollinger(20, 2.0)
    c = b.close
    quiet = (hr >= 21) | (hr < 5)
    with np.errstate(invalid="ignore"):
        return SignalSet(quiet & (c < lo), quiet & (c > up), c > mid, c < mid)


def virada_do_mes(b: Bars, p: dict) -> SignalSet:
    """Virada do mês nos índices: compra para o último pregão do mês e segura 4 pregões (calendário, não preço)."""
    n = b.n
    months = np.array([datetime.fromtimestamp(int(t), timezone.utc).month for t in b.time])
    le = np.zeros(n, dtype=bool)
    le[: n - 2] = months[2:] != months[1:-1]  # o próximo candle é o último do mês
    z = np.zeros(n, dtype=bool)
    return SignalSet(le, z, z, z)


def pullback_tendencia(b: Bars, p: dict) -> SignalSet:
    """Correção dentro da tendência: médias 50 > 200 e o IFR(14) volta a subir acima de 40 (venda: espelho)."""
    c = b.close
    e50, e200 = b.ema(50), b.ema(200)
    r = ind.rsi(c, 14)
    with np.errstate(invalid="ignore"):
        up = (e50 > e200) & (c > e200)
        dn = (e50 < e200) & (c < e200)
    z = np.zeros(b.n, dtype=bool)
    return SignalSet(up & ind.cross_over(r, 40), dn & ind.cross_under(r, 60), z, z)


@dataclass
class Spec:
    key: str
    name: str
    fn: object
    risk: dict
    groups: list[tuple[str, str, list[str]]]  # (grupo, tempo gráfico, ativos)
    long_only: bool = False
    params: dict = field(default_factory=dict)


NO_MGMT = {"break_even_r": 0.0, "trailing_start_r": 0.0}
NEW = [
    Spec("rsi2", "RSI(2) de Connors", rsi2, {"sl_atr": 3.0, "tp_r": 0, "max_bars": 10, **NO_MGMT},
         [("índices", "D1", IDX), ("ouro", "D1", GOLD), ("forex", "D1", FX)]),
    Spec("ibs", "IBS (fechamento perto da mínima)", ibs, {"sl_atr": 3.0, "tp_r": 0, "max_bars": 5, **NO_MGMT},
         [("índices", "D1", IDX), ("ouro", "D1", GOLD), ("forex", "D1", FX)]),
    Spec("rsi2_compra", "RSI(2) só compra", rsi2, {"sl_atr": 3.0, "tp_r": 0, "max_bars": 10, **NO_MGMT},
         [("índices", "D1", IDX), ("índices", "H4", IDX)], long_only=True),
    Spec("ibs_compra", "IBS só compra", ibs, {"sl_atr": 3.0, "tp_r": 0, "max_bars": 5, **NO_MGMT},
         [("índices", "D1", IDX)], long_only=True),
    Spec("virada_mes", "Virada do mês", virada_do_mes, {"sl_atr": 4.0, "tp_r": 0, "max_bars": 4, **NO_MGMT},
         [("índices", "D1", IDX), ("ouro", "D1", GOLD)], long_only=True),
    Spec("donchian_longo", "Tartarugas sem alvo (55/20)", donchian_tendencia, {"sl_atr": 2.0, "tp_r": 0, "max_bars": 0, **NO_MGMT},
         [("forex", "D1", FX), ("forex", "H4", FX), ("índices", "D1", IDX), ("ouro", "D1", GOLD), ("ouro", "H4", GOLD)]),
    Spec("medias_longas", "Médias 20/100 sem alvo", medias_longas, {"sl_atr": 3.0, "tp_r": 0, "max_bars": 0, **NO_MGMT},
         [("forex", "D1", FX), ("forex", "H4", FX), ("índices", "D1", IDX), ("ouro", "D1", GOLD), ("ouro", "H4", GOLD)]),
    Spec("london", "Rompimento de Londres", london_breakout, {"sl_atr": 1.5, "tp_r": 1.5, "max_bars": 8, **NO_MGMT},
         [("forex", "H1", FX), ("ouro", "H1", GOLD)]),
    Spec("asia", "Volta à média na madrugada", asia_reversao, {"sl_atr": 1.5, "tp_r": 0, "max_bars": 6, **NO_MGMT},
         [("forex", "H1", FX)]),
    Spec("pullback", "Correção na tendência", pullback_tendencia, {"sl_atr": 2.0, "tp_r": 2.0, "max_bars": 30, **NO_MGMT},
         [("forex", "H4", FX), ("forex", "D1", FX), ("índices", "D1", IDX), ("ouro", "H4", GOLD), ("ouro", "D1", GOLD)]),
]


# --------------------------------------------------------------------------- avaliação
@dataclass
class Result:
    rs_pre: list[tuple[int, float]] = field(default_factory=list)  # (hora de entrada, R)
    rs_hold: list[tuple[int, float]] = field(default_factory=list)
    by_symbol: dict = field(default_factory=dict)


def summarize(name: str, res: Result, seg_edges: list[float]) -> dict:
    pre = [r for _, r in res.rs_pre]
    hold = [r for _, r in res.rs_hold]
    n = len(pre)
    exp = float(np.mean(pre)) if pre else 0.0
    gains, losses = sum(r for r in pre if r > 0), -sum(r for r in pre if r < 0)
    pf = gains / losses if losses > 0 else (9.99 if gains > 0 else 0.0)
    sym_pos = [s for s, v in res.by_symbol.items() if v and np.mean(v) > 0]
    seg = []
    for a, z in zip(seg_edges[:-1], seg_edges[1:]):
        part = [r for t, r in res.rs_pre if a <= t < z]
        seg.append(sum(part))
    seg_pos = sum(1 for v in seg if v > 0)
    passed = n >= 60 and exp >= 0.05 and pf >= 1.1 and len(sym_pos) >= 0.6 * max(1, len(res.by_symbol)) and seg_pos >= 3
    return {
        "name": name, "n": n, "exp": exp, "pf": pf, "win": (sum(1 for r in pre if r > 0) / n) if n else 0.0,
        "sym": f"{len(sym_pos)}/{len(res.by_symbol)}", "seg": f"{seg_pos}/4", "passed": passed,
        "hold_n": len(hold), "hold_exp": float(np.mean(hold)) if hold else 0.0, "hold_sum": sum(hold),
    }


async def main() -> int:
    import argparse

    from app.core.backtest import CostModel

    parser = argparse.ArgumentParser()
    parser.add_argument("--carteira", action="store_true", help="simula a carteira diária de índices e ouro com uma posição por ativo")
    parser.add_argument("--custo", choices=["atual", "raw", "zero"], default="atual",
                        help="atual: custos do simulado; raw: conta com spread baixo + comissão (tipo Pepperstone Razor); zero: sem custo (só para ver a vantagem bruta)")
    args = parser.parse_args()
    settings = get_settings()
    set_secret_box(SecretBox(settings.secret_key))
    configure(settings.resolved_database_url)
    init_db()
    from app.agents.office import Office

    office = Office(settings)
    office.ensure_setup()
    strategist = office.agent("strategist")
    market = office.market
    hold_start = (datetime.now(timezone.utc) - timedelta(days=HOLDOUT_DAYS)).timestamp()

    cache: dict[tuple[str, str], tuple[Bars, object] | None] = {}

    async def data(symbol: str, tf: str):
        key = (symbol, tf)
        if key not in cache:
            try:
                bars = await market.rates(symbol, tf, COUNT[tf], closed_only=True, max_age=86400)
                spec = await market.spec(symbol)
                costs = strategist.costs(spec)
                if args.custo == "zero":
                    costs = CostModel(spread=0.0, slippage=0.0, commission=0.0)
                elif args.custo == "raw" and symbol in FX:
                    costs = CostModel(spread=3 * spec["point"], slippage=costs.slippage / 2, commission=costs.commission)
                cache[key] = (bars, costs) if bars.n > 300 else None
            except Exception as exc:  # noqa: BLE001
                line(f"   ! sem dados de {symbol} {tf}: {exc}")
                cache[key] = None
        return cache[key]

    line(f"== Custos: {args.custo} ==")
    if args.carteira:
        await carteira(data, strategist, hold_start)
        return 0
    line(f"== Pesquisa de estratégias · preços: {market.source()} · últimos {HOLDOUT_DAYS} dias guardados para conferir ==")
    rows: list[dict] = []
    first_time: dict[str, float] = {}

    async def run(label: str, fn, risk: dict, tf: str, symbols: list[str], long_only: bool) -> None:
        res = Result()
        starts = []
        for sym in symbols:
            got = await data(sym, tf)
            if got is None:
                continue
            bars, costs = got
            starts.append(float(bars.time[0]))
            sigs = fn(bars)
            trades = run_backtest(bars, sigs, RiskParams.from_dict(risk), costs, allow_short=not long_only, warmup=210)
            mine = []
            for t in trades:
                (res.rs_hold if t.entry_time >= hold_start else res.rs_pre).append((t.entry_time, t.r))
                if t.entry_time < hold_start:
                    mine.append(t.r)
            res.by_symbol[sym] = mine
        if not starts:
            return
        a = float(np.median(starts)) + 210 * {"H1": 3600, "H4": 14400, "D1": 86400}[tf]
        edges = list(np.linspace(a, hold_start, 5))
        row = summarize(label, res, edges)
        row["tf"] = tf
        rows.append(row)

    t0 = time.time()
    # estratégias que já temos: parâmetros padrão e a mesma gestão de saída da equipe (zero a zero, trailing)
    groups_existing = [("forex", "H1", FX), ("forex", "H4", FX), ("forex", "D1", FX), ("índices", "H1", IDX), ("índices", "D1", IDX), ("ouro", "H1", GOLD), ("ouro", "D1", GOLD)]
    for s in STRATEGIES:
        for group, tf, syms in groups_existing:
            risk = strategist.risk_for(s.key, None, "H4" if tf == "D1" else tf)
            await run(f"[atual] {s.name} · {group}", lambda b, s=s: s.signals(b, s.defaults()), risk, tf, syms, False)
    for spec in NEW:
        for group, tf, syms in spec.groups:
            await run(f"[nova] {spec.name} · {group}", lambda b, f=spec.fn: f(b, {}), spec.risk, tf, syms, spec.long_only)
    line(f"   {len(rows)} combinações testadas em {time.time() - t0:.0f}s")

    def show(r: dict) -> str:
        return (
            f"   {r['name'][:58]:58s} {r['tf']:3s} {r['n']:5d} op · acerto {r['win']:.0%} · {r['exp']:+.3f}R/op · fator {r['pf']:.2f} · "
            f"ativos + {r['sym']:5s} · pedaços + {r['seg']} || últimos 6 meses: {r['hold_n']:4d} op · {r['hold_exp']:+.3f}R/op · soma {r['hold_sum']:+.1f}R"
        )

    rows.sort(key=lambda r: r["exp"], reverse=True)
    line("")
    line("== PASSARAM no período de escolha (expectativa > 0,05R, fator ≥ 1,1, 60% dos ativos e 3 de 4 pedaços no positivo) ==")
    for r in [r for r in rows if r["passed"]]:
        line(show(r))
    line("")
    line("== Todas as combinações (ordem: expectativa no período de escolha) ==")
    for r in rows:
        line(show(r))
    by_kind = defaultdict(list)
    for r in rows:
        by_kind[r["name"].split("]")[0] + "]"].append(r)
    line("")
    for k, lst in by_kind.items():
        tot = sum(r["n"] for r in lst)
        avg = sum(r["exp"] * r["n"] for r in lst) / tot if tot else 0
        line(f"   {k}: {len(lst)} combinações · {sum(r['passed'] for r in lst)} passaram · média {avg:+.3f}R por operação")
    return 0


# Carteira diária: as combinações de índices e ouro no D1 que passaram no período de escolha (sem olhar os 6 meses guardados)
CARTEIRA = {
    "índices": ["pullback", "cruzamento_medias", "nrtr", "supertrend", "squeeze", "ichimoku", "adx_dmi", "rsi2_compra", "setup_91", "price_action_engolfo"],
    "ouro": ["donchian", "macd_histograma", "hilo_activator", "cruzamento_medias", "nrtr", "virada_mes"],
}


async def carteira(data, strategist, hold_start: float) -> None:
    from app.core.strategies import REGISTRY

    new = {s.key: s for s in NEW}
    groups = {"índices": IDX, "ouro": GOLD}
    all_trades = []
    for group, keys in CARTEIRA.items():
        for sym in groups[group]:
            got = await data(sym, "D1")
            if got is None:
                continue
            bars, costs = got
            cands = []
            for prio, key in enumerate(keys):
                if key in new:
                    spec = new[key]
                    sigs, risk, long_only = spec.fn(bars, {}), spec.risk, spec.long_only
                else:
                    strat = next(s for s in REGISTRY.values() if s.key.startswith(key))
                    sigs, risk, long_only = strat.signals(bars, strat.defaults()), strategist.risk_for(strat.key, None, "H4"), False
                for t in run_backtest(bars, sigs, RiskParams.from_dict(risk), costs, allow_short=not long_only, warmup=210):
                    cands.append((t.entry_time, prio, t.exit_time, t.r, key))
            busy_until = 0
            for entry, prio, exit_, r, key in sorted(cands):
                if entry < busy_until:
                    continue  # já tem posição neste ativo
                busy_until = exit_
                all_trades.append((exit_, entry, sym, key, r))
    all_trades.sort()
    if not all_trades:
        line("   sem operações")
        return
    first = datetime.fromtimestamp(all_trades[0][1], timezone.utc)
    years = defaultdict(list)
    for exit_, entry, sym, key, r in all_trades:
        years[datetime.fromtimestamp(exit_, timezone.utc).year].append(r)
    line(f"== Carteira diária (índices + ouro, uma posição por ativo) desde {first:%m/%Y} ==")
    acc = peak = dd = 0.0
    for _, _, _, _, r in all_trades:
        acc += r
        peak = max(peak, acc)
        dd = min(dd, acc - peak)
    weeks = (all_trades[-1][0] - all_trades[0][1]) / (7 * 86400)
    rs = [x[4] for x in all_trades]
    line(f"   {len(rs)} operações ({len(rs) / weeks:.1f} por semana) · acerto {sum(1 for r in rs if r > 0) / len(rs):.0%} · {np.mean(rs):+.3f}R/op · soma {sum(rs):+.1f}R · maior queda {dd:+.1f}R")
    for y in sorted(years):
        v = years[y]
        line(f"   {y}: {len(v):3d} op · soma {sum(v):+6.1f}R · {np.mean(v):+.3f}R/op")
    hold = [x[4] for x in all_trades if x[1] >= hold_start]
    acc = peak = dd = 0.0
    for r in hold:
        acc += r
        peak = max(peak, acc)
        dd = min(dd, acc - peak)
    line(f"   últimos 6 meses (guardados): {len(hold)} op · soma {sum(hold):+.1f}R · {np.mean(hold) if hold else 0:+.3f}R/op · maior queda {dd:+.1f}R")
    by_key = defaultdict(list)
    for x in all_trades:
        by_key[(x[2], x[3])].append(x[4])
    for (sym, key), v in sorted(by_key.items()):
        line(f"     {sym:7s} {key:22s} {len(v):4d} op · {np.mean(v):+.3f}R/op · soma {sum(v):+.1f}R")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

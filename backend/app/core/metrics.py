"""Métricas de backtest, pontuação do ranking e critérios de aprovação."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from app.core.backtest import BTTrade

RANK_LABELS = {
    "win_rate": "taxa de acerto (conservadora)",
    "expectancy": "expectativa por operação",
    "profit_factor": "fator de lucro",
    "net": "resultado ajustado ao risco",
}


def wilson_lower(wins: int, n: int, z: float = 1.96) -> float:
    """Limite inferior do intervalo de Wilson: a taxa de acerto "garantida" com 95% de confiança."""
    if n <= 0:
        return 0.0
    p = wins / n
    denom = 1.0 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (center - margin) / denom)


def wilson_upper(wins: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 1.0
    p = wins / n
    denom = 1.0 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return min(1.0, (center + margin) / denom)


def _max_consecutive(flags: Iterable[bool]) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


EMPTY = {
    "trades": 0,
    "wins": 0,
    "losses": 0,
    "win_rate": 0.0,
    "wilson_lb": 0.0,
    "expectancy_r": 0.0,
    "profit_factor": 0.0,
    "net_r": 0.0,
    "return_pct": 0.0,
    "max_dd_pct": 0.0,
    "sharpe": 0.0,
    "avg_bars": 0.0,
    "max_consec_losses": 0,
    "best_r": 0.0,
    "worst_r": 0.0,
    "long_trades": 0,
    "short_trades": 0,
    "long_win_rate": 0.0,
    "short_win_rate": 0.0,
    "trades_per_month": 0.0,
}


def compute_metrics(trades: list[BTTrade], risk_pct: float = 1.0, span_seconds: float | None = None) -> dict:
    n = len(trades)
    if n == 0:
        return dict(EMPTY)
    rs = np.array([tr.r for tr in trades], dtype=float)
    wins = int((rs > 0).sum())
    gross_win = float(rs[rs > 0].sum())
    gross_loss = float(-rs[rs < 0].sum())
    if gross_loss > 0:
        pf = gross_win / gross_loss
    else:
        pf = 99.0 if gross_win > 0 else 0.0
    growth = np.clip(1.0 + (risk_pct / 100.0) * rs, 0.01, None)
    equity = np.cumprod(growth)
    peak = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    dd = (peak - equity) / peak
    std = float(rs.std(ddof=1)) if n > 1 else 0.0
    longs = [tr for tr in trades if tr.direction > 0]
    shorts = [tr for tr in trades if tr.direction < 0]
    months = (span_seconds / (30 * 86400)) if span_seconds else 0
    return {
        "trades": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": round(wins / n, 4),
        "wilson_lb": round(wilson_lower(wins, n), 4),
        "expectancy_r": round(float(rs.mean()), 4),
        "profit_factor": round(min(pf, 99.0), 3),
        "net_r": round(float(rs.sum()), 3),
        "return_pct": round(float((equity[-1] - 1.0) * 100.0), 2),
        "max_dd_pct": round(float(dd.max() * 100.0), 2),
        "sharpe": round(float(rs.mean() / std * math.sqrt(n)) if std > 0 else 0.0, 3),
        "avg_bars": round(float(np.mean([tr.bars_held for tr in trades])), 1),
        "max_consec_losses": _max_consecutive(r <= 0 for r in rs),
        "best_r": round(float(rs.max()), 3),
        "worst_r": round(float(rs.min()), 3),
        "long_trades": len(longs),
        "short_trades": len(shorts),
        "long_win_rate": round(sum(1 for tr in longs if tr.r > 0) / len(longs), 4) if longs else 0.0,
        "short_win_rate": round(sum(1 for tr in shorts if tr.r > 0) / len(shorts), 4) if shorts else 0.0,
        "trades_per_month": round(n / months, 2) if months > 0 else 0.0,
    }


def equity_curve(trades: list[BTTrade], risk_pct: float = 1.0, max_points: int = 300) -> list[dict]:
    points = [{"t": trades[0].entry_time if trades else 0, "equity": 100.0}]
    eq = 100.0
    for tr in trades:
        eq *= max(0.01, 1.0 + (risk_pct / 100.0) * tr.r)
        points.append({"t": tr.exit_time, "equity": round(eq, 3)})
    if len(points) > max_points:
        step = len(points) / max_points
        points = [points[int(i * step)] for i in range(max_points)] + [points[-1]]
    return points


def hour_stats(trades: list[BTTrade]) -> dict:
    """Resultado por hora de entrada (UTC): {hora: [operações, acertos, soma de R]}."""
    out: dict[str, list] = {}
    for tr in trades:
        hour = str((tr.entry_time // 3600) % 24)
        row = out.setdefault(hour, [0, 0, 0.0])
        row[0] += 1
        row[1] += 1 if tr.r > 0 else 0
        row[2] = round(row[2] + tr.r, 4)
    return out


def objective(m: dict, rank_by: str) -> float:
    """Função objetivo usada na evolução (quanto maior, melhor)."""
    n = m.get("trades", 0)
    if n < 3:
        return -1e9
    if rank_by == "win_rate":
        base = m["wilson_lb"]
        return base if m["expectancy_r"] > 0 else base - 1.0
    if rank_by == "profit_factor":
        return min(m["profit_factor"], 5.0) * (1 - math.exp(-n / 20.0))
    if rank_by == "net":
        return m["return_pct"] / (5.0 + m["max_dd_pct"])
    return m["expectancy_r"] * math.sqrt(min(n, 200))


def score(m: dict, rank_by: str) -> float:
    """Nota de 0 a 1 para o ranking; considera o tamanho da amostra."""
    n = m.get("trades", 0)
    if n == 0:
        return 0.0
    confidence = 1 - math.exp(-n / 30.0)
    if rank_by == "win_rate":
        base = m["wilson_lb"] * (1.0 if m["expectancy_r"] > 0 else 0.35)
    elif rank_by == "profit_factor":
        base = min(m["profit_factor"], 4.0) / 4.0
    elif rank_by == "net":
        base = 1 / (1 + math.exp(-m["return_pct"] / (10.0 + m["max_dd_pct"])))
    else:
        base = 1 / (1 + math.exp(-3.0 * m["expectancy_r"]))
    return round(base * (0.5 + 0.5 * confidence), 4)


@dataclass
class ApprovalRules:
    min_trades: int = 25
    min_profit_factor: float = 1.1
    oos_fraction: float = 0.3

    @property
    def min_oos_trades(self) -> int:
        return max(4, int(self.min_trades * self.oos_fraction * 0.6))


@dataclass
class Verdict:
    approved: bool
    reasons: list[str] = field(default_factory=list)


def _fmt(x: float, nd: int = 2) -> str:
    return f"{x:.{nd}f}".replace(".", ",")


def approve(full: dict, oos: dict, rules: ApprovalRules) -> Verdict:
    reasons: list[str] = []
    if full["trades"] < rules.min_trades:
        reasons.append(f"poucas operações ({full['trades']} < {rules.min_trades})")
    if full["profit_factor"] < rules.min_profit_factor:
        reasons.append(f"fator de lucro {_fmt(full['profit_factor'])} < {_fmt(rules.min_profit_factor)}")
    if full["expectancy_r"] <= 0:
        reasons.append("expectativa negativa depois dos custos")
    if oos["trades"] < rules.min_oos_trades:
        reasons.append(f"fora da amostra: poucas operações ({oos['trades']} < {rules.min_oos_trades})")
    elif oos["expectancy_r"] <= 0 or oos["profit_factor"] < 1.0:
        reasons.append("fora da amostra: não se sustentou (prejuízo no período que não foi usado para escolher)")
    return Verdict(approved=not reasons, reasons=reasons)

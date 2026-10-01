"""Revisão das posições abertas (o Gustavo, de hora em hora, junto com o Caio).

O Caio já cuida do básico a cada 5 s (stop, alvo, zero a zero, trailing e saída por tempo).
A revisão olha o **mercado de agora** e decide, para cada posição:

- **fechar** quando o motivo da entrada sumiu: evento forte chegando com lucro na mão,
  notícias fortes contra, estratégia que saiu do plano, operação devolvendo o lucro ou
  parada há muito tempo sem andar;
- **apertar o stop** (nunca afrouxar): garantir parte do lucro, reduzir o risco quando o
  contexto piorou ou quando a volatilidade caiu;
- **mudar o alvo**: esticar quando a tendência está forte perto do alvo (protegendo o lucro)
  ou trazer para perto quando o movimento enfraqueceu e a operação já está velha.

As regras são números (sem IA) para serem previsíveis. O aprendizado está na
"paciência" (-0,3 a +0,3): depois de cada fechamento pela revisão, o Gustavo confere o que
teria acontecido se tivesse segurado (``counterfactual``) e fica mais paciente quando saiu
cedo demais, ou mais rápido quando a saída evitou prejuízo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

PATIENCE_LIMIT = 0.3
CHECK_BARS = 12  # candles depois da saída usados para conferir a decisão


@dataclass
class PositionContext:
    direction: int  # +1 compra / -1 venda
    entry: float
    price: float  # preço de saída agora (bid na compra, ask na venda)
    sl: float | None
    tp: float | None
    risk: float  # distância do stop inicial (1R em preço)
    spread: float
    point: float
    atr_now: float
    atr_entry: float
    mfe_r: float  # melhor momento da operação, em R
    trend: str  # "with" | "against" | "flat"
    adx: float
    age_frac: float  # tempo em posição / tempo máximo
    age_text: str = ""
    typical_frac: float | None = None  # tempo em posição / tempo médio das operações da estratégia no backtest
    event: dict | None = None  # evento forte antes da próxima revisão
    event_minutes: float | None = None
    news_against: bool = False
    strategy_ok: bool = True
    patience: float = 0.0

    @property
    def r_now(self) -> float:
        return (self.price - self.entry) * self.direction / self.risk if self.risk > 0 else 0.0

    @property
    def min_gap(self) -> float:
        """Distância mínima entre o preço e um stop/alvo novo (não deixa colado no preço)."""
        return max(3 * self.spread, 0.3 * self.atr_now, 5 * self.point)


@dataclass
class ReviewDecision:
    action: str  # "close" | "adjust" | "hold"
    sl: float | None = None
    tp: float | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def why(self) -> str:
        return "; ".join(self.reasons)


def _r(value: float) -> str:
    return f"{value:+.1f}R".replace(".", ",")


def decide(c: PositionContext) -> ReviewDecision:
    """Decide o que fazer com uma posição. Nunca afrouxa o stop."""
    d = c.direction
    R = c.risk
    r = c.r_now
    if R <= 0:
        return ReviewDecision("hold", reasons=["sem stop inicial para medir o risco"])
    patience = 1.0 + max(-PATIENCE_LIMIT, min(PATIENCE_LIMIT, c.patience))

    # 1) fechar: o motivo da entrada sumiu
    if c.event and r >= 0.3:
        mins = f" em {c.event_minutes:.0f} min" if c.event_minutes is not None else ""
        return ReviewDecision("close", reasons=[f"{c.event.get('title', 'evento forte')} ({c.event.get('currency', '')}){mins} e o lucro já está na mão"])
    if c.news_against and r > 0.2:
        return ReviewDecision("close", reasons=["notícias fortes contra a posição; melhor garantir o lucro"])
    if not c.strategy_ok and r > 0.2:
        return ReviewDecision("close", reasons=["a estratégia saiu do plano (em revalidação); saio no lucro"])
    if c.mfe_r >= 1.2 * patience and 0 < r <= 0.4 * c.mfe_r and c.trend != "with":
        return ReviewDecision("close", reasons=[f"devolvendo o lucro: chegou a {_r(c.mfe_r)} e voltou para {_r(r)} sem tendência a favor"])
    old = c.age_frac >= 0.5 * patience or (c.typical_frac is not None and c.typical_frac >= 2.0 * patience)
    if old and abs(r) < 0.25 and c.mfe_r < 0.5:
        typical = ", o dobro do normal dessa estratégia" if c.typical_frac is not None and c.typical_frac >= 2.0 else ""
        return ReviewDecision("close", reasons=[f"parada há {c.age_text or 'muito tempo'}{typical} sem andar ({_r(r)}); libera o risco para um setup melhor"])

    best_sl: float | None = None
    reasons: list[str] = []

    def tighter_than(new: float, ref: float | None) -> bool:
        return ref is None or (new - ref) * d > 0

    def consider(new_sl: float, why: str) -> None:
        nonlocal best_sl
        if (c.price - new_sl) * d < c.min_gap:
            return  # colado no preço: sairia no primeiro ruído
        if not tighter_than(new_sl, c.sl) or not tighter_than(new_sl, best_sl):
            return
        best_sl = new_sl
        reasons.append(why)

    # 2) apertar o stop quando o contexto piorou (corta pela metade o risco que falta)
    bad = []
    if c.event:
        bad.append(f"{c.event.get('title', 'evento forte')} chegando")
    if c.news_against:
        bad.append("notícias contra")
    if not c.strategy_ok:
        bad.append("estratégia em revalidação")
    if c.trend == "against" and r < 0 and c.age_frac >= 0.3:
        bad.append("tendência virou contra")
    if bad and c.sl is not None:
        remaining = (c.price - c.sl) * d
        if remaining > 0:
            consider(c.price - d * remaining / 2, f"{', '.join(bad)}: risco que falta cortado pela metade")

    # 3) garantir lucro e acompanhar a volatilidade
    if r >= 1.5:
        lock = max(0.5, math.floor((r - 1.0) * 2) / 2)
        consider(c.entry + d * lock * R, f"garante {_r(lock)} com a operação em {_r(r)}")
    if c.atr_entry > 0 and c.atr_now < 0.6 * c.atr_entry and r > 0.5:
        consider(c.price - d * 1.5 * c.atr_now, "a volatilidade caiu bem desde a entrada; stop mais perto")

    # 4) alvo
    new_tp: float | None = None
    if c.tp is not None:
        dist_tp = (c.tp - c.price) * d
        tp_r = (c.tp - c.entry) * d / R
        if c.trend == "with" and c.adx >= 25 and dist_tp <= 0.3 * R and tp_r < 4.0:
            cand = c.entry + d * min(4.0, tp_r + 1.0) * R
            if (cand - c.price) * d >= c.min_gap:
                new_tp = cand
                reasons.append(f"tendência forte (ADX {c.adx:.0f}) perto do alvo: alvo +1R")
                consider(c.entry + d * max(0.0, r - 1.0) * R, "protege o lucro ao esticar o alvo")
        elif c.trend != "with" and (c.age_frac >= 0.6 or (c.typical_frac or 0.0) >= 2.0) and r > 0.3 and dist_tp > R:
            cand = c.price + d * max(0.5 * R, 2 * c.min_gap)
            if (cand - c.tp) * d < 0:
                new_tp = cand
                reasons.append("sem tendência a favor e a operação já passou do tempo normal: alvo mais perto")

    if best_sl is not None or new_tp is not None:
        return ReviewDecision("adjust", sl=best_sl, tp=new_tp, reasons=reasons)
    if c.trend == "with":
        why = "tendência a favor, segue o plano"
    elif r < 0:
        why = "dentro do risco planejado, o stop está no lugar"
    else:
        why = "sem motivo para mexer agora"
    return ReviewDecision("hold", reasons=[why])


def counterfactual(direction: int, entry: float, risk: float, sl: float | None, tp: float | None, highs, lows, closes) -> float | None:
    """R que a operação teria feito se tivesse ficado aberta (stop/alvo da hora da saída).

    Candle que toca stop e alvo conta como stop (conservador). Sem toque, sai no último fechamento."""
    if risk <= 0 or len(closes) == 0:
        return None
    d = direction
    for hi, lo in zip(highs, lows):
        worst, best = (lo, hi) if d > 0 else (hi, lo)
        if sl is not None and (worst - sl) * d <= 0:
            return (sl - entry) * d / risk
        if tp is not None and (best - tp) * d >= 0:
            return (tp - entry) * d / risk
    return (float(closes[-1]) - entry) * d / risk


def learn_patience(current: float, diff_r: float) -> tuple[float, str]:
    """Ajusta a paciência depois de conferir uma saída (diff = teria feito − fez, em R)."""
    if diff_r > 0.3:
        new, verdict = current + 0.05, "cedo"
    elif diff_r < -0.3:
        new, verdict = current - 0.03, "acertou"
    else:
        new, verdict = current, "neutro"
    return round(max(-PATIENCE_LIMIT, min(PATIENCE_LIMIT, new)), 3), verdict

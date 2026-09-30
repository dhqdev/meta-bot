"""Motor de backtest do Estrategista.

Regras (as mesmas da operação real):

- Sinais só no **fechamento** do candle; a entrada a mercado acontece na
  **abertura seguinte** (compra no ask = bid + spread; venda no bid).
- Ordens stop (Setup 9.1) ficam válidas por N candles e executam quando o
  preço toca o gatilho (ou na abertura, se abrir além dele).
- Stop e alvo são conferidos com a máxima e a mínima de cada candle. Se os
  dois cabem no mesmo candle, conta o **stop** (pior caso).
- Custos: spread, slippage e comissão (em unidades de preço por lote).
- Break-even e trailing (se ligados) passam a valer a partir do candle
  seguinte ao que atingiu o gatilho.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from app.core.bars import Bars
from app.core.strategies import SignalSet


@dataclass
class RiskParams:
    sl_atr: float = 1.5
    tp_r: float = 2.0  # alvo em múltiplos do risco (0 = sem alvo)
    atr_period: int = 14
    max_bars: int = 0  # 0 = sem limite de tempo
    break_even_r: float = 0.0
    trailing_start_r: float = 0.0
    trailing_atr: float = 2.0
    min_sl_atr: float = 0.5
    max_sl_atr: float = 4.0

    @classmethod
    def from_dict(cls, data: dict | None) -> "RiskParams":
        data = data or {}
        fields = {k: data[k] for k in cls.__dataclass_fields__ if k in data and data[k] is not None}
        return cls(**fields)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CostModel:
    spread: float | None = None  # em preço; None = usa o spread de cada candle
    slippage: float = 0.0
    commission: float = 0.0  # ida e volta, em preço


@dataclass
class BTTrade:
    entry_i: int
    exit_i: int
    entry_time: int
    exit_time: int
    direction: int  # +1 compra / -1 venda
    entry: float
    exit: float
    sl: float
    tp: float | None
    risk: float
    pnl: float
    r: float
    reason: str
    bars_held: int

    def to_dict(self) -> dict:
        d = asdict(self)
        d["side"] = "buy" if self.direction > 0 else "sell"
        return d


def _spread_at(bars: Bars, costs: CostModel, i: int) -> float:
    if costs.spread is not None:
        return costs.spread
    s = bars.spread[i]
    return float(s) if np.isfinite(s) and s > 0 else 0.0


def run_backtest(
    bars: Bars,
    sigs: SignalSet,
    risk: RiskParams | None = None,
    costs: CostModel | None = None,
    allow_long: bool = True,
    allow_short: bool = True,
    warmup: int = 50,
) -> list[BTTrade]:
    risk = risk or RiskParams()
    costs = costs or CostModel()
    n = bars.n
    if n < warmup + 5:
        return []
    atr = bars.atr(risk.atr_period)
    o, h, l, c, t = bars.open, bars.high, bars.low, bars.close, bars.time
    le = sigs.long_entry if allow_long else np.zeros(n, dtype=bool)
    se = sigs.short_entry if allow_short else np.zeros(n, dtype=bool)
    lx, sx = sigs.long_exit, sigs.short_exit
    stop_entries = sigs.entry_type == "stop"
    slip = costs.slippage
    trades: list[BTTrade] = []

    pos = 0  # +1 comprado, -1 vendido, 0 fora
    entry = sl = risk_px = 0.0
    tp: float | None = None
    entry_i = 0
    be_done = trailed = False
    pending_market = 0  # direção a entrar na abertura do candle i
    pending_stop: tuple[int, float, float, int, int] | None = None  # (dir, gatilho, stop estrutural, válido até, candle do sinal)
    exit_next = False
    exit_reason = ""

    def open_levels(direction: int, fill: float, sig_i: int, structural: float | None) -> tuple[float, float | None, float]:
        a = atr[sig_i]
        if structural is not None and np.isfinite(structural):
            dist = abs(fill - structural)
            dist = min(max(dist, risk.min_sl_atr * a), risk.max_sl_atr * a)
        else:
            dist = risk.sl_atr * a
        stop = fill - direction * dist
        target = fill + direction * risk.tp_r * dist if risk.tp_r > 0 else None
        return stop, target, dist

    def stop_reason() -> str:
        if trailed and (sl - entry) * pos > costs.commission + 1e-12:
            return "trailing"
        if be_done and (sl - entry) * pos >= -1e-12:
            return "be"
        return "sl"

    def close_trade(i: int, price: float, reason: str) -> None:
        nonlocal pos
        pnl = (price - entry) * pos - costs.commission
        trades.append(
            BTTrade(
                entry_i=entry_i,
                exit_i=i,
                entry_time=int(t[entry_i]),
                exit_time=int(t[i]),
                direction=pos,
                entry=entry,
                exit=price,
                sl=sl,
                tp=tp,
                risk=risk_px,
                pnl=pnl,
                r=pnl / risk_px if risk_px > 0 else 0.0,
                reason=reason,
                bars_held=i - entry_i,
            )
        )
        pos = 0

    for i in range(warmup, n):
        spr = _spread_at(bars, costs, i)

        # 1) saída agendada no fechamento anterior: executa na abertura
        if pos != 0 and exit_next:
            fill = (o[i] - slip) if pos > 0 else (o[i] + spr + slip)
            close_trade(i, fill, exit_reason)
            exit_next = False

        # 2) entradas
        if pos == 0 and pending_market != 0:
            direction = pending_market
            pending_market = 0
            fill = (o[i] + spr + slip) if direction > 0 else (o[i] - slip)
            new_sl, new_tp, dist = open_levels(direction, fill, i - 1, None)
            if np.isfinite(new_sl) and dist > 0:
                pos, entry, entry_i = direction, fill, i
                sl, tp, risk_px = new_sl, new_tp, dist
                be_done = trailed = False
        elif pos == 0 and pending_stop is not None:
            direction, trigger, structural, valid_until, sig_i = pending_stop
            fill = None
            if direction > 0 and h[i] + spr >= trigger:
                fill = max(o[i] + spr, trigger) + slip
            elif direction < 0 and l[i] <= trigger:
                fill = min(o[i], trigger) - slip
            if fill is not None:
                new_sl, new_tp, dist = open_levels(direction, fill, sig_i, structural)
                if np.isfinite(new_sl) and dist > 0:
                    pos, entry, entry_i = direction, fill, i
                    sl, tp, risk_px = new_sl, new_tp, dist
                    be_done = trailed = False
                pending_stop = None
            elif i >= valid_until:
                pending_stop = None

        # 3) stop / alvo dentro do candle
        if pos != 0:
            if pos > 0:
                if l[i] <= sl:
                    fill = (min(sl, o[i]) if i > entry_i else sl) - slip
                    close_trade(i, fill, stop_reason())
                elif tp is not None and h[i] >= tp:
                    close_trade(i, max(tp, o[i]) if i > entry_i else tp, "tp")
            else:
                if h[i] + spr >= sl:
                    fill = (max(sl, o[i] + spr) if i > entry_i else sl) + slip
                    close_trade(i, fill, stop_reason())
                elif tp is not None and l[i] + spr <= tp:
                    close_trade(i, min(tp, o[i] + spr) if i > entry_i else tp, "tp")

        # 4) no fechamento: gestão e sinais
        if pos != 0:
            fav = (c[i] - entry) if pos > 0 else (entry - (c[i] + spr))
            if risk.break_even_r > 0 and not be_done and fav >= risk.break_even_r * risk_px:
                new_sl = entry + pos * costs.commission
                if (new_sl - sl) * pos > 0:
                    sl = new_sl
                be_done = True
            if risk.trailing_start_r > 0 and fav >= risk.trailing_start_r * risk_px and np.isfinite(atr[i]):
                trail = (c[i] - risk.trailing_atr * atr[i]) if pos > 0 else (c[i] + spr + risk.trailing_atr * atr[i])
                if (trail - sl) * pos > 0:
                    sl = trail
                    trailed = True
            if i + 1 < n:
                if (pos > 0 and (se[i] or lx[i])) or (pos < 0 and (le[i] or sx[i])):
                    exit_next, exit_reason = True, "sinal"
                    # reversão: entra do outro lado na mesma abertura
                    if not stop_entries:
                        if pos > 0 and se[i]:
                            pending_market = -1
                        elif pos < 0 and le[i]:
                            pending_market = 1
                elif risk.max_bars > 0 and i - entry_i + 1 >= risk.max_bars:
                    exit_next, exit_reason = True, "tempo"
        elif i + 1 < n and pending_stop is None and pending_market == 0 and np.isfinite(atr[i]):
            if le[i] or se[i]:
                direction = 1 if le[i] else -1
                if stop_entries:
                    trig = sigs.long_trigger if direction > 0 else sigs.short_trigger
                    stp = sigs.long_stop if direction > 0 else sigs.short_stop
                    if trig is not None and np.isfinite(trig[i]):
                        structural = float(stp[i]) if stp is not None else float("nan")
                        pending_stop = (direction, float(trig[i]), structural, i + max(1, sigs.valid_bars), i)
                else:
                    pending_market = direction

    if pos != 0:
        last = n - 1
        close_trade(last, c[last] - slip if pos > 0 else c[last] + _spread_at(bars, costs, last) + slip, "fim")
    return trades


def split_trades(trades: list[BTTrade], split_index: int) -> tuple[list[BTTrade], list[BTTrade]]:
    """Divide em dentro da amostra (entrada antes do corte) e fora da amostra."""
    ins = [tr for tr in trades if tr.entry_i < split_index]
    oos = [tr for tr in trades if tr.entry_i >= split_index]
    return ins, oos


def simulate_exit(
    bars: Bars,
    start_i: int,
    direction: int,
    entry: float,
    stop: float,
    target: float | None,
    risk: RiskParams,
    spread: float = 0.0,
) -> tuple[float, str, int]:
    """Refaz a gestão de uma operação já feita (entrada no candle ``start_i``) com outros parâmetros.

    Devolve (resultado em R, motivo da saída, candles até sair). Usado pelo Caixa
    para aprender break-even e trailing com as operações reais.
    """
    atr = bars.atr(risk.atr_period)
    risk_px = abs(entry - stop)
    if risk_px <= 0:
        return 0.0, "invalido", 0
    sl, be_done = stop, False
    h, l, c = bars.high, bars.low, bars.close
    end = bars.n if risk.max_bars <= 0 else min(bars.n, start_i + risk.max_bars)
    for i in range(start_i, end):
        if direction > 0:
            if l[i] <= sl:
                return (sl - entry) / risk_px, "sl", i - start_i
            if target is not None and h[i] >= target:
                return (target - entry) / risk_px, "tp", i - start_i
            fav = c[i] - entry
        else:
            if h[i] + spread >= sl:
                return (entry - sl) / risk_px, "sl", i - start_i
            if target is not None and l[i] + spread <= target:
                return (entry - target) / risk_px, "tp", i - start_i
            fav = entry - (c[i] + spread)
        if risk.break_even_r > 0 and not be_done and fav >= risk.break_even_r * risk_px:
            if (entry - sl) * direction > 0:
                sl = entry
            be_done = True
        if risk.trailing_start_r > 0 and fav >= risk.trailing_start_r * risk_px and np.isfinite(atr[i]):
            trail = c[i] - direction * risk.trailing_atr * atr[i]
            if (trail - sl) * direction > 0:
                sl = trail
    last = min(end, bars.n) - 1
    exit_px = c[last] if direction > 0 else c[last] + spread
    return (exit_px - entry) * direction / risk_px, "tempo", last - start_i

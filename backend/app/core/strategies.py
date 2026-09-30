"""Biblioteca de estratégias do Estrategista.

Escolhidas entre os setups do **Vilela One** (vilela.one), os indicadores de
sinal mais populares do **IndicatorSpot** (indicatorspot.com), setups clássicos
do day trade brasileiro e clássicos internacionais. Cada uma gera sinais de
compra e venda **no fechamento do candle** (nada do futuro), e o motor de
backtest executa na abertura seguinte com stop, alvo e custos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from app.core import indicators as ind
from app.core.bars import Bars


@dataclass(frozen=True)
class Param:
    name: str
    label: str
    default: float
    min: float
    max: float
    step: float
    kind: str = "int"  # int | float | bool

    def clean(self, value: Any) -> float | int | bool:
        if self.kind == "bool":
            return bool(value)
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = float(self.default)
        v = min(max(v, self.min), self.max)
        steps = round((v - self.min) / self.step) if self.step > 0 else 0
        v = min(self.min + steps * self.step, self.max)
        if self.kind == "int":
            return int(round(v))
        return round(v, 6)

    def values(self) -> list:
        if self.kind == "bool":
            return [False, True]
        out = []
        v = self.min
        while v <= self.max + 1e-9:
            out.append(int(round(v)) if self.kind == "int" else round(v, 6))
            v += self.step
        return out


@dataclass
class SignalSet:
    long_entry: np.ndarray
    short_entry: np.ndarray
    long_exit: np.ndarray
    short_exit: np.ndarray
    entry_type: str = "market"  # market | stop
    long_trigger: np.ndarray | None = None
    short_trigger: np.ndarray | None = None
    long_stop: np.ndarray | None = None  # stop estrutural (ex.: mínima do candle de sinal)
    short_stop: np.ndarray | None = None
    valid_bars: int = 1


@dataclass
class Strategy:
    key: str
    name: str
    source: str
    style: str
    description: str
    params: list[Param]
    fn: Callable[[Bars, dict], SignalSet]
    risk: dict = field(default_factory=lambda: {"sl_atr": 1.5, "tp_r": 2.0})
    how: str = ""

    def defaults(self) -> dict:
        return {p.name: p.clean(p.default) for p in self.params}

    def clean(self, params: dict | None) -> dict:
        params = params or {}
        return {p.name: p.clean(params.get(p.name, p.default)) for p in self.params}

    def signals(self, bars: Bars, params: dict | None = None) -> SignalSet:
        return self.fn(bars, self.clean(params))

    def info(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "source": self.source,
            "style": self.style,
            "description": self.description,
            "how": self.how,
            "risk": self.risk,
            "params": [
                {"name": p.name, "label": p.label, "default": p.default, "min": p.min, "max": p.max, "step": p.step, "kind": p.kind}
                for p in self.params
            ],
        }


def _flips(direction: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    prev = ind.shift(direction)
    return (direction > 0) & (prev < 0), (direction < 0) & (prev > 0)


def _falses(n: int) -> np.ndarray:
    return np.zeros(n, dtype=bool)


# ------------------------------------------------------------ Vilela One
def _ifr(b: Bars, p: dict) -> SignalSet:
    r = b.rsi(p["period"])
    long_entry = ind.cross_over(r, p["low"])
    short_entry = ind.cross_under(r, p["high"])
    if p["exit_mid"]:
        long_exit, short_exit = ind.cross_over(r, 50), ind.cross_under(r, 50)
    else:
        long_exit, short_exit = ind.cross_over(r, p["high"]), ind.cross_under(r, p["low"])
    return SignalSet(long_entry, short_entry, long_exit, short_exit)


def _ichimoku(b: Bars, p: dict) -> SignalSet:
    ic = b.ichimoku(p["tenkan"], p["kijun"], p["senkou"])
    c = b.close
    with np.errstate(invalid="ignore"):
        top = np.fmax(ic["span_a"], ic["span_b"])
        bottom = np.fmin(ic["span_a"], ic["span_b"])
        rules_long = (c > top).astype(int) + (ic["span_a"] > ic["span_b"]).astype(int) + (c > ic["kijun"]).astype(int) + (ic["tenkan"] > ic["kijun"]).astype(int)
        rules_short = (c < bottom).astype(int) + (ic["span_a"] < ic["span_b"]).astype(int) + (c < ic["kijun"]).astype(int) + (ic["tenkan"] < ic["kijun"]).astype(int)
    need = int(p["min_rules"])
    long_entry = ind.rising_edge(rules_long >= need)
    short_entry = ind.rising_edge(rules_short >= need)
    return SignalSet(long_entry, short_entry, ind.cross_under(c, ic["kijun"]), ind.cross_over(c, ic["kijun"]))


def _keltner(b: Bars, p: dict) -> SignalSet:
    up, mid, lo = b.keltner(p["period"], p["atr_period"], p["mult"])
    c = b.close
    return SignalSet(ind.cross_over(c, up), ind.cross_under(c, lo), ind.cross_under(c, mid), ind.cross_over(c, mid))


def _macd(b: Bars, p: dict) -> SignalSet:
    fast, slow = int(p["fast"]), int(p["slow"])
    if fast >= slow:
        fast = max(2, slow - 5)
    _, _, hist = b.macd(fast, slow, p["signal"])
    if int(p["mode"]) == 0:
        long_entry, short_entry = ind.cross_over(hist, 0), ind.cross_under(hist, 0)
    else:
        # "Muda de cor": o histograma vira para cima abaixo de zero (ou para baixo acima de zero).
        h1, h2 = ind.shift(hist), ind.shift(hist, 2)
        with np.errstate(invalid="ignore"):
            long_entry = (hist > h1) & (h1 <= h2) & (hist < 0)
            short_entry = (hist < h1) & (h1 >= h2) & (hist > 0)
    return SignalSet(long_entry, short_entry, ind.cross_under(hist, 0), ind.cross_over(hist, 0))


def _medias(b: Bars, p: dict) -> SignalSet:
    fast, slow = int(p["fast"]), int(p["slow"])
    if fast >= slow:
        slow = fast + 5
    f = b.ema(fast) if int(p["kind"]) == 0 else b.sma(fast)
    s = b.ema(slow) if int(p["kind"]) == 0 else b.sma(slow)
    return SignalSet(ind.cross_over(f, s), ind.cross_under(f, s), ind.cross_under(f, s), ind.cross_over(f, s))


def _bollinger(b: Bars, p: dict) -> SignalSet:
    up, mid, lo = b.bollinger(p["period"], p["dev"])
    c = b.close
    c1, up1, lo1 = ind.shift(c), ind.shift(up), ind.shift(lo)
    with np.errstate(invalid="ignore"):
        long_entry = (c > lo) & (c1 < lo1)
        short_entry = (c < up) & (c1 > up1)
    return SignalSet(long_entry, short_entry, ind.cross_over(c, mid), ind.cross_under(c, mid))


def _engolfo(b: Bars, p: dict) -> SignalSet:
    o, h, l, c = b.open, b.high, b.low, b.close
    o1, c1 = ind.shift(o), ind.shift(c)
    trend = b.ema(p["trend_ema"])
    rng = h - l
    with np.errstate(invalid="ignore", divide="ignore"):
        body_ok = np.abs(c - o) >= p["min_body"] * rng
        bull = (c > o) & (c1 < o1) & (c >= o1) & (o <= c1) & body_ok
        bear = (c < o) & (c1 > o1) & (c <= o1) & (o >= c1) & body_ok
        if int(p["mode"]) == 0:  # a favor da tendência
            long_entry, short_entry = bull & (c > trend), bear & (c < trend)
        else:  # reversão contra a tendência esticada
            long_entry, short_entry = bull & (c < trend), bear & (c > trend)
    n = b.n
    return SignalSet(long_entry, short_entry, _falses(n), _falses(n))


# ---------------------------------------------------------- IndicatorSpot
def _halftrend(b: Bars, p: dict) -> SignalSet:
    d = b.halftrend(p["amplitude"])
    le, se = _flips(d)
    return SignalSet(le, se, se.copy(), le.copy())


def _adx(b: Bars, p: dict) -> SignalSet:
    adx, pdi, mdi = b.adx(p["period"])
    with np.errstate(invalid="ignore"):
        strong = adx >= p["adx_min"]
    up, down = ind.cross_over(pdi, mdi), ind.cross_over(mdi, pdi)
    return SignalSet(up & strong, down & strong, down, up)


def _supertrend(b: Bars, p: dict) -> SignalSet:
    _, d = b.supertrend(p["period"], p["mult"])
    le, se = _flips(d)
    return SignalSet(le, se, se.copy(), le.copy())


def _nrtr(b: Bars, p: dict) -> SignalSet:
    d = b.nrtr(p["period"], p["mult"])
    le, se = _flips(d)
    return SignalSet(le, se, se.copy(), le.copy())


def _gmma(b: Bars, p: dict) -> SignalSet:
    scale = float(p["scale"])
    short_p = [max(2, int(round(x * scale))) for x in (3, 5, 8, 10, 12, 15)]
    long_p = [max(3, int(round(x * scale))) for x in (30, 35, 40, 45, 50, 60)]
    shorts = np.vstack([b.ema(n) for n in short_p])
    longs = np.vstack([b.ema(n) for n in long_p])
    with np.errstate(invalid="ignore"):
        bull = shorts.min(axis=0) > longs.max(axis=0)
        bear = shorts.max(axis=0) < longs.min(axis=0)
        s_avg, l_avg = shorts.mean(axis=0), longs.mean(axis=0)
    return SignalSet(ind.rising_edge(bull), ind.rising_edge(bear), ind.cross_under(s_avg, l_avg), ind.cross_over(s_avg, l_avg))


# ------------------------------------------------------ setups brasileiros
def _hilo(b: Bars, p: dict) -> SignalSet:
    d = b.hilo(p["period"])
    le, se = _flips(d)
    return SignalSet(le, se, se.copy(), le.copy())


def _setup91(b: Bars, p: dict) -> SignalSet:
    e = b.ema(p["ema"])
    slope = e - ind.shift(e)
    s1 = ind.shift(slope)
    with np.errstate(invalid="ignore"):
        turn_up = (slope > 0) & (s1 <= 0)
        turn_down = (slope < 0) & (s1 >= 0)
    tick = b.point if b.point > 0 else 0.0
    return SignalSet(
        long_entry=turn_up,
        short_entry=turn_down,
        long_exit=turn_down.copy(),
        short_exit=turn_up.copy(),
        entry_type="stop",
        long_trigger=b.high + tick,
        short_trigger=b.low - tick,
        long_stop=b.low - tick,
        short_stop=b.high + tick,
        valid_bars=int(p["valid_bars"]),
    )


def _didi(b: Bars, p: dict) -> SignalSet:
    fast, mid, slow = int(p["fast"]), int(p["mid"]), int(p["slow"])
    base = b.sma(mid)
    d_fast = b.sma(fast) - base
    d_slow = b.sma(slow) - base
    w = int(p["window"]) + 1
    fu, fd = ind.cross_over(d_fast, 0), ind.cross_under(d_fast, 0)
    su, sd = ind.cross_over(d_slow, 0), ind.cross_under(d_slow, 0)
    long_entry = (fu & ind.recent_any(sd, w)) | (sd & ind.recent_any(fu, w))
    short_entry = (fd & ind.recent_any(su, w)) | (su & ind.recent_any(fd, w))
    return SignalSet(long_entry, short_entry, fd, fu)


# -------------------------------------------------------------- clássicos
def _donchian(b: Bars, p: dict) -> SignalSet:
    hh = ind.shift(b.highest(p["entry"]))
    ll = ind.shift(b.lowest(p["entry"]))
    xh = ind.shift(b.highest(p["exit"]))
    xl = ind.shift(b.lowest(p["exit"]))
    c = b.close
    with np.errstate(invalid="ignore"):
        return SignalSet(c > hh, c < ll, c < xl, c > xh)


def _squeeze(b: Bars, p: dict) -> SignalSet:
    bb_up, _, bb_lo = b.bollinger(p["bb_period"], p["bb_dev"])
    kc_up, _, kc_lo = b.keltner(p["bb_period"], p["bb_period"], p["kc_mult"])
    with np.errstate(invalid="ignore"):
        on = (bb_up < kc_up) & (bb_lo > kc_lo)
    release = ind.shift_bool(on) & ~on
    mom = b.close - b.sma(p["mom_period"])
    with np.errstate(invalid="ignore"):
        long_entry = release & (mom > 0)
        short_entry = release & (mom < 0)
    return SignalSet(long_entry, short_entry, ind.cross_under(mom, 0), ind.cross_over(mom, 0))


def _stoch(b: Bars, p: dict) -> SignalSet:
    k, d = b.stoch(p["k"], p["d"], p["smooth"])
    k1 = ind.shift(k)
    with np.errstate(invalid="ignore"):
        long_entry = ind.cross_over(k, d) & (k1 < p["low"])
        short_entry = ind.cross_under(k, d) & (k1 > p["high"])
        long_exit = ind.cross_under(k, d) & (k > 50)
        short_exit = ind.cross_over(k, d) & (k < 50)
    return SignalSet(long_entry, short_entry, long_exit, short_exit)


P = Param

STRATEGIES: list[Strategy] = [
    Strategy(
        "ifr_reversao", "IFR (RSI) Reversão", "Vilela One", "reversão",
        "Compra quando o IFR sai da sobrevenda e vende quando sai da sobrecompra (padrão 30/70).",
        [P("period", "Período do IFR", 14, 5, 30, 1), P("low", "Sobrevenda", 30, 10, 45, 5), P("high", "Sobrecompra", 70, 55, 90, 5), P("exit_mid", "Sai no 50", 1, 0, 1, 1, "bool")],
        _ifr, {"sl_atr": 1.5, "tp_r": 1.5},
        "Compra: IFR cruza para cima a linha de sobrevenda. Venda: cruza para baixo a de sobrecompra. Saída: IFR volta ao 50 (ou ao extremo oposto).",
    ),
    Strategy(
        "ichimoku_4regras", "Ichimoku (4 regras)", "Vilela One", "tendência",
        "Entra quando as regras do Ichimoku se alinham: preço acima da nuvem, nuvem de alta, preço acima da Kijun e Tenkan acima da Kijun.",
        [P("tenkan", "Tenkan", 9, 5, 20, 1), P("kijun", "Kijun", 26, 15, 40, 1), P("senkou", "Senkou B", 52, 30, 80, 2), P("min_rules", "Regras exigidas", 4, 3, 4, 1)],
        _ichimoku, {"sl_atr": 2.0, "tp_r": 2.0},
        "Compra no candle em que as regras exigidas passam a valer. Saída quando o preço perde a Kijun.",
    ),
    Strategy(
        "keltner_rompimento", "Rompimento de Keltner", "Vilela One", "rompimento",
        "Compra quando fecha acima da banda superior do canal de Keltner; vende quando fecha abaixo da inferior.",
        [P("period", "Média (EMA)", 20, 10, 50, 1), P("atr_period", "Período do ATR", 10, 5, 30, 1), P("mult", "Multiplicador", 2.0, 1.0, 3.5, 0.25, "float")],
        _keltner, {"sl_atr": 1.5, "tp_r": 2.0},
        "Entrada no fechamento fora do canal. Saída quando o preço volta para a média do canal.",
    ),
    Strategy(
        "macd_histograma", "MACD Histograma", "Vilela One", "momentum",
        "Compra quando o histograma do MACD vira de vermelho para verde; vende no inverso.",
        [P("fast", "Rápida", 12, 5, 20, 1), P("slow", "Lenta", 26, 20, 50, 1), P("signal", "Sinal", 9, 5, 15, 1), P("mode", "Modo (0=cruza zero, 1=muda de cor)", 0, 0, 1, 1)],
        _macd, {"sl_atr": 1.5, "tp_r": 2.0},
        "Modo 0: histograma cruza o zero. Modo 1: histograma vira para cima abaixo de zero (ou para baixo acima). Saída quando cruza o zero contra.",
    ),
    Strategy(
        "cruzamento_medias", "Cruzamento de Médias", "Vilela One", "tendência",
        "Compra quando a média rápida cruza a lenta para cima; vende quando cruza para baixo.",
        [P("fast", "Média rápida", 9, 3, 30, 1), P("slow", "Média lenta", 21, 10, 100, 1), P("kind", "Tipo (0=EMA, 1=SMA)", 0, 0, 1, 1)],
        _medias, {"sl_atr": 2.0, "tp_r": 2.0},
        "Entrada no cruzamento; saída no cruzamento contrário.",
    ),
    Strategy(
        "bollinger_reversao", "Bandas de Bollinger (reversão)", "Vilela One", "reversão",
        "Compra quando o preço fecha de volta para dentro após fechar abaixo da banda inferior; vende no inverso.",
        [P("period", "Período", 20, 10, 40, 1), P("dev", "Desvios", 2.0, 1.5, 3.0, 0.25, "float")],
        _bollinger, {"sl_atr": 1.5, "tp_r": 1.5},
        "Saída quando o preço chega na média central.",
    ),
    Strategy(
        "price_action_engolfo", "Price Action: Engolfo", "Vilela One", "price action",
        "Candle de engolfo com corpo forte, a favor da tendência (EMA) ou contra ela (modo reversão).",
        [P("trend_ema", "EMA da tendência", 50, 20, 200, 10), P("min_body", "Corpo mínimo (% do candle)", 0.6, 0.3, 0.9, 0.1, "float"), P("mode", "Modo (0=a favor, 1=reversão)", 0, 0, 1, 1)],
        _engolfo, {"sl_atr": 1.5, "tp_r": 1.5},
        "Só stop e alvo: sem sinal de saída.",
    ),
    Strategy(
        "half_trend", "Half Trend", "IndicatorSpot", "tendência",
        "Indicador de tendência sem repintura: compra quando vira para alta, vende quando vira para baixa.",
        [P("amplitude", "Amplitude", 2, 1, 10, 1)],
        _halftrend, {"sl_atr": 1.5, "tp_r": 2.0},
        "Entrada na virada; saída na virada contrária.",
    ),
    Strategy(
        "adx_dmi", "ADX Buy/Sell (DMI)", "IndicatorSpot", "tendência",
        "Compra quando o +DI cruza o -DI para cima com ADX forte; vende no inverso.",
        [P("period", "Período", 14, 7, 30, 1), P("adx_min", "ADX mínimo", 20, 10, 35, 1)],
        _adx, {"sl_atr": 1.5, "tp_r": 2.0},
        "Saída no cruzamento contrário dos DIs.",
    ),
    Strategy(
        "supertrend", "Super Signals (Supertrend)", "IndicatorSpot", "tendência",
        "Seguidor de tendência por ATR: compra quando o Supertrend vira para alta, vende quando vira para baixa.",
        [P("period", "Período do ATR", 10, 5, 30, 1), P("mult", "Multiplicador", 3.0, 1.5, 5.0, 0.25, "float")],
        _supertrend, {"sl_atr": 1.5, "tp_r": 2.0},
        "Entrada e saída nas viradas.",
    ),
    Strategy(
        "nrtr", "NRTR (reversão por trailing)", "IndicatorSpot", "tendência",
        "Nick Rypock Trailing Reverse: segue o extremo do movimento e inverte quando o preço recua mais que N ATRs.",
        [P("period", "Período do ATR", 14, 5, 30, 1), P("mult", "Distância (ATRs)", 2.5, 1.0, 5.0, 0.25, "float")],
        _nrtr, {"sl_atr": 1.5, "tp_r": 2.0},
        "Entrada e saída nas inversões.",
    ),
    Strategy(
        "gmma", "Guppy (GMMA)", "IndicatorSpot", "tendência",
        "Médias múltiplas de Guppy: compra quando todo o grupo curto (3-15) fica acima do grupo longo (30-60).",
        [P("scale", "Escala dos períodos", 1.0, 0.5, 2.0, 0.25, "float")],
        _gmma, {"sl_atr": 2.0, "tp_r": 2.0},
        "Saída quando a média do grupo curto cruza a do grupo longo.",
    ),
    Strategy(
        "hilo_activator", "HiLo Activator", "Setup brasileiro", "tendência",
        "Média das máximas e das mínimas: compra quando fecha acima da média das máximas, vende abaixo da das mínimas.",
        [P("period", "Período", 8, 3, 30, 1)],
        _hilo, {"sl_atr": 2.0, "tp_r": 2.0},
        "Entrada e saída nas viradas do HiLo.",
    ),
    Strategy(
        "setup_91", "Setup 9.1 (Larry Williams)", "Setup brasileiro", "tendência",
        "A média exponencial de 9 vira para cima: compra no rompimento da máxima do candle que virou, com stop na mínima.",
        [P("ema", "Média exponencial", 9, 5, 21, 1), P("valid_bars", "Validade da ordem (candles)", 1, 1, 3, 1)],
        _setup91, {"sl_atr": 1.5, "tp_r": 2.0},
        "Ordem stop na máxima (compra) ou mínima (venda) do candle de sinal. Saída quando a média vira contra.",
    ),
    Strategy(
        "didi_agulhada", "Agulhada do Didi", "Setup brasileiro", "tendência",
        "Didi Index (médias 3, 8 e 20): a curta e a longa cruzam a de 8 em sentidos opostos ao mesmo tempo.",
        [P("fast", "Curta", 3, 2, 5, 1), P("mid", "Base", 8, 6, 12, 1), P("slow", "Longa", 20, 15, 30, 1), P("window", "Tolerância (candles)", 1, 0, 3, 1)],
        _didi, {"sl_atr": 1.5, "tp_r": 2.0},
        "Saída quando a média curta volta a cruzar a base.",
    ),
    Strategy(
        "donchian_turtle", "Rompimento Donchian (Tartarugas)", "Clássico", "rompimento",
        "Compra na máxima de N candles, vende na mínima; sai na mínima/máxima de M candles.",
        [P("entry", "Canal de entrada", 20, 10, 60, 5), P("exit", "Canal de saída", 10, 5, 30, 5)],
        _donchian, {"sl_atr": 2.0, "tp_r": 2.5},
        "Sistema das Tartarugas simplificado.",
    ),
    Strategy(
        "squeeze_rompimento", "Squeeze (compressão e rompimento)", "Clássico", "rompimento",
        "Bollinger dentro do Keltner (compressão); entra quando a compressão se desfaz com momentum a favor.",
        [P("bb_period", "Período", 20, 10, 40, 1), P("bb_dev", "Desvios da Bollinger", 2.0, 1.5, 3.0, 0.25, "float"), P("kc_mult", "Multiplicador do Keltner", 1.5, 1.0, 2.5, 0.25, "float"), P("mom_period", "Momentum", 12, 5, 30, 1)],
        _squeeze, {"sl_atr": 1.5, "tp_r": 2.0},
        "Saída quando o momentum troca de sinal.",
    ),
    Strategy(
        "estocastico", "Estocástico (cruzamento nos extremos)", "Clássico", "reversão",
        "Compra quando %K cruza %D para cima na sobrevenda; vende quando cruza para baixo na sobrecompra.",
        [P("k", "%K", 14, 5, 21, 1), P("d", "%D", 3, 2, 5, 1), P("smooth", "Suavização", 3, 1, 5, 1), P("low", "Sobrevenda", 20, 10, 30, 5), P("high", "Sobrecompra", 80, 70, 90, 5)],
        _stoch, {"sl_atr": 1.5, "tp_r": 1.5},
        "Saída no cruzamento contrário acima/abaixo de 50.",
    ),
]

REGISTRY: dict[str, Strategy] = {s.key: s for s in STRATEGIES}


def get_strategy(key: str) -> Strategy:
    try:
        return REGISTRY[key]
    except KeyError:
        raise KeyError(f"estratégia desconhecida: {key}") from None


# ------------------------------------------------------------- filtros
@dataclass(frozen=True)
class FilterDef:
    key: str
    name: str
    description: str


FILTERS: dict[str, FilterDef] = {
    "tendencia_ema200": FilterDef("tendencia_ema200", "Tendência (EMA 200)", "Só compra acima da EMA 200 e só vende abaixo dela."),
    "adx_min": FilterDef("adx_min", "Força (ADX ≥ 20)", "Só entra com ADX(14) de pelo menos 20."),
    "volatilidade": FilterDef("volatilidade", "Volatilidade normal", "ATR entre 0,6× e 2× a sua mediana dos últimos 100 candles."),
    "volume": FilterDef("volume", "Volume acima da média", "Volume do candle de sinal acima da média de 20."),
    "rsi_confirma": FilterDef("rsi_confirma", "IFR confirma", "Compra só com IFR(14) acima de 50; venda abaixo de 50."),
    "horarios": FilterDef("horarios", "Melhores horários", "Só entra nos horários (UTC) aprovados pelo agente de Horários."),
}


def apply_filters(bars: Bars, sigs: SignalSet, filters: dict | None) -> SignalSet:
    """Aplica filtros (mutações que o Estrategista testa) às entradas."""
    if not filters:
        return sigs
    long_ok = np.ones(bars.n, dtype=bool)
    short_ok = np.ones(bars.n, dtype=bool)
    with np.errstate(invalid="ignore"):
        if "tendencia_ema200" in filters:
            e = bars.ema(200)
            long_ok &= bars.close > e
            short_ok &= bars.close < e
        if "adx_min" in filters:
            level = float((filters.get("adx_min") or {}).get("level", 20))
            adx, _, _ = bars.adx(14)
            long_ok &= adx >= level
            short_ok &= adx >= level
        if "volatilidade" in filters:
            a = bars.atr(14)
            med = bars.cached(("atr_med", 100), lambda: ind._s(a).rolling(100, min_periods=50).median().to_numpy())
            ok = (a >= 0.6 * med) & (a <= 2.0 * med)
            long_ok &= ok
            short_ok &= ok
        if "volume" in filters:
            vol_ok = bars.volume > ind.sma(bars.volume, 20)
            if np.nansum(bars.volume) > 0:
                long_ok &= vol_ok
                short_ok &= vol_ok
        if "rsi_confirma" in filters:
            r = bars.rsi(14)
            long_ok &= r > 50
            short_ok &= r < 50
        if "horarios" in filters:
            hours = set(int(h) for h in (filters.get("horarios") or {}).get("hours", []))
            if hours:
                bar_hours = (bars.time // 3600) % 24
                ok = np.isin(bar_hours, list(hours))
                long_ok &= ok
                short_ok &= ok
    return SignalSet(
        long_entry=sigs.long_entry & long_ok,
        short_entry=sigs.short_entry & short_ok,
        long_exit=sigs.long_exit,
        short_exit=sigs.short_exit,
        entry_type=sigs.entry_type,
        long_trigger=sigs.long_trigger,
        short_trigger=sigs.short_trigger,
        long_stop=sigs.long_stop,
        short_stop=sigs.short_stop,
        valid_bars=sigs.valid_bars,
    )

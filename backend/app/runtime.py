"""Configurações editáveis pela tela (guardadas no banco)."""

from __future__ import annotations

import re
import threading
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

TIMEFRAMES = ["M5", "M15", "M30", "H1", "H4", "D1"]
TIMEFRAME_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}

FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Os 10 pares de moedas com que a equipe trabalha (nada mais, nada menos): notícias, calendário,
# estratégias e operações giram só em torno deles. Na corretora o nome pode ter sufixo (EURUSDm).
TRADING_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "EURJPY", "GBPJPY", "EURGBP"]


class NewsFeed(BaseModel):
    name: str
    url: str
    lang: str = "en"
    enabled: bool = True


DEFAULT_FEEDS = [
    NewsFeed(name="FXStreet", url="https://www.fxstreet.com/rss/news"),
    NewsFeed(name="Investing.com Forex", url="https://www.investing.com/rss/news_1.rss"),
    NewsFeed(name="CNBC Markets", url="https://www.cnbc.com/id/15839069/device/rss/rss.html"),
    NewsFeed(name="MarketWatch", url="https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    NewsFeed(name="Yahoo Finance", url="https://finance.yahoo.com/news/rssindex"),
    # cripto e mercado brasileiro: fora dos 10 pares, ficam desligadas (dá para religar na tela)
    NewsFeed(name="CoinDesk", url="https://www.coindesk.com/arc/outboundfeeds/rss/", enabled=False),
    NewsFeed(name="InfoMoney", url="https://www.infomoney.com.br/feed/", lang="pt", enabled=False),
    NewsFeed(name="Money Times", url="https://www.moneytimes.com.br/feed/", lang="pt", enabled=False),
    NewsFeed(name="Investing.com Brasil", url="https://br.investing.com/rss/news.rss", lang="pt", enabled=False),
]
OFF_PAIR_FEEDS = {f.url for f in DEFAULT_FEEDS if not f.enabled}

CONFIG_VERSION = 4


def pair_symbols(suffix: str = "") -> list[str]:
    """Os 10 pares com o sufixo da corretora (EURUSD + "m" = EURUSDm)."""
    return [p + suffix for p in TRADING_PAIRS]


class RuntimeConfig(BaseModel):
    config_version: int = CONFIG_VERSION

    # --- Sistema
    system_running: bool = False
    mode: Literal["paper", "live"] = "paper"
    # auto = MT5 quando conectado; senão, preços reais públicos (Yahoo/Binance); sem internet, o simulado
    data_source: Literal["auto", "mt5", "real", "synthetic"] = "auto"

    # --- Ativos: os 10 pares fixos (a tela só muda o sufixo da corretora)
    watchlist: list[str] = Field(default_factory=pair_symbols)
    symbol_suffix: str = Field("", max_length=12)
    # M5 = operações curtas (scalper); M15/H1 = day trade; H4 = posições mais longas
    timeframes: list[str] = Field(default_factory=lambda: ["M5", "M15", "H1", "H4"])
    enabled_strategies: list[str] = Field(default_factory=list)  # vazio = todas

    # --- Conta simulada
    # moeda da conta simulada: saldo, comissão, metas em valor e resultado (no modo real vale a moeda da conta do MT5)
    paper_currency: Literal["BRL", "USD"] = "BRL"
    paper_initial_balance: float = Field(10000.0, ge=100, le=100_000_000)
    paper_commission_per_lot: float = Field(7.0, ge=0, le=500)
    paper_slippage_points: float = Field(2.0, ge=0, le=1000)

    # --- Metas do dia (a Rita para a equipe ao bater qualquer uma; 0 = desligada)
    daily_loss_limit: float = Field(3.0, ge=0, le=100_000_000)
    daily_loss_unit: Literal["percent", "money"] = "percent"
    daily_profit_target: float = Field(0.0, ge=0, le=100_000_000)
    daily_profit_unit: Literal["percent", "money"] = "percent"
    close_on_daily_limit: bool = True  # encerra as posições abertas quando bater o limite ou a meta

    # --- Risco (Rita)
    risk_per_trade_pct: float = Field(0.5, ge=0.05, le=5)
    max_drawdown_pct: float = Field(12.0, ge=2, le=50)
    max_open_positions: int = Field(3, ge=1, le=20)
    max_positions_per_symbol: int = Field(1, ge=1, le=5)
    max_currency_exposure: int = Field(2, ge=1, le=10)
    max_spread_multiplier: float = Field(2.5, ge=1, le=10)
    adaptive_risk: bool = True
    min_lot_overrisk: float = Field(1.5, ge=1, le=5)

    # --- Estrategista (Estela)
    rank_by: Literal["win_rate", "expectancy", "profit_factor", "net"] = "win_rate"
    min_trades: int = Field(25, ge=5, le=1000)
    min_profit_factor: float = Field(1.1, ge=0.5, le=5)
    oos_fraction: float = Field(0.3, ge=0.1, le=0.5)
    ranking_interval_hours: float = Field(6, ge=0.5, le=168)
    evolution_enabled: bool = True
    evolution_interval_hours: float = Field(24, ge=1, le=720)

    # --- Gerente (Gustavo)
    decision_interval_minutes: int = Field(15, ge=1, le=240)
    # Setups vigiados ao mesmo tempo. Vigiar mais não aumenta o risco: a Rita continua limitando as
    # posições abertas, uma por ativo e a exposição por moeda; só aumenta a chance de pegar um sinal.
    max_active_setups: int = Field(6, ge=1, le=10)
    max_setups_per_symbol: int = Field(2, ge=1, le=5)  # mesmo par em tempos gráficos/estratégias diferentes
    min_hour_quality: float = Field(0.35, ge=0, le=1)
    use_news_filter: bool = True
    news_block_threshold: float = Field(0.55, ge=0.1, le=1)
    position_review_minutes: int = Field(60, ge=0, le=720)  # Gustavo revisa as posições abertas (0 = desligado)

    # --- Caixa (Caio)
    break_even_r: float = Field(1.0, ge=0, le=5)
    trailing_start_r: float = Field(1.5, ge=0, le=10)
    trailing_atr_mult: float = Field(2.0, ge=0.5, le=10)
    adaptive_exits: bool = True
    max_bars_in_trade: int = Field(0, ge=0, le=5000)
    close_before_weekend: bool = True
    weekend_close: bool = True  # escritório fecha sozinho com o mercado (sexta 18h) e reabre no domingo
    b3_close_time: str = "18:20"
    b3_prefixes: list[str] = Field(default_factory=lambda: ["WIN", "WDO", "IND", "DOL", "BIT"])

    # --- Horários e calendário (Hugo)
    blackout_before_min: int = Field(30, ge=0, le=240)
    blackout_after_min: int = Field(30, ge=0, le=240)
    blackout_impacts: list[str] = Field(default_factory=lambda: ["High"])
    calendar_url: str = FF_CALENDAR_URL

    # --- Notícias (Nina)
    news_enabled: bool = True
    news_interval_minutes: int = Field(10, ge=2, le=240)
    news_feeds: list[NewsFeed] = Field(default_factory=lambda: [f.model_copy() for f in DEFAULT_FEEDS])

    # --- Daily (reunião de fim de dia com relatório e aprendizado)
    daily_meeting_enabled: bool = True
    daily_meeting_time: str = "19:00"
    daily_break_minutes: int = Field(60, ge=0, le=720)  # escritório fechado depois da daily (0 = sem pausa)

    # --- IA (OpenRouter, modelo fixo por agente)
    ai_enabled: bool = True
    # Economia: intervalo mínimo entre análises de notícias por IA e validade do plano da IA
    ai_news_interval_minutes: int = Field(15, ge=5, le=240)
    ai_plan_refresh_minutes: int = Field(60, ge=15, le=720)
    ai_max_calls_per_hour: int = Field(12, ge=0, le=500)
    ai_daily_budget_usd: float = Field(0.5, ge=0, le=1000)

    # --- MetaTrader 5
    magic_number: int = Field(770077, ge=1, le=2_147_483_647)
    deviation_points: int = Field(20, ge=0, le=1000)
    server_utc_offset_hours: float | None = Field(None, ge=-14, le=14)

    @field_validator("watchlist")
    @classmethod
    def _clean_watchlist(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for item in value:
            sym = str(item).strip()
            if sym and sym not in out:
                out.append(sym[:40])
        if not out:
            raise ValueError("escolha pelo menos um ativo")
        if len(out) > 30:
            raise ValueError("no máximo 30 ativos")
        return out

    @field_validator("symbol_suffix")
    @classmethod
    def _clean_suffix(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9._#$-]*", value):
            raise ValueError("só letras, números e . _ - # $ (ex.: m, .a, -ECN)")
        return value

    @field_validator("timeframes")
    @classmethod
    def _clean_timeframes(cls, value: list[str]) -> list[str]:
        out = [tf.upper() for tf in value if tf.upper() in TIMEFRAMES]
        out = [tf for tf in TIMEFRAMES if tf in out]
        if not out:
            raise ValueError("escolha pelo menos um tempo gráfico")
        return out

    @field_validator("b3_close_time", "daily_meeting_time")
    @classmethod
    def _check_time(cls, value: str) -> str:
        hh, _, mm = value.partition(":")
        if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError("horário no formato HH:MM")
        return f"{int(hh):02d}:{int(mm):02d}"

    @model_validator(mode="after")
    def _check_daily_targets(self) -> "RuntimeConfig":
        if self.daily_loss_unit == "percent" and self.daily_loss_limit > 50:
            raise ValueError("limite de perda do dia: no máximo 50% do patrimônio")
        if self.daily_profit_unit == "percent" and self.daily_profit_target > 100:
            raise ValueError("meta de ganho do dia: no máximo 100% do patrimônio")
        return self


_KEY = "runtime_config"
_lock = threading.Lock()
_cache: RuntimeConfig | None = None


def get_config() -> RuntimeConfig:
    global _cache
    with _lock:
        if _cache is None:
            from app.kv import kv_get

            stored = _migrate(kv_get(_KEY, {}) or {})
            try:
                _cache = RuntimeConfig.model_validate(stored)
            except Exception:
                # Campo inválido salvo por uma versão antiga: mantém o que for válido.
                _cache = _keep_valid(stored)
        return _cache


def _keep_valid(stored: dict) -> RuntimeConfig:
    """Descarta só os campos que a validação recusou (antes, um campo ruim podia levar junto as metas do dia)."""
    trial = {k: v for k, v in stored.items() if k in RuntimeConfig.model_fields}
    for _ in range(len(trial) + 1):
        try:
            return RuntimeConfig.model_validate(trial)
        except ValidationError as exc:
            bad = {str(e["loc"][0]) for e in exc.errors() if e.get("loc")}
            if not bad & set(trial):
                break
            for k in bad:
                trial.pop(k, None)
    base = RuntimeConfig().model_dump()
    # unidades antes dos valores: "500" de perda só vale junto com a unidade "money"
    for k, v in sorted(stored.items(), key=lambda kv: not kv[0].endswith("_unit")):
        trial = dict(base, **{k: v})
        try:
            RuntimeConfig.model_validate(trial)
            base = trial
        except Exception:
            continue
    return RuntimeConfig.model_validate(base)


def _migrate(stored: dict) -> dict:
    """Converte a configuração salva por versões antigas para a atual."""
    stored = dict(stored)
    if int(stored.get("config_version") or 1) < 2:
        if "max_daily_loss_pct" in stored and "daily_loss_limit" not in stored:
            stored["daily_loss_limit"] = stored["max_daily_loss_pct"]
            stored["daily_loss_unit"] = "percent"
        tfs = list(stored.get("timeframes") or [])
        if tfs and "M5" not in tfs:
            stored["timeframes"] = ["M5", *tfs]
        stored["config_version"] = 2
    if int(stored.get("config_version") or 1) < 3:
        # v3: só os 10 pares fixos; aproveita o sufixo da corretora que já estava em uso (EURUSDm -> "m")
        suffix = ""
        for sym in stored.get("watchlist") or []:
            sym = str(sym).strip()
            for pair in TRADING_PAIRS:
                if sym.upper().startswith(pair) and len(sym) > len(pair):
                    suffix = sym[len(pair):]
                    break
            if suffix:
                break
        stored["symbol_suffix"] = suffix
        stored["watchlist"] = pair_symbols(suffix)
        feeds = stored.get("news_feeds")
        if isinstance(feeds, list):
            stored["news_feeds"] = [dict(f, enabled=False) if isinstance(f, dict) and f.get("url") in OFF_PAIR_FEEDS else f for f in feeds]
        stored["config_version"] = 3
    if int(stored.get("config_version") or 1) < 4:
        # v4: o plano vigiava só 3 setups (um por par) e quase sempre os mais lentos (H4): o dia passava
        # sem nenhum sinal. Quem estava no padrão antigo (3) passa para o novo (6).
        if stored.get("max_active_setups") in (None, 3):
            stored["max_active_setups"] = 6
        stored["config_version"] = CONFIG_VERSION
    for old in ("max_daily_loss_pct", "ai_provider", "ai_model", "ai_news_model", "ai_auditor_model", "openrouter_manager_model",
                "openrouter_news_model", "openrouter_auditor_model", "openrouter_fallback_model"):
        stored.pop(old, None)
    return stored


def update_config(patch: dict) -> RuntimeConfig:
    global _cache
    from app.kv import kv_set

    current = get_config().model_dump()
    current.update({k: v for k, v in patch.items() if k in RuntimeConfig.model_fields})
    new = RuntimeConfig.model_validate(current)
    with _lock:
        kv_set(_KEY, new.model_dump(mode="json"))
        _cache = new
    return new


def reset_cache() -> None:
    global _cache
    with _lock:
        _cache = None

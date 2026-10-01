"""Tabelas do Meta-Bot."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TZDateTime(TypeDecorator):
    """DateTime sempre em UTC e com fuso (o SQLite não guarda fuso)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            value = value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


# ----------------------------------------------------------------- acesso
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    totp_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime())
    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")


class Secret(Base):
    """Segredos criptografados (chave da IA, senha da corretora...)."""

    __tablename__ = "secrets"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_enc: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)


class KV(Base):
    """Configurações e estados persistidos (JSON)."""

    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(150), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)


class Terminal(Base):
    """Um terminal MetaTrader 5 (um container mt5 = uma conta de corretora)."""

    __tablename__ = "terminals"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    bridge_url: Mapped[str] = mapped_column(String(255))
    token_enc: Mapped[str] = mapped_column(Text, default="")
    login: Mapped[str] = mapped_column(String(40), default="")
    server: Mapped[str] = mapped_column(String(120), default="")
    password_enc: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


# --------------------------------------------------------------- atividade
class Activity(Base):
    __tablename__ = "activity"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    agent: Mapped[str] = mapped_column(String(30), index=True)
    kind: Mapped[str] = mapped_column(String(30), default="info")
    level: Mapped[str] = mapped_column(String(10), default="info")
    text: Mapped[str] = mapped_column(Text)
    data: Mapped[Any] = mapped_column(JSON, default=dict)


class AgentMessage(Base):
    """Conversa da equipe: mensagens de um agente para outro (ou para todos)."""

    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    sender: Mapped[str] = mapped_column(String(30), index=True)
    recipient: Mapped[str] = mapped_column(String(30), default="all", index=True)
    kind: Mapped[str] = mapped_column(String(20), default="info")
    text: Mapped[str] = mapped_column(Text)
    data: Mapped[Any] = mapped_column(JSON, default=dict)


class DailyReport(Base):
    """Relatório da daily (reunião de fim de dia): o que aconteceu, o que aprenderam e o que muda amanhã."""

    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[str] = mapped_column(String(10), unique=True, index=True)  # AAAA-MM-DD (horário local)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    mode: Mapped[str] = mapped_column(String(10), default="paper")
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="normal")  # normal | meta | limite
    mood: Mapped[str] = mapped_column(String(20), default="neutro")
    summary: Mapped[str] = mapped_column(Text, default="")
    transcript: Mapped[Any] = mapped_column(JSON, default=list)
    sections: Mapped[Any] = mapped_column(JSON, default=list)
    adjustments: Mapped[Any] = mapped_column(JSON, default=list)
    lessons: Mapped[Any] = mapped_column(JSON, default=list)
    focus: Mapped[Any] = mapped_column(JSON, default=list)
    metrics: Mapped[Any] = mapped_column(JSON, default=dict)
    ai: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str] = mapped_column(String(80), default="")


class AIUsage(Base):
    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    agent: Mapped[str] = mapped_column(String(30))
    purpose: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(60))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(String(300), default="")


# ------------------------------------------------------------------ skills
class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("agent", "key", name="uq_skill_agent_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agent: Mapped[str] = mapped_column(String(30), index=True)
    key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    level: Mapped[int] = mapped_column(Integer, default=1)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[Any] = mapped_column(JSON, default=dict)
    params: Mapped[Any] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)


class SkillEvent(Base):
    __tablename__ = "skill_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    agent: Mapped[str] = mapped_column(String(30), index=True)
    skill_key: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20))  # xp | levelup | evolved | demoted
    delta_xp: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[Any] = mapped_column(JSON, default=dict)


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    agent: Mapped[str] = mapped_column(String(30), index=True)
    text: Mapped[str] = mapped_column(Text)
    evidence: Mapped[Any] = mapped_column(JSON, default=dict)
    score: Mapped[float] = mapped_column(Float, default=1.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(20), default="auditor")


# --------------------------------------------------------- notícias/agenda
class NewsItem(Base):
    __tablename__ = "news"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(700), unique=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    lang: Mapped[str] = mapped_column(String(5), default="en")
    published_at: Mapped[datetime] = mapped_column(TZDateTime(), index=True)
    fetched_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    impact: Mapped[str] = mapped_column(String(10), default="low")
    category: Mapped[str] = mapped_column(String(30), default="other")
    assets: Mapped[Any] = mapped_column(JSON, default=dict)  # {"USD": 0.6, "EUR": -0.2}
    symbols: Mapped[Any] = mapped_column(JSON, default=dict)  # {"EURUSD": -0.8}
    summary_pt: Mapped[str] = mapped_column(Text, default="")
    ai: Mapped[bool] = mapped_column(Boolean, default=False)
    evaluated: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome: Mapped[Any] = mapped_column(JSON, default=dict)


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(10), index=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), index=True)
    impact: Mapped[str] = mapped_column(String(10))
    forecast: Mapped[str] = mapped_column(String(40), default="")
    previous: Mapped[str] = mapped_column(String(40), default="")
    actual: Mapped[str] = mapped_column(String(40), default="")
    source: Mapped[str] = mapped_column(String(40), default="forexfactory")


# ------------------------------------------------------------- estratégias
class StrategyProfile(Base):
    """O que o Estrategista sabe de uma estratégia num ativo e tempo gráfico."""

    __tablename__ = "strategy_profiles"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "strategy", name="uq_profile"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    timeframe: Mapped[str] = mapped_column(String(5))
    strategy: Mapped[str] = mapped_column(String(60))
    params: Mapped[Any] = mapped_column(JSON, default=dict)
    filters: Mapped[Any] = mapped_column(JSON, default=dict)
    risk: Mapped[Any] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="nova")  # nova | aprovada | reprovada | observacao
    score: Mapped[float] = mapped_column(Float, default=0.0)
    metrics: Mapped[Any] = mapped_column(JSON, default=dict)
    is_metrics: Mapped[Any] = mapped_column(JSON, default=dict)
    oos_metrics: Mapped[Any] = mapped_column(JSON, default=dict)
    hour_stats: Mapped[Any] = mapped_column(JSON, default=dict)
    live: Mapped[Any] = mapped_column(JSON, default=dict)
    data_source: Mapped[str] = mapped_column(String(20), default="")
    tested_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    evolved_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(20))  # ranking | evolucao | manual
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    timeframe: Mapped[str] = mapped_column(String(5))
    strategy: Mapped[str] = mapped_column(String(60))
    params: Mapped[Any] = mapped_column(JSON, default=dict)
    filters: Mapped[Any] = mapped_column(JSON, default=dict)
    risk: Mapped[Any] = mapped_column(JSON, default=dict)
    bars: Mapped[int] = mapped_column(Integer, default=0)
    start_ts: Mapped[int] = mapped_column(Integer, default=0)
    end_ts: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[Any] = mapped_column(JSON, default=dict)
    is_metrics: Mapped[Any] = mapped_column(JSON, default=dict)
    oos_metrics: Mapped[Any] = mapped_column(JSON, default=dict)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    data_source: Mapped[str] = mapped_column(String(20), default="")


# -------------------------------------------------------- decisões/ordens
class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    plan: Mapped[Any] = mapped_column(JSON, default=list)
    candidates: Mapped[Any] = mapped_column(JSON, default=list)
    rationale: Mapped[str] = mapped_column(Text, default="")
    ai: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str] = mapped_column(String(60), default="")
    context: Mapped[Any] = mapped_column(JSON, default=dict)


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    timeframe: Mapped[str] = mapped_column(String(5))
    strategy: Mapped[str] = mapped_column(String(60))
    direction: Mapped[str] = mapped_column(String(5))  # buy | sell
    entry_type: Mapped[str] = mapped_column(String(10), default="market")  # market | stop
    price: Mapped[float] = mapped_column(Float)
    trigger: Mapped[float | None] = mapped_column(Float, nullable=True)
    sl: Mapped[float] = mapped_column(Float)
    tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr: Mapped[float] = mapped_column(Float, default=0.0)
    bar_time: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(15), default="proposto")
    reason: Mapped[str] = mapped_column(Text, default="")
    profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trade_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    context: Mapped[Any] = mapped_column(JSON, default=dict)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(10), index=True)  # paper | live
    terminal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    direction: Mapped[str] = mapped_column(String(5))
    volume: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    entry_time: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    initial_sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_time: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    exit_reason: Mapped[str] = mapped_column(String(40), default="")
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_r: Mapped[float] = mapped_column(Float, default=0.0)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    swap: Mapped[float] = mapped_column(Float, default=0.0)
    risk_money: Mapped[float] = mapped_column(Float, default=0.0)
    strategy: Mapped[str] = mapped_column(String(60), default="")
    timeframe: Mapped[str] = mapped_column(String(5), default="")
    profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    signal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ticket: Mapped[str] = mapped_column(String(40), default="", index=True)
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)
    mgmt: Mapped[Any] = mapped_column(JSON, default=dict)
    context: Mapped[Any] = mapped_column(JSON, default=dict)


class EquitySnapshot(Base):
    __tablename__ = "equity"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    mode: Mapped[str] = mapped_column(String(10), index=True)
    balance: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)

"""Verificação de ponta a ponta com preços reais (precisa de internet).

1. Preços: busca cotação e candles reais (Yahoo Finance e Binance) dos ativos padrão.
2. Escritório: liga os 8 agentes numa conta simulada com preços reais por alguns minutos
   (notícias, calendário, ranking, plano, sinais, risco, ordens), faz a daily e mostra um
   relatório de cada agente. Sai com erro se algo essencial não funcionou.

Uso (na pasta backend):  python scripts/verificar_sistema.py --minutos 4
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp(prefix="metabot-verificacao-")
os.environ.setdefault("MB_DATA_DIR", _TMP)
os.environ.setdefault("MB_DATABASE_URL", f"sqlite:///{_TMP}/verificacao.db")
os.environ.setdefault("MB_SECRET_KEY", "verificacao-local-0123456789abcdef0123456789abcdef")
os.environ["MB_NETWORK_ENABLED"] = "true"
os.environ["MB_AGENTS_ENABLED"] = "true"

from sqlalchemy import func, select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import configure, init_db, session_scope  # noqa: E402
from app.security import SecretBox, set_secret_box  # noqa: E402

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "US500", "BTCUSD", "ETHUSD", "WIN$N", "WDO$N"]
TIMEFRAMES = ["M5", "M15", "H1", "H4", "D1"]

problems: list[str] = []
notes: list[str] = []


def line(text: str = "") -> None:
    print(text, flush=True)


def fmt_age(seconds: float) -> str:
    if seconds < 120:
        return f"{seconds:.0f}s"
    if seconds < 7200:
        return f"{seconds / 60:.0f}min"
    return f"{seconds / 3600:.1f}h"


async def check_prices(office) -> None:
    line("== 1. Preços reais (sem corretora) ==")
    market = office.market
    assert market.source() == "real", f"origem dos preços deveria ser 'real', veio {market.source()}"
    for symbol in SYMBOLS:
        try:
            tick = await market.tick(symbol)
            spec = await market.spec(symbol)
        except Exception as exc:
            problems.append(f"sem cotação de {symbol}: {exc}")
            line(f"  ✗ {symbol:8s} sem cotação: {exc}")
            continue
        mid = (tick["bid"] + tick["ask"]) / 2
        age = time.time() - tick["time"]
        parts = []
        for tf in TIMEFRAMES:
            try:
                bars = await market.rates(symbol, tf, 2000)
                last_age = time.time() - int(bars.time[-1])
                parts.append(f"{tf}:{bars.n}({fmt_age(last_age)})")
                if tf in ("M15", "H1") and bars.n < 300:
                    problems.append(f"{symbol} {tf}: histórico curto ({bars.n} candles)")
            except Exception as exc:
                parts.append(f"{tf}:erro")
                problems.append(f"{symbol} {tf}: {exc}")
        status = "aberto" if tick["open"] else "fechado"
        line(f"  ✓ {symbol:8s} {mid:>14,.{spec['digits']}f}  spread {tick['ask'] - tick['bid']:.{spec['digits']}f}  {status:7s} cotação há {fmt_age(age):6s} {tick.get('source', '')}")
        line(f"             {'  '.join(parts)}")
        if mid <= 0:
            problems.append(f"{symbol}: preço inválido {mid}")


async def run_office(office, minutes: float) -> None:
    from app.runtime import update_config

    line("")
    line(f"== 2. Escritório ligado com preços reais por {minutes:g} min ==")
    update_config({"system_running": True})
    await office.start()
    deadline = time.time() + minutes * 60
    last = 0.0
    while time.time() < deadline:
        await asyncio.sleep(5)
        if time.time() - last >= 30:
            last = time.time()
            states = " ".join(f"{a.id}:{a.state}" for a in office.agents.values())
            line(f"  [{datetime.now(timezone.utc):%H:%M:%S}] {states}")
    line("  fazendo a daily…")
    report = await office.daily.run(force=True)
    if report:
        line(f"  daily: {report['summary']}")
        line(f"  ajustes: {len(report['adjustments'])} · lições: {len(report['lessons'])} · falas: {len(report['transcript'])}")
    else:
        problems.append("a daily não rodou")
    update_config({"system_running": False})
    await asyncio.sleep(1)
    await office.stop()


def report(office) -> None:
    from app.models import Activity, AgentMessage, CalendarEvent, Decision, NewsItem, Signal, StrategyProfile, Trade

    line("")
    line("== 3. Relatório dos agentes ==")
    with session_scope() as s:
        news = s.scalar(select(func.count(NewsItem.id))) or 0
        news_sources = dict(s.execute(select(NewsItem.source, func.count(NewsItem.id)).group_by(NewsItem.source)).all())
        events = s.scalar(select(func.count(CalendarEvent.id))) or 0
        profiles = dict(s.execute(select(StrategyProfile.status, func.count(StrategyProfile.id)).group_by(StrategyProfile.status)).all())
        sources = dict(s.execute(select(StrategyProfile.data_source, func.count(StrategyProfile.id)).group_by(StrategyProfile.data_source)).all())
        decisions = list(s.scalars(select(Decision).order_by(Decision.ts.desc()).limit(1)))
        signals = dict(s.execute(select(Signal.status, func.count(Signal.id)).group_by(Signal.status)).all())
        trades = list(s.scalars(select(Trade).order_by(Trade.id)))
        messages = dict(s.execute(select(AgentMessage.sender, func.count(AgentMessage.id)).group_by(AgentMessage.sender)).all())
        bad = list(s.scalars(select(Activity).where(Activity.level.in_(("warning", "error"))).order_by(Activity.ts)))
        acts = dict(s.execute(select(Activity.agent, func.count(Activity.id)).group_by(Activity.agent)).all())
    infra = office.agent("infra")
    feed = infra.feed or {}
    real = office.market.real
    line(f"  Yahoo: {'sessão de navegador com crumb' if real._crumb else 'sem crumb'} · último erro: {real.last_error or 'nenhum'}")
    line(f"  Tito (TI): preços reais {'ok' if feed.get('ok') else 'FALHANDO'} · {len(feed.get('symbols') or {})} ativos conferidos · erros: {feed.get('errors') or 'nenhum'}")
    line(f"  Nina (notícias): {news} manchetes de {len(news_sources)} fontes {dict(sorted(news_sources.items(), key=lambda kv: -kv[1])[:6])}")
    line(f"  Hugo (calendário): {events} eventos econômicos · horários mapeados: {len(office.agent('schedule')._profiles)} ativos")
    line(f"  Estela (estratégias): perfis {profiles} · testados com {sources}")
    if decisions:
        plan = decisions[0].plan or []
        line(f"  Gustavo (plano): {len(plan)} setup(s): " + "; ".join(f"{p['symbol']} {p['timeframe']} {p['strategy_name']} ({p.get('horizon')})" for p in plan))
        line(f"    motivo: {(decisions[0].rationale or '')[:300]}")
    else:
        line("  Gustavo (plano): nenhuma decisão")
    cands = office.agent("manager").build_candidates()
    free = [c for c in cands if not c["blocked"]]
    blocks = Counter(b.split("(")[0].strip() for c in cands for b in c["blocked"])
    line(f"    candidatos: {len(cands)} · livres: {len(free)} · bloqueios: {dict(blocks)}")
    for c in sorted(cands, key=lambda c: -c["score"])[:4]:
        line(f"    - {c['symbol']} {c['timeframe']} {c['strategy_name']}: pontuação {c['score']} votos {c['votes']} {'BLOQUEADO ' + ', '.join(c['blocked']) if c['blocked'] else 'livre'}")
    risk = office.agent("risk").status()
    line(f"  Rita (risco): patrimônio {risk.get('equity')} · hoje {risk.get('day_pnl')} · limite do dia {risk.get('daily_loss_money')} · posições {risk.get('open_positions')}/{risk.get('max_positions')}")
    line(f"  Sinais: {signals or 'nenhum ainda (dependem de candle fechando no setup)'}")
    for t in trades:
        line(f"  Caio (ordem): {t.direction} {t.volume:g} {t.symbol} a {t.entry_price} stop {t.sl} alvo {t.tp} · {t.status} {t.exit_reason or ''} {t.pnl:+.2f}")
    if not trades:
        line("  Caio (ordens): nenhuma operação nesse período (normal em poucos minutos)")
    line(f"  Conversa da equipe: {sum(messages.values())} mensagens {messages}")
    line(f"  Atividade por agente: {acts}")
    errors_by_agent = {a.id: a.last_error for a in office.agents.values() if a.last_error}
    if errors_by_agent:
        problems.append(f"agentes com erro: {errors_by_agent}")
    for item in bad[-25:]:
        line(f"  ! [{item.level}] {item.agent}: {item.text[:220]}")
    if not feed.get("ok"):
        problems.append("o Tito não confirmou os preços reais")
    if not profiles:
        problems.append("a Estela não testou nenhuma estratégia")
    if sources and set(sources) - {"real"}:
        problems.append(f"estratégias testadas com outra origem: {sources}")
    if news == 0:
        notes.append("nenhuma notícia lida (fontes RSS fora do ar ou bloqueadas)")
    if events == 0:
        notes.append("calendário econômico vazio (Forex Factory fora do ar ou bloqueado)")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutos", type=float, default=4.0)
    args = parser.parse_args()
    settings = get_settings()
    set_secret_box(SecretBox(settings.secret_key))
    configure(settings.resolved_database_url)
    init_db()
    from app.agents.office import Office

    office = Office(settings)
    office.ensure_setup()
    started = time.time()
    await check_prices(office)
    if len([p for p in problems if p.startswith("sem cotação")]) < len(SYMBOLS):
        await run_office(office, args.minutos)
        report(office)
    line("")
    line(f"== Resultado ({time.time() - started:.0f}s) ==")
    for n in notes:
        line(f"  aviso: {n}")
    if problems:
        for p in problems:
            line(f"  ✗ {p}")
        return 1
    line("  ✓ tudo funcionando com preços reais")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

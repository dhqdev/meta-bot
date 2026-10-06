"""Daily das 19h: a equipe se reúne, conta como foi o dia, escreve o relatório e melhora para amanhã.

Como funciona:

1. **Números do dia:** operações, resultado, acerto, estratégias, horários, horizontes (scalper x
   day trade x posição longa), saídas, sinais vetados, notícias, eventos e metas.
2. **Cada agente analisa a sua área** (regras, sem IA) e decide **ajustes para amanhã**, sempre
   dentro de limites seguros: nunca aumenta o risco, só reduz ou recupera aos poucos.
   - Hugo: horas que deram prejuízo hoje ficam evitadas amanhã.
   - Estela: estratégia que perdeu várias vezes hoje vai para revalidação.
   - Gustavo: recalibra a preferência entre scalper, day trade e posição longa.
   - Rita: depois de um dia no limite de perda, começa amanhã com menos risco.
   - Caio: muitas saídas por tempo → revisa a gestão de saída esta noite.
3. **Reunião:** com a IA (modelo fixo da daily), cada agente fala no seu jeito, com os números do
   dia; sem IA, as falas saem das próprias análises. A Aurora registra as lições, que entram no
   prompt dos agentes no dia seguinte.
4. **Relatório** fica salvo (aba Daily) e o Gustavo lembra o foco na manhã seguinte.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import func, select

from app.agents.personas import PERSONAS, persona_prompt, speak
from app.agents.skills import SkillDef, active_lessons, add_lesson, playbook
from app.config import get_settings
from app.core.horizons import HORIZONS, horizon_of, label as horizon_label
from app.core.strategies import REGISTRY
from app.db import session_scope
from app.events import bus, record_activity
from app.kv import kv_get, kv_set
from app.models import Activity, AIUsage, CalendarEvent, DailyReport, NewsItem, Signal, SkillEvent, StrategyProfile, Trade
from app.runtime import get_config
from app.services.llm import to_json

if TYPE_CHECKING:  # pragma: no cover
    from app.agents.office import Office

log = logging.getLogger("metabot.daily")

DAILY_SKILL = SkillDef("aprendizado_daily", "Aprendizado da daily", "Melhora a cada daily: aplica no dia seguinte o que a equipe aprendeu.")
ORDER = ["manager", "infra", "news", "schedule", "strategist", "risk", "cashier", "auditor"]
VALID_LESSON_TARGETS = {"manager", "strategist", "risk", "cashier", "news", "schedule", "all"}


class AIDailyLine(BaseModel):
    agent: str
    text: str


class AIDailyLesson(BaseModel):
    agent: str
    text: str


class AIDaily(BaseModel):
    summary: str
    mood: str
    transcript: list[AIDailyLine]
    lessons: list[AIDailyLesson]
    focus: list[str]


def _br(value: float, digits: int = 2) -> str:
    return f"{value:+.{digits}f}".replace(".", ",")


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def _money(value: float, currency: str) -> str:
    """Valor com o símbolo da moeda, no jeito brasileiro: "R$ 1.234,56"."""
    symbol = {"BRL": "R$", "USD": "US$"}.get(currency, currency)
    text = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'−' if value < 0 else ''}{symbol} {text}".strip()


def _clip(text: str, limit: int) -> str:
    """Corta no fim de uma palavra e marca com reticências (nada de frase cortada no meio)."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:—-")
    return cut + "…"


# chave do kv com o dia em que os ajustes da daily já foram aplicados (uma vez por dia)
APPLIED_KEY = "daily.applied"


class DailyMeeting:
    def __init__(self, office: "Office"):
        self.office = office
        self.running = False

    # ------------------------------------------------------------ agenda
    def tz(self) -> ZoneInfo:
        return ZoneInfo(get_settings().timezone)

    def local_now(self) -> datetime:
        return datetime.now(self.tz())

    def due(self) -> bool:
        cfg = get_config()
        if not cfg.daily_meeting_enabled or self.running:
            return False
        now = self.local_now()
        hh, mm = (int(x) for x in cfg.daily_meeting_time.split(":"))
        if (now.hour, now.minute) < (hh, mm):
            return False
        today = now.date().isoformat()
        if kv_get("daily.last") == today:
            return False
        # só faz sentido com a equipe trabalhando hoje (ou com operações no dia)
        if cfg.system_running or self._trades_today() > 0:
            return True
        # sexta: o escritório fechou com o mercado antes da daily, mas a equipe trabalhou hoje
        brk = kv_get("office.break") or {}
        return brk.get("kind") == "weekend" and str(brk.get("started", "")) >= self._day_start().isoformat()

    def next_at(self) -> str:
        cfg = get_config()
        now = self.local_now()
        hh, mm = (int(x) for x in cfg.daily_meeting_time.split(":"))
        at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if kv_get("daily.last") == now.date().isoformat() or at <= now:
            at += timedelta(days=1)
        return at.isoformat()

    def meeting_passed(self) -> bool:
        """Já passou do horário da daily hoje? Antes disso, a daily feita pelo botão é só uma prévia."""
        hh, mm = (int(x) for x in get_config().daily_meeting_time.split(":"))
        now = self.local_now()
        return (now.hour, now.minute) >= (hh, mm)

    def _day_start(self) -> datetime:
        return self.local_now().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

    def _trades_today(self) -> int:
        with session_scope() as s:
            return int(s.scalar(select(func.count(Trade.id)).where(Trade.status == "closed", Trade.exit_time >= self._day_start())) or 0)

    # ------------------------------------------------------------ dados
    def collect(self) -> dict:
        cfg = get_config()
        tz = self.tz()
        start = self._day_start()
        mode = cfg.mode
        with session_scope() as s:
            trades = [
                {
                    "id": t.id, "symbol": t.symbol, "strategy": t.strategy, "strategy_name": REGISTRY[t.strategy].name if t.strategy in REGISTRY else (t.strategy or "manual"),
                    "timeframe": t.timeframe, "direction": t.direction, "pnl": round(t.pnl, 2), "r": round(t.pnl_r, 2), "exit": t.exit_reason,
                    "profile_id": t.profile_id, "entry_hour_utc": t.entry_time.astimezone(timezone.utc).hour, "entry_hour_local": t.entry_time.astimezone(tz).hour,
                    "minutes": round((t.exit_time - t.entry_time).total_seconds() / 60, 1) if t.exit_time and t.entry_time else None,
                }
                for t in s.scalars(select(Trade).where(Trade.status == "closed", Trade.mode == mode, Trade.exit_time >= start).order_by(Trade.exit_time))
            ]
            open_n = int(s.scalar(select(func.count(Trade.id)).where(Trade.status == "open", Trade.mode == mode)) or 0)
            sigs = list(s.scalars(select(Signal).where(Signal.ts >= start)))
            signals = {"total": len(sigs), "by_status": dict(Counter(x.status for x in sigs))}
            vetoes = Counter()
            for x in sigs:
                if x.status == "vetado" and x.reason:
                    who, _, why = x.reason.partition(":")
                    vetoes[f"{who.strip()}: {why.strip()[:60]}"] += 1
            signals["top_vetoes"] = [{"reason": k, "n": n} for k, n in vetoes.most_common(4)]
            news_n = int(s.scalar(select(func.count(NewsItem.id)).where(NewsItem.fetched_at >= start)) or 0)
            events = [
                {"title": e.title, "currency": e.currency}
                for e in s.scalars(
                    select(CalendarEvent).where(CalendarEvent.ts >= start, CalendarEvent.ts <= datetime.now(timezone.utc), CalendarEvent.impact == "High").order_by(CalendarEvent.ts).limit(6)
                )
            ]
            news_high = int(s.scalar(select(func.count(NewsItem.id)).where(NewsItem.published_at >= start, NewsItem.impact == "high")) or 0)
            errors = int(s.scalar(select(func.count(Activity.id)).where(Activity.ts >= start, Activity.level == "error")) or 0)
            ai_cost = float(s.scalar(select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0)).where(AIUsage.ts >= start)) or 0.0)
            xp_rows = s.execute(select(SkillEvent.agent, func.coalesce(func.sum(SkillEvent.delta_xp), 0)).where(SkillEvent.ts >= start, SkillEvent.kind == "xp").group_by(SkillEvent.agent)).all()
        n = len(trades)
        wins = sum(1 for t in trades if t["pnl"] > 0)
        pnl = round(sum(t["pnl"] for t in trades), 2)
        r_sum = round(sum(t["r"] for t in trades), 2)

        def group(key: str) -> list[dict]:
            acc: dict[str, dict] = defaultdict(lambda: {"n": 0, "wins": 0, "r": 0.0, "pnl": 0.0})
            for t in trades:
                g = acc[str(t[key])]
                g["n"] += 1
                g["wins"] += int(t["pnl"] > 0)
                g["r"] = round(g["r"] + t["r"], 2)
                g["pnl"] = round(g["pnl"] + t["pnl"], 2)
            return sorted(({"key": k, **v} for k, v in acc.items()), key=lambda x: x["r"], reverse=True)

        for t in trades:
            t["horizon"] = horizon_of(t["minutes"])
        risk = self.office.agent("risk").status()
        news_stats = self.office.agent("news").skills.get("sentimento_ativos").get("stats", {})
        prev = self.last_report(before=self.local_now().date().isoformat())
        reviews = kv_get("manager.review_day") or {}
        if reviews.get("day") != self.local_now().date().isoformat():
            reviews = {}
        return {
            "day": self.local_now().date().isoformat(),
            "mode": mode,
            "currency": risk.get("currency") or "",
            "trades": trades,
            "totals": {"n": n, "wins": wins, "losses": n - wins, "win_rate": round(wins / n, 4) if n else None, "pnl": pnl, "r": r_sum, "open": open_n},
            "by_strategy": group("strategy_name"),
            "by_symbol": group("symbol"),
            "by_horizon": group("horizon"),
            "by_hour_local": group("entry_hour_local"),
            "exits": dict(Counter(t["exit"] for t in trades)),
            "signals": signals,
            "risk": {k: risk.get(k) for k in ("day_pnl", "day_pct", "day_stop", "daily_loss_money", "daily_target_money", "adaptive_mult", "kill_switch", "drawdown_pct")},
            "news": {"headlines": news_n, "high_impact": news_high, "accuracy": news_stats.get("accuracy"), "checked": news_stats.get("n", 0)},
            "events": events,
            "infra": {"source": self.office.market.source(), "mt5": (self.office.agent("infra").status or {}).get("connected", False), "errors": errors},
            "ai_cost_usd": round(ai_cost, 4),
            "xp": {a: int(x) for a, x in xp_rows},
            "horizons": self.office.agent("strategist").horizon_summary(days=30),
            "reviews": {k: int(reviews.get(k, 0)) for k in ("close", "sl", "tp", "hold")},
            "review_learning": {**(kv_get("manager.review_stats") or {}), "patience": float(kv_get("manager.review_patience") or 0.0)},
            "previous_focus": (prev or {}).get("focus", []),
            "previous_pnl": (prev or {}).get("pnl"),
        }

    # ------------------------------------------------------------ análise
    def analyze(self, data: dict, apply: bool = True) -> tuple[list[dict], list[dict], list[dict]]:
        """Cada agente olha a sua área. Devolve (seções, ajustes, lições por regra).

        ``apply=False`` (prévia ou daily repetida no mesmo dia): descreve os ajustes sem mexer em nada."""
        cfg = get_config()
        tz = self.tz()
        cur = data["currency"]
        tot = data["totals"]
        trades = data["trades"]
        sections: list[dict] = []
        adjustments: list[dict] = []
        lessons: list[dict] = []
        tomorrow_end = (self.local_now() + timedelta(days=1)).replace(hour=23, minute=59, second=0, microsecond=0).astimezone(timezone.utc).isoformat()

        def section(agent: str, bullets: list[str]) -> None:
            sections.append({"agent": agent, "name": PERSONAS[agent].name, "role": PERSONAS[agent].role, "bullets": [b for b in bullets if b]})

        # Gustavo: visão geral + horizontes
        risk = data["risk"]
        stop = risk.get("day_stop")
        head = f"{tot['n']} operações, {tot['wins']} vencedoras ({_pct(tot['win_rate'])}), resultado {'+' if tot['pnl'] >= 0 else ''}{_money(tot['pnl'], cur)} ({_br(tot['r'])}R)." if tot["n"] else "Dia sem operações: nenhum setup passou nos filtros ou o mercado não deu sinal."
        manager_bullets = [head]
        if stop == "target":
            manager_bullets.append("Batemos a meta do dia e encerramos cedo.")
        elif stop == "loss":
            manager_bullets.append("Paramos no limite de perda do dia.")
        if data["previous_focus"]:
            manager_bullets.append("Foco de hoje era: " + "; ".join(data["previous_focus"][:3]))
        rv = data.get("reviews") or {}
        if sum(rv.values()):
            manager_bullets.append(
                f"Revisões das posições abertas: {rv.get('close', 0)} fechada(s) pela revisão, {rv.get('sl', 0)} stop(s) apertado(s), "
                f"{rv.get('tp', 0)} alvo(s) ajustado(s), {rv.get('hold', 0)} vez(es) mantida(s)."
            )
        rl = data.get("review_learning") or {}
        if rl.get("checked"):
            manager_bullets.append(
                f"Saídas da revisão conferidas: {rl.get('good', 0)} acerto(s), {rl.get('early', 0)} cedo demais em {rl['checked']}; paciência em {rl.get('patience', 0.0):+.2f}.".replace(".", ",", 1)
            )
        horizon_notes = self.office.agent("manager").learn_horizons(data["horizons"], save=apply)
        for note in horizon_notes:
            adjustments.append({"agent": "manager", "kind": "horizonte", "text": f"Preferência de horizonte ajustada — {note}"})
        best_h = max((h for h in data["horizons"]["horizons"] if h["approved"]), key=lambda h: h["bt_oos_expectancy_r"], default=None)
        if best_h:
            manager_bullets.append(f"No backtest, o melhor horizonte agora é {best_h['label']} ({_br(best_h['bt_oos_expectancy_r'])}R por operação fora da amostra).")
        section("manager", manager_bullets)

        # Tito: dados e conexão
        infra = data["infra"]
        source_name = {"mt5": "MT5 (corretora)", "real": "preços reais públicos (Yahoo/Binance)"}.get(infra["source"], "mercado simulado")
        section("infra", [
            f"Dados de {source_name}{' com MT5 conectado' if infra['mt5'] else ''}.",
            f"{infra['errors']} erro(s) registrados hoje." if infra["errors"] else "Nenhum erro técnico hoje.",
            f"IA custou US$ {data['ai_cost_usd']:.3f} hoje." if data["ai_cost_usd"] else "",
        ])

        # Nina: notícias
        nws = data["news"]
        section("news", [
            f"{nws['headlines']} manchetes lidas, {nws['high_impact']} de alto impacto.",
            f"Acerto acumulado das previsões: {_pct(nws['accuracy'])} em {nws['checked']} conferidas." if nws.get("checked") else "Ainda juntando previsões para medir o acerto.",
        ])

        # Hugo: horários e calendário (horas com prejuízo ficam evitadas amanhã)
        avoid = kv_get("team.avoid_hours") or {}
        now_iso = datetime.now(timezone.utc).isoformat()
        avoid = {k: v for k, v in avoid.items() if (v or {}).get("until", "") >= now_iso}
        losses_by: dict[tuple[str, int], list[float]] = defaultdict(list)
        losses_hour: dict[int, list[float]] = defaultdict(list)
        for t in trades:
            if t["pnl"] <= 0:
                losses_by[(t["symbol"], t["entry_hour_utc"])].append(t["r"])
                losses_hour[t["entry_hour_utc"]].append(t["r"])
        hugo = []
        for (symbol, hour), rs in losses_by.items():
            if len(rs) >= 2 and sum(rs) <= -1.0:
                entry = avoid.setdefault(symbol, {"hours_utc": [], "until": tomorrow_end})
                if hour not in entry["hours_utc"]:
                    entry["hours_utc"].append(hour)
                entry["until"] = tomorrow_end
                local_h = datetime.now(timezone.utc).replace(hour=hour, minute=0).astimezone(tz).hour
                adjustments.append({"agent": "schedule", "kind": "horario", "text": f"Evitar {symbol} às {local_h}h amanhã ({len(rs)} perdas hoje nesse horário).", "data": {"symbol": symbol, "hour_utc": hour}})
        for hour, rs in losses_hour.items():
            if len(rs) >= 3 and sum(rs) <= -2.0:
                entry = avoid.setdefault("*", {"hours_utc": [], "until": tomorrow_end})
                if hour not in entry["hours_utc"]:
                    entry["hours_utc"].append(hour)
                entry["until"] = tomorrow_end
                local_h = datetime.now(timezone.utc).replace(hour=hour, minute=0).astimezone(tz).hour
                adjustments.append({"agent": "schedule", "kind": "horario", "text": f"Evitar entradas às {local_h}h amanhã em todos os ativos ({len(rs)} perdas hoje).", "data": {"symbol": "*", "hour_utc": hour}})
        if apply:
            kv_set("team.avoid_hours", avoid)
        by_hour = data["by_hour_local"]
        if by_hour:
            best = by_hour[0]
            hugo.append(f"Melhor horário hoje: {best['key']}h ({_br(best['r'])}R em {best['n']} operação(ões)).")
        hugo.append(f"Eventos fortes hoje: {', '.join(e['title'] + ' (' + e['currency'] + ')' for e in data['events'][:3])}." if data["events"] else "Nenhum evento de alto impacto hoje.")
        section("schedule", hugo)

        # Estela: estratégias (quem perdeu várias vezes vai para revalidação)
        estela = []
        by_profile: dict[int, list[dict]] = defaultdict(list)
        for t in trades:
            if t["profile_id"]:
                by_profile[t["profile_id"]].append(t)
        with session_scope() as s:
            for pid, rows in by_profile.items():
                r_sum = sum(t["r"] for t in rows)
                if len(rows) >= 2 and r_sum <= -1.5:
                    prof = s.get(StrategyProfile, pid)
                    if prof is not None and prof.status == "aprovada":
                        if apply:
                            prof.status = "observacao"
                            # fica fora do plano amanhã; depois disso a Estela revalida com o histórico mais recente
                            prof.live = {**(prof.live or {}), "revalidate_after": tomorrow_end, "flagged_by": "daily"}
                        name = REGISTRY[prof.strategy].name if prof.strategy in REGISTRY else prof.strategy
                        adjustments.append({"agent": "strategist", "kind": "estrategia", "text": f"{name} em {prof.symbol} {prof.timeframe} fica fora do plano amanhã e só volta se passar na revalidação ({len(rows)} operações, {_br(r_sum)}R hoje).", "data": {"profile_id": pid}})
                        lessons.append({"agent": "strategist", "text": f"{name} em {prof.symbol} {prof.timeframe} perdeu {len(rows)} vezes no mesmo dia ({_br(r_sum)}R): revalidar antes de voltar ao plano."})
        if data["by_strategy"]:
            top = data["by_strategy"][0]
            estela.append(f"Melhor estratégia do dia: {top['key']} ({top['wins']}/{top['n']}, {_br(top['r'])}R).")
            if len(data["by_strategy"]) > 1 and data["by_strategy"][-1]["r"] < 0:
                low = data["by_strategy"][-1]
                estela.append(f"Pior: {low['key']} ({low['wins']}/{low['n']}, {_br(low['r'])}R).")
        hz = [h for h in data["horizons"]["horizons"] if h["profiles"]]
        if hz:
            estela.append("Horizontes aprovados no backtest: " + ", ".join(f"{h['label']} {h['approved']}" for h in hz) + ".")
        live_h = [h for h in data["by_horizon"]]
        if live_h:
            estela.append("Hoje por horizonte: " + ", ".join(f"{horizon_label(h['key'])} {_br(h['r'])}R ({h['n']})" for h in live_h) + ".")
        section("strategist", estela or ["Sem operações para avaliar hoje; o ranking segue atualizado."])

        # Rita: metas e risco (nunca aumenta o risco)
        rita = []
        if risk.get("daily_loss_money"):
            pnl_day = float(risk.get("day_pnl") or 0)
            rita.append(f"Limite de perda do dia: {_money(risk['daily_loss_money'], cur)}; resultado {'+' if pnl_day >= 0 else ''}{_money(pnl_day, cur)}.")
        if risk.get("daily_target_money"):
            rita.append(f"Meta de ganho do dia: {_money(risk['daily_target_money'], cur)}.")
        st = self.office.agent("risk").state_kv()
        mult = float(st.get("adaptive_mult", 1.0))
        if stop == "loss":
            new_mult = round(min(mult, 0.75), 4)
            if new_mult < mult and apply:
                st["adaptive_mult"] = new_mult
                self.office.agent("risk").save_state(st)
            adjustments.append({"agent": "risk", "kind": "risco", "text": f"Rita começa amanhã com {new_mult:.0%} do risco normal (dia fechou no limite de perda)."})
            lessons.append({"agent": "risk", "text": "Dia no limite de perda: começar o dia seguinte com risco reduzido e só voltar ao normal depois de ganhos."})
        elif tot["pnl"] > 0 and mult < 1.0:
            new_mult = round(min(1.0, mult + 0.1), 4)
            if apply:
                st["adaptive_mult"] = new_mult
                self.office.agent("risk").save_state(st)
            adjustments.append({"agent": "risk", "kind": "risco", "text": f"Dia positivo: risco volta aos poucos, de {mult:.0%} para {new_mult:.0%} do normal."})
        rita.append(f"Risco adaptativo em {float(self.office.agent('risk').state_kv().get('adaptive_mult', 1.0)):.0%} do normal.")
        if risk.get("kill_switch"):
            rita.append("Trava geral ativa: aguardando o dono liberar.")
        section("risk", rita)

        # Caio: saídas
        exits = data["exits"]
        caio = []
        if exits:
            caio.append("Saídas: " + ", ".join(f"{k} {v}" for k, v in sorted(exits.items(), key=lambda kv: -kv[1])) + ".")
            if tot["n"] >= 3 and exits.get("tempo", 0) / tot["n"] >= 0.4:
                adjustments.append({"agent": "cashier", "kind": "saida", "text": "Muitas saídas por tempo: Caio revisa a gestão de saída esta noite com as operações reais."})
                if apply:
                    self.office.agent("cashier").request("exits_review")
        caio.append(f"{tot['open']} posição(ões) ainda aberta(s)." if tot["open"] else "Nenhuma posição aberta.")
        section("cashier", caio)

        # Aurora: auditoria
        aurora = []
        if tot["n"] >= 3 and data["by_hour_local"]:
            worst = data["by_hour_local"][-1]
            if worst["r"] < 0 and worst["n"] >= 2:
                lessons.append({"agent": "schedule", "text": f"{worst['n']} operações às {worst['key']}h (horário de Brasília) deram {_br(worst['r'])}R hoje: observar esse horário."})
        vet = data["signals"]["top_vetoes"]
        if vet:
            aurora.append("Vetos mais comuns: " + "; ".join(f"{v['reason']} ({v['n']})" for v in vet[:2]) + ".")
        xp_total = sum(data["xp"].values())
        aurora.append(f"A equipe ganhou {xp_total} XP hoje." if xp_total else "Dia calmo nas skills.")
        section("auditor", aurora)

        return sections, adjustments, lessons

    # --------------------------------------------------------- reunião
    def deterministic(self, data: dict, sections: list[dict], adjustments: list[dict], preview: bool = False) -> AIDaily:
        tot = data["totals"]
        cur = data["currency"]
        by_agent = {s["agent"]: s["bullets"] for s in sections}
        opening = "Prévia da daily, time! " if preview else f"Daily das {get_config().daily_meeting_time}, time! "
        lines = [AIDailyLine(agent="manager", text=opening + (by_agent["manager"][0] if by_agent.get("manager") else ""))]
        for agent in ORDER[1:]:
            bullets = by_agent.get(agent) or []
            if bullets:
                lines.append(AIDailyLine(agent=agent, text=_clip(bullets[0], 220)))
        focus = [_clip(a["text"], 140) for a in adjustments[:3]] or ["Manter a disciplina: stop sempre no lugar e só setups aprovados."]
        lines.append(AIDailyLine(agent="manager", text=_clip(("Até a daily oficial, foco em: " if preview else "Foco de amanhã: ") + "; ".join(focus), 420)))
        if tot["n"]:
            summary = f"Dia com {tot['n']} operações ({tot['wins']} vencedoras), resultado {'+' if tot['pnl'] >= 0 else ''}{_money(tot['pnl'], cur)} ({_br(tot['r'])}R)."
        else:
            summary = "Dia sem operações: a equipe ficou de fora (nenhum setup passou nos filtros ou o mercado não deu sinal)."
        stop = data["risk"].get("day_stop")
        if stop == "target":
            summary += " A meta do dia foi batida e a equipe encerrou cedo."
        elif stop == "loss":
            summary += " O dia fechou no limite de perda."
        if preview:
            summary = f"Prévia (a daily oficial é às {get_config().daily_meeting_time}): " + summary
        mood = "bom" if tot["pnl"] > 0 else "ruim" if tot["pnl"] < 0 else "neutro"
        return AIDaily(summary=summary, mood=mood, transcript=lines, lessons=[], focus=focus)

    async def with_ai(self, data: dict, sections: list[dict], adjustments: list[dict]) -> tuple[AIDaily | None, str, str]:
        llm = self.office.llm
        if not llm.available():
            return None, "", "IA desligada ou sem chave"
        compact = {k: v for k, v in data.items() if k not in ("trades", "horizons")}
        compact["trades"] = [{k: t[k] for k in ("symbol", "strategy_name", "timeframe", "direction", "r", "exit", "entry_hour_local", "horizon")} for t in data["trades"][:40]]
        compact["horizons"] = [{k: h[k] for k in ("label", "approved", "bt_oos_expectancy_r", "live_trades", "live_r")} for h in data["horizons"]["horizons"]]
        lessons = "\n".join(f"- ({l['agent']}) {l['text']}" for l in active_lessons(None, 8)) or "- (nenhuma)"
        system = (
            playbook("daily")
            + "\n\n## Personalidades\n" + persona_prompt(ORDER)
            + "\n\n" + playbook("equipe")
            + "\n\n## Lições que já estão valendo\n" + lessons
        )
        user = (
            f"Números do dia:\n{to_json(compact)}\n\n"
            f"Análise de cada agente:\n{to_json(sections)}\n\n"
            f"Ajustes já decididos para amanhã:\n{to_json(adjustments)}"
        )
        res = await llm.complete_json(agent="auditor", purpose="daily", tier="daily", system=system, user=user, schema_model=AIDaily, effort="medium", max_tokens=6000)
        if not res.ok or res.data is None:
            return None, res.model, res.error
        return res.data, res.model, ""

    async def run(self, force: bool = False) -> dict | None:
        if self.running:
            return None
        self.running = True
        try:
            report = await self._run(force)
        finally:
            self.running = False
        cfg = get_config()
        # depois da daily automática, a equipe descansa e o escritório reabre sozinho
        if not force and cfg.daily_break_minutes > 0 and cfg.system_running:
            self.office.start_break(cfg.daily_break_minutes)
        return report

    async def _run(self, force: bool) -> dict:
        manager = self.office.agent("manager")
        day = self.local_now().date().isoformat()
        # Daily oficial: a automática, ou a do botão depois do horário. Antes do horário o botão faz uma prévia:
        # mostra como está o dia sem mexer em nada e sem tirar a daily das 19h.
        official = not force or self.meeting_passed()
        # os ajustes (horários, estratégias, risco, preferência de horizonte) valem uma vez por dia
        apply = official and kv_get(APPLIED_KEY) != day
        if official:
            kv_set("daily.last", day)
        manager.tell("all", "📣 Time, hora da daily! Todo mundo na sala de reunião." if official else "📣 Time, prévia da daily: como está o nosso dia até agora?", kind="daily")
        record_activity("manager", "Daily começando: a equipe vai para a sala de reunião" if official else "Prévia da daily (a oficial continua no horário)", kind="daily")
        data = self.collect()
        sections, adjustments, rule_lessons = self.analyze(data, apply=apply)
        if official and not apply:
            # daily repetida no mesmo dia: os ajustes já valeram; mostra os que foram decididos, sem aplicar de novo
            previous = self.report_for(day)
            if previous and not (previous.get("metrics") or {}).get("preview"):
                adjustments = previous.get("adjustments") or adjustments
        if not apply:
            rule_lessons = []
        if apply:
            kv_set(APPLIED_KEY, day)
        base = self.deterministic(data, sections, adjustments, preview=not official)
        # a prévia não gasta IA: as falas pela IA ficam para a daily oficial
        ai_data, model, ai_error = await self.with_ai(data, sections, adjustments) if official else (None, "", "")
        result = base
        if ai_data is not None:
            transcript = [line for line in ai_data.transcript if line.agent in PERSONAS and line.text.strip()][:14]
            result = AIDaily(
                summary=ai_data.summary.strip()[:1200] or base.summary,
                mood=ai_data.mood if ai_data.mood in ("bom", "neutro", "ruim") else base.mood,
                transcript=[AIDailyLine(agent=x.agent, text=_clip(x.text.strip(), 420)) for x in transcript] or base.transcript,
                lessons=[x for x in ai_data.lessons if x.agent in VALID_LESSON_TARGETS and x.text.strip()][:4],
                focus=[_clip(f.strip(), 160) for f in ai_data.focus if f.strip()][:3] or base.focus,
            )
        elif ai_error:
            log.info("daily sem IA: %s", ai_error)
        lessons = rule_lessons + ([{"agent": x.agent, "text": x.text.strip()} for x in result.lessons] if apply else [])
        for lesson in lessons:
            add_lesson(lesson["agent"], lesson["text"], {"day": day}, source="daily")
        status = {"target": "meta", "loss": "limite"}.get(data["risk"].get("day_stop") or "", "normal")
        report = {
            "day": day, "mode": data["mode"], "pnl": data["totals"]["pnl"], "trades": data["totals"]["n"], "wins": data["totals"]["wins"],
            "status": status, "mood": result.mood, "summary": result.summary,
            "transcript": [x.model_dump() for x in result.transcript], "sections": sections, "adjustments": adjustments,
            "lessons": lessons, "focus": result.focus,
            "metrics": {k: data[k] for k in ("totals", "by_strategy", "by_symbol", "by_horizon", "by_hour_local", "exits", "signals", "risk", "news", "infra", "ai_cost_usd", "xp", "currency", "events", "reviews")}
            | {"horizons": data["horizons"]["horizons"], "preview": not official},
            "ai": ai_data is not None, "model": model if ai_data is not None else "",
        }
        with session_scope() as s:
            row = s.scalar(select(DailyReport).where(DailyReport.day == day))
            if row is None:
                row = DailyReport(day=day)
                s.add(row)
            for k, v in report.items():
                if k != "day":
                    setattr(row, k, v)
            row.ts = datetime.now(timezone.utc)
            s.flush()
            report["id"] = row.id
        # skills: cada um aprende com a daily (mais XP quando teve ajuste na sua área)
        if apply:  # XP só uma vez por dia (repetir a daily não sobe nível)
            touched = Counter(a["agent"] for a in adjustments)
            for agent_id, agent in self.office.agents.items():
                xp = 5 + (5 if data["totals"]["pnl"] > 0 else 0) + 3 * touched.get(agent_id, 0)
                agent.skills.gain(DAILY_SKILL.key, xp, f"daily de {day}")
        # reunião no escritório (todos na sala) e aviso para as telas
        participants = [a for a in ORDER if a != "manager"]
        self.office.meeting(host="manager", participants=participants, lines=report["transcript"], title="Daily")
        bus.publish({"type": "daily", "day": day, "id": report["id"], "summary": report["summary"], "pnl": report["pnl"], "mood": report["mood"]})
        record_activity("manager", f"📋 Daily de {day}: {report['summary']}", kind="daily", data={"day": day, "adjustments": len(adjustments)})
        if official:
            manager.tell("all", "✅ Daily concluída. Relatório na aba Daily; amanhã a gente aplica o que aprendeu.", kind="daily")
        else:
            manager.tell("all", f"✅ Prévia feita. A daily oficial continua às {get_config().daily_meeting_time}.", kind="daily")
        if apply:
            # o próximo plano já considera as lições e os ajustes (não reaproveita o plano antigo da IA)
            manager._ai_cache = None
            manager.request("decide")
        return report

    # ------------------------------------------------------------ consulta
    @staticmethod
    def report_for(day: str) -> dict | None:
        with session_scope() as s:
            row = s.scalar(select(DailyReport).where(DailyReport.day == day))
            return report_dict(row) if row else None

    @staticmethod
    def last_report(before: str | None = None) -> dict | None:
        with session_scope() as s:
            q = select(DailyReport).order_by(DailyReport.day.desc())
            if before:
                q = q.where(DailyReport.day < before)
            row = s.scalar(q.limit(1))
            return report_dict(row) if row else None


def report_dict(r: DailyReport, full: bool = True) -> dict:
    out = {
        "id": r.id, "day": r.day, "ts": r.ts.isoformat() if r.ts else None, "mode": r.mode, "pnl": r.pnl, "trades": r.trades, "wins": r.wins,
        "status": r.status, "mood": r.mood, "summary": r.summary, "ai": r.ai, "model": r.model,
        "focus": r.focus or [], "adjustments_count": len(r.adjustments or []),
    }
    if full:
        out.update({"transcript": r.transcript or [], "sections": r.sections or [], "adjustments": r.adjustments or [], "lessons": r.lessons or [], "metrics": r.metrics or {}})
    return out


def horizon_names() -> dict:
    return {k: v["label"] for k, v in HORIZONS.items()}


__all__ = ["DailyMeeting", "DAILY_SKILL", "report_dict", "speak"]

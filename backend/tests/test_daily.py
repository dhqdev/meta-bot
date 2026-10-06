"""Daily das 19h: relatório, ajustes para o dia seguinte, lições e reunião."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.db import session_scope
from app.kv import kv_get, kv_set, secret_set
from app.models import AgentMessage, DailyReport, Lesson, Skill, StrategyProfile, Trade
from app.runtime import update_config
from app.services.llm import LLMService
from tests.test_agents import SYMBOL, make_profile


def seed_losses(profile_id: int, n: int = 2, r: float = -1.0) -> None:
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        for _ in range(n):
            s.add(
                Trade(
                    mode="paper", symbol=SYMBOL, direction="buy", volume=0.01, entry_price=60_000, entry_time=now - timedelta(minutes=20),
                    exit_price=59_000, exit_time=now, exit_reason="sl", pnl=-50.0 * abs(r), pnl_r=r, risk_money=50.0,
                    strategy="ifr_reversao", timeframe="M5", profile_id=profile_id, status="closed",
                )
            )


def test_daily_without_ai_writes_report_and_adjusts_tomorrow(office):
    async def run():
        update_config({"system_running": True, "watchlist": [SYMBOL], "daily_meeting_time": "00:00"})
        office.ensure_setup()
        pid = make_profile()
        seed_losses(pid, n=2, r=-1.0)
        report = await office.daily.run(force=True)
        assert report["trades"] == 2 and report["wins"] == 0 and report["pnl"] == -100.0
        assert report["ai"] is False and report["mood"] == "ruim"
        agents_speaking = {line["agent"] for line in report["transcript"]}
        assert {"manager", "strategist", "risk", "cashier", "auditor"} <= agents_speaking
        kinds = {a["kind"] for a in report["adjustments"]}
        assert {"horario", "estrategia"} <= kinds  # Hugo evita o horário; Estela revalida a estratégia
        assert report["metrics"]["by_horizon"][0]["key"] == "scalp"  # 20 minutos em posição
        with session_scope() as s:
            assert s.get(StrategyProfile, pid).status == "observacao"
            assert s.scalar(select(DailyReport).where(DailyReport.day == report["day"])) is not None
            lessons = list(s.scalars(select(Lesson).where(Lesson.source == "daily")))
            assert any(lesson.agent == "strategist" for lesson in lessons)
            xp = s.scalar(select(Skill.xp).where(Skill.agent == "manager", Skill.key == "aprendizado_daily"))
            assert xp and xp >= 5
            daily_msgs = list(s.scalars(select(AgentMessage).where(AgentMessage.kind == "daily")))
            assert len(daily_msgs) >= 2
        avoid = kv_get("team.avoid_hours")
        assert SYMBOL in avoid and avoid[SYMBOL]["hours_utc"]
        assert kv_get("daily.last") == report["day"]
        # o gerente passa a bloquear o horário evitado (quando a hora atual é a evitada)
        update_config({"timeframes": ["H1"]})
        with session_scope() as s:
            s.add(StrategyProfile(
                symbol=SYMBOL, timeframe="H1", strategy="supertrend", params={}, filters={}, risk={"sl_atr": 2.0, "tp_r": 2.0},
                status="aprovada", score=0.7, metrics={"trades": 60, "win_rate": 0.55, "wilson_lb": 0.45, "expectancy_r": 0.2},
                oos_metrics={"trades": 20, "expectancy_r": 0.1}, tested_at=datetime.now(timezone.utc),
            ))
        cands = office.agent("manager").build_candidates()
        assert cands
        blocked = any(any("hora evitada" in b for b in c["blocked"]) for c in cands)
        assert blocked == (datetime.now(timezone.utc).hour in avoid[SYMBOL]["hours_utc"])

    asyncio.run(run())


def test_daily_with_ai_uses_the_fixed_daily_model(office):
    answer = {
        "summary": "Dia difícil no BTCUSD: dois stops seguidos no mesmo horário.",
        "mood": "ruim",
        "transcript": [
            {"agent": "manager", "text": "Daily, time! Dois stops no BTCUSD."},
            {"agent": "strategist", "text": "A IFR no M5 não se sustentou hoje. Vou revalidar."},
            {"agent": "intruso", "text": "fala inválida"},
            {"agent": "auditor", "text": "Todo erro é uma aula: evitar esse horário amanhã."},
        ],
        "lessons": [{"agent": "schedule", "text": "Evitar BTCUSD no horário dos dois stops."}, {"agent": "hacker", "text": "x"}],
        "focus": ["Revalidar a IFR no M5", "Evitar o horário dos stops"],
    }
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json={"model": body["model"], "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}], "usage": {"prompt_tokens": 3000, "completion_tokens": 600, "cost": 0.006}})

    async def run():
        update_config({"system_running": True, "watchlist": [SYMBOL], "daily_meeting_time": "00:00"})
        secret_set("openrouter_api_key", "sk-or-test")
        office.llm = LLMService(transport=httpx.MockTransport(handler))
        office.ensure_setup()
        seed_losses(make_profile(), n=2)
        report = await office.daily.run(force=True)
        assert report["ai"] is True and report["model"] == "anthropic/claude-haiku-4.5"
        assert calls[0]["model"] == "anthropic/claude-haiku-4.5"
        assert "Personalidades" in calls[0]["messages"][0]["content"][0]["text"]
        assert [line["agent"] for line in report["transcript"]] == ["manager", "strategist", "auditor"]
        assert report["focus"] == answer["focus"]
        assert any(lesson["agent"] == "schedule" for lesson in report["lessons"])
        assert all(lesson["agent"] != "hacker" for lesson in report["lessons"])

    asyncio.run(run())


def test_daily_schedule_and_morning_focus(office):
    async def run():
        update_config({"system_running": True, "daily_meeting_time": "00:00"})
        assert office.daily.due() is True
        kv_set("daily.last", office.daily.local_now().date().isoformat())
        assert office.daily.due() is False
        update_config({"daily_meeting_enabled": False})
        kv_set("daily.last", "2000-01-01")
        assert office.daily.due() is False
        # relatório de ontem com foco → o Gerente lembra a equipe pela manhã
        yesterday = (office.daily.local_now() - timedelta(days=1)).date().isoformat()
        with session_scope() as s:
            s.add(DailyReport(day=yesterday, focus=["Evitar XAUUSD às 10h", "Preferir scalper"]))
        office.agent("manager").morning_focus()
        with session_scope() as s:
            texts = [m.text for m in s.scalars(select(AgentMessage).where(AgentMessage.sender == "manager"))]
        assert any("Evitar XAUUSD às 10h" in t for t in texts)
        office.agent("manager").morning_focus()  # só uma vez por dia
        with session_scope() as s:
            assert len(list(s.scalars(select(AgentMessage).where(AgentMessage.sender == "manager")))) == len(texts)

    asyncio.run(run())


def test_daily_api(client_owner):
    client = client_owner
    r = client.get("/api/daily")
    assert r.status_code == 200 and r.json()["reports"] == [] and r.json()["time"] == "19:00"
    r = client.post("/api/daily/run")
    assert r.status_code == 200
    day = r.json()["day"]
    listing = client.get("/api/daily").json()
    assert listing["reports"][0]["day"] == day
    full = client.get(f"/api/daily/{day}").json()
    assert full["transcript"] and "sections" in full
    assert client.get("/api/daily/1999-01-01").status_code == 404


def test_button_before_meeting_time_is_a_preview(office):
    """Antes das 19h o botão faz uma prévia: não aplica ajustes e não tira a daily automática do dia."""
    async def run():
        update_config({"system_running": True, "watchlist": [SYMBOL], "daily_meeting_time": "23:59"})
        if office.daily.meeting_passed():  # roda exatamente às 23:59: nada a provar
            return
        office.ensure_setup()
        pid = make_profile()
        seed_losses(pid, n=2, r=-1.0)
        weights = kv_get("manager.horizon_weights")
        report = await office.daily.run(force=True)
        assert report["metrics"]["preview"] is True and report["summary"].startswith("Prévia")
        assert report["adjustments"]  # mostra o que seria ajustado...
        with session_scope() as s:
            assert s.get(StrategyProfile, pid).status == "aprovada"  # ...sem mexer em nada
            assert not list(s.scalars(select(Lesson).where(Lesson.source == "daily")))
            assert not s.scalar(select(Skill.xp).where(Skill.agent == "manager", Skill.key == "aprendizado_daily"))
        assert kv_get("team.avoid_hours") in (None, {})
        assert kv_get("manager.horizon_weights") == weights
        assert kv_get("daily.last") is None
        assert office.daily.next_at().startswith(office.daily.local_now().date().isoformat())  # a das 23:59 continua hoje

    asyncio.run(run())


def test_repeating_the_daily_does_not_apply_twice(office):
    async def run():
        update_config({"system_running": True, "watchlist": [SYMBOL], "daily_meeting_time": "00:00"})
        office.ensure_setup()
        seed_losses(make_profile(), n=2, r=-1.0)
        first = await office.daily.run(force=True)
        weights = kv_get("manager.horizon_weights")
        with session_scope() as s:
            xp = s.scalar(select(Skill.xp).where(Skill.agent == "manager", Skill.key == "aprendizado_daily"))
            n_lessons = len(list(s.scalars(select(Lesson).where(Lesson.source == "daily"))))
        for _ in range(3):
            again = await office.daily.run(force=True)
        assert kv_get("manager.horizon_weights") == weights
        assert again["adjustments"] == first["adjustments"]
        with session_scope() as s:
            assert s.scalar(select(Skill.xp).where(Skill.agent == "manager", Skill.key == "aprendizado_daily")) == xp
            assert len(list(s.scalars(select(Lesson).where(Lesson.source == "daily")))) == n_lessons

    asyncio.run(run())

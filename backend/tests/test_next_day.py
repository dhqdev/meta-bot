"""Daily → dia seguinte: o que a equipe combina na daily muda mesmo o comportamento amanhã.

E a pausa de 1 hora depois da daily automática (o escritório fecha e reabre sozinho).
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.db import session_scope
from app.kv import kv_get, kv_set, secret_set
from app.models import AgentMessage, DailyReport, StrategyProfile
from app.runtime import get_config, update_config
from app.services.llm import LLMService
from tests.test_agents import SYMBOL, make_profile
from tests.test_daily import seed_losses


def second_profile() -> int:
    with session_scope() as s:
        p = StrategyProfile(
            symbol=SYMBOL, timeframe="H1", strategy="supertrend", params={}, filters={}, risk={"sl_atr": 2.0, "tp_r": 2.0},
            status="aprovada", score=0.65, metrics={"trades": 70, "win_rate": 0.55, "wilson_lb": 0.45, "expectancy_r": 0.2},
            oos_metrics={"trades": 22, "expectancy_r": 0.1}, tested_at=datetime.now(timezone.utc),
        )
        s.add(p)
        s.flush()
        return p.id


def text_of(content) -> str:
    return content if isinstance(content, str) else "".join(part.get("text", "") for part in content)


def fake_eval(approved: bool, recent_r: list[float]):
    """Resultado de backtest controlado (para testar a revalidação sem depender do mercado simulado)."""

    def _evaluate(bars, strategy, cand, costs, rules, rank_by="win_rate", risk_pct=1.0):
        trades = [SimpleNamespace(r=r) for r in recent_r]
        return SimpleNamespace(approved=approved, trades=trades, full={"trades": 60, "win_rate": 0.55}, oos={"trades": 20}, reasons=[], candidate=cand)

    return _evaluate


def test_daily_lessons_apply_on_the_next_day(office, monkeypatch):
    plan_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        plan_calls.append(body)
        answer = {"picks": [], "rationale": "Hoje ficamos de fora."}
        return httpx.Response(200, json={"model": body["model"], "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.001}})

    async def run():
        update_config({"system_running": True, "watchlist": [SYMBOL], "timeframes": ["H1"], "daily_loss_limit": 0.5})
        risk = office.agent("risk")
        strategist = office.agent("strategist")
        manager = office.agent("manager")
        pid = make_profile()
        other = second_profile()
        # ---- dia 1: começa com 10.000, perde 2 vezes na mesma estratégia e bate o limite do dia (0,5%)
        kv_set("paper_reset_at", (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())
        await risk.guard()
        seed_losses(pid, n=2, r=-1.0)
        await risk.guard()
        assert risk.day_stopped() == "loss"
        report = await office.daily.run(force=True)
        kinds = {a["kind"] for a in report["adjustments"]}
        assert {"estrategia", "risco", "horario"} <= kinds
        with session_scope() as s:
            prof = s.get(StrategyProfile, pid)
            assert prof.status == "observacao"
            wait_until = prof.live["revalidate_after"]
        assert wait_until > datetime.now(timezone.utc).isoformat()  # fica fora até amanhã à noite
        assert risk.state_kv()["adaptive_mult"] == 0.75

        # ---- dia 2 (a Rita libera o dia novo)
        st = risk.state_kv()
        st.update({"day_blocked": False, "day_stop": None})
        risk.save_state(st)
        # a revalidação de 30 em 30 min NÃO desfaz o ajuste da daily (antes aprovava de novo na hora)
        await strategist.revalidate_flagged()
        with session_scope() as s:
            assert s.get(StrategyProfile, pid).status == "observacao"
        cands = manager.build_candidates()
        assert pid not in {c["profile_id"] for c in cands} and other in {c["profile_id"] for c in cands}
        # a Rita começa o dia com 75% do risco: lote menor
        tick = await office.market.tick(SYMBOL)
        verdict = await risk.evaluate(SYMBOL, "buy", tick["ask"], tick["ask"] - 1500, 1.0)
        assert verdict.ok and verdict.risk_pct == pytest.approx(get_config().risk_per_trade_pct * 0.75)
        # o relatório vira "ontem": o Gustavo lembra o foco de manhã
        yesterday = (office.daily.local_now() - timedelta(days=1)).date().isoformat()
        with session_scope() as s:
            s.scalar(select(DailyReport).where(DailyReport.day == report["day"])).day = yesterday
        kv_set("daily.focus_told", None)
        manager.morning_focus()
        with session_scope() as s:
            texts = [m.text for m in s.scalars(select(AgentMessage).where(AgentMessage.sender == "manager"))]
        assert any(report["focus"][0][:40] in t for t in texts)
        # as lições da daily (inclusive as da Estela, que não usa IA) chegam no plano da IA do Gustavo
        secret_set("openrouter_api_key", "sk-or-test")
        office.llm = LLMService(transport=httpx.MockTransport(handler))
        plan, _, _ = await manager.ai_plan(cands)
        assert plan == []
        system, user = (text_of(m["content"]) for m in plan_calls[0]["messages"][:2])
        assert "perdeu 2 vezes no mesmo dia" in system
        assert report["focus"][0][:40] in user

        # ---- depois do tempo fora: revalida com o histórico atualizado
        with session_scope() as s:
            prof = s.get(StrategyProfile, pid)
            prof.live = {**prof.live, "revalidate_after": "2000-01-01T00:00:00+00:00", "n": 10, "wins": 3, "sum_r": -4.0}
        monkeypatch.setattr("app.agents.strategist.evaluate", fake_eval(True, [-1.0] * 8 + [1.0] * 2))
        await strategist.revalidate_flagged()
        with session_scope() as s:
            prof = s.get(StrategyProfile, pid)
            # backtest aprova, mas as operações mais recentes estão no negativo: o mercado de agora não combina
            assert prof.status == "reprovada" and prof.live["blocked_until"] > datetime.now(timezone.utc).isoformat()
            assert "revalidate_after" not in prof.live
        # o ranking das 6 h não aprova de volta enquanto estiver bloqueada
        approved_ev = fake_eval(True, [1.0] * 10)(None, None, SimpleNamespace(params={}, filters={}, risk={}), None, None)
        approved_ev.score, approved_ev.ins, approved_ev.hours = 0.7, {}, {}
        bars = await office.market.rates(SYMBOL, "H1", 300)
        strategist._save_ranking(SYMBOL, "H1", bars, [{"key": "ifr_reversao", "eval": approved_ev}], "synthetic")
        with session_scope() as s:
            assert s.get(StrategyProfile, pid).status == "reprovada"
        # segunda chance: revalidação boa → volta ao plano e o resultado real antigo pesa metade
        with session_scope() as s:
            prof = s.get(StrategyProfile, pid)
            prof.status = "observacao"
            prof.live = {**prof.live, "revalidate_after": "2000-01-01T00:00:00+00:00"}
        monkeypatch.setattr("app.agents.strategist.evaluate", fake_eval(True, [1.0, -1.0, 2.0] * 4))
        await strategist.revalidate_flagged()
        with session_scope() as s:
            prof = s.get(StrategyProfile, pid)
            assert prof.status == "aprovada" and prof.live["n"] == 5 and prof.live["sum_r"] == -2.0
            assert "blocked_until" not in prof.live

    asyncio.run(run())


def test_auditor_flag_waits_before_revalidation(office):
    async def run():
        update_config({"system_running": True, "watchlist": [SYMBOL]})
        from app.models import Trade

        pid = make_profile(win_rate=0.7)
        now = datetime.now(timezone.utc)
        for i in range(10):
            with session_scope() as s:
                tr = Trade(
                    mode="paper", symbol=SYMBOL, direction="buy", volume=0.01, entry_price=60_000, entry_time=now, exit_price=59_000,
                    exit_time=now, exit_reason="sl", pnl=-50.0 if i < 8 else 50.0, pnl_r=-1.0 if i < 8 else 1.0, risk_money=50.0,
                    strategy="ifr_reversao", timeframe="H1", profile_id=pid, status="closed",
                )
                s.add(tr)
                s.flush()
                s.expunge(tr)
            office.agent("auditor").on_trade_closed(tr)
        with session_scope() as s:
            prof = s.get(StrategyProfile, pid)
            assert prof.status == "observacao" and prof.live["flagged_by"] == "auditor"
            assert prof.live["revalidate_after"] > (now + timedelta(hours=23)).isoformat()
        await office.agent("strategist").revalidate_flagged()
        with session_scope() as s:
            assert s.get(StrategyProfile, pid).status == "observacao"

    asyncio.run(run())


def test_office_closes_for_an_hour_after_the_daily_and_reopens(office):
    async def run():
        update_config({"system_running": True, "watchlist": [SYMBOL]})
        assert get_config().daily_break_minutes == 60
        await office.daily.run()  # automática (a do horário)
        assert get_config().system_running is False
        info = office.break_info()
        until = datetime.fromisoformat(info["until"])
        assert timedelta(minutes=59) < until - datetime.now(timezone.utc) <= timedelta(minutes=60)
        assert office.office_info["office_break"]["until"] == info["until"]
        with session_scope() as s:
            texts = [m.text for m in s.scalars(select(AgentMessage).where(AgentMessage.sender == "manager"))]
        assert any("voltamos às" in t for t in texts)
        assert office.check_break() is False  # ainda na pausa
        kv_set("office.break", {**info, "until": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()})
        assert office.check_break() is True
        assert get_config().system_running is True and office.break_info() is None
        assert office.office_info["office_break"] == {}
        with session_scope() as s:
            texts = [m.text for m in s.scalars(select(AgentMessage).where(AgentMessage.sender == "manager"))]
        assert any("Fim da pausa" in t for t in texts)
        # daily pelo botão não fecha o escritório; pausa 0 = sem pausa
        await office.daily.run(force=True)
        assert get_config().system_running is True
        update_config({"daily_break_minutes": 0})
        kv_set("daily.last", None)
        await office.daily.run()
        assert get_config().system_running is True and office.break_info() is None

    asyncio.run(run())


def test_manual_switch_cancels_the_break(client_owner):
    client = client_owner
    until = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    kv_set("office.break", {"until": until, "minutes": 60})
    assert client.get("/api/system").json()["office_break"]["until"] == until
    r = client.post("/api/system/running", json={"running": False})
    assert r.status_code == 200
    assert kv_get("office.break") is None
    assert client.get("/api/system").json()["office_break"] is None
    # desligar a pausa no meio dela reabre o escritório
    kv_set("office.break", {"until": until, "minutes": 60})
    assert client.put("/api/settings", json={"daily_break_minutes": 0}).status_code == 200
    assert kv_get("office.break") is None and get_config().system_running is True

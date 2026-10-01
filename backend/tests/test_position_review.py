"""Revisão das posições abertas (Gustavo + Caio): fechar, apertar o stop, mudar o alvo e aprender."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.agents.review import PositionContext, ReviewDecision, counterfactual, decide, learn_patience
from app.db import session_scope
from app.kv import kv_get, kv_set
from app.models import AgentMessage, CalendarEvent, StrategyProfile, Trade
from app.runtime import get_config, update_config
from tests.test_agents import SYMBOL, activate_plan, make_profile, new_signal


def ctx(**kw) -> PositionContext:
    """Compra a 100 com stop em 99 (1R = 1) e alvo em 102; mercado calmo por padrão."""
    base = dict(
        direction=1, entry=100.0, price=100.5, sl=99.0, tp=102.0, risk=1.0, spread=0.01, point=0.01,
        atr_now=0.5, atr_entry=0.5, mfe_r=0.6, trend="flat", adx=18.0, age_frac=0.2, age_text="3 h",
    )
    base.update(kw)
    return PositionContext(**base)


# ------------------------------------------------------------- regras
def test_closes_when_the_reason_to_stay_is_gone():
    event = {"title": "Payroll", "currency": "USD"}
    assert decide(ctx(price=100.6, event=event, event_minutes=25)).action == "close"
    assert decide(ctx(price=100.4, news_against=True)).action == "close"
    assert decide(ctx(price=100.4, strategy_ok=False)).action == "close"
    # chegou a +1,5R, voltou para +0,4R sem tendência a favor → sai com o que sobrou
    give_back = decide(ctx(price=100.4, mfe_r=1.5))
    assert give_back.action == "close" and "devolvendo" in give_back.why
    # parada há muito tempo sem andar
    stale = decide(ctx(price=100.05, mfe_r=0.3, age_frac=0.55, age_text="20 h"))
    assert stale.action == "close" and "20 h" in stale.why
    # passou do dobro do tempo normal da estratégia no backtest, sem andar → fecha antes do tempo máximo
    typical = decide(ctx(price=100.1, mfe_r=0.4, age_frac=0.2, typical_frac=2.5, age_text="9 h"))
    assert typical.action == "close" and "dobro do normal" in typical.why
    assert decide(ctx(price=100.1, mfe_r=0.4, age_frac=0.2, typical_frac=1.2)).action != "close"
    # mais paciência (aprendida) espera mais antes de fechar a parada
    assert decide(ctx(price=100.05, mfe_r=0.3, age_frac=0.55, patience=0.3)).action != "close"


def test_tightens_the_stop_but_never_widens_it():
    # +2R: garante +1R
    d = decide(ctx(price=102.0, tp=104.0, mfe_r=2.0))
    assert d.action == "adjust" and d.sl == pytest.approx(101.0) and d.tp is None
    # perdendo com evento chegando: corta pela metade o risco que falta (stop 99 → 99,5)
    d = decide(ctx(price=100.0, event={"title": "CPI", "currency": "USD"}, event_minutes=40))
    assert d.action == "adjust" and d.sl == pytest.approx(99.5)
    # venda: o stop só desce
    d = decide(ctx(direction=-1, entry=100.0, price=98.0, sl=101.0, tp=96.0, mfe_r=2.0))
    assert d.sl == pytest.approx(99.0)
    # stop já acima do que a regra pediria: não mexe
    d = decide(ctx(price=102.0, sl=101.5, tp=104.0, mfe_r=2.0))
    assert d.sl is None
    # volatilidade caiu bem: stop a 1,5 ATR do preço
    d = decide(ctx(price=100.8, atr_entry=0.5, atr_now=0.2, mfe_r=0.9))
    assert d.sl == pytest.approx(100.5)


def test_moves_the_target_with_the_market():
    # tendência forte perto do alvo: alvo +1R e stop protegendo
    d = decide(ctx(price=101.8, mfe_r=1.8, trend="with", adx=32))
    assert d.action == "adjust" and d.tp == pytest.approx(103.0) and d.sl == pytest.approx(100.8)
    # nunca passa de 4R
    d = decide(ctx(price=103.8, tp=104.0, sl=102.5, mfe_r=3.8, trend="with", adx=40))
    assert d.tp == pytest.approx(104.0) or d.tp is None
    # movimento enfraqueceu numa operação velha: alvo mais perto
    d = decide(ctx(price=100.6, tp=103.0, mfe_r=0.7, trend="against", age_frac=0.7))
    assert d.action == "adjust" and d.tp == pytest.approx(101.1)


def test_holds_when_nothing_changed():
    d = decide(ctx(trend="with", adx=30))
    assert d.action == "hold" and "tendência a favor" in d.why


def test_counterfactual_and_patience():
    # compra a 100, stop 99, alvo 102: depois da saída o preço foi ao alvo → teria feito +2R
    assert counterfactual(1, 100.0, 1.0, 99.0, 102.0, [100.5, 102.2], [100.1, 100.4], [100.4, 102.0]) == pytest.approx(2.0)
    # foi ao stop primeiro → -1R
    assert counterfactual(1, 100.0, 1.0, 99.0, 102.0, [100.2, 101.0], [98.9, 99.5], [99.2, 100.9]) == pytest.approx(-1.0)
    assert learn_patience(0.0, 1.5) == (0.05, "cedo")
    assert learn_patience(0.0, -1.2) == (-0.03, "acertou")
    assert learn_patience(0.29, 2.0)[0] == 0.3  # com limite


# -------------------------------------------------------- com a equipe
@pytest.fixture
def running():
    update_config({"system_running": True, "watchlist": [SYMBOL], "timeframes": ["H1"], "paper_commission_per_lot": 0.0})


async def open_old_trade(office, hours: float = 5) -> int:
    pid = make_profile()
    activate_plan(office, pid)
    trade_id = await office.submit_signal(await new_signal(office, pid))
    assert trade_id is not None
    with session_scope() as s:
        s.get(Trade, trade_id).entry_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    return trade_id


def test_manager_review_adjusts_and_closes_through_the_cashier(office, running, monkeypatch):
    async def run():
        manager = office.agent("manager")
        cashier = office.agent("cashier")
        trade_id = await open_old_trade(office)
        with session_scope() as s:
            tr = s.get(Trade, trade_id)
            entry, sl0, tp0 = tr.entry_price, tr.sl, tr.tp
        # o Caio recusa afrouxar o stop
        ok, why = await cashier.adjust(trade_id, sl=sl0 - 500)
        assert not ok and "apertado" in why
        # revisão real (mercado simulado): sempre registra a decisão na posição
        results = await manager.review_positions()
        assert results and results[0]["trade_id"] == trade_id
        with session_scope() as s:
            mg = s.get(Trade, trade_id).mgmt or {}
            if s.get(Trade, trade_id).status == "open":
                assert mg["last_review"]["action"] in ("hold", "adjust", "sl", "tp", "close")
        if results[0]["action"] == "close":
            # o mercado simulado pediu para fechar: segue o teste com outra posição
            with session_scope() as s:
                assert s.get(Trade, trade_id).exit_reason == "revisao"
            office.agent("manager").plan = []
            trade_id = await open_old_trade(office)
            with session_scope() as s:
                tr = s.get(Trade, trade_id)
                entry, sl0, tp0 = tr.entry_price, tr.sl, tr.tp
            kv_set("manager.review_day", None)
        # ajuste: o Gustavo pede, o Caio aperta o stop e confirma
        new_sl = sl0 + 600
        monkeypatch.setattr("app.agents.manager.review_decide", lambda c: ReviewDecision("adjust", sl=new_sl, reasons=["teste: protege"]))
        manager._last_run.pop("review", None)
        await manager.review_positions()
        with session_scope() as s:
            tr = s.get(Trade, trade_id)
            assert tr.sl == pytest.approx(new_sl) and tr.tp == tp0
            assert tr.mgmt["last_review"]["action"] == "adjust" and len(tr.mgmt["reviews"]) >= 2
        # fechar: posição encerrada com o motivo "revisao" e o que tinha de stop/alvo guardado
        monkeypatch.setattr("app.agents.manager.review_decide", lambda c: ReviewDecision("close", reasons=["teste: evento"]))
        await manager.review_positions()
        with session_scope() as s:
            tr = s.get(Trade, trade_id)
            assert tr.status == "closed" and tr.exit_reason == "revisao"
            assert tr.mgmt["review_close"]["sl"] == pytest.approx(new_sl)
            msgs = list(s.scalars(select(AgentMessage).where(AgentMessage.sender.in_(("manager", "cashier")))))
        texts = [(m.sender, m.recipient, m.text) for m in msgs]
        assert any(snd == "manager" and rcp == "cashier" for snd, rcp, _ in texts)
        assert any(snd == "cashier" and rcp == "manager" and "✅" in t for snd, rcp, t in texts)
        stats = kv_get("manager.review_day")
        assert stats["close"] == 1 and stats["sl"] == 1
        # 13 h depois: o Gustavo confere o que teria acontecido e ajusta a paciência
        with session_scope() as s:
            tr = s.get(Trade, trade_id)
            tr.exit_time = datetime.now(timezone.utc) - timedelta(hours=13)
            tr.entry_time = tr.exit_time - timedelta(hours=5)
        checks = await manager.check_review_outcomes()
        assert len(checks) == 1 and checks[0]["verdict"] in ("cedo", "acertou", "neutro")
        with session_scope() as s:
            assert s.get(Trade, trade_id).mgmt["review_check"]["cf_r"] == checks[0]["cf_r"]
        assert kv_get("manager.review_stats")["checked"] == 1
        assert await manager.check_review_outcomes() == []  # uma vez só

    asyncio.run(run())


def test_review_respects_config_and_new_trades(office, running):
    async def run():
        manager = office.agent("manager")
        # posição recém-aberta: deixa respirar
        pid = make_profile()
        activate_plan(office, pid)
        await office.submit_signal(await new_signal(office, pid))
        assert await manager.review_positions() == []
        # 0 = revisão desligada no ciclo do gerente
        update_config({"position_review_minutes": 0})
        manager._last_run.pop("review", None)
        called = []
        manager.review_positions = lambda: called.append(1)  # type: ignore[method-assign]
        await manager.tick()
        assert called == []
        assert get_config().position_review_minutes == 0

    asyncio.run(run())


def test_event_and_lost_approval_reach_the_review_context(office, running):
    async def run():
        manager = office.agent("manager")
        trade_id = await open_old_trade(office)
        with session_scope() as s:
            tr = s.get(Trade, trade_id)
            s.add(CalendarEvent(uid="ev-btc-usd", ts=datetime.now(timezone.utc) + timedelta(minutes=20), currency="USD", impact="High", title="Payroll"))
            prof = s.get(StrategyProfile, tr.profile_id)
            prof.status = "observacao"
            s.flush()
            s.expunge(tr)
        c = await manager._position_context(tr)
        assert c is not None
        assert c.event and c.event["title"] == "Payroll" and 0 < c.event_minutes <= 20
        assert c.strategy_ok is False
        assert c.risk > 0 and 0 <= c.age_frac and c.trend in ("with", "against", "flat")
        kv_set("manager.review_patience", 0.2)
        assert manager.patience() == 0.2

    asyncio.run(run())

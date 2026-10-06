"""Dinheiro é dinheiro, porcentagem é porcentagem: o que a IA lê e o que a equipe decide sobre a meta do dia."""

import asyncio

from app.runtime import update_config
from tests.test_agents import SYMBOL, running  # noqa: F401  (fixture)
from tests.test_settings_effects import approved


def fake_status(**over) -> dict:
    st = {
        "mode": "paper", "currency": "BRL", "equity": 10004.16, "day_start_equity": 10000.0, "day_pnl": 4.16, "day_pct": 0.042,
        "daily_loss_money": 300.0, "daily_target_money": 400.0, "daily_room_money": 300.0, "target_progress": 0.0104,
        "drawdown_pct": 0.0, "kill_switch": False, "day_blocked": False, "adaptive_mult": 1.0, "open_positions": 0, "max_positions": 3, "exposure": {},
    }
    st.update(over)
    return st


def test_ai_reads_money_and_percent_with_units(office):
    update_config({"daily_profit_target": 4.0, "daily_profit_unit": "percent", "daily_loss_limit": 3.0, "daily_loss_unit": "percent"})
    risk = office.agent("risk")
    risk._status = fake_status()
    text = risk.summary_for_ai()
    assert "Resultado do dia: +R$ 4,16 (+0,04% do patrimônio do início do dia, R$ 10.000,00)" in text
    assert "Meta de ganho do dia: R$ 400,00 (4,00% do patrimônio do início do dia)" in text
    assert "1,04% da meta" in text
    assert "Limite de perda do dia: R$ 300,00 (3,00% do patrimônio do início do dia)" in text
    assert "ABERTO" in text
    update_config({"daily_profit_target": 4.0, "daily_profit_unit": "money"})
    risk._status = fake_status(daily_target_money=4.0, target_progress=1.04)
    assert "Meta de ganho do dia: R$ 4,00 (R$ 4,00 fixos)" in risk.summary_for_ai()


def test_ai_cannot_end_the_day_on_its_own(office, running, monkeypatch):
    """Com o dia aberto, um plano vazio da IA "porque bateu a meta" não vale: segue a pontuação da equipe."""
    approved(SYMBOL)
    update_config({"watchlist": [SYMBOL], "min_hour_quality": 0.0, "ai_enabled": True})
    manager = office.agent("manager")
    office.agent("risk")._status = fake_status()
    monkeypatch.setattr(office.llm, "available", lambda: True)

    calls = []

    async def fake_ai(cands):
        calls.append(len(cands))
        return [], "Meta do dia atingida (4,16% de lucro, superando a meta de 4,0%). Encerrar as operações.", "modelo"

    monkeypatch.setattr(manager, "ai_plan", fake_ai)
    asyncio.run(manager.decide())
    assert calls, "a IA precisa ter sido consultada"
    assert manager.plan, "a equipe precisa continuar vigiando os setups"
    assert "4,16%" not in manager.rationale

    # com o dia encerrado pela Rita, plano vazio é o certo
    office.agent("risk")._status = fake_status(day_blocked=True)
    assert manager._invented_day_stop("Meta do dia batida", manager.build_candidates()) is False

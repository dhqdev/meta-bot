"""Conta simulada em reais: valor do tick, lote, saldo e troca de moeda."""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.broker.market import OFFLINE_USD_RATES
from app.core.risk import position_size, value_per_price_unit
from app.db import session_scope
from app.models import Trade
from app.runtime import update_config

BRL = OFFLINE_USD_RATES["BRL"]


def test_tick_value_and_lot_in_reais(office):
    async def run():
        usd = await office.market.spec("EURUSD")
        update_config({"paper_currency": "BRL"})
        brl = await office.market.spec("EURUSD")
        assert usd["account_currency"] == "USD" and brl["account_currency"] == "BRL"
        assert brl["tick_value"] == round(usd["tick_value"] * BRL, 8)
        # mesmo risco em porcentagem: em reais o lote sai 5,4 vezes menor para o mesmo número de reais
        lot_usd = position_size(usd, 10_000 * BRL, 1.0, 1.1000, 1.0950).volume
        lot_brl = position_size(brl, 10_000 * BRL, 1.0, 1.1000, 1.0950).volume
        assert abs(lot_brl * BRL - lot_usd) <= 0.05 * lot_usd + 0.01
        # 1% de R$ 10.000 = R$ 100 de risco entre a entrada e o stop
        sizing = position_size(brl, 10_000, 1.0, 1.1000, 1.0950)
        assert sizing.ok and sizing.risk_money <= 100.0 * 1.5
        assert abs(0.0050 * value_per_price_unit(brl) * sizing.volume - sizing.risk_money) < 0.01
        acc = await office.paper.account()
        assert acc["currency"] == "BRL" and acc["balance"] == 10_000

    asyncio.run(run())


def test_mt5_values_already_come_in_the_account_currency(office):
    async def run():
        market = office.market
        market.mt5_currency = "BRL"
        assert (await market._in_account_currency({"tick_value": 1.0}, "mt5", "BRL"))["tick_value"] == 1.0
        market.mt5_currency = "USD"  # conta do MT5 em dólar, conta simulada em reais
        assert (await market._in_account_currency({"tick_value": 1.0}, "mt5", "BRL"))["tick_value"] == BRL
        assert (await market._in_account_currency({"tick_value": 1.0}, "real", "BRL"))["tick_value"] == BRL

    asyncio.run(run())


def test_switching_currency_restarts_the_paper_account(office):
    office.sync_paper_currency()  # conta em dólar (padrão dos testes)
    with session_scope() as s:
        s.add(Trade(symbol="EURUSD", timeframe="H1", strategy="x", direction="buy", volume=0.1, entry_price=1.1, exit_price=1.2,
                    entry_time=datetime.now(timezone.utc), exit_time=datetime.now(timezone.utc), status="closed", pnl=500.0, pnl_r=2.0, mode="paper"))
    update_config({"paper_currency": "BRL"})
    assert office.sync_paper_currency() is True
    assert office.sync_paper_currency() is False
    acc = asyncio.run(office.paper.account())
    assert acc["currency"] == "BRL" and acc["balance"] == 10_000  # o lucro em dólar não entra na conta em reais
    with session_scope() as s:
        modes = {t.mode for t in s.scalars(select(Trade))}
    assert modes == {"paper-usd"}

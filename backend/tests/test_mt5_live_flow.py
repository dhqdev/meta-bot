"""Conta MT5 de ponta a ponta: o Caio abre, acompanha e fecha pelo bridge (HTTP de verdade) com um MetaTrader5 falso."""

import asyncio
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models import Signal, Terminal, Trade
from app.runtime import update_config
from app.security import box
from tests import fake_mt5
from tests.test_mt5_bridge import TOKEN, load_bridge

MAGIC = 770077


@pytest.fixture
def live_office(office):
    fake_mt5.reset()
    fake_mt5.initialize()
    fake_mt5.login(123, "certa", "Fake-Demo")
    mod = load_bridge()
    server = mod.serve(mod.Bridge(fake_mt5), "127.0.0.1", 0, TOKEN)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with session_scope() as s:
        s.add(Terminal(name="MT5 teste", bridge_url=f"http://127.0.0.1:{server.server_address[1]}", token_enc=box().encrypt(TOKEN), active=True))
    update_config({"system_running": True, "mode": "live", "data_source": "mt5", "magic_number": MAGIC, "server_utc_offset_hours": 3})
    office.market.mt5_ok = True
    office.market.mt5_currency = "USD"
    yield office
    server.shutdown()


def new_signal(side: str, price: float, sl_dist: float) -> dict:
    d = 1 if side == "buy" else -1
    with session_scope() as s:
        sig = Signal(
            symbol="EURUSD", timeframe="", strategy="ifr_reversao", direction=side, entry_type="market",
            price=price, sl=price - d * sl_dist, tp=price + d * 2 * sl_dist, atr=sl_dist,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        s.add(sig)
        s.flush()
        sid = sig.id
    return {
        "id": sid, "symbol": "EURUSD", "direction": side, "entry_type": "market", "trigger": None, "price": price,
        "sl": price - d * sl_dist, "tp": price + d * 2 * sl_dist, "volume": 0.1, "strategy": "ifr_reversao",
        "timeframe": "", "profile_id": None, "votes": {}, "risk_pct": 0.5, "max_bars": 0,
    }


def test_live_order_goes_through_the_bridge_and_closes_by_stop(live_office):
    cashier = live_office.agent("cashier")

    async def run():
        # sinal visto a 1.0990; a ordem é executada a 1.10012 (ask do terminal): stop e alvo andam junto com o preço
        trade_id = await cashier._open(new_signal("buy", 1.0990, 0.0020))
        assert trade_id is not None
        sent = [o for o in fake_mt5.state.orders_seen if o.get("type_filling") != fake_mt5.ORDER_FILLING_FOK]
        assert sent[-1]["magic"] == MAGIC and sent[-1]["volume"] == 0.1
        with session_scope() as s:
            tr = s.get(Trade, trade_id)
            assert tr.mode == "live" and tr.entry_price == pytest.approx(1.10012)
            assert tr.sl == pytest.approx(1.09812) and tr.tp == pytest.approx(1.10412)
            ticket = int(tr.ticket)
        pos = fake_mt5.state.positions[ticket]
        assert pos.sl == pytest.approx(1.09812) and pos.tp == pytest.approx(1.10412)

        # posição ainda aberta na corretora: nada muda
        await cashier.monitor()
        with session_scope() as s:
            assert s.get(Trade, trade_id).status == "open"

        # a corretora fecha no stop: o Caio percebe e grava o resultado que veio do MT5
        fake_mt5.state.positions.pop(ticket)
        server_now = int(datetime.now(timezone.utc).timestamp()) + 3 * 3600
        fake_mt5.state.deals.append(fake_mt5.Deal(99, 0, server_now, server_now * 1000, 1, 1, MAGIC, ticket, 4, 0.1, 1.09812, -3.5, -0.4, -20.0, 0.0, "EURUSD", "sl"))
        await cashier.monitor()
        with session_scope() as s:
            tr = s.get(Trade, trade_id)
            assert tr.status == "closed" and tr.exit_reason == "sl"
            assert tr.exit_price == pytest.approx(1.09812)
            assert tr.pnl == pytest.approx(-20.0 - 3.5 - 3.5 - 0.4)
            assert abs((tr.exit_time.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()) < 60

    asyncio.run(run())


def test_live_close_by_the_team_goes_through_the_bridge(live_office):
    cashier = live_office.agent("cashier")

    async def run():
        trade_id = await cashier._open(new_signal("sell", 1.1000, 0.0020))
        with session_scope() as s:
            tr = s.get(Trade, trade_id)
            s.expunge(tr)
        assert int(tr.ticket) in fake_mt5.state.positions
        assert await cashier._close(tr, "revisao")
        assert int(tr.ticket) not in fake_mt5.state.positions
        with session_scope() as s:
            tr = s.get(Trade, trade_id)
            assert tr.status == "closed" and tr.exit_reason == "revisao" and tr.exit_price == pytest.approx(1.10012)

    asyncio.run(run())


def test_refused_order_is_not_recorded(live_office):
    cashier = live_office.agent("cashier")
    fake_mt5.state.reject_fok = True

    async def run():
        info = new_signal("buy", 1.0990, 0.0020)
        info["symbol"] = "NAOEXISTE"
        assert await cashier._open(info) is None
        with session_scope() as s:
            assert s.scalar(select(Trade.id)) is None
            assert s.get(Signal, info["id"]).status == "falhou"

    asyncio.run(run())


@pytest.mark.parametrize("refuse", [False, True])
def test_slippage_moves_stop_and_target_with_the_fill(live_office, refuse):
    cashier = live_office.agent("cashier")
    fake_mt5.state.slippage = 0.0003
    fake_mt5.state.reject_sltp = refuse

    async def run():
        trade_id = await cashier._open(new_signal("buy", 1.10012, 0.0020))
        with session_scope() as s:
            tr = s.get(Trade, trade_id)
            entry, sl, tp, ticket = tr.entry_price, tr.sl, tr.tp, int(tr.ticket)
        pos = fake_mt5.state.positions[ticket]
        assert entry == pytest.approx(1.10042)
        if refuse:  # corretora recusou o ajuste: o registro fica igual ao que está na corretora
            assert sl == pytest.approx(1.09812) and tp == pytest.approx(1.10412)
        else:  # a mesma distância de stop e alvo, agora a partir do preço executado
            assert sl == pytest.approx(1.09842) and tp == pytest.approx(1.10442)
        assert pos.sl == pytest.approx(sl) and pos.tp == pytest.approx(tp)

    asyncio.run(run())

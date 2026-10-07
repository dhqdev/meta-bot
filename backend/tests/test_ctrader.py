"""Conta da cTrader (Open API) de ponta a ponta contra um servidor cTrader falso: conexão, cotações, ordens e tela."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.broker.ctrader import CTraderClient
from app.config import get_settings
from app.db import session_scope
from app.models import Signal, Terminal, Trade
from app.runtime import update_config
from app.security import box
from tests import fake_ctrader
from tests.fake_ctrader import ACCOUNT_ID, CLIENT_ID, CLIENT_SECRET, FakeCTrader

MAGIC = 770077


@asynccontextmanager
async def fake_server():
    async with FakeCTrader() as fake:
        get_settings().ctrader_endpoint = fake.endpoint
        try:
            yield fake
        finally:
            get_settings().ctrader_endpoint = ""


def client_for(fake, **kw) -> CTraderClient:
    host, port = fake.endpoint.split(":")
    args = dict(environment="demo", client_id=CLIENT_ID, client_secret=CLIENT_SECRET, access_token="token-1", refresh_token="refresh-1", account_id=ACCOUNT_ID, host=host, port=int(port), use_ssl=False, timeout=5)
    args.update(kw)
    return CTraderClient(**args)


def test_client_account_quotes_and_candles():
    async def run():
        async with fake_server() as fake:
            client = client_for(fake)
            try:
                health = await client.health()
                assert health["connected"] and health["account"]["currency"] == "USD"
                assert health["account"]["balance"] == 10000.0 and health["account"]["leverage"] == 500
                assert health["server_time_hint"]["tick_time"] == health["server_time_hint"]["utc_now"]  # UTC: fuso 0
                tick = await client.tick("EURUSD")
                assert tick["bid"] == pytest.approx(1.1) and tick["ask"] == pytest.approx(1.10012)
                eur = await client.symbol("EURUSD")
                assert eur["digits"] == 5 and eur["trade_contract_size"] == 100000
                assert eur["trade_tick_value"] == pytest.approx(1.0)  # 1 ponto num lote = 1 dólar
                assert eur["volume_min"] == pytest.approx(0.01) and eur["volume_step"] == pytest.approx(0.01)
                assert eur["spread"] == 12
                jpy = await client.symbol("USDJPY")
                assert jpy["trade_tick_value"] == pytest.approx(100 / 150.006, rel=1e-4)  # iene convertido para dólar
                rates = await client.rates("EURUSD", "H1", 50)
                assert len(rates["rows"]) == 50
                t, o, h, low, c = rates["rows"][-1][:5]
                assert low <= o <= h and low <= c <= h and o == pytest.approx(low + 0.00003)
                assert [r[0] for r in rates["rows"]] == sorted(r[0] for r in rates["rows"])
                assert all(r[6] == 12 for r in rates["rows"])  # spread de agora em pontos (custo no backtest)
                with pytest.raises(Exception):
                    await client.symbol("NAOEXISTE")
            finally:
                await client.aclose()

    asyncio.run(run())


def test_expired_token_is_renewed_and_saved():
    saved = []

    async def run():
        async with fake_server() as fake:
            fake.expired = True
            client = client_for(fake, on_tokens=lambda a, r: saved.append((a, r)))
            try:
                assert (await client.health())["connected"]
            finally:
                await client.aclose()

    asyncio.run(run())
    assert saved == [("token-2", "refresh-2")]


@pytest.fixture
def ctrader_office(office):
    creds = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "access_token": "token-1", "refresh_token": "refresh-1"}
    with session_scope() as s:
        s.add(Terminal(name="cTrader demo", bridge_url="ctrader://demo", token_enc=box().encrypt(json.dumps(creds)), login=str(ACCOUNT_ID), active=True))
    update_config({"system_running": True, "mode": "live", "data_source": "mt5", "magic_number": MAGIC, "server_utc_offset_hours": 0})
    office.market.mt5_ok = True
    office.market.mt5_currency = "USD"
    return office


def new_signal(side: str, price: float, sl_dist: float) -> dict:
    d = 1 if side == "buy" else -1
    with session_scope() as s:
        sig = Signal(symbol="EURUSD", timeframe="", strategy="ifr_reversao", direction=side, entry_type="market", price=price, sl=price - d * sl_dist, tp=price + d * 2 * sl_dist, atr=sl_dist, expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        s.add(sig)
        s.flush()
        sid = sig.id
    return {"id": sid, "symbol": "EURUSD", "direction": side, "entry_type": "market", "trigger": None, "price": price, "sl": price - d * sl_dist, "tp": price + d * 2 * sl_dist, "volume": 0.1, "strategy": "ifr_reversao", "timeframe": "", "profile_id": None, "votes": {}, "risk_pct": 0.5, "max_bars": 0}


def test_team_trades_a_ctrader_account(ctrader_office):
    office = ctrader_office
    cashier = office.agent("cashier")

    async def run():
        async with fake_server() as fake:
            # o Tito confere a conexão como faria com o MT5
            await office.agent("infra").check_mt5()
            assert office.agent("infra").status["connected"] and office.market.offset_hours == 0
            trade_id = await cashier._open(new_signal("buy", 1.0990, 0.0020))
            assert trade_id is not None
            order = [m for t, m in fake.requests if type(m).__name__ == "ProtoOANewOrderReq"][-1]
            assert order.volume == 1_000_000 and order.label == f"MB:{MAGIC}"
            assert order.relativeStopLoss == 200 and order.relativeTakeProfit == 400  # 20 e 40 pips
            with session_scope() as s:
                tr = s.get(Trade, trade_id)
                assert tr.mode == "live" and tr.entry_price == pytest.approx(1.10012)
                assert tr.sl == pytest.approx(1.09812) and tr.tp == pytest.approx(1.10412)
                ticket = int(tr.ticket)
            assert fake.positions[ticket].stopLoss == pytest.approx(1.09812)
            assert await office.live.open_tickets() == {str(ticket)}

            await cashier.monitor()
            with session_scope() as s:
                assert s.get(Trade, trade_id).status == "open"

            # a corretora fecha no stop: o Caio percebe e grava o resultado da cTrader
            fake.close_by_broker(ticket, 1.09812)
            await cashier.monitor()
            with session_scope() as s:
                tr = s.get(Trade, trade_id)
                assert tr.status == "closed" and tr.exit_reason == "sl"
                assert tr.exit_price == pytest.approx(1.09812)
                assert tr.pnl == pytest.approx(-20.0 - 7.0 - 0.4)

            # venda fechada pela equipe
            trade_id = await cashier._open(new_signal("sell", 1.1000, 0.0020))
            with session_scope() as s:
                tr = s.get(Trade, trade_id)
                s.expunge(tr)
            assert await cashier._close(tr, "revisao")
            assert int(tr.ticket) not in fake.positions
            with session_scope() as s:
                tr = s.get(Trade, trade_id)
                assert tr.status == "closed" and tr.exit_reason == "revisao" and tr.exit_price == pytest.approx(1.10012)

    asyncio.run(run())


def test_demo_connected_before_is_adopted_once(ctrader_office):
    """Demo conectada com a equipe no simulado: o Tito passa a operar nela; se o dono voltar ao simulado, fica."""
    from app.api.system import apply_mode
    from app.runtime import get_config

    office = ctrader_office
    update_config({"mode": "paper", "data_source": "auto"})

    async def run():
        async with fake_server():
            infra = office.agent("infra")
            await infra.check_mt5()
            assert get_config().mode == "live" and get_config().data_source == "mt5"
            assert (await office.broker.account())["balance"] == 10000
            apply_mode(office, "paper")
            await infra.check_mt5()
            assert get_config().mode == "paper"
            await office.terminals.client().aclose()

    asyncio.run(run())


@pytest.mark.parametrize("refuse", [False, True])
def test_slippage_keeps_stop_where_the_broker_has_it(ctrader_office, refuse):
    cashier = ctrader_office.agent("cashier")

    async def run():
        async with fake_server() as fake:
            fake.slippage = 0.0003
            fake.reject_amend = refuse
            trade_id = await cashier._open(new_signal("buy", 1.10012, 0.0020))
            with session_scope() as s:
                tr = s.get(Trade, trade_id)
                entry, sl, tp, ticket = tr.entry_price, tr.sl, tr.tp, int(tr.ticket)
            pos = fake.positions[ticket]
            assert entry == pytest.approx(1.10042)
            # a cTrader já põe stop e alvo a partir do preço executado: nada para ajustar, aceite ou não
            assert sl == pytest.approx(1.09842) and tp == pytest.approx(1.10442)
            assert pos.stopLoss == pytest.approx(sl) and pos.takeProfit == pytest.approx(tp)

    asyncio.run(run())


def test_refused_order_is_not_recorded(ctrader_office):
    cashier = ctrader_office.agent("cashier")

    async def run():
        async with fake_server():
            info = new_signal("buy", 1.0990, 0.0020)
            info["symbol"] = "NAOEXISTE"
            assert await cashier._open(info) is None
            with session_scope() as s:
                assert s.scalar(select(Trade.id)) is None
                assert s.get(Signal, info["id"]).status == "falhou"

    asyncio.run(run())


def test_connect_ctrader_account_from_settings(client_owner, monkeypatch):
    from app.api import ctrader as ctrader_api

    password = "SenhaForte123"
    # sem senha não começa
    assert client_owner.post("/api/ctrader/start", json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "password": "errada"}).status_code == 403
    # entrada pelo login do cTrader ID: devolve o endereço de autorização e o de retorno
    res = client_owner.post("/api/ctrader/start", json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "password": password})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["url"].startswith("https://id.ctrader.com/") and "scope=trading" in body["url"]
    assert body["redirect_uri"].endswith("/api/ctrader/callback")
    state = body["url"].split("state=")[1].split("&")[0]

    async def fake_exchange(code, uri, cid, secret):
        assert (code, cid, secret) == ("codigo", CLIENT_ID, CLIENT_SECRET)
        return {"accessToken": "token-1", "refreshToken": "refresh-1"}

    monkeypatch.setattr(ctrader_api, "exchange_code", fake_exchange)
    bad = client_owner.get("/api/ctrader/callback", params={"code": "codigo", "state": "outro"}, follow_redirects=False)
    assert bad.status_code == 302 and "expirado" in bad.headers["location"]
    ok = client_owner.get("/api/ctrader/callback", params={"code": "codigo", "state": state}, follow_redirects=False)
    assert ok.status_code == 302 and ok.headers["location"] == "/config?ctrader=contas"
    assert client_owner.get("/api/ctrader/status").json()["authorized"]

    async def serve():
        async with FakeCTrader() as fake:
            get_settings().ctrader_endpoint = fake.endpoint
            await asyncio.Event().wait()

    import threading

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_until_complete, args=(serve(),), daemon=True)
    thread.start()
    for _ in range(100):
        if get_settings().ctrader_endpoint:
            break
        import time

        time.sleep(0.05)
    try:
        accounts = client_owner.get("/api/ctrader/accounts")
        assert accounts.status_code == 200, accounts.text
        acc = accounts.json()[0]
        assert acc == {"account_id": ACCOUNT_ID, "login": 777001, "live": False, "broker": "Pepperstone", "can_trade": True}
        assert client_owner.get("/api/system").json()["mode"] == "paper"
        res = client_owner.post("/api/ctrader/connect", json={**acc, "password": password})
        assert res.status_code == 200, res.text
        # testada na hora: o saldo da demo volta na resposta e a equipe passa a operar nela
        body = res.json()
        assert body["connected"] and body["account"]["balance"] == 10000 and body["account"]["currency"] == "USD"
        assert body["mode"] == "live"
        system = client_owner.get("/api/system").json()
        assert system["mode"] == "live" and system["account"]["balance"] == 10000 and system["data_source"] == "mt5"
        terms = client_owner.get("/api/settings/terminals").json()
        assert terms[-1]["kind"] == "ctrader" and terms[-1]["active"] and terms[-1]["bridge_url"] == "ctrader://demo"
        assert "token" not in json.dumps(terms).replace("token_set", "")
        test = client_owner.post("/api/settings/terminals/test", json={"bridge_url": "ctrader://demo", "terminal_id": terms[-1]["id"]})
        assert test.json()["ok"], test.text
        assert "777001" in test.json()["message"]
        assert not client_owner.get("/api/ctrader/status").json()["authorized"]  # tokens saíram do rascunho e foram para o terminal
        # conta real conectada com a equipe na corretora: volta ao simulado até o dono escolher em Modo de operação
        client_owner.post("/api/ctrader/start", json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "access_token": "token-1", "refresh_token": "refresh-1", "password": password})
        res = client_owner.post("/api/ctrader/connect", json={**acc, "live": True, "password": password})
        assert res.status_code == 200 and res.json()["mode"] == "paper"
    finally:
        get_settings().ctrader_endpoint = ""
        loop.call_soon_threadsafe(loop.stop)


def test_symbols_names_with_slash_match():
    assert fake_ctrader.SYMBOLS  # nomes como "EUR/USD" também casam com "EURUSD"
    from app.broker.ctrader import norm

    assert norm("EUR/USD") == norm("eurusd") == "EURUSD"

"""Bridge do MT5 (roda no Wine) testado com um MetaTrader5 falso + o cliente do backend contra ele."""

import asyncio
import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests import fake_mt5

BRIDGE_PATH = Path(__file__).resolve().parents[2] / "mt5" / "Metatrader" / "bridge" / "metabot_bridge.py"
TOKEN = "t" * 32


def load_bridge():
    spec = importlib.util.spec_from_file_location("metabot_bridge", BRIDGE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["metabot_bridge"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def bridge_server():
    fake_mt5.reset()
    mod = load_bridge()
    bridge = mod.Bridge(fake_mt5)
    server = mod.serve(bridge, "127.0.0.1", 0, TOKEN)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def call(url, path, body=None, token=TOKEN):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url + path, data=data, method="POST" if body is not None else "GET")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_requires_token(bridge_server):
    assert call(bridge_server, "/ping", token=None)[0] == 200
    status, body = call(bridge_server, "/health", token=None)
    assert status == 401
    status, _ = call(bridge_server, "/health", token="errado" * 6)
    assert status == 401


def test_health_login_and_rates(bridge_server):
    status, health = call(bridge_server, "/health")
    assert status == 200 and health["initialized"] and health["account"] is None
    status, body = call(bridge_server, "/login", {"login": 123, "password": "errada", "server": "Fake-Demo"})
    assert status == 502
    status, body = call(bridge_server, "/login", {"login": 123, "password": "certa", "server": "Fake-Demo"})
    assert status == 200 and body["account"]["login"] == 123
    status, rates = call(bridge_server, "/rates?symbol=EURUSD&timeframe=H1&count=50")
    assert status == 200 and len(rates["rows"]) == 50 and rates["fields"][0] == "time"
    status, _ = call(bridge_server, "/rates?symbol=EURUSD&timeframe=X9&count=50")
    assert status == 400
    status, _ = call(bridge_server, "/symbol?name=NAOEXISTE")
    assert status == 404


def test_order_retries_filling_and_manages_position(bridge_server):
    call(bridge_server, "/login", {"login": 123, "password": "certa", "server": "Fake-Demo"})
    status, res = call(bridge_server, "/order/send", {"action": "deal", "symbol": "EURUSD", "side": "buy", "volume": 0.1, "sl": 1.09, "tp": 1.12, "magic": 7})
    assert status == 200 and res["ok"]
    fillings = [o.get("type_filling") for o in fake_mt5.state.orders_seen]
    assert fillings[:2] == [fake_mt5.ORDER_FILLING_FOK, fake_mt5.ORDER_FILLING_IOC]  # FOK recusado → IOC
    ticket = res["order"]
    status, positions = call(bridge_server, "/positions?magic=7")
    assert len(positions) == 1 and positions[0]["sl"] == pytest.approx(1.09)
    status, res = call(bridge_server, "/position/modify", {"ticket": ticket, "sl": 1.095, "tp": 1.12})
    assert res["ok"]
    status, res = call(bridge_server, "/position/close", {"ticket": ticket})
    assert res["ok"]
    status, deals = call(bridge_server, f"/history/deals?position={ticket}")
    assert any(d["entry"] == 1 for d in deals)
    status, res = call(bridge_server, "/order/send", {"action": "deal", "symbol": "EURUSD", "side": "comprar", "volume": 0.1})
    assert status == 400


def test_backend_client_and_market_against_bridge(bridge_server):
    from app.broker.market import normalize_spec, rows_to_frame
    from app.broker.mt5 import MT5Client, MT5Error

    async def run():
        client = MT5Client(bridge_server, TOKEN)
        try:
            await client.login(123, "certa", "Fake-Demo")
            acc = await client.account()
            assert acc["login"] == 123
            info = await client.symbol("EURUSD")
            spec = normalize_spec(info)
            assert spec["tick_size"] == pytest.approx(1e-5) and spec["volume_step"] == pytest.approx(0.01)
            data = await client.rates("EURUSD", "H1", 30)
            df = rows_to_frame(data["rows"], spec["point"], offset_hours=3)
            assert len(df) == 30 and df["time"].iloc[0] == 1_700_000_000 - 3 * 3600
            assert df["spread"].iloc[0] == pytest.approx(12 * 1e-5)
            res = await client.order_send({"action": "deal", "symbol": "EURUSD", "side": "sell", "volume": 0.2, "magic": 770077})
            assert res["ok"]
            positions = await client.positions(770077)
            assert positions and positions[0]["type"] == 1
            bad = MT5Client(bridge_server, "x" * 32)
            with pytest.raises(MT5Error):
                await bad.account()
            await bad.aclose()
        finally:
            await client.aclose()

    asyncio.run(run())

import pyotp
import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.kv import kv_get, kv_set
from app.runtime import TRADING_PAIRS, reset_cache

ORIGIN = {"Origin": "http://testserver"}
PASSWORD = "SenhaForte123"


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        c.headers.update(ORIGIN)
        yield c


def setup_owner(c):
    code = auth_api.setup_code()
    r = c.post("/api/auth/setup", json={"email": "dono@example.com", "password": PASSWORD, "setup_code": code})
    assert r.status_code == 200, r.text
    return r


def test_setup_requires_code_and_only_once(client):
    assert client.get("/api/auth/status").json()["needs_setup"] is True
    r = client.post("/api/auth/setup", json={"email": "x@example.com", "password": PASSWORD, "setup_code": "ERRADO"})
    assert r.status_code == 403
    setup_owner(client)
    st = client.get("/api/auth/status").json()
    assert st["logged_in"] and not st["needs_setup"]
    r = client.post("/api/auth/setup", json={"email": "y@example.com", "password": PASSWORD, "setup_code": "QUALQUER"})
    assert r.status_code == 409


def test_login_logout_and_protected_routes(client):
    setup_owner(client)
    client.post("/api/auth/logout")
    assert client.get("/api/agents").status_code == 401
    assert client.get("/api/auth/forward").status_code == 401
    r = client.post("/api/auth/login", json={"email": "dono@example.com", "password": "errada"})
    assert r.status_code == 401
    r = client.post("/api/auth/login", json={"email": "DONO@example.com", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["ok"]
    assert client.get("/api/auth/forward").status_code == 204
    agents = client.get("/api/agents").json()
    assert {a["id"] for a in agents} == {"infra", "news", "schedule", "strategist", "manager", "risk", "cashier", "auditor"}
    assert all(a["skills"] for a in agents)


def test_csrf_blocks_foreign_origin(client):
    setup_owner(client)
    r = client.post("/api/system/running", json={"running": True}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/system/running", json={"running": True})
    assert r.status_code == 200 and r.json()["running"] is True


def test_settings_validation_and_protected_fields(client):
    setup_owner(client)
    r = client.put("/api/settings", json={"risk_per_trade_pct": 50})
    assert r.status_code == 400
    r = client.put("/api/settings", json={"mode": "live"})
    assert r.status_code == 400
    r = client.put("/api/settings", json={"timeframes": ["H1", "D1", "X"], "risk_per_trade_pct": 1})
    assert r.status_code == 200
    cfg = r.json()["config"]
    assert cfg["timeframes"] == ["H1", "D1"]


def test_the_ten_pairs_are_fixed(client):
    """Só os 10 pares: a tela não troca a lista, só o sufixo da corretora (EURUSDm)."""
    setup_owner(client)
    view = client.get("/api/settings").json()
    assert view["config"]["watchlist"] == TRADING_PAIRS and len(set(TRADING_PAIRS)) == 10
    assert view["options"]["pairs"] == TRADING_PAIRS
    r = client.put("/api/settings", json={"watchlist": ["EURUSD", "BTCUSD"]})
    assert r.status_code == 400 and "10 pares" in r.json()["detail"]
    r = client.put("/api/settings", json={"symbol_suffix": "m"})
    assert r.status_code == 200 and r.json()["config"]["watchlist"] == [p + "m" for p in TRADING_PAIRS]
    r = client.put("/api/settings", json={"symbol_suffix": "m m"})
    assert r.status_code == 400 and r.json()["detail"].startswith("Sufixo da corretora")
    # a lista inteira de volta (o que a tela manda junto com outra mudança) é aceita
    r = client.put("/api/settings", json={"watchlist": [p + "m" for p in TRADING_PAIRS], "max_open_positions": 2})
    assert r.status_code == 200


def test_presets_survive_restart_and_old_saves(client):
    """Metas do dia salvas pela tela continuam depois de reiniciar, mesmo com um campo antigo inválido no banco."""
    setup_owner(client)
    preset = {"risk_per_trade_pct": 0.25, "daily_loss_limit": 1.5, "daily_loss_unit": "percent", "daily_profit_target": 1, "daily_profit_unit": "percent", "max_open_positions": 2, "max_drawdown_pct": 8}
    assert client.put("/api/settings", json=preset).status_code == 200
    reset_cache()
    cfg = client.get("/api/settings").json()["config"]
    assert {k: cfg[k] for k in preset} == preset
    stored = kv_get("runtime_config")
    kv_set("runtime_config", {**stored, "daily_loss_limit": 500, "daily_loss_unit": "money", "timeframes": ["X"]})
    reset_cache()
    cfg = client.get("/api/settings").json()["config"]
    assert (cfg["daily_loss_limit"], cfg["daily_loss_unit"], cfg["daily_profit_target"]) == (500, "money", 1)


def test_live_mode_requires_password_and_mt5(client):
    setup_owner(client)
    r = client.post("/api/system/mode", json={"mode": "live", "password": "errada", "confirm": True})
    assert r.status_code == 403
    r = client.post("/api/system/mode", json={"mode": "live", "password": PASSWORD, "confirm": True})
    assert r.status_code == 409  # MT5 não conectado
    assert client.post("/api/system/mode", json={"mode": "paper"}).json()["mode"] == "paper"


def test_manual_backtest_endpoint(client):
    setup_owner(client)
    r = client.post("/api/strategies/backtest", json={"symbol": "EURUSD", "timeframe": "H4", "strategies": ["supertrend", "ifr_reversao"]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["data_source"] == "synthetic"
    assert len(data["results"]) == 2
    first = data["results"][0]
    assert {"metrics", "oos_metrics", "equity", "trades", "approved"} <= set(first)
    catalog = client.get("/api/strategies").json()
    assert len(catalog["strategies"]) == 18 and {"Vilela One", "IndicatorSpot"} <= set(catalog["sources"])


def test_market_endpoints(client):
    setup_owner(client)
    candles = client.get("/api/market/candles", params={"symbol": "XAUUSD", "timeframe": "H1", "count": 50}).json()
    assert len(candles["candles"]) == 50
    overview = client.get("/api/market/overview").json()
    assert overview["symbols"] and "news" in overview["symbols"][0]
    assert client.get("/api/market/symbols", params={"q": "ouro"}).json()[0]["name"] == "XAUUSD"


def test_two_factor_flow(client):
    setup_owner(client)
    r = client.post("/api/auth/2fa/setup", json={"password": PASSWORD})
    secret = r.json()["secret"]
    assert client.post("/api/auth/2fa/enable", json={"code": "000000"}).status_code == 400
    assert client.post("/api/auth/2fa/enable", json={"code": pyotp.TOTP(secret).now()}).status_code == 200
    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"email": "dono@example.com", "password": PASSWORD})
    assert r.json() == {"ok": False, "needs_code": True}
    r = client.post("/api/auth/login", json={"email": "dono@example.com", "password": PASSWORD, "code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200 and r.json()["ok"]


def test_websocket_snapshot(client):
    setup_owner(client)
    with client.websocket_connect("/ws", headers=ORIGIN) as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert len(snap["agents"]) == 8
        assert "system" in snap and "activity" in snap


def test_websocket_rejects_anonymous(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.receive_json()


def test_openrouter_key_via_settings(client, monkeypatch):
    setup_owner(client)
    view = client.get("/api/settings").json()
    assert view["ai"]["available"] is False and view["ai"]["key_set"] is False
    assert view["ai"]["models"]["news"]["model"] == "deepseek/deepseek-v4-flash"
    assert "ai_provider" not in view["config"] and "openrouter_news_model" not in view["config"]
    office = client.app.state.office

    async def fake_check(key):
        return (key == "sk-or-v1-boa", "chave do OpenRouter válida")

    monkeypatch.setattr(office.llm, "check_key", fake_check)
    r = client.post("/api/settings/ai-key", json={"api_key": "sk-or-v1-ruim", "password": PASSWORD})
    assert r.status_code == 400
    r = client.post("/api/settings/ai-key", json={"api_key": "sk-or-v1-boa", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["status"]["available"] is True
    view = client.get("/api/settings").json()
    assert view["ai"]["available"] is True and view["ai"]["key_masked"].startswith("sk-o")
    assert client.get("/api/system").json()["ai"] is True
    # modelos não mudam pela tela
    client.put("/api/settings", json={"openrouter_news_model": "openai/gpt-5"})
    assert client.get("/api/settings").json()["ai"]["models"]["news"]["model"] == "deepseek/deepseek-v4-flash"


def test_stack_placeholders_block_startup(monkeypatch):
    from app.config import Settings, unfilled_placeholders

    s = Settings(secret_key="x" * 40, database_url="postgresql+psycopg://postgres:TROQUE_SENHA_DO_POSTGRES@postgres_postgres:5432/metabot", openrouter_api_key="TROQUE_CHAVE")
    assert unfilled_placeholders(s) == ["MB_DATABASE_URL", "MB_OPENROUTER_API_KEY"]


def test_diagnostico_explains_market_hours(client):
    """O painel "por que não operou" diz se o câmbio está fechado e quando reabre."""
    from datetime import datetime, timezone

    from app.core.market_hours import fx_status

    setup_owner(client)
    friday_night = datetime(2026, 10, 2, 22, 0, tzinfo=timezone.utc)
    st = fx_status(friday_night)
    assert not st["open"] and st["next_change"].startswith("2026-10-04T22:00")
    st = fx_status(datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc))
    assert st["open"] and st["next_change"].startswith("2026-10-02T21:00") and st["next_change_in_min"] == 60
    r = client.get("/api/system/diagnostico")
    assert r.status_code == 200
    data = r.json()
    assert [x["symbol"] for x in data["symbols"]] == TRADING_PAIRS
    assert data["reasons"] and data["reasons"][0]["text"].startswith("Escritório desligado")
    assert set(data["funnel"]) >= {"approved", "candidates", "plan", "signals", "trades"}


def test_news_only_about_the_pairs(office):
    """A Nina só guarda e pontua notícias que mexem com as moedas dos 10 pares."""
    from app.agents.news import watched_codes

    assert watched_codes() == {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"}
    agent = office.agent("news")
    assert agent._symbols_from_assets({"BTC": 0.8}) == {}
    assert set(agent._symbols_from_assets({"EUR": 0.5})) == {"EURUSD", "EURJPY", "EURGBP"}

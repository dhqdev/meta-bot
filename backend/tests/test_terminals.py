from sqlalchemy import select

from app.broker.terminals import DEFAULT_NAME, TerminalManager
from app.config import Settings
from app.db import session_scope
from app.models import Terminal
from app.security import box


def _terminals():
    with session_scope() as s:
        return [(t.name, t.bridge_url, box().decrypt(t.token_enc), t.active) for t in s.scalars(select(Terminal).order_by(Terminal.id))]


def test_no_default_terminal_without_bridge_url():
    TerminalManager(Settings(mt5_bridge_url="", mt5_bridge_token="x" * 32)).ensure_default()
    assert _terminals() == []


def test_default_terminal_follows_stack_variables():
    token = "a" * 48
    TerminalManager(Settings(mt5_bridge_url="http://metabot-mt5:8001", mt5_bridge_token=token)).ensure_default()
    assert _terminals() == [(DEFAULT_NAME, "http://metabot-mt5:8001", token, True)]

    # MT5 mudou de lugar (PC/VPS Windows via Tailscale): a stack manda e o terminal acompanha
    new_token = "b" * 48
    manager = TerminalManager(Settings(mt5_bridge_url="http://100.64.10.20:8001/", mt5_bridge_token=new_token))
    manager.ensure_default()
    assert _terminals() == [(DEFAULT_NAME, "http://100.64.10.20:8001", new_token, True)]
    assert manager.active()["bridge_url"] == "http://100.64.10.20:8001"


def test_default_terminal_does_not_steal_active_flag():
    with session_scope() as s:
        s.add(Terminal(name="Corretora B", bridge_url="http://10.0.0.5:8001", token_enc=box().encrypt("c" * 32), active=True))
    TerminalManager(Settings(mt5_bridge_url="http://100.64.10.20:8001", mt5_bridge_token="d" * 32)).ensure_default()
    rows = {name: active for name, _, _, active in _terminals()}
    assert rows == {"Corretora B": True, DEFAULT_NAME: False}

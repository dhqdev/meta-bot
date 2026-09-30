from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="metabot-tests-")
os.environ.update(
    {
        "MB_ENV": "test",
        "MB_DATA_DIR": _TMP,
        "MB_DATABASE_URL": f"sqlite:///{_TMP}/test.db",
        "MB_AGENTS_ENABLED": "false",
        "MB_NETWORK_ENABLED": "false",
        "MB_SECRET_KEY": "test-secret-key-only-for-tests-0123456789abcdef",
        "MB_MT5_BRIDGE_TOKEN": "",
        "MB_ANTHROPIC_API_KEY": "",
        "MB_ADMIN_EMAIL": "",
        "MB_ADMIN_PASSWORD": "",
    }
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db import Base, configure, get_engine, init_db  # noqa: E402
from app.runtime import reset_cache  # noqa: E402
from app.security import SecretBox, set_secret_box  # noqa: E402

set_secret_box(SecretBox(get_settings().secret_key))
configure(get_settings().resolved_database_url)
init_db()


@pytest.fixture(autouse=True)
def clean_db():
    """Banco limpo e configuração padrão em cada teste."""
    engine = get_engine()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def office():
    from app.agents.office import Office

    o = Office(get_settings())
    o.ensure_setup()
    return o


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)

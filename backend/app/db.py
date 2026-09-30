"""Banco de dados (SQLAlchemy 2). PostgreSQL em produção, SQLite no desenvolvimento e nos testes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
SessionLocal = sessionmaker(expire_on_commit=False)


def configure(url: str) -> Engine:
    """(Re)cria o engine. Chamado na inicialização e pelos testes."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        path = url.split("sqlite:///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    else:
        kwargs.update(pool_size=10, max_overflow=10)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    SessionLocal.configure(bind=engine)
    _engine = engine
    return engine


def ensure_database(url: str, attempts: int = 30) -> None:
    """No PostgreSQL, cria o banco (ex.: ``metabot``) se ele ainda não existir.

    Permite usar um Postgres já existente na stack (ex.: ``postgres_postgres``)
    informando só usuário e senha. Espera o servidor subir (até ~60 s).
    """
    import re
    import time

    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import OperationalError

    u = make_url(url)
    if not u.drivername.startswith("postgresql") or not u.database:
        return
    name = u.database
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name):
        raise RuntimeError(f"nome de banco inválido: {name}")
    admin = create_engine(u.set(database="postgres"), isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        for i in range(attempts):
            try:
                with admin.connect() as conn:
                    exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).scalar()
                    if not exists:
                        conn.execute(text(f'CREATE DATABASE "{name}"'))
                return
            except OperationalError:
                if i == attempts - 1:
                    raise
                time.sleep(2)
    finally:
        admin.dispose()


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("banco não configurado: chame db.configure()")
    return _engine


def init_db() -> None:
    from app import models  # noqa: F401  (registra as tabelas)

    Base.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """Dependência do FastAPI."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

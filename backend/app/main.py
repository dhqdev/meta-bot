"""Aplicação FastAPI do Meta-Bot."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api import agents, auth, market, settings as settings_api, strategies, system, trades, ws
from app.config import ensure_secret_key, get_settings
from app.db import configure, init_db, session_scope
from app.security import SecretBox, set_secret_box

log = logging.getLogger("metabot")

MAX_BODY = 256 * 1024
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _setup_core() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    set_secret_box(SecretBox(ensure_secret_key(settings)))
    configure(settings.resolved_database_url)
    init_db()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_core()
    settings = get_settings()
    with session_scope() as s:
        code = auth.bootstrap_admin(s)
    if code:
        log.warning("=" * 64)
        log.warning("PRIMEIRO ACESSO: use o código de configuração %s para criar a conta do dono.", code)
        log.warning("(ou defina MB_ADMIN_EMAIL e MB_ADMIN_PASSWORD e reinicie)")
        log.warning("=" * 64)
    from app.agents.office import Office

    office = Office(settings)
    app.state.office = office
    if settings.agents_enabled:
        await office.start()
    else:
        office.ensure_setup()
    try:
        yield
    finally:
        await office.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Meta-Bot",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/api/openapi.json",
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # tamanho máximo do corpo
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY:
            return JSONResponse({"detail": "Requisição grande demais."}, status_code=413)
        # CSRF: requisições que alteram dados precisam vir do próprio site
        if request.method not in SAFE_METHODS and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin:
                host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
                allowed = {host.split(",")[0].strip()} | {
                    urlparse(o.strip()).netloc for o in settings.allowed_origins.split(",") if o.strip()
                }
                if urlparse(origin).netloc not in allowed:
                    return JSONResponse({"detail": "Origem não permitida."}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    for module in (auth, system, agents, strategies, market, trades, settings_api):
        app.include_router(module.router)
    app.include_router(ws.router)
    return app


app = create_app()

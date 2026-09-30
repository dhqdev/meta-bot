"""WebSocket do escritório: estado inicial + eventos em tempo real."""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.db import SessionLocal
from app.deps import COOKIE_NAME, user_from_token, ws_office
from app.events import bus
from app.models import Activity

router = APIRouter()


def _origin_ok(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    host = websocket.headers.get("x-forwarded-host") or websocket.headers.get("host", "")
    return urlparse(origin).netloc == host.split(",")[0].strip()


@router.websocket("/ws")
async def office_ws(websocket: WebSocket) -> None:
    if not _origin_ok(websocket):
        await websocket.close(code=4403)
        return
    db = SessionLocal()
    try:
        user = user_from_token(db, websocket.cookies.get(COOKIE_NAME))
        db.commit()
        recent = [
            {"type": "activity", "id": a.id, "agent": a.agent, "kind": a.kind, "level": a.level, "text": a.text, "data": a.data, "ts": a.ts.isoformat()}
            for a in db.scalars(select(Activity).order_by(Activity.ts.desc()).limit(60))
        ] if user else []
    finally:
        db.close()
    if user is None:
        await websocket.close(code=4401)
        return
    office = ws_office(websocket)
    await websocket.accept()
    queue = bus.subscribe()
    try:
        snapshot = office.snapshot() if office else {"type": "snapshot", "agents": [], "system": {}, "office": {}}
        snapshot["activity"] = list(reversed(recent))
        await websocket.send_json(snapshot)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(queue)

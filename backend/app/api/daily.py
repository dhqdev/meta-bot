"""Daily: relatórios das reuniões de fim de dia e o botão para fazer a daily agora."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.agents.daily import report_dict
from app.db import session_scope
from app.deps import current_user, get_office
from app.kv import kv_get
from app.models import DailyReport, User
from app.runtime import get_config

router = APIRouter(prefix="/api/daily", tags=["daily"])


@router.get("")
def list_reports(limit: int = Query(30, le=365), user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    cfg = get_config()
    with session_scope() as s:
        rows = [report_dict(r, full=False) for r in s.scalars(select(DailyReport).order_by(DailyReport.day.desc()).limit(limit))]
    return {
        "reports": rows,
        "enabled": cfg.daily_meeting_enabled,
        "time": cfg.daily_meeting_time,
        "next_at": office.daily.next_at(),
        "running": office.daily.running,
        "last_day": kv_get("daily.last"),
    }


@router.get("/{day}")
def get_report(day: str, user: User = Depends(current_user)) -> dict:
    with session_scope() as s:
        row = s.scalar(select(DailyReport).where(DailyReport.day == day))
        if row is None:
            raise HTTPException(status_code=404, detail="Não há daily neste dia.")
        return report_dict(row)


@router.post("/run")
async def run_now(user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    """Faz a daily agora (atualiza o relatório de hoje se ele já existir)."""
    if office.daily.running:
        raise HTTPException(status_code=409, detail="A daily já está acontecendo.")
    report = await office.daily.run(force=True)
    if report is None:
        raise HTTPException(status_code=409, detail="A daily já está acontecendo.")
    return report

"""Agentes: estado, skills, lições e atividade."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.agents.skills import active_lessons
from app.db import session_scope
from app.deps import current_user, get_office
from app.models import Activity, AgentMessage, Lesson, SkillEvent, User

router = APIRouter(prefix="/api", tags=["agents"])

JOBS = {
    "infra": ["health", "warm", "housekeeping"],
    "news": ["fetch", "classify", "evaluate"],
    "schedule": ["calendar", "hours"],
    "strategist": ["ranking", "evolution", "revalidate"],
    "manager": ["decide"],
    "risk": ["guard"],
    "cashier": ["equity", "exits_review"],
    "auditor": ["prune"],
}


@router.get("/agents")
def list_agents(user: User = Depends(current_user), office=Depends(get_office)) -> list[dict]:
    out = []
    for agent in office.agents.values():
        skills = agent.skills.all()
        out.append({**agent.snapshot(), "skills": skills, "level": round(sum(s["level"] for s in skills) / max(1, len(skills)), 1), "jobs": JOBS.get(agent.id, [])})
    return out


@router.get("/agents/{agent_id}")
def agent_detail(agent_id: str, user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    agent = office.agents.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agente não encontrado.")
    with session_scope() as s:
        events = [
            {"ts": e.ts.isoformat(), "skill": e.skill_key, "kind": e.kind, "xp": e.delta_xp, "text": e.text}
            for e in s.scalars(select(SkillEvent).where(SkillEvent.agent == agent_id).order_by(SkillEvent.ts.desc()).limit(60))
        ]
        activity = [
            {"ts": a.ts.isoformat(), "kind": a.kind, "level": a.level, "text": a.text}
            for a in s.scalars(select(Activity).where(Activity.agent == agent_id).order_by(Activity.ts.desc()).limit(80))
        ]
    return {
        **agent.snapshot(),
        "skills": agent.skills.all(),
        "skill_events": events,
        "activity": activity,
        "lessons": active_lessons(agent_id, 20),
        "jobs": JOBS.get(agent_id, []),
    }


@router.post("/agents/{agent_id}/run")
def run_job(agent_id: str, job: str = Query("*"), user: User = Depends(current_user), office=Depends(get_office)) -> dict:
    agent = office.agents.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agente não encontrado.")
    if job != "*" and job not in JOBS.get(agent_id, []):
        raise HTTPException(status_code=400, detail="Tarefa desconhecida para este agente.")
    agent.request(job)
    return {"ok": True, "agent": agent_id, "job": job}


@router.get("/activity")
def activity(limit: int = Query(100, le=500), agent: str | None = None, user: User = Depends(current_user)) -> list[dict]:
    with session_scope() as s:
        q = select(Activity).order_by(Activity.ts.desc()).limit(limit)
        if agent:
            q = q.where(Activity.agent == agent)
        return [{"id": a.id, "ts": a.ts.isoformat(), "agent": a.agent, "kind": a.kind, "level": a.level, "text": a.text, "data": a.data} for a in s.scalars(q)]


@router.get("/messages")
def messages(limit: int = Query(100, le=500), agent: str | None = None, user: User = Depends(current_user)) -> list[dict]:
    """Conversa da equipe (mais recentes primeiro). Com ``agent``, só as que ele mandou ou recebeu."""
    with session_scope() as s:
        q = select(AgentMessage).order_by(AgentMessage.ts.desc()).limit(limit)
        if agent:
            q = q.where((AgentMessage.sender == agent) | (AgentMessage.recipient == agent) | (AgentMessage.recipient == "all"))
        return [{"id": m.id, "ts": m.ts.isoformat(), "sender": m.sender, "recipient": m.recipient, "kind": m.kind, "text": m.text, "data": m.data} for m in s.scalars(q)]


@router.get("/lessons")
def lessons(user: User = Depends(current_user)) -> list[dict]:
    return active_lessons(None, 100)


@router.delete("/lessons/{lesson_id}")
def delete_lesson(lesson_id: int, user: User = Depends(current_user)) -> dict:
    with session_scope() as s:
        row = s.get(Lesson, lesson_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Lição não encontrada.")
        row.active = False
    return {"ok": True}

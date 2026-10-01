"""Skills dos agentes: nível e XP que sobem com trabalho e resultado reais.

Cada agente tem skills (ex.: o Estrategista tem uma por estratégia). A XP vem
de eventos concretos: um backtest aprovado, uma evolução confirmada fora da
amostra, uma notícia cujo sentimento acertou a direção do preço, uma operação
bem gerida. Os parâmetros aprendidos (ex.: peso de cada fonte de notícia,
parâmetros de saída do Caixa) ficam em ``params`` da skill.

Os "playbooks" (arquivos .md em ``playbooks/``) são o conhecimento-base que
vai no prompt dos agentes com IA, somado às lições aprendidas (tabela ``lessons``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.db import session_scope
from app.events import bus, record_activity
from app.models import Lesson, Skill, SkillEvent

LEVEL_XP = [0, 50, 130, 260, 450, 720, 1100, 1600, 2300, 3200]  # nível 1..10
MAX_LEVEL = len(LEVEL_XP)
PLAYBOOKS = Path(__file__).parent / "playbooks"


def level_for(xp: int) -> int:
    level = 1
    for i, need in enumerate(LEVEL_XP):
        if xp >= need:
            level = i + 1
    return level


def progress(xp: int) -> dict:
    level = level_for(xp)
    if level >= MAX_LEVEL:
        return {"level": level, "xp": xp, "next": None, "pct": 1.0}
    cur, nxt = LEVEL_XP[level - 1], LEVEL_XP[level]
    return {"level": level, "xp": xp, "next": nxt, "pct": round((xp - cur) / (nxt - cur), 3)}


@dataclass(frozen=True)
class SkillDef:
    key: str
    name: str
    description: str


class SkillBook:
    def __init__(self, agent_id: str, agent_name: str = ""):
        self.agent = agent_id
        self.agent_name = agent_name or agent_id

    def ensure(self, defs: list[SkillDef]) -> None:
        with session_scope() as s:
            existing = {k for (k,) in s.execute(select(Skill.key).where(Skill.agent == self.agent))}
            for d in defs:
                if d.key not in existing:
                    s.add(Skill(agent=self.agent, key=d.key, name=d.name, description=d.description, stats={}, params={}))

    def gain(self, key: str, xp: int, reason: str = "", data: dict | None = None) -> bool:
        """Soma XP; devolve True se subiu de nível (e avisa o escritório)."""
        if xp <= 0:
            return False
        with session_scope() as s:
            row = s.scalar(select(Skill).where(Skill.agent == self.agent, Skill.key == key))
            if row is None:
                return False
            old = row.level
            row.xp += int(xp)
            row.level = level_for(row.xp)
            leveled = row.level > old
            name, level = row.name, row.level
            if xp >= 5 or leveled:
                s.add(SkillEvent(agent=self.agent, skill_key=key, kind="xp", delta_xp=int(xp), text=reason[:500], data=data or {}))
            if leveled:
                s.add(SkillEvent(agent=self.agent, skill_key=key, kind="levelup", delta_xp=0, text=f"nível {level}", data={}))
        if leveled:
            bus.publish({"type": "levelup", "agent": self.agent, "skill": key, "name": name, "level": level})
            record_activity(self.agent, f"⭐ {self.agent_name} subiu para o nível {level} em {name}", kind="levelup", data={"skill": key, "level": level})
        return leveled

    def event(self, key: str, kind: str, text: str, data: dict | None = None) -> None:
        with session_scope() as s:
            s.add(SkillEvent(agent=self.agent, skill_key=key, kind=kind, delta_xp=0, text=text[:500], data=data or {}))

    def get(self, key: str) -> dict:
        with session_scope() as s:
            row = s.scalar(select(Skill).where(Skill.agent == self.agent, Skill.key == key))
            if row is None:
                return {}
            return {"key": row.key, "name": row.name, "level": row.level, "xp": row.xp, "stats": dict(row.stats or {}), "params": dict(row.params or {})}

    def update(self, key: str, stats: dict | None = None, params: dict | None = None) -> None:
        with session_scope() as s:
            row = s.scalar(select(Skill).where(Skill.agent == self.agent, Skill.key == key))
            if row is None:
                return
            if stats is not None:
                row.stats = {**(row.stats or {}), **stats}
            if params is not None:
                row.params = {**(row.params or {}), **params}

    def all(self) -> list[dict]:
        with session_scope() as s:
            rows = list(s.scalars(select(Skill).where(Skill.agent == self.agent).order_by(Skill.id)))
            return [
                {
                    "key": r.key,
                    "name": r.name,
                    "description": r.description,
                    **progress(r.xp),
                    "stats": r.stats or {},
                    "params": r.params or {},
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                }
                for r in rows
            ]


def playbook(name: str) -> str:
    path = PLAYBOOKS / f"{name}.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def active_lessons(agent: str | None = None, limit: int = 12) -> list[dict]:
    with session_scope() as s:
        q = select(Lesson).where(Lesson.active.is_(True))
        if agent:
            q = q.where(Lesson.agent.in_([agent, "all"]))
        rows = list(s.scalars(q.order_by(Lesson.score.desc(), Lesson.ts.desc()).limit(limit)))
        return [{"id": r.id, "agent": r.agent, "text": r.text, "score": r.score, "source": r.source, "ts": r.ts.isoformat()} for r in rows]


def add_lesson(agent: str, text: str, evidence: dict | None = None, source: str = "auditor", score: float = 1.0) -> None:
    text = text.strip()[:500]
    if not text:
        return
    with session_scope() as s:
        dup = s.scalar(select(Lesson).where(Lesson.agent == agent, Lesson.text == text, Lesson.active.is_(True)))
        if dup is not None:
            dup.score = min(5.0, dup.score + 0.5)
            return
        s.add(Lesson(agent=agent, text=text, evidence=evidence or {}, source=source, score=score))

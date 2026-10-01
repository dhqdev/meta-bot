"""Base dos agentes: estado no escritório, agenda de tarefas e skills."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.agents.personas import persona_dict, speak
from app.agents.skills import SkillBook, SkillDef
from app.db import session_scope
from app.events import bus, record_activity
from app.models import AgentMessage
from app.runtime import get_config

if TYPE_CHECKING:  # pragma: no cover
    from app.agents.office import Office

log = logging.getLogger("metabot.agents")

# Lugares do escritório para onde um agente pode ir (o frontend sabe onde ficam).
LOCATIONS = {"desk", "meeting", "tv", "whiteboard", "server", "vault", "library", "coffee", "window", "lounge", "manager"}


@dataclass(frozen=True)
class AgentProfile:
    id: str
    name: str
    role: str
    emoji: str
    uses_ai: bool
    description: str


class Agent:
    profile: AgentProfile
    interval: float = 10.0
    skill_defs: list[SkillDef] = []
    always_on = False  # roda mesmo com o sistema desligado (TI e Caixa)
    idle_task = "Acompanhando o mercado"

    def __init__(self, office: "Office"):
        self.office = office
        self.skills = SkillBook(self.profile.id, self.profile.name)
        self.state = "off"
        self.location = "lounge"
        self.task = "Aguardando o sistema ser ligado"
        self.emoji = "💤"
        self.last_error = ""
        self.last_tick_at: float | None = None
        self._last_run: dict[str, float] = {}
        self._forced: set[str] = set()
        self._wake = asyncio.Event()
        self._was_running: bool | None = None

    # ------------------------------------------------------ apresentação
    @property
    def id(self) -> str:
        return self.profile.id

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.profile.id,
            "name": self.profile.name,
            "role": self.profile.role,
            "emoji": self.profile.emoji,
            "uses_ai": self.profile.uses_ai,
            "description": self.profile.description,
            "state": self.state,
            "location": self.location,
            "task": self.task,
            "status_emoji": self.emoji,
            "last_error": self.last_error,
            "last_tick_at": self.last_tick_at,
            "persona": persona_dict(self.profile.id),
        }

    def set_state(self, state: str, location: str | None = None, task: str | None = None, emoji: str | None = None) -> None:
        changed = state != self.state or (location and location != self.location) or (task is not None and task != self.task)
        self.state = state
        if location:
            self.location = location if location in LOCATIONS or location.startswith("agent:") else "desk"
        if task is not None:
            self.task = task[:160]
        if emoji is not None:
            self.emoji = emoji
        if changed:
            bus.publish({"type": "agent", **self.snapshot()})

    def say(self, text: str, emoji: str = "", to: str | None = None) -> None:
        """Balão no escritório (fala solta, sem destinatário)."""
        if to:
            self.tell(to, text)
            return
        bus.publish({"type": "say", "agent": self.id, "text": text[:180], "emoji": emoji})

    def tell(self, to: str, text: str, kind: str = "info", data: dict | None = None) -> None:
        """Mensagem para um colega (ou "all"): fica na conversa da equipe e aparece no escritório."""
        text = text.strip()[:280]
        if not text:
            return
        ts = datetime.now(timezone.utc)
        row_id = None
        try:
            with session_scope() as s:
                row = AgentMessage(ts=ts, sender=self.id, recipient=to or "all", kind=kind, text=text, data=data or {})
                s.add(row)
                s.flush()
                row_id = row.id
        except Exception:  # a conversa nunca derruba um agente
            log.exception("falha ao gravar mensagem de %s", self.id)
        bus.publish({"type": "message", "id": row_id, "sender": self.id, "recipient": to or "all", "kind": kind, "text": text, "data": data or {}, "ts": ts.isoformat()})

    def line(self, key: str, **fields) -> str:
        """Fala no jeito do agente (personalidade)."""
        return speak(self.id, key, **fields)

    def log(self, text: str, kind: str = "info", level: str = "info", data: dict | None = None) -> None:
        record_activity(self.id, text, kind=kind, level=level, data=data)

    def work(self, task: str, location: str = "desk", emoji: str = "⚙️") -> None:
        self.set_state("working", location, task, emoji)

    def idle(self, task: str | None = None) -> None:
        self.set_state("idle", "desk", task or self.idle_task, "")

    # ------------------------------------------------------------- agenda
    def due(self, job: str, seconds: float) -> bool:
        now = time.time()
        if job in self._forced or "*" in self._forced:
            self._forced.discard(job)
            self._last_run[job] = now
            return True
        last = self._last_run.get(job)
        if last is None or now - last >= seconds:
            self._last_run[job] = now
            return True
        return False

    def request(self, job: str = "*") -> None:
        """Pede para rodar uma tarefa agora (ex.: botão na tela)."""
        self._forced.add(job)
        self._wake.set()

    def is_running(self) -> bool:
        return get_config().system_running

    async def run_forever(self) -> None:
        await asyncio.sleep(0.5)
        while True:
            try:
                running = self.is_running()
                if running != self._was_running:
                    self._was_running = running
                    await self.on_system_change(running)
                if running or self.always_on:
                    await self.tick()
                    if "*" in self._forced:
                        self._forced.discard("*")
                self.last_tick_at = time.time()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # um erro nunca derruba o agente
                log.exception("erro no agente %s", self.id)
                self.last_error = f"{exc.__class__.__name__}: {exc}"[:300]
                self.set_state("error", None, f"Erro: {self.last_error}", "⚠️")
                self.log(f"Erro inesperado: {self.last_error}", kind="error", level="error")
                await asyncio.sleep(5)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def on_system_change(self, running: bool) -> None:
        if running:
            self.set_state("idle", "desk", self.idle_task, "☕")
            await self.greet()
        elif not self.always_on:
            self.set_state("off", "lounge", "Sistema desligado", "💤")

    async def greet(self) -> None:
        """Chega na mesa e cumprimenta a equipe (cada um no seu tempo, para não falarem juntos)."""
        await asyncio.sleep(random.uniform(0.5, 6.0))
        self.say(self.line("start", **self.greet_fields()))

    def greet_fields(self) -> dict:
        return {}

    async def tick(self) -> None:  # pragma: no cover - cada agente implementa
        raise NotImplementedError

"""Horário do mercado de câmbio: quando abre, quando fecha e quanto falta.

Mesma regra do mercado simulado e dos preços reais (``open_mask``): o forex fecha
na sexta às 21:00 UTC (18h de Brasília) e reabre no domingo às 22:00 UTC (19h).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from app.broker.synthetic import open_mask


def is_open(session: str, at: datetime | None = None) -> bool:
    at = at or datetime.now(timezone.utc)
    return bool(open_mask(session, np.array([int(at.timestamp())]))[0])


def next_change(session: str, at: datetime | None = None) -> datetime | None:
    """Próxima abertura (se fechado) ou próximo fechamento (se aberto), com precisão de 1 minuto."""
    at = (at or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    start = int(at.timestamp())
    times = np.arange(start + 60, start + 8 * 86400, 60, dtype=np.int64)
    mask = open_mask(session, times)
    now_open = is_open(session, at)
    idx = np.flatnonzero(mask != now_open)
    if not len(idx):
        return None
    return datetime.fromtimestamp(int(times[idx[0]]), tz=timezone.utc)


def fx_status(at: datetime | None = None) -> dict:
    at = at or datetime.now(timezone.utc)
    open_now = is_open("fx", at)
    change = next_change("fx", at)
    return {
        "open": open_now,
        "next_change": change.isoformat() if change else None,
        "next_change_in_min": int((change - at) / timedelta(minutes=1)) if change else None,
    }

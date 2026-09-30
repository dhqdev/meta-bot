"""IA dos agentes (Claude, da Anthropic).

- Respostas em JSON validado (structured outputs: ``output_config.format``).
- Prompt de sistema estável com cache (``cache_control``) para baratear chamadas repetidas.
- Fallback automático no servidor quando o modelo recusa (``fallbacks: "default"``).
- Limite de chamadas por hora e orçamento diário em dólares (configuráveis na tela).
- Cada chamada fica registrada (tokens e custo estimado) por agente.

Sem chave cadastrada, ``available()`` é falso e os agentes usam as regras sem IA.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select

from app.config import get_settings
from app.db import session_scope
from app.kv import kv_set, secret_get
from app.models import AIUsage
from app.runtime import get_config

log = logging.getLogger("metabot.llm")

T = TypeVar("T", bound=BaseModel)

# Preço por milhão de tokens: (entrada, saída, leitura de cache, escrita de cache)
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25),
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5),
}
# Modelos que aceitam effort e o fallback automático no servidor.
EFFORT_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-opus-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class LLMResult:
    ok: bool
    data: BaseModel | None = None
    error: str = ""
    model: str = ""
    cost_usd: float = 0.0


def estimate_cost(model: str, usage) -> float:
    price = PRICES.get(model, PRICES["claude-opus-5-5"])
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (inp * price[0] + out * price[1] + cr * price[2] + cw * price[3]) / 1_000_000


class LLMService:
    def __init__(self) -> None:
        self._client: anthropic.AsyncAnthropic | None = None
        self._client_key: str | None = None
        self._calls: list[float] = []

    # ------------------------------------------------------------- estado
    def api_key(self) -> str | None:
        return secret_get("anthropic_api_key") or get_settings().anthropic_api_key or None

    def available(self) -> bool:
        cfg = get_config()
        return bool(cfg.ai_enabled and self.api_key())

    def _get_client(self) -> anthropic.AsyncAnthropic:
        key = self.api_key()
        if not key:
            raise RuntimeError("sem chave da Anthropic")
        if self._client is None or self._client_key != key:
            self._client = anthropic.AsyncAnthropic(api_key=key, timeout=120.0, max_retries=2)
            self._client_key = key
        return self._client

    def spent_today(self) -> float:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        with session_scope() as s:
            return float(s.scalar(select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0)).where(AIUsage.ts >= start)) or 0.0)

    def _budget_problem(self) -> str | None:
        cfg = get_config()
        now = time.time()
        self._calls = [t for t in self._calls if now - t < 3600]
        if len(self._calls) >= cfg.ai_max_calls_per_hour:
            return f"limite de {cfg.ai_max_calls_per_hour} chamadas de IA por hora atingido"
        if cfg.ai_daily_budget_usd > 0 and self.spent_today() >= cfg.ai_daily_budget_usd:
            return f"orçamento diário de IA (US$ {cfg.ai_daily_budget_usd:.2f}) atingido"
        return None

    def _record(self, agent: str, purpose: str, model: str, usage, ok: bool, error: str = "") -> float:
        cost = estimate_cost(model, usage) if usage is not None else 0.0
        try:
            with session_scope() as s:
                s.add(
                    AIUsage(
                        agent=agent,
                        purpose=purpose,
                        model=model,
                        input_tokens=getattr(usage, "input_tokens", 0) or 0,
                        output_tokens=getattr(usage, "output_tokens", 0) or 0,
                        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                        cost_usd=cost,
                        ok=ok,
                        error=error[:300],
                    )
                )
        except Exception:
            log.exception("falha ao registrar uso da IA")
        return cost

    # ------------------------------------------------------------- chamada
    async def complete_json(
        self,
        *,
        agent: str,
        purpose: str,
        model: str,
        system: str,
        user: str,
        schema_model: type[T],
        effort: str = "medium",
        max_tokens: int = 16000,
    ) -> LLMResult:
        if not self.available():
            return LLMResult(False, error="IA desligada ou sem chave")
        problem = self._budget_problem()
        if problem:
            return LLMResult(False, error=problem)
        client = self._get_client()
        self._calls.append(time.time())
        schema = schema_model.model_json_schema()
        output_config: dict = {"format": {"type": "json_schema", "schema": _strict(schema)}}
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }
        try:
            if model in EFFORT_MODELS:
                output_config["effort"] = effort
                response = await client.beta.messages.create(
                    **kwargs, output_config=output_config, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = await client.messages.create(**kwargs, output_config=output_config)
        except anthropic.AuthenticationError:
            kv_set("ai_status", {"ok": False, "error": "chave da Anthropic inválida", "at": _now()})
            self._record(agent, purpose, model, None, False, "chave inválida")
            return LLMResult(False, error="chave da Anthropic inválida", model=model)
        except anthropic.PermissionDeniedError:
            self._record(agent, purpose, model, None, False, "sem permissão")
            return LLMResult(False, error="a chave não tem permissão para este modelo", model=model)
        except anthropic.NotFoundError:
            self._record(agent, purpose, model, None, False, "modelo não encontrado")
            return LLMResult(False, error=f"modelo {model} não encontrado", model=model)
        except anthropic.RateLimitError:
            self._record(agent, purpose, model, None, False, "limite de taxa")
            return LLMResult(False, error="limite de uso da Anthropic atingido; tento de novo mais tarde", model=model)
        except anthropic.BadRequestError as exc:
            self._record(agent, purpose, model, None, False, f"requisição inválida: {exc.message}")
            return LLMResult(False, error=f"requisição recusada: {exc.message}", model=model)
        except anthropic.APIStatusError as exc:
            self._record(agent, purpose, model, None, False, f"erro {exc.status_code}")
            return LLMResult(False, error=f"erro da Anthropic ({exc.status_code})", model=model)
        except anthropic.APIConnectionError:
            self._record(agent, purpose, model, None, False, "sem conexão")
            return LLMResult(False, error="sem conexão com a Anthropic", model=model)

        served = getattr(response, "model", model) or model
        usage = getattr(response, "usage", None)
        if response.stop_reason == "refusal":
            cost = self._record(agent, purpose, served, usage, False, "recusa")
            return LLMResult(False, error="a IA recusou esta solicitação", model=served, cost_usd=cost)
        if response.stop_reason == "max_tokens":
            cost = self._record(agent, purpose, served, usage, False, "resposta cortada")
            return LLMResult(False, error="resposta da IA cortada (max_tokens)", model=served, cost_usd=cost)
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
        try:
            data = schema_model.model_validate_json(text)
        except (ValidationError, ValueError) as exc:
            cost = self._record(agent, purpose, served, usage, False, "JSON inválido")
            log.warning("resposta da IA fora do formato: %s", exc)
            return LLMResult(False, error="resposta da IA fora do formato", model=served, cost_usd=cost)
        cost = self._record(agent, purpose, served, usage, True)
        kv_set("ai_status", {"ok": True, "error": "", "at": _now(), "model": served})
        return LLMResult(True, data=data, model=served, cost_usd=cost)

    async def check_key(self, key: str) -> tuple[bool, str]:
        """Confere a chave consultando a lista de modelos (não gasta tokens)."""
        client = anthropic.AsyncAnthropic(api_key=key, timeout=20.0, max_retries=1)
        try:
            await client.models.retrieve(get_config().ai_model)
            return True, "chave válida"
        except anthropic.AuthenticationError:
            return False, "chave inválida"
        except anthropic.NotFoundError:
            return True, "chave válida (o modelo configurado não está disponível para ela)"
        except anthropic.APIConnectionError:
            return False, "sem conexão com a Anthropic para conferir a chave"
        except anthropic.APIStatusError as exc:
            return False, f"erro ao conferir a chave ({exc.status_code})"
        finally:
            await client.close()

    def usage_summary(self, days: int = 7) -> dict:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        with session_scope() as s:
            rows = s.execute(
                select(AIUsage.agent, func.count(), func.coalesce(func.sum(AIUsage.cost_usd), 0.0))
                .where(AIUsage.ts >= since)
                .group_by(AIUsage.agent)
            ).all()
        return {
            "today_usd": round(self.spent_today(), 4),
            "by_agent": [{"agent": a, "calls": int(n), "cost_usd": round(float(c), 4)} for a, n, c in rows],
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_DROP_KEYS = {
    "title", "$defs", "default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "pattern", "minItems", "maxItems", "multipleOf",
}


def _strict(schema: dict) -> dict:
    """Ajusta o schema do Pydantic ao formato aceito (objetos fechados, sem títulos, $defs resolvidos)."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"].split("/")[-1]
                return walk(defs[ref])
            out = {k: walk(v) for k, v in node.items() if k not in _DROP_KEYS}
            if out.get("type") == "object":
                out["additionalProperties"] = False
                props = out.get("properties", {})
                out["required"] = list(props.keys())
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def to_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)

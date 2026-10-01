"""IA dos agentes, sempre pelo OpenRouter, com um modelo fixo para cada agente.

- Cada agente com IA tem o seu modelo, escolhido por economia (o sistema fica ligado o
  dia todo) e **definido no código**: não muda pela tela.
- Respostas em JSON validado (``response_format`` com JSON Schema; se o modelo não aceitar,
  modo JSON simples com o schema no prompt).
- Modelo reserva quando o principal falha e custo real de cada chamada (``usage.cost``).
- Limite de chamadas por hora e orçamento diário em dólares (configuráveis na tela).
- Cada chamada fica registrada (tokens e custo) por agente.

Sem a chave do OpenRouter, ``available()`` é falso e os agentes usam só as regras.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select

from app import __version__
from app.config import get_settings
from app.db import session_scope
from app.kv import kv_get, kv_set, secret_get
from app.models import AIUsage
from app.runtime import get_config

log = logging.getLogger("metabot.llm")

T = TypeVar("T", bound=BaseModel)

OPENROUTER_URL = "https://openrouter.ai/api/v1"
REPO_URL = "https://github.com/dhqdev/meta-bot"

# Modelo de cada tarefa com IA (fixo). Preço de referência em US$ por milhão de tokens (entrada, saída).
AGENT_MODELS: dict[str, dict] = {
    "news": {
        "agent": "news",
        "model": "deepseek/deepseek-v4-flash",
        "why": "classifica manchetes o dia todo: o que mais chama a IA, então o mais barato",
        "price": (0.089, 0.177),
    },
    "manager": {
        "agent": "manager",
        "model": "google/gemini-3.1-flash-lite",
        "why": "escolhe entre candidatos que as regras já filtraram: leve e rápido",
        "price": (0.25, 1.50),
    },
    "daily": {
        "agent": "auditor",
        "model": "anthropic/claude-haiku-4.5",
        "why": "escreve a daily das 19h (1 vez por dia): a fala de cada agente, o resumo e as lições",
        "price": (1.0, 5.0),
    },
}
FALLBACK_MODEL = "google/gemini-3.1-flash-lite"
FALLBACK_PRICE = (0.25, 1.50)
# Modelos em que o cache do prompt é explícito (cache_control).
CACHE_PREFIXES = ("anthropic/",)
# Modelos que aceitam o controle de raciocínio (reasoning.effort).
REASONING_PREFIXES = ("anthropic/claude-opus-5", "anthropic/claude-sonnet-5", "google/gemini-3", "openai/gpt-5", "openai/o")
# Economia: respostas curtas bastam para os formatos dos agentes.
MAX_TOKENS = 8000


def model_for(tier: str) -> str:
    return AGENT_MODELS.get(tier, AGENT_MODELS["manager"])["model"]


def reference_price(model: str) -> tuple[float, float] | None:
    if model == FALLBACK_MODEL:
        return FALLBACK_PRICE
    for info in AGENT_MODELS.values():
        if info["model"] == model:
            return info["price"]
    return None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None  # custo informado pelo OpenRouter


@dataclass
class LLMResult:
    ok: bool
    data: BaseModel | None = None
    error: str = ""
    model: str = ""
    cost_usd: float = 0.0
    retry: bool = False  # vale tentar o modelo reserva


def _usage(raw) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    details = raw.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens") or 0)
    cost = raw.get("cost")
    return Usage(
        input_tokens=max(0, int(raw.get("prompt_tokens") or 0) - cached),
        output_tokens=int(raw.get("completion_tokens") or 0),
        cache_read_tokens=cached,
        cache_write_tokens=int(details.get("cache_write_tokens") or 0),
        cost_usd=float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
    )


class LLMService:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._calls: list[float] = []
        self._transport = transport  # os testes injetam um transporte falso

    # ------------------------------------------------------------- estado
    def api_key(self) -> str | None:
        return secret_get("openrouter_api_key") or get_settings().openrouter_api_key or None

    def available(self) -> bool:
        return bool(get_config().ai_enabled and self.api_key())

    def status(self) -> dict:
        return {
            "available": self.available(),
            "key_set": bool(self.api_key()),
            "models": {tier: {k: v for k, v in info.items()} for tier, info in AGENT_MODELS.items()},
            "fallback": FALLBACK_MODEL,
            "last": kv_get("ai_status"),  # resultado da última chamada (ok ou o erro, para a tela)
        }

    def _http(self, timeout: float = 120.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=OPENROUTER_URL, timeout=timeout, transport=self._transport)

    def _headers(self, key: str) -> dict:
        return {
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": get_settings().public_url or REPO_URL,
            "X-Title": "Meta-Bot",
            "User-Agent": f"meta-bot/{__version__}",
        }

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

    @staticmethod
    def _cost(model: str, usage: Usage | None) -> float:
        if usage is None:
            return 0.0
        if usage.cost_usd is not None:
            return usage.cost_usd
        price = reference_price(model)
        if price is None:
            return 0.0
        return ((usage.input_tokens + usage.cache_read_tokens) * price[0] + usage.output_tokens * price[1]) / 1_000_000

    def _record(self, agent: str, purpose: str, model: str, usage: Usage | None, ok: bool, error: str = "") -> float:
        cost = self._cost(model, usage)
        try:
            with session_scope() as s:
                s.add(
                    AIUsage(
                        agent=agent,
                        purpose=purpose,
                        model=f"openrouter:{model}"[:80],
                        input_tokens=usage.input_tokens if usage else 0,
                        output_tokens=usage.output_tokens if usage else 0,
                        cache_read_tokens=usage.cache_read_tokens if usage else 0,
                        cache_write_tokens=usage.cache_write_tokens if usage else 0,
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
        system: str,
        user: str,
        schema_model: type[T],
        tier: str = "manager",
        effort: str = "medium",
        max_tokens: int = MAX_TOKENS,
    ) -> LLMResult:
        if not get_config().ai_enabled:
            return LLMResult(False, error="IA desligada")
        if not self.api_key():
            return LLMResult(False, error="IA sem chave do OpenRouter")
        problem = self._budget_problem()
        if problem:
            return LLMResult(False, error=problem)
        self._calls.append(time.time())
        model = model_for(tier)
        res = await self._call(agent, purpose, model, system, user, schema_model, effort, max_tokens)
        if not res.ok and res.retry and FALLBACK_MODEL != model:
            log.warning("OpenRouter: %s falhou (%s); tentando o reserva %s", model, res.error, FALLBACK_MODEL)
            res = await self._call(agent, purpose, FALLBACK_MODEL, system, user, schema_model, effort, max_tokens)
        if res.ok:
            kv_set("ai_status", {"ok": True, "error": "", "at": _now(), "model": res.model})
        return res

    async def _call(self, agent, purpose, model, system, user, schema_model, effort, max_tokens) -> LLMResult:
        key = self.api_key() or ""
        schema = _strict(schema_model.model_json_schema())
        # O schema também vai no texto: modelos sem structured outputs seguem a instrução.
        guide = (
            "\n\nResponda SOMENTE com um objeto JSON válido (sem texto antes ou depois, sem ```), "
            f"seguindo exatamente este JSON Schema:\n{json.dumps(schema, ensure_ascii=False)}"
        )
        sys_content: str | list = system + guide
        if model.startswith(CACHE_PREFIXES):
            sys_content = [{"type": "text", "text": system + guide, "cache_control": {"type": "ephemeral"}}]
        body: dict = {
            "model": model,
            "max_tokens": min(max_tokens, MAX_TOKENS),
            "messages": [{"role": "system", "content": sys_content}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_model.__name__, "strict": True, "schema": schema},
            },
            "provider": {"require_parameters": True},
            "usage": {"include": True},
        }
        # Economia: raciocínio curto só onde o agente pede análise, nunca nas notícias.
        if effort in ("medium", "high") and model.startswith(REASONING_PREFIXES):
            body["reasoning"] = {"effort": "low", "exclude": True}

        def rec(m, u, ok, err=""):
            return self._record(agent, purpose, m, u, ok, err)

        try:
            async with self._http() as http:
                resp = await http.post("/chat/completions", json=body, headers=self._headers(key))
                if resp.status_code in (400, 404) and _format_unsupported(resp):
                    # Nenhum provedor do modelo aceita JSON Schema: modo JSON simples (o schema segue no prompt).
                    body["response_format"] = {"type": "json_object"}
                    body.pop("provider", None)
                    resp = await http.post("/chat/completions", json=body, headers=self._headers(key))
        except httpx.HTTPError:
            rec(model, None, False, "sem conexão")
            return LLMResult(False, error="sem conexão com o OpenRouter", model=model, retry=True)

        payload = _json_or_empty(resp)
        if resp.status_code != 200 or (payload.get("error") and not payload.get("choices")):
            message = _error_message(payload) or f"HTTP {resp.status_code}"
            code = resp.status_code if resp.status_code != 200 else _int((payload.get("error") or {}).get("code"), 500)
            friendly = _friendly_error(code, message, model)
            if code == 401:
                kv_set("ai_status", {"ok": False, "error": friendly, "at": _now()})
            rec(model, _usage(payload.get("usage")), False, f"{code}: {message}")
            # Chave e créditos resolvem-se com o dono; o resto (modelo fora do ar, sumiu, lotado) tenta o reserva.
            return LLMResult(False, error=friendly, model=model, retry=code not in (401, 402))

        served = payload.get("model") or model
        usage = _usage(payload.get("usage"))
        choices = payload.get("choices") or []
        if not choices:
            rec(served, usage, False, "sem resposta")
            return LLMResult(False, error="o OpenRouter não devolveu resposta", model=served, retry=True)
        choice = choices[0]
        if choice.get("error"):
            message = _error_message(choice) or "erro do provedor"
            cost = rec(served, usage, False, message)
            return LLMResult(False, error=f"erro do provedor no OpenRouter: {message}", model=served, cost_usd=cost, retry=True)
        finish = choice.get("finish_reason")
        if finish == "content_filter" or choice.get("native_finish_reason") == "refusal":
            cost = rec(served, usage, False, "recusa")
            return LLMResult(False, error="a IA recusou esta solicitação", model=served, cost_usd=cost)
        if finish == "length":
            cost = rec(served, usage, False, "resposta cortada")
            return LLMResult(False, error="resposta da IA cortada (max_tokens)", model=served, cost_usd=cost)
        content = (choice.get("message") or {}).get("content") or ""
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        try:
            data = schema_model.model_validate_json(_extract_json(content))
        except (ValidationError, ValueError) as exc:
            cost = rec(served, usage, False, "JSON inválido")
            log.warning("resposta da IA fora do formato: %s", exc)
            return LLMResult(False, error="resposta da IA fora do formato", model=served, cost_usd=cost, retry=True)
        cost = rec(served, usage, True)
        return LLMResult(True, data=data, model=served, cost_usd=cost)

    # ------------------------------------------------------------- chave
    async def check_key(self, key: str) -> tuple[bool, str]:
        """Confere a chave sem gastar tokens."""
        try:
            async with self._http(timeout=20.0) as http:
                resp = await http.get("/key", headers=self._headers(key))
                if resp.status_code == 404:
                    resp = await http.get("/auth/key", headers=self._headers(key))
        except httpx.HTTPError:
            return False, "sem conexão com o OpenRouter para conferir a chave"
        if resp.status_code in (401, 403):
            return False, "chave do OpenRouter inválida"
        if resp.status_code != 200:
            return False, f"erro ao conferir a chave do OpenRouter ({resp.status_code})"
        info = _json_or_empty(resp).get("data") or {}
        remaining = info.get("limit_remaining")
        extra = f" (limite restante: US$ {float(remaining):.2f})" if isinstance(remaining, (int, float)) else ""
        return True, "chave do OpenRouter válida" + extra

    def usage_summary(self, days: int = 7) -> dict:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        with session_scope() as s:
            rows = s.execute(
                select(AIUsage.agent, func.count(), func.coalesce(func.sum(AIUsage.cost_usd), 0.0))
                .where(AIUsage.ts >= since)
                .group_by(AIUsage.agent)
            ).all()
            month_start = datetime.now(timezone.utc) - timedelta(days=30)
            month = float(s.scalar(select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0)).where(AIUsage.ts >= month_start)) or 0.0)
        return {
            "today_usd": round(self.spent_today(), 4),
            "last_30d_usd": round(month, 4),
            "by_agent": [{"agent": a, "calls": int(n), "cost_usd": round(float(c), 4)} for a, n, c in rows],
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _json_or_empty(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _error_message(payload: dict) -> str:
    err = payload.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or "")[:300]
    return str(err or "")[:300]


def _format_unsupported(resp: httpx.Response) -> bool:
    text = _error_message(_json_or_empty(resp)).lower()
    return any(k in text for k in ("response_format", "json_schema", "structured output", "requested parameters", "no endpoints found"))


def _friendly_error(code: int, message: str, model: str) -> str:
    if code == 401:
        return "chave do OpenRouter inválida"
    if code == 402:
        return "sem créditos no OpenRouter (adicione créditos em openrouter.ai)"
    if code == 403:
        return f"o OpenRouter bloqueou a solicitação: {message}"
    if code == 404:
        return f"modelo {model} não encontrado no OpenRouter"
    if code == 408:
        return "o OpenRouter demorou demais para responder"
    if code == 429:
        return "limite de uso do OpenRouter atingido; tento de novo mais tarde"
    if code in (502, 503):
        return f"o modelo {model} está indisponível no OpenRouter agora"
    if code == 400:
        return f"requisição recusada pelo OpenRouter: {message}"
    return f"erro do OpenRouter ({code}): {message}"


def _extract_json(text: str) -> str:
    """Tira cercas de código e texto em volta do objeto JSON."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


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

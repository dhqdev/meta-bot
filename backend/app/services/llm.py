"""IA dos agentes: Claude direto na Anthropic ou qualquer modelo via OpenRouter.

- Respostas em JSON validado (structured outputs / ``response_format`` com JSON Schema).
- Cada agente tem o seu modelo (Nina, Gustavo e Aurora), escolhido por economia.
- Anthropic: prompt de sistema com cache e fallback automático no servidor (``fallbacks``).
- OpenRouter: modelo reserva se o principal falhar e custo real de cada chamada (``usage.cost``).
- Limite de chamadas por hora e orçamento diário em dólares (configuráveis na tela), valendo
  para os dois provedores.
- Cada chamada fica registrada (tokens e custo) por agente.

Provedor "auto": usa a Anthropic se houver chave dela; senão, o OpenRouter. Sem nenhuma chave,
``available()`` é falso e os agentes usam as regras sem IA.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TypeVar

import anthropic
import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select

from app import __version__
from app.config import get_settings
from app.db import session_scope
from app.kv import kv_set, secret_get
from app.models import AIUsage
from app.runtime import OPENROUTER_SUGGESTIONS, get_config

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

OPENROUTER_URL = "https://openrouter.ai/api/v1"
REPO_URL = "https://github.com/dhqdev/meta-bot"
# Modelos do OpenRouter em que o cache do prompt é explícito (cache_control).
OR_CACHE_PREFIXES = ("anthropic/",)
# Modelos do OpenRouter que aceitam o controle de raciocínio (reasoning.effort).
OR_REASONING_PREFIXES = ("anthropic/claude-opus-5", "anthropic/claude-sonnet-5", "anthropic/claude-fable-5", "google/gemini-3", "openai/gpt-5", "openai/o")
# Economia no OpenRouter: respostas curtas bastam para os formatos dos agentes.
OR_MAX_TOKENS = 8000
# Preços de referência no OpenRouter (US$ por milhão: entrada, saída; set/2026), usados quando o
# catálogo dele não responde. O custo real de cada chamada vem na resposta (usage.cost).
OR_REFERENCE_PRICES = {
    "deepseek/deepseek-v4-flash": (0.089, 0.177),
    "google/gemini-2.5-flash-lite": (0.10, 0.40),
    "google/gemini-3.1-flash-lite": (0.25, 1.50),
    "anthropic/claude-haiku-4.5": (1.0, 5.0),
    "anthropic/claude-sonnet-5.5": (2.0, 10.0),
    "anthropic/claude-opus-5.5": (4.0, 20.0),
}
MODELS_TTL = 6 * 3600

PROVIDER_NAMES = {"anthropic": "Anthropic", "openrouter": "OpenRouter"}
TIERS = ("manager", "news", "auditor")


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None  # custo informado pelo provedor (OpenRouter)


@dataclass
class LLMResult:
    ok: bool
    data: BaseModel | None = None
    error: str = ""
    model: str = ""
    cost_usd: float = 0.0
    provider: str = ""
    retry: bool = False  # vale tentar o modelo reserva (OpenRouter)


def estimate_cost(model: str, usage) -> float:
    price = PRICES.get(model, PRICES["claude-opus-5-5"])
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", None)
    cr = getattr(usage, "cache_read_tokens", 0) if cr is None else cr
    cw = getattr(usage, "cache_creation_input_tokens", None)
    cw = getattr(usage, "cache_write_tokens", 0) if cw is None else cw
    return (inp * price[0] + out * price[1] + (cr or 0) * price[2] + (cw or 0) * price[3]) / 1_000_000


def _anthropic_usage(usage) -> Usage | None:
    if usage is None:
        return None
    return Usage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


def _openrouter_usage(raw) -> Usage | None:
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
        self._client: anthropic.AsyncAnthropic | None = None
        self._client_key: str | None = None
        self._calls: list[float] = []
        self._transport = transport  # os testes injetam um transporte falso para o OpenRouter
        self._or_models: list[dict] = []
        self._or_models_at = 0.0

    # ------------------------------------------------------------- estado
    def anthropic_key(self) -> str | None:
        return secret_get("anthropic_api_key") or get_settings().anthropic_api_key or None

    def openrouter_key(self) -> str | None:
        return secret_get("openrouter_api_key") or get_settings().openrouter_api_key or None

    def provider(self) -> str | None:
        """Provedor que será usado agora (ou None se faltar chave)."""
        choice = get_config().ai_provider
        if choice == "anthropic":
            return "anthropic" if self.anthropic_key() else None
        if choice == "openrouter":
            return "openrouter" if self.openrouter_key() else None
        if self.anthropic_key():
            return "anthropic"
        if self.openrouter_key():
            return "openrouter"
        return None

    def available(self) -> bool:
        return bool(get_config().ai_enabled and self.provider())

    def status(self) -> dict:
        provider = self.provider()
        return {
            "available": self.available(),
            "provider": provider,
            "provider_name": PROVIDER_NAMES.get(provider or "", ""),
            "models": {tier: self.resolve_model(provider, tier) for tier in TIERS} if provider else {},
        }

    def resolve_model(self, provider: str | None, tier: str = "manager", model: str | None = None) -> str:
        """Modelo de cada agente no provedor: 'manager' (Gustavo), 'news' (Nina), 'auditor' (Aurora).

        Um ``model`` explícito vale só para o provedor dele (``fornecedor/modelo`` = OpenRouter)."""
        cfg = get_config()
        if provider == "openrouter":
            if model and "/" in model:
                return model
            return {"news": cfg.openrouter_news_model, "auditor": cfg.openrouter_auditor_model}.get(tier, cfg.openrouter_manager_model)
        if model and "/" not in model:
            return model
        return {"news": cfg.ai_news_model, "auditor": cfg.ai_auditor_model}.get(tier, cfg.ai_model)

    def _get_client(self) -> anthropic.AsyncAnthropic:
        key = self.anthropic_key()
        if not key:
            raise RuntimeError("sem chave da Anthropic")
        if self._client is None or self._client_key != key:
            self._client = anthropic.AsyncAnthropic(api_key=key, timeout=120.0, max_retries=2)
            self._client_key = key
        return self._client

    def _http(self, timeout: float = 120.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=OPENROUTER_URL, timeout=timeout, transport=self._transport)

    def _or_headers(self, key: str) -> dict:
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

    def _cost(self, provider: str, model: str, usage: Usage | None) -> float:
        if usage is None:
            return 0.0
        if usage.cost_usd is not None:
            return usage.cost_usd
        if provider == "openrouter":
            price = next((m for m in self._or_models if m["id"] == model), None)
            if price and price["prompt"] is not None and price["completion"] is not None:
                pin, pout = price["prompt"], price["completion"]
            elif model in OR_REFERENCE_PRICES:
                pin, pout = (v / 1_000_000 for v in OR_REFERENCE_PRICES[model])
            else:
                equivalent = _anthropic_equivalent(model)
                return estimate_cost(equivalent, usage) if equivalent in PRICES else 0.0
            return (usage.input_tokens + usage.cache_read_tokens) * pin + usage.output_tokens * pout
        return estimate_cost(model, usage)

    def _record(self, agent: str, purpose: str, model: str, usage: Usage | None, ok: bool, error: str = "", provider: str = "anthropic") -> float:
        cost = self._cost(provider, model, usage)
        tag = model if provider == "anthropic" else f"openrouter:{model}"
        try:
            with session_scope() as s:
                s.add(
                    AIUsage(
                        agent=agent,
                        purpose=purpose,
                        model=tag[:80],
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
        model: str | None = None,
        effort: str = "medium",
        max_tokens: int = 16000,
    ) -> LLMResult:
        if not get_config().ai_enabled:
            return LLMResult(False, error="IA desligada")
        provider = self.provider()
        if provider is None:
            return LLMResult(False, error="IA sem chave cadastrada (Anthropic ou OpenRouter)")
        problem = self._budget_problem()
        if problem:
            return LLMResult(False, error=problem, provider=provider)
        self._calls.append(time.time())
        chosen = self.resolve_model(provider, tier, model)
        if provider == "openrouter":
            res = await self._openrouter(agent, purpose, chosen, system, user, schema_model, effort, max_tokens)
        else:
            res = await self._anthropic(agent, purpose, chosen, system, user, schema_model, effort, max_tokens)
        res.provider = provider
        if res.ok:
            kv_set("ai_status", {"ok": True, "error": "", "at": _now(), "model": res.model, "provider": provider})
        return res

    async def _anthropic(self, agent, purpose, model, system, user, schema_model, effort, max_tokens) -> LLMResult:
        client = self._get_client()
        schema = schema_model.model_json_schema()
        output_config: dict = {"format": {"type": "json_schema", "schema": _strict(schema)}}
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }

        def rec(m, u, ok, err=""):
            return self._record(agent, purpose, m, u, ok, err, "anthropic")

        try:
            if model in EFFORT_MODELS:
                output_config["effort"] = effort
                response = await client.beta.messages.create(
                    **kwargs, output_config=output_config, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = await client.messages.create(**kwargs, output_config=output_config)
        except anthropic.AuthenticationError:
            kv_set("ai_status", {"ok": False, "error": "chave da Anthropic inválida", "at": _now(), "provider": "anthropic"})
            rec(model, None, False, "chave inválida")
            return LLMResult(False, error="chave da Anthropic inválida", model=model)
        except anthropic.PermissionDeniedError:
            rec(model, None, False, "sem permissão")
            return LLMResult(False, error="a chave não tem permissão para este modelo", model=model)
        except anthropic.NotFoundError:
            rec(model, None, False, "modelo não encontrado")
            return LLMResult(False, error=f"modelo {model} não encontrado", model=model)
        except anthropic.RateLimitError:
            rec(model, None, False, "limite de taxa")
            return LLMResult(False, error="limite de uso da Anthropic atingido; tento de novo mais tarde", model=model)
        except anthropic.BadRequestError as exc:
            rec(model, None, False, f"requisição inválida: {exc.message}")
            return LLMResult(False, error=f"requisição recusada: {exc.message}", model=model)
        except anthropic.APIStatusError as exc:
            rec(model, None, False, f"erro {exc.status_code}")
            return LLMResult(False, error=f"erro da Anthropic ({exc.status_code})", model=model)
        except anthropic.APIConnectionError:
            rec(model, None, False, "sem conexão")
            return LLMResult(False, error="sem conexão com a Anthropic", model=model)

        served = getattr(response, "model", model) or model
        usage = _anthropic_usage(getattr(response, "usage", None))
        if response.stop_reason == "refusal":
            cost = rec(served, usage, False, "recusa")
            return LLMResult(False, error="a IA recusou esta solicitação", model=served, cost_usd=cost)
        if response.stop_reason == "max_tokens":
            cost = rec(served, usage, False, "resposta cortada")
            return LLMResult(False, error="resposta da IA cortada (max_tokens)", model=served, cost_usd=cost)
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
        return self._parse(text, schema_model, served, usage, rec)

    async def _openrouter(self, agent, purpose, model, system, user, schema_model, effort, max_tokens) -> LLMResult:
        """Chamada no formato OpenAI (chat/completions) do OpenRouter, com modelo reserva."""
        fallback = get_config().openrouter_fallback_model
        res = await self._openrouter_once(agent, purpose, model, system, user, schema_model, effort, max_tokens)
        if not res.ok and res.retry and fallback and fallback != model:
            log.warning("OpenRouter: %s falhou (%s); tentando o reserva %s", model, res.error, fallback)
            res = await self._openrouter_once(agent, purpose, fallback, system, user, schema_model, effort, max_tokens)
        return res

    async def _openrouter_once(self, agent, purpose, model, system, user, schema_model, effort, max_tokens) -> LLMResult:
        key = self.openrouter_key() or ""
        schema = _strict(schema_model.model_json_schema())
        # O schema também vai no texto: modelos sem structured outputs seguem a instrução.
        guide = (
            "\n\nResponda SOMENTE com um objeto JSON válido (sem texto antes ou depois, sem ```), "
            f"seguindo exatamente este JSON Schema:\n{json.dumps(schema, ensure_ascii=False)}"
        )
        sys_content: str | list = system + guide
        if model.startswith(OR_CACHE_PREFIXES):
            sys_content = [{"type": "text", "text": system + guide, "cache_control": {"type": "ephemeral"}}]
        body: dict = {
            "model": model,
            "max_tokens": min(max_tokens, OR_MAX_TOKENS),
            "messages": [{"role": "system", "content": sys_content}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_model.__name__, "strict": True, "schema": schema},
            },
            "provider": {"require_parameters": True},
            "usage": {"include": True},
        }
        # Economia: raciocínio curto só onde o agente pede análise (gerente/auditora), nunca nas notícias.
        if effort in ("medium", "high") and model.startswith(OR_REASONING_PREFIXES):
            body["reasoning"] = {"effort": "low", "exclude": True}

        def rec(m, u, ok, err=""):
            return self._record(agent, purpose, m, u, ok, err, "openrouter")

        try:
            async with self._http() as http:
                resp = await http.post("/chat/completions", json=body, headers=self._or_headers(key))
                if resp.status_code in (400, 404) and _format_unsupported(resp):
                    # Nenhum provedor do modelo aceita JSON Schema: modo JSON simples (o schema segue no prompt).
                    body["response_format"] = {"type": "json_object"}
                    body.pop("provider", None)
                    resp = await http.post("/chat/completions", json=body, headers=self._or_headers(key))
        except httpx.HTTPError:
            rec(model, None, False, "sem conexão")
            return LLMResult(False, error="sem conexão com o OpenRouter", model=model)

        payload = _json_or_empty(resp)
        if resp.status_code != 200 or (payload.get("error") and not payload.get("choices")):
            message = _or_error_message(payload) or f"HTTP {resp.status_code}"
            code = resp.status_code if resp.status_code != 200 else _int((payload.get("error") or {}).get("code"), 500)
            friendly = _or_friendly_error(code, message, model)
            if code == 401:
                kv_set("ai_status", {"ok": False, "error": friendly, "at": _now(), "provider": "openrouter"})
            rec(model, _openrouter_usage(payload.get("usage")), False, f"{code}: {message}")
            res = LLMResult(False, error=friendly, model=model)
            # Chave e créditos resolvem-se com o dono; o resto (modelo fora do ar, sumiu, lotado) tenta o reserva.
            res.retry = code not in (401, 402)
            return res

        served = payload.get("model") or model
        usage = _openrouter_usage(payload.get("usage"))
        choices = payload.get("choices") or []
        if not choices:
            rec(served, usage, False, "sem resposta")
            return _retryable(LLMResult(False, error="o OpenRouter não devolveu resposta", model=served))
        choice = choices[0]
        if choice.get("error"):
            message = _or_error_message(choice) or "erro do provedor"
            cost = rec(served, usage, False, message)
            return _retryable(LLMResult(False, error=f"erro do provedor no OpenRouter: {message}", model=served, cost_usd=cost))
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
        res = self._parse(_extract_json(content), schema_model, served, usage, rec)
        if not res.ok:
            res.retry = True
        return res

    def _parse(self, text: str, schema_model, served: str, usage: Usage | None, rec) -> LLMResult:
        try:
            data = schema_model.model_validate_json(text)
        except (ValidationError, ValueError) as exc:
            cost = rec(served, usage, False, "JSON inválido")
            log.warning("resposta da IA fora do formato: %s", exc)
            return LLMResult(False, error="resposta da IA fora do formato", model=served, cost_usd=cost)
        cost = rec(served, usage, True)
        return LLMResult(True, data=data, model=served, cost_usd=cost)

    # ------------------------------------------------------------- chaves e catálogo
    async def check_key(self, key: str, provider: str = "anthropic") -> tuple[bool, str]:
        """Confere a chave sem gastar tokens."""
        if provider == "openrouter":
            return await self._check_openrouter_key(key)
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

    async def _check_openrouter_key(self, key: str) -> tuple[bool, str]:
        try:
            async with self._http(timeout=20.0) as http:
                resp = await http.get("/key", headers=self._or_headers(key))
                if resp.status_code == 404:
                    resp = await http.get("/auth/key", headers=self._or_headers(key))
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

    async def openrouter_models(self) -> list[dict]:
        """Catálogo do OpenRouter (público), com preço por token. Fica em cache por 6 h."""
        if self._or_models and time.time() - self._or_models_at < MODELS_TTL:
            return self._or_models
        models: list[dict] = []
        if get_settings().network_enabled or self._transport is not None:
            try:
                async with self._http(timeout=20.0) as http:
                    resp = await http.get("/models", headers={"User-Agent": f"meta-bot/{__version__}"})
                for m in _json_or_empty(resp).get("data") or []:
                    if not isinstance(m, dict) or not m.get("id"):
                        continue
                    pricing = m.get("pricing") or {}
                    params = m.get("supported_parameters") or []
                    models.append(
                        {
                            "id": str(m["id"]),
                            "name": str(m.get("name") or m["id"]),
                            "prompt": _to_float(pricing.get("prompt")),
                            "completion": _to_float(pricing.get("completion")),
                            "context": _int(m.get("context_length"), 0),
                            "structured": "structured_outputs" in params or "response_format" in params,
                        }
                    )
            except (httpx.HTTPError, ValueError, TypeError):
                log.warning("não consegui baixar o catálogo do OpenRouter")
        if models:
            self._or_models = models
            self._or_models_at = time.time()
            return models
        fallback = []
        for m in OPENROUTER_SUGGESTIONS:
            ref = OR_REFERENCE_PRICES.get(m)
            fallback.append(
                {
                    "id": m,
                    "name": f"{m} (preço de referência)",
                    "prompt": ref[0] / 1_000_000 if ref else None,
                    "completion": ref[1] / 1_000_000 if ref else None,
                    "context": 0,
                    "structured": True,
                }
            )
        return fallback

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


def _retryable(res: LLMResult) -> LLMResult:
    res.retry = True
    return res


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _anthropic_equivalent(or_model: str) -> str:
    """anthropic/claude-haiku-4.5 → claude-haiku-4-5 (para estimar custo sem o catálogo)."""
    return or_model.split("/", 1)[-1].split(":", 1)[0].replace(".", "-")


def _json_or_empty(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _or_error_message(payload: dict) -> str:
    err = payload.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or "")[:300]
    return str(err or "")[:300]


def _format_unsupported(resp: httpx.Response) -> bool:
    text = _or_error_message(_json_or_empty(resp)).lower()
    return any(k in text for k in ("response_format", "json_schema", "structured output", "requested parameters", "no endpoints found"))


def _or_friendly_error(code: int, message: str, model: str) -> str:
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

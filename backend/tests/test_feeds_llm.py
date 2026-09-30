import asyncio
from types import SimpleNamespace

from app.agents.news import AINewsBatch, keyword_classify
from app.kv import secret_set
from app.runtime import update_config
from app.services.feeds import parse_calendar, parse_feed
from app.services.llm import LLMService, _strict, estimate_cost

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>X</title>
<item><title>Fed signals rate hike as U.S. inflation surges</title><link>https://ex.com/a</link>
<pubDate>Tue, 29 Sep 2026 12:00:00 GMT</pubDate><description><![CDATA[<p>Powell said...</p>]]></description></item>
<item><title>Sem link</title></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Y</title>
<entry><title>Ouro dispara com guerra</title><link href="https://ex.com/b"/><updated>2026-09-29T10:00:00Z</updated><summary>Tensões</summary></entry>
</feed>"""

EVIL = """<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]><rss><channel><item><title>&lol2;</title><link>https://ex.com/c</link></item></channel></rss>"""


def test_parse_rss_and_atom():
    items = parse_feed(RSS, "Teste")
    assert len(items) == 1 and items[0].url == "https://ex.com/a"
    assert items[0].summary == "Powell said..."
    atom = parse_feed(ATOM, "Atom", "pt")
    assert atom[0].title == "Ouro dispara com guerra" and atom[0].lang == "pt"


def test_parse_rejects_entity_bombs():
    assert parse_feed(EVIL, "Ruim") == []


def test_calendar_parse():
    data = [
        {"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-10-02T08:30:00-04:00", "impact": "High", "forecast": "150K", "previous": "142K"},
        {"title": "", "country": "EUR", "date": "x"},
    ]
    items = parse_calendar(data)
    assert len(items) == 1
    assert items[0].ts.hour == 12 and items[0].impact == "High" and len(items[0].uid) == 64


def test_keyword_classifier():
    assets, impact, category = keyword_classify("Fed signals rate hike as U.S. inflation surges", "")
    assert "USD" in assets and impact == "high"
    assets, impact, _ = keyword_classify("Ouro dispara com guerra no Oriente Médio", "")
    assert "XAU" in assets and assets["XAU"] > 0 and impact == "high"


def test_strict_schema_closes_objects():
    schema = _strict(AINewsBatch.model_json_schema())
    item = schema["properties"]["items"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"id", "relevant", "impact", "category", "assets", "summary_pt"}
    assert "$defs" not in schema and "$ref" not in str(schema)


class FakeMessages:
    def __init__(self, text, stop="end_turn"):
        self.text = text
        self.stop = stop
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)], stop_reason=self.stop, usage=usage, model=kwargs["model"])


def fake_client(text, stop="end_turn"):
    msgs = FakeMessages(text, stop)
    return SimpleNamespace(beta=SimpleNamespace(messages=msgs), messages=msgs), msgs


def test_llm_structured_output_and_usage(monkeypatch):
    secret_set("anthropic_api_key", "sk-ant-test")
    client, msgs = fake_client('{"items":[{"id":1,"relevant":true,"impact":"high","category":"monetary","assets":[{"code":"USD","sentiment":0.7}],"summary_pt":"Fed duro"}]}')
    svc = LLMService()
    monkeypatch.setattr(svc, "_get_client", lambda: client)
    res = asyncio.run(svc.complete_json(agent="news", purpose="teste", model="claude-opus-5-5", system="s", user="u", schema_model=AINewsBatch, effort="low"))
    assert res.ok and res.data.items[0].assets[0].code == "USD"
    call = msgs.calls[0]
    assert call["fallbacks"] == "default" and call["output_config"]["effort"] == "low"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert svc.spent_today() > 0
    assert estimate_cost("claude-opus-5-5", SimpleNamespace(input_tokens=1_000_000, output_tokens=0)) == 4.0


def test_llm_haiku_skips_effort_and_refusal_is_handled(monkeypatch):
    secret_set("anthropic_api_key", "sk-ant-test")
    client, msgs = fake_client("{}", stop="refusal")
    svc = LLMService()
    monkeypatch.setattr(svc, "_get_client", lambda: client)
    res = asyncio.run(svc.complete_json(agent="news", purpose="t", model="claude-haiku-4-5", system="s", user="u", schema_model=AINewsBatch))
    assert not res.ok and "recusou" in res.error
    assert "effort" not in msgs.calls[0]["output_config"] and "fallbacks" not in msgs.calls[0]


def test_llm_budget_limits(monkeypatch):
    secret_set("anthropic_api_key", "sk-ant-test")
    update_config({"ai_max_calls_per_hour": 1})
    client, _ = fake_client('{"items":[]}')
    svc = LLMService()
    monkeypatch.setattr(svc, "_get_client", lambda: client)
    kw = dict(agent="news", purpose="t", model="claude-opus-5-5", system="s", user="u", schema_model=AINewsBatch)
    assert asyncio.run(svc.complete_json(**kw)).ok
    second = asyncio.run(svc.complete_json(**kw))
    assert not second.ok and "limite" in second.error


def test_llm_unavailable_without_key():
    assert not LLMService().available()


# ------------------------------------------------------------------ OpenRouter
import json as _json  # noqa: E402

import httpx  # noqa: E402

NEWS_JSON = '{"items":[{"id":1,"relevant":true,"impact":"high","category":"monetary","assets":[{"code":"USD","sentiment":0.7}],"summary_pt":"Fed duro"}]}'


def or_transport(handler_map):
    """Transporte falso do OpenRouter: responde por caminho e guarda as requisições."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content) if request.content else None
        calls.append((request.url.path, body, dict(request.headers)))
        fn = handler_map[request.url.path]
        return fn(body, len([c for c in calls if c[0] == request.url.path]))

    return httpx.MockTransport(handler), calls


def completion(content, model, cost=0.0004, finish="stop"):
    return httpx.Response(
        200,
        json={
            "id": "gen-1",
            "model": model,
            "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 300, "prompt_tokens_details": {"cached_tokens": 200}, "cost": cost},
        },
    )


def test_openrouter_is_used_with_only_its_key_and_per_agent_models():
    secret_set("openrouter_api_key", "sk-or-test")
    transport, calls = or_transport({"/api/v1/chat/completions": lambda body, n: completion(NEWS_JSON, body["model"])})
    svc = LLMService(transport=transport)
    assert svc.provider() == "openrouter" and svc.available()
    res = asyncio.run(svc.complete_json(agent="news", purpose="t", tier="news", system="s", user="u", schema_model=AINewsBatch, effort="low"))
    assert res.ok and res.provider == "openrouter" and res.data.items[0].assets[0].code == "USD"
    path, body, headers = calls[0]
    assert body["model"] == "deepseek/deepseek-v4-flash"  # modelo econômico predefinido da Nina
    assert body["response_format"]["type"] == "json_schema" and body["response_format"]["json_schema"]["strict"] is True
    assert body["max_tokens"] <= 8000 and "reasoning" not in body  # notícias: sem raciocínio (economia)
    assert headers["authorization"] == "Bearer sk-or-test" and headers["x-title"] == "Meta-Bot"
    assert abs(res.cost_usd - 0.0004) < 1e-9 and svc.spent_today() >= 0.0004
    status = svc.status()
    assert status["models"] == {"manager": "google/gemini-3.1-flash-lite", "news": "deepseek/deepseek-v4-flash", "auditor": "anthropic/claude-haiku-4.5"}


def test_openrouter_manager_gets_short_reasoning_and_anthropic_cache():
    secret_set("openrouter_api_key", "sk-or-test")
    update_config({"openrouter_manager_model": "anthropic/claude-sonnet-5.5"})
    transport, calls = or_transport({"/api/v1/chat/completions": lambda body, n: completion("```json\n" + NEWS_JSON + "\n```", body["model"])})
    svc = LLMService(transport=transport)
    res = asyncio.run(svc.complete_json(agent="manager", purpose="t", system="s", user="u", schema_model=AINewsBatch, effort="high"))
    assert res.ok  # cercas de código são removidas
    body = calls[0][1]
    assert body["reasoning"] == {"effort": "low", "exclude": True}
    assert body["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_openrouter_falls_back_to_json_mode_and_reserve_model():
    secret_set("openrouter_api_key", "sk-or-test")

    def chat(body, n):
        if body["model"] == "deepseek/deepseek-v4-flash":
            if body["response_format"]["type"] == "json_schema":
                return httpx.Response(404, json={"error": {"code": 404, "message": "No endpoints found that can handle the requested parameters."}})
            return httpx.Response(503, json={"error": {"code": 503, "message": "provider down"}})
        return completion(NEWS_JSON, body["model"])

    transport, calls = or_transport({"/api/v1/chat/completions": chat})
    svc = LLMService(transport=transport)
    res = asyncio.run(svc.complete_json(agent="news", purpose="t", tier="news", system="s", user="u", schema_model=AINewsBatch))
    assert res.ok and res.model == "google/gemini-3.1-flash-lite"
    kinds = [(c[1]["model"], c[1]["response_format"]["type"]) for c in calls]
    assert kinds == [("deepseek/deepseek-v4-flash", "json_schema"), ("deepseek/deepseek-v4-flash", "json_object"), ("google/gemini-3.1-flash-lite", "json_schema")]


def test_openrouter_errors_are_friendly_and_no_credit_does_not_retry():
    secret_set("openrouter_api_key", "sk-or-test")
    transport, calls = or_transport({"/api/v1/chat/completions": lambda body, n: httpx.Response(402, json={"error": {"code": 402, "message": "Insufficient credits"}})})
    svc = LLMService(transport=transport)
    res = asyncio.run(svc.complete_json(agent="news", purpose="t", tier="news", system="s", user="u", schema_model=AINewsBatch))
    assert not res.ok and "créditos" in res.error and len(calls) == 1


def test_openrouter_key_check_and_catalog():
    transport, _ = or_transport(
        {
            "/api/v1/key": lambda body, n: httpx.Response(200, json={"data": {"label": "x", "limit_remaining": 4.5}}),
            "/api/v1/models": lambda body, n: httpx.Response(
                200,
                json={"data": [{"id": "deepseek/deepseek-v4-flash", "name": "DeepSeek V4 Flash", "pricing": {"prompt": "0.000000089", "completion": "0.000000177"}, "context_length": 1000000, "supported_parameters": ["structured_outputs"]}]},
            ),
        }
    )
    svc = LLMService(transport=transport)
    ok, message = asyncio.run(svc.check_key("sk-or-good", "openrouter"))
    assert ok and "4.50" in message
    models = asyncio.run(svc.openrouter_models())
    assert models[0]["id"] == "deepseek/deepseek-v4-flash" and models[0]["structured"] and models[0]["prompt"] > 0

    bad, _ = or_transport({"/api/v1/key": lambda body, n: httpx.Response(401, json={"error": {"message": "No auth"}})})
    ok, message = asyncio.run(LLMService(transport=bad).check_key("sk-or-bad", "openrouter"))
    assert not ok and "inválida" in message


def test_provider_choice_prefers_anthropic_in_auto_and_respects_explicit_choice():
    secret_set("openrouter_api_key", "sk-or-test")
    secret_set("anthropic_api_key", "sk-ant-test")
    svc = LLMService()
    assert svc.provider() == "anthropic"
    update_config({"ai_provider": "openrouter"})
    assert svc.provider() == "openrouter"
    assert svc.resolve_model("anthropic", "news") == "claude-haiku-4-5"
    secret_set("openrouter_api_key", None)
    assert svc.provider() is None and not svc.available()

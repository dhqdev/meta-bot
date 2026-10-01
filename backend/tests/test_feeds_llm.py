import asyncio

from app.agents.news import AINewsBatch, keyword_classify
from app.kv import secret_set
from app.runtime import update_config
from app.services.feeds import parse_calendar, parse_feed
from app.services.llm import AGENT_MODELS, FALLBACK_MODEL, LLMService, _strict

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



# ------------------------------------------------------------------ OpenRouter (única IA)
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


def test_llm_unavailable_without_key():
    svc = LLMService()
    assert not svc.available()
    res = asyncio.run(svc.complete_json(agent="news", purpose="t", tier="news", system="s", user="u", schema_model=AINewsBatch))
    assert not res.ok and "chave" in res.error


def test_each_agent_has_a_fixed_model():
    secret_set("openrouter_api_key", "sk-or-test")
    transport, calls = or_transport({"/api/v1/chat/completions": lambda body, n: completion(NEWS_JSON, body["model"])})
    svc = LLMService(transport=transport)
    assert svc.available()
    for tier in ("news", "manager", "daily"):
        res = asyncio.run(svc.complete_json(agent="x", purpose="t", tier=tier, system="s", user="u", schema_model=AINewsBatch, effort="low"))
        assert res.ok and res.data.items[0].assets[0].code == "USD"
    models = [c[1]["model"] for c in calls]
    assert models == [AGENT_MODELS[t]["model"] for t in ("news", "manager", "daily")]
    assert models[0] == "deepseek/deepseek-v4-flash"
    body, headers = calls[0][1], calls[0][2]
    assert body["response_format"]["type"] == "json_schema" and body["response_format"]["json_schema"]["strict"] is True
    assert body["max_tokens"] <= 8000 and "reasoning" not in body  # notícias: sem raciocínio (economia)
    assert headers["authorization"] == "Bearer sk-or-test" and headers["x-title"] == "Meta-Bot"
    assert svc.spent_today() >= 0.0012
    assert svc.status()["models"]["daily"]["model"] == "anthropic/claude-haiku-4.5"


def test_manager_gets_short_reasoning_and_code_fences_are_removed():
    secret_set("openrouter_api_key", "sk-or-test")
    transport, calls = or_transport({"/api/v1/chat/completions": lambda body, n: completion("```json\n" + NEWS_JSON + "\n```", body["model"])})
    svc = LLMService(transport=transport)
    res = asyncio.run(svc.complete_json(agent="manager", purpose="t", tier="manager", system="s", user="u", schema_model=AINewsBatch, effort="high"))
    assert res.ok
    assert calls[0][1]["reasoning"] == {"effort": "low", "exclude": True}  # gemini-3.x aceita raciocínio


def test_daily_prompt_uses_cache_control():
    secret_set("openrouter_api_key", "sk-or-test")
    transport, calls = or_transport({"/api/v1/chat/completions": lambda body, n: completion(NEWS_JSON, body["model"])})
    svc = LLMService(transport=transport)
    assert asyncio.run(svc.complete_json(agent="auditor", purpose="t", tier="daily", system="s", user="u", schema_model=AINewsBatch)).ok
    assert calls[0][1]["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_falls_back_to_json_mode_and_reserve_model():
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
    assert res.ok and res.model == FALLBACK_MODEL
    kinds = [(c[1]["model"], c[1]["response_format"]["type"]) for c in calls]
    assert kinds == [("deepseek/deepseek-v4-flash", "json_schema"), ("deepseek/deepseek-v4-flash", "json_object"), (FALLBACK_MODEL, "json_schema")]


def test_errors_are_friendly_and_no_credit_does_not_retry():
    secret_set("openrouter_api_key", "sk-or-test")
    transport, calls = or_transport({"/api/v1/chat/completions": lambda body, n: httpx.Response(402, json={"error": {"code": 402, "message": "Insufficient credits"}})})
    svc = LLMService(transport=transport)
    res = asyncio.run(svc.complete_json(agent="news", purpose="t", tier="news", system="s", user="u", schema_model=AINewsBatch))
    assert not res.ok and "créditos" in res.error and len(calls) == 1


def test_refusal_and_cut_answers_are_handled():
    secret_set("openrouter_api_key", "sk-or-test")
    transport, _ = or_transport({"/api/v1/chat/completions": lambda body, n: completion("{}", body["model"], finish="content_filter")})
    res = asyncio.run(LLMService(transport=transport).complete_json(agent="news", purpose="t", tier="news", system="s", user="u", schema_model=AINewsBatch))
    assert not res.ok and "recusou" in res.error


def test_budget_limits():
    secret_set("openrouter_api_key", "sk-or-test")
    update_config({"ai_max_calls_per_hour": 1})
    transport, _ = or_transport({"/api/v1/chat/completions": lambda body, n: completion('{"items":[]}', body["model"])})
    svc = LLMService(transport=transport)
    kw = dict(agent="news", purpose="t", tier="news", system="s", user="u", schema_model=AINewsBatch)
    assert asyncio.run(svc.complete_json(**kw)).ok
    second = asyncio.run(svc.complete_json(**kw))
    assert not second.ok and "limite" in second.error


def test_key_check():
    good, _ = or_transport({"/api/v1/key": lambda body, n: httpx.Response(200, json={"data": {"label": "x", "limit_remaining": 4.5}})})
    ok, message = asyncio.run(LLMService(transport=good).check_key("sk-or-good"))
    assert ok and "4.50" in message
    bad, _ = or_transport({"/api/v1/key": lambda body, n: httpx.Response(401, json={"error": {"message": "No auth"}})})
    ok, message = asyncio.run(LLMService(transport=bad).check_key("sk-or-bad"))
    assert not ok and "inválida" in message

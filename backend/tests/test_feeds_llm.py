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

"""Leitura de notícias (RSS/Atom) e do calendário econômico (ForexFactory).

Conteúdo externo é sempre tratado como **dado**: o XML é lido com defusedxml e
o texto nunca vira instrução para a IA.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from defusedxml import ElementTree as DET

log = logging.getLogger("metabot.feeds")

USER_AGENT = "Mozilla/5.0 (compatible; Meta-Bot/1.0; +https://github.com/dhqdev/meta-bot)"
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


@dataclass
class FeedItem:
    source: str
    title: str
    url: str
    summary: str
    published_at: datetime
    lang: str


def clean_text(value: str | None, limit: int = 600) -> str:
    if not value:
        return ""
    text = html.unescape(_TAG.sub(" ", value))
    return _WS.sub(" ", text).strip()[:limit]


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(xml_text: str, source: str, lang: str = "en") -> list[FeedItem]:
    """Aceita RSS 2.0 e Atom."""
    try:
        root = DET.fromstring(xml_text)
    except Exception as exc:  # XML inválido ou malicioso
        log.warning("feed %s inválido: %s", source, exc.__class__.__name__)
        return []
    items: list[FeedItem] = []
    now = datetime.now(timezone.utc)
    for node in root.iter():
        name = _local(node.tag)
        if name not in ("item", "entry"):
            continue
        fields: dict[str, str] = {}
        link = ""
        for child in node:
            cname = _local(child.tag)
            if cname == "link":
                link = child.attrib.get("href") or (child.text or "")
            elif cname in ("title", "description", "summary", "content", "encoded", "pubdate", "published", "updated", "date"):
                fields.setdefault(cname, child.text or "")
        title = clean_text(fields.get("title"), 300)
        link = link.strip()
        if not title or not link.startswith(("http://", "https://")):
            continue
        published = (
            _parse_date(fields.get("pubdate"))
            or _parse_date(fields.get("published"))
            or _parse_date(fields.get("updated"))
            or _parse_date(fields.get("date"))
            or now
        )
        if published > now:
            published = now
        summary = clean_text(fields.get("description") or fields.get("summary") or fields.get("encoded") or fields.get("content"))
        items.append(FeedItem(source, title, link[:700], summary, published, lang))
    return items


async def fetch_feed(client: httpx.AsyncClient, name: str, url: str, lang: str) -> list[FeedItem]:
    try:
        resp = await client.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml"})
    except httpx.HTTPError as exc:
        log.info("feed %s indisponível: %s", name, exc.__class__.__name__)
        return []
    if resp.status_code != 200 or len(resp.content) > 5_000_000:
        log.info("feed %s respondeu %s", name, resp.status_code)
        return []
    return parse_feed(resp.text, name, lang)


# ------------------------------------------------------------- calendário
@dataclass
class CalendarItem:
    uid: str
    title: str
    currency: str
    ts: datetime
    impact: str
    forecast: str
    previous: str
    actual: str


def parse_calendar(data: list[dict]) -> list[CalendarItem]:
    out: list[CalendarItem] = []
    for row in data if isinstance(data, list) else []:
        try:
            title = str(row.get("title", "")).strip()[:255]
            currency = str(row.get("country", "")).strip().upper()[:10]
            ts = _parse_date(str(row.get("date", "")))
            impact = str(row.get("impact", "")).strip().title()[:10]
        except AttributeError:
            continue
        if not title or not currency or ts is None:
            continue
        uid = hashlib.sha256(f"{title}|{currency}|{ts.isoformat()}".encode()).hexdigest()[:64]
        out.append(
            CalendarItem(
                uid=uid,
                title=title,
                currency=currency,
                ts=ts,
                impact=impact or "Low",
                forecast=str(row.get("forecast", "") or "")[:40],
                previous=str(row.get("previous", "") or "")[:40],
                actual=str(row.get("actual", "") or "")[:40],
            )
        )
    return out


async def fetch_calendar(client: httpx.AsyncClient, url: str) -> list[CalendarItem]:
    try:
        resp = await client.get(url, headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as exc:
        log.info("calendário indisponível: %s", exc.__class__.__name__)
        return []
    if resp.status_code != 200:
        log.info("calendário respondeu %s", resp.status_code)
        return []
    try:
        return parse_calendar(resp.json())
    except ValueError:
        return []

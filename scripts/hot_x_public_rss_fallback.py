#!/usr/bin/env python3
"""Publish fresh direct X links from public Nitter-compatible RSS feeds.

This is an emergency fallback for installations where X recent-search API returns
HTTP 402. It never invents status IDs: every public feed link must contain a real
``/<username>/status/<numeric-id>`` path, which is converted to x.com.
"""
from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from hot_x_items import TOTAL_ITEMS, duplicate_free, is_direct_post, valid_item

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "hot_tweets.json"
STATUS = ROOT / "data" / "hot_x_public_fallback_status.json"
MAX_AGE_DAYS = 14
MIN_ITEMS = 4
UA = "BriefRoomsHotXPublicFallback/1.0"

INSTANCES = (
    "https://nitter.app",
    "https://xcancel.com",
    "https://nitter.poast.org",
)

ACCOUNTS: tuple[dict[str, str], ...] = (
    {
        "username": "NATO",
        "category": "geopolitics",
        "label_pl": "GEOPOLITYKA",
        "label_en": "GEOPOLITICS",
        "title_pl": "NATO: najnowszy komunikat o bezpieczeństwie",
        "comment_pl": "Najnowszy bezpośredni wpis z oficjalnego konta NATO. Dotyczy bezpieczeństwa, obronności lub współpracy sojuszniczej i prowadzi do oryginalnego postu na X.",
        "image": "/assets/hot-x/topic-geopolitics.svg",
    },
    {
        "username": "SecGenNATO",
        "category": "security",
        "label_pl": "BEZPIECZEŃSTWO",
        "label_en": "SECURITY",
        "title_pl": "Sekretarz generalny NATO: najnowszy wpis",
        "comment_pl": "Bezpośredni wpis sekretarza generalnego NATO dotyczący bezpieczeństwa międzynarodowego, obronności albo relacji sojuszniczych. Link otwiera oryginał na X.",
        "image": "/assets/hot-x/topic-geopolitics.svg",
    },
    {
        "username": "OpenAI",
        "category": "ai",
        "label_pl": "SZTUCZNA INTELIGENCJA",
        "label_en": "ARTIFICIAL INTELLIGENCE",
        "title_pl": "OpenAI: najnowsza aktualizacja dotycząca AI",
        "comment_pl": "Najnowszy bezpośredni komunikat OpenAI o modelach, produktach, badaniach lub bezpieczeństwie sztucznej inteligencji. Karta prowadzi do oryginalnego wpisu na X.",
        "image": "/assets/hot-x/topic-ai-tech.svg",
    },
    {
        "username": "OpenAIDevs",
        "category": "ai-tools",
        "label_pl": "AI I NARZĘDZIA",
        "label_en": "AI & TOOLS",
        "title_pl": "OpenAI Developers: nowość dla twórców aplikacji",
        "comment_pl": "Bezpośredni wpis zespołu OpenAI Developers o API, narzędziach i funkcjach dla programistów. Link prowadzi do pełnego, oryginalnego komunikatu na X.",
        "image": "/assets/hot-x/topic-ai-tech.svg",
    },
    {
        "username": "CoinDesk",
        "category": "crypto",
        "label_pl": "KRYPTO",
        "label_en": "CRYPTO",
        "title_pl": "CoinDesk: najnowszy temat z rynku kryptowalut",
        "comment_pl": "Najnowszy bezpośredni wpis redakcji CoinDesk o Bitcoinie, Ethereum, regulacjach lub rynku aktywów cyfrowych. Karta otwiera oryginalny post na X.",
        "image": "/assets/hot-x/topic-crypto.svg",
    },
    {
        "username": "federalreserve",
        "category": "macro",
        "label_pl": "FED I MAKRO",
        "label_en": "FED & MACRO",
        "title_pl": "Rezerwa Federalna: najnowszy oficjalny komunikat",
        "comment_pl": "Bezpośredni wpis Rezerwy Federalnej dotyczący polityki pieniężnej, gospodarki lub działań banku centralnego. Link prowadzi do oficjalnego postu na X.",
        "image": "/assets/hot-x/topic-macro-rates.svg",
    },
    {
        "username": "ecb",
        "category": "central-banks",
        "label_pl": "BANKI CENTRALNE",
        "label_en": "CENTRAL BANKS",
        "title_pl": "Europejski Bank Centralny: najnowszy komunikat",
        "comment_pl": "Bezpośredni wpis Europejskiego Banku Centralnego o inflacji, stopach procentowych, euro lub stabilności finansowej. Karta prowadzi do oryginału na X.",
        "image": "/assets/hot-x/topic-macro-rates.svg",
    },
    {
        "username": "WHO",
        "category": "health",
        "label_pl": "ZDROWIE",
        "label_en": "HEALTH",
        "title_pl": "WHO: najnowszy komunikat dotyczący zdrowia",
        "comment_pl": "Najnowszy bezpośredni wpis Światowej Organizacji Zdrowia o profilaktyce, chorobach, bezpieczeństwie zdrowotnym lub działaniach międzynarodowych.",
        "image": "/assets/hot-x/topic-health.svg",
    },
    {
        "username": "NASA",
        "category": "science",
        "label_pl": "NAUKA I KOSMOS",
        "label_en": "SCIENCE & SPACE",
        "title_pl": "NASA: najnowszy wpis o nauce i kosmosie",
        "comment_pl": "Bezpośredni wpis NASA o misjach kosmicznych, badaniach, obserwacjach lub nowych technologiach. Link otwiera pełny komunikat na oficjalnym koncie X.",
        "image": "/assets/hot-x/topic-science-space.svg",
    },
    {
        "username": "NvidiaAI",
        "category": "technology",
        "label_pl": "TECHNOLOGIE",
        "label_en": "TECHNOLOGY",
        "title_pl": "NVIDIA AI: najnowsza wiadomość technologiczna",
        "comment_pl": "Bezpośredni wpis NVIDIA AI dotyczący układów, modeli, infrastruktury lub zastosowań sztucznej inteligencji. Karta prowadzi do oryginalnego postu na X.",
        "image": "/assets/hot-x/topic-ai-tech.svg",
    },
    {
        "username": "EU_Commission",
        "category": "europe",
        "label_pl": "EUROPA",
        "label_en": "EUROPE",
        "title_pl": "Komisja Europejska: najnowszy oficjalny wpis",
        "comment_pl": "Bezpośredni komunikat Komisji Europejskiej dotyczący polityki, gospodarki, bezpieczeństwa lub regulacji Unii Europejskiej. Link otwiera oryginał na X.",
        "image": "/assets/hot-x/topic-geopolitics.svg",
    },
    {
        "username": "ZelenskyyUa",
        "category": "ukraine",
        "label_pl": "UKRAINA",
        "label_en": "UKRAINE",
        "title_pl": "Prezydent Ukrainy: najnowszy komunikat",
        "comment_pl": "Bezpośredni wpis prezydenta Ukrainy dotyczący wojny, dyplomacji, bezpieczeństwa lub wsparcia międzynarodowego. Karta prowadzi do oryginału na X.",
        "image": "/assets/hot-x/topic-geopolitics.svg",
    },
)

STATUS_RE = re.compile(r"/(?:[^/]+)/status/(\d+)", re.I)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def clean_text(value: object, limit: int = 700) -> str:
    text = html.unescape(TAG_RE.sub(" ", str(value or "")))
    text = SPACE_RE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit + 1].rsplit(" ", 1)[0].rstrip() + "…"


def fetch(url: str) -> bytes | None:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(request, timeout=20) as response:
            if 200 <= response.status < 300:
                return response.read()
    except Exception as exc:
        print(f"WARN public Hot X fetch failed: {url} :: {exc}", file=sys.stderr)
    return None


def parse_date(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def direct_x_url(username: str, value: object) -> str:
    match = STATUS_RE.search(str(value or ""))
    if not match:
        return ""
    return f"https://x.com/{username}/status/{match.group(1)}"


def parse_rss(raw: bytes, account: dict[str, str]) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    output: list[dict[str, Any]] = []
    username = account["username"]
    for node in root.findall(".//item")[:20]:
        link = str(node.findtext("link") or node.findtext("guid") or "")
        tweet_url = direct_x_url(username, link)
        if not is_direct_post(tweet_url):
            continue
        published = parse_date(node.findtext("pubDate"))
        if published and published < cutoff:
            continue
        raw_title = clean_text(node.findtext("title"), 700)
        raw_description = clean_text(node.findtext("description"), 700)
        text = raw_title or raw_description
        if not text or text.lower().startswith(("rt by ", "retweet by ")):
            continue
        if text.startswith("R to @") or text.startswith("Replying to @"):
            continue
        title_en = clean_text(text, 115)
        comment_en = clean_text(raw_description or text, 560)
        if len(comment_en) < 80:
            comment_en = clean_text(
                f"{comment_en} Direct public post from the official or editorial @{username} account on X.",
                560,
            )
        item = {
            "category": account["category"],
            "label_pl": account["label_pl"],
            "label_en": account["label_en"],
            "tweet_url": tweet_url,
            "search_url": "",
            "image": account["image"],
            "title_pl": account["title_pl"],
            "title_en": title_en,
            "comment_pl": account["comment_pl"],
            "comment_en": comment_en,
            "summary_pl": account["comment_pl"],
            "summary_en": comment_en,
            "source_pl": f"X / @{username}",
            "source_en": f"X / @{username}",
            "link_kind": "x_post",
            "selected_by": "public-nitter-rss-fallback",
            "x_post_created_at": published.isoformat() if published else "",
        }
        if valid_item(item):
            output.append(item)
    return output


def newest_for_account(account: dict[str, str]) -> tuple[dict[str, Any] | None, str]:
    errors: list[str] = []
    for base in INSTANCES:
        url = f"{base.rstrip('/')}/{account['username']}/rss"
        raw = fetch(url)
        if not raw:
            errors.append(f"{base}: fetch failed")
            continue
        candidates = parse_rss(raw, account)
        if candidates:
            candidates.sort(key=lambda item: item.get("x_post_created_at") or "", reverse=True)
            return candidates[0], base
        errors.append(f"{base}: no recent direct item")
    return None, "; ".join(errors)


def main() -> None:
    old_items: list[dict[str, Any]] = []
    old_urls: set[str] = set()
    try:
        old_payload = json.loads(OUT.read_text(encoding="utf-8"))
        old_items = [item for item in old_payload.get("items") or [] if isinstance(item, dict)]
        old_urls = {str(item.get("tweet_url") or "") for item in old_items}
    except Exception:
        pass

    selected: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}
    seen_urls: set[str] = set()
    for account in ACCOUNTS:
        item, detail = newest_for_account(account)
        diagnostics[account["username"]] = {
            "result": "selected" if item else "unavailable",
            "source": detail,
            "url": item.get("tweet_url") if item else "",
            "created_at": item.get("x_post_created_at") if item else "",
        }
        if not item or item["tweet_url"] in seen_urls:
            continue
        seen_urls.add(item["tweet_url"])
        selected.append(item)

    selected.sort(key=lambda item: item.get("x_post_created_at") or "", reverse=True)
    selected = selected[:TOTAL_ITEMS]
    fresh_urls = {item["tweet_url"] for item in selected} - old_urls
    status = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "mode": "public-nitter-rss-fallback",
        "selected_count": len(selected),
        "fresh_url_count": len(fresh_urls),
        "accounts": diagnostics,
    }
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if len(selected) < MIN_ITEMS:
        raise RuntimeError(f"public Hot X fallback found only {len(selected)} valid direct posts")
    if not fresh_urls:
        raise RuntimeError("public Hot X fallback found no URLs newer than the current feed")
    if not duplicate_free(selected) or not all(valid_item(item) for item in selected):
        raise RuntimeError("public Hot X fallback produced an invalid or duplicate feed")

    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    payload = {
        "updated_at": now,
        "mode": "public-nitter-rss-direct-posts",
        "method_pl": (
            "Awaryjne odświeżenie korzysta z publicznych kanałów RSS profili X. Każda karta zawiera "
            "rzeczywisty identyfikator statusu i prowadzi bezpośrednio do oryginalnego wpisu na x.com."
        ),
        "method_en": (
            "Emergency refresh uses public RSS feeds for X profiles. Every card contains a real numeric "
            "status identifier and links directly to the original post on x.com."
        ),
        "initial_visible_items": min(4, len(selected)),
        "target_items": len(selected),
        "items": selected,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Published {len(selected)} public RSS Hot X posts; {len(fresh_urls)} URLs are new")


if __name__ == "__main__":
    main()

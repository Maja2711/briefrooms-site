#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "hot_tweets.json"
WARSAW = timezone(timedelta(hours=2))

CATEGORIES = [
    {
        "category": "economy",
        "label_pl": "EKONOMIA",
        "label_en": "ECONOMY",
        "query": "Fed inflation rates dollar markets Reuters OR Bloomberg",
        "fallback_title_en": "Markets watch Fed, rates and inflation",
        "fallback_title_pl": "Rynki śledzą Fed, stopy i inflację",
        "fallback_summary_en": "Hot economy topic refreshed automatically. Open X to see the latest discussion around rates, inflation and market policy.",
        "fallback_summary_pl": "Gorący temat ekonomiczny odświeżany automatycznie. Otwórz X, aby zobaczyć najnowszą dyskusję o stopach, inflacji i polityce rynkowej.",
        "image": "/assets/hot-x/fed-market.svg",
    },
    {
        "category": "politics",
        "label_pl": "POLITYKA",
        "label_en": "POLITICS",
        "query": "US politics Supreme Court White House geopolitical risk Reuters BBC",
        "fallback_title_en": "Politics and geopolitical risk dominate discussion",
        "fallback_title_pl": "Polityka i ryzyko geopolityczne dominują w dyskusji",
        "fallback_summary_en": "Hot politics topic refreshed automatically. Open X to see the latest posts around policy, courts and geopolitical risk.",
        "fallback_summary_pl": "Gorący temat polityczny odświeżany automatycznie. Otwórz X, aby zobaczyć najnowsze wpisy o polityce, sądach i ryzyku geopolitycznym.",
        "image": "/assets/hot-x/us-court-politics.svg",
    },
    {
        "category": "crypto",
        "label_pl": "KRYPTO",
        "label_en": "CRYPTO",
        "query": "Bitcoin Ethereum ETF crypto market CoinDesk Reuters",
        "fallback_title_en": "Bitcoin, Ethereum and ETF flows in focus",
        "fallback_title_pl": "Bitcoin, Ethereum i przepływy ETF w centrum uwagi",
        "fallback_summary_en": "Hot crypto topic refreshed automatically. Open X to see the latest discussion around BTC, ETH and ETF flows.",
        "fallback_summary_pl": "Gorący temat krypto odświeżany automatycznie. Otwórz X, aby zobaczyć najnowszą dyskusję o BTC, ETH i przepływach ETF.",
        "image": "https://commons.wikimedia.org/wiki/Special:FilePath/Bitcoin.svg",
    },
]


def now_iso() -> str:
    return datetime.now(WARSAW).isoformat(timespec="seconds")


def clean_text(text: str, limit: int = 170) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def http_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 18) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers=headers or {"User-Agent": "BriefRoomsHotX/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if 200 <= r.status < 300:
                return r.read()
    except Exception as exc:
        print(f"WARN fetch failed: {url} :: {exc}", file=sys.stderr)
    return None


def x_search_url(query: str) -> str:
    return "https://x.com/search?q=" + urllib.parse.quote(query) + "&src=typed_query&f=top"


def bing_news_pick(query: str) -> Optional[Tuple[str, str, str]]:
    url = "https://www.bing.com/news/search?q=" + urllib.parse.quote_plus(query) + "&format=RSS"
    raw = http_get(url)
    if not raw:
        return None
    try:
        root = ET.fromstring(raw)
        item = root.find("./channel/item")
        if item is None:
            return None
        title = clean_text(item.findtext("title") or "")
        desc = clean_text(item.findtext("description") or "")
        source = clean_text(item.findtext("source") or item.findtext("link") or "News / X")
        if title:
            return title, desc, source
    except Exception as exc:
        print(f"WARN RSS parse failed for {query}: {exc}", file=sys.stderr)
    return None


def x_api_pick(category: Dict[str, str]) -> Optional[Dict[str, Any]]:
    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        return None
    query = category["query"] + " lang:en -is:retweet"
    params = {
        "query": query,
        "max_results": "25",
        "tweet.fields": "created_at,public_metrics,author_id",
        "expansions": "author_id",
        "user.fields": "username,name,verified",
    }
    url = "https://api.x.com/2/tweets/search/recent?" + urllib.parse.urlencode(params)
    raw = http_get(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "BriefRoomsHotX/1.0"})
    if not raw:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
        tweets = data.get("data") or []
        users = {u.get("id"): u for u in (data.get("includes", {}) or {}).get("users", [])}
        if not tweets:
            return None
        def score(t: Dict[str, Any]) -> int:
            m = t.get("public_metrics") or {}
            return int(m.get("like_count") or 0) + 2 * int(m.get("retweet_count") or 0) + int(m.get("reply_count") or 0)
        t = sorted(tweets, key=score, reverse=True)[0]
        user = users.get(t.get("author_id"), {})
        username = user.get("username") or "i"
        tweet_id = t.get("id")
        text = clean_text(t.get("text") or "", 190)
        return {
            "tweet_url": f"https://x.com/{username}/status/{tweet_id}",
            "search_url": x_search_url(category["query"]),
            "summary_en": text,
            "summary_pl": text,
            "source_en": f"X / @{username}",
            "source_pl": f"X / @{username}",
            "selected_by": "x-api",
        }
    except Exception as exc:
        print(f"WARN X API parse failed: {exc}", file=sys.stderr)
    return None


def build_item(category: Dict[str, str]) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "category": category["category"],
        "label_pl": category["label_pl"],
        "label_en": category["label_en"],
        "tweet_url": "",
        "search_url": x_search_url(category["query"]),
        "image": category["image"],
        "selected_at": now_iso(),
    }
    api = x_api_pick(category)
    if api:
        item.update(api)
        # For API mode the tweet text is the summary, while the title remains a clear topic label.
        item["title_en"] = category["fallback_title_en"]
        item["title_pl"] = category["fallback_title_pl"]
        return item

    picked = bing_news_pick(category["query"])
    if picked:
        title, desc, source = picked
        item.update({
            "title_en": title,
            "title_pl": title,
            "summary_en": desc or category["fallback_summary_en"],
            "summary_pl": desc or category["fallback_summary_pl"],
            "source_en": f"{source} / X search",
            "source_pl": f"{source} / wyszukiwanie X",
            "selected_by": "automatic-news-to-x-search",
        })
        return item

    item.update({
        "title_en": category["fallback_title_en"],
        "title_pl": category["fallback_title_pl"],
        "summary_en": category["fallback_summary_en"],
        "summary_pl": category["fallback_summary_pl"],
        "source_en": "Automatic topic / X search",
        "source_pl": "Automatyczny temat / wyszukiwanie X",
        "selected_by": "automatic-fallback",
    })
    return item


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    items = [build_item(c) for c in CATEGORIES]
    mode = "automatic-4h-x-api" if os.environ.get("X_BEARER_TOKEN") else "automatic-4h-topic-to-x-search"
    payload = {
        "updated_at": now_iso(),
        "mode": mode,
        "refresh_interval_hours": 4,
        "method_pl": "Automatycznie co 4 godziny. Jeśli sekret X_BEARER_TOKEN jest dostępny, skrypt wybiera konkretne tweety przez X API. Bez tokenu wybiera gorące tematy z news/RSS i prowadzi do wyników X dla dokładnego tematu.",
        "method_en": "Automatically every 4 hours. If X_BEARER_TOKEN is available, the script selects concrete tweets through the X API. Without the token, it selects hot news/RSS topics and links to X results for the exact topic.",
        "items": items,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {OUT} with {len(items)} Hot X topics in mode={mode}")


if __name__ == "__main__":
    main()

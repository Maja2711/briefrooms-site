#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "hot_tweets.json"
WARSAW = timezone(timedelta(hours=2))
SLOT_HOURS = 4

# Each workflow run chooses one complete slot. This makes the public Hot X panel
# actually change every 4 hours instead of refreshing the same three static cards.
TOPIC_SLOTS: List[List[Dict[str, str]]] = [
    [
        {
            "category": "economy",
            "label_pl": "EKONOMIA",
            "label_en": "ECONOMY",
            "query": "Fed inflation interest rates dollar markets Reuters Bloomberg",
            "fallback_title_en": "Fed, rates and inflation",
            "fallback_title_pl": "Fed, stopy i inflacja",
            "fallback_summary_en": "Rotating Hot X topic: central banks, rates, inflation and market reaction.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: banki centralne, stopy, inflacja i reakcja rynku.",
            "image": "/assets/hot-x/fed-market.svg",
        },
        {
            "category": "politics",
            "label_pl": "POLITYKA",
            "label_en": "POLITICS",
            "query": "White House US politics Supreme Court Congress Reuters BBC",
            "fallback_title_en": "US politics and courts",
            "fallback_title_pl": "Polityka USA i sądy",
            "fallback_summary_en": "Rotating Hot X topic: US policy, courts and political risk.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: polityka USA, sądy i ryzyko polityczne.",
            "image": "/assets/hot-x/us-court-politics.svg",
        },
        {
            "category": "crypto",
            "label_pl": "KRYPTO",
            "label_en": "CRYPTO",
            "query": "Bitcoin Ethereum ETF crypto market CoinDesk Reuters",
            "fallback_title_en": "Bitcoin, Ethereum and ETF flows",
            "fallback_title_pl": "Bitcoin, Ethereum i przepływy ETF",
            "fallback_summary_en": "Rotating Hot X topic: BTC, ETH, ETFs and market sentiment.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: BTC, ETH, ETF-y i nastroje rynku.",
            "image": "https://commons.wikimedia.org/wiki/Special:FilePath/Bitcoin.svg",
        },
    ],
    [
        {
            "category": "markets",
            "label_pl": "RYNKI",
            "label_en": "MARKETS",
            "query": "S&P 500 Nasdaq Nvidia AI stocks yields VIX Reuters Bloomberg",
            "fallback_title_en": "Stocks, yields and AI trade",
            "fallback_title_pl": "Akcje, rentowności i handel AI",
            "fallback_summary_en": "Rotating Hot X topic: S&P 500, Nasdaq, yields, VIX and AI stocks.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: S&P 500, Nasdaq, rentowności, VIX i spółki AI.",
            "image": "/assets/hot-x/fed-market.svg",
        },
        {
            "category": "geopolitics",
            "label_pl": "GEOPOLITYKA",
            "label_en": "GEOPOLITICS",
            "query": "Ukraine Russia NATO sanctions security Reuters BBC",
            "fallback_title_en": "Ukraine, Russia and NATO risk",
            "fallback_title_pl": "Ukraina, Rosja i ryzyko NATO",
            "fallback_summary_en": "Rotating Hot X topic: war, sanctions, NATO and security risk.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: wojna, sankcje, NATO i ryzyko bezpieczeństwa.",
            "image": "/assets/hot-x/us-court-politics.svg",
        },
        {
            "category": "crypto",
            "label_pl": "KRYPTO",
            "label_en": "CRYPTO",
            "query": "Bitcoin price crypto liquidation ETF flows whale CoinDesk",
            "fallback_title_en": "Bitcoin price and liquidations",
            "fallback_title_pl": "Cena Bitcoina i likwidacje",
            "fallback_summary_en": "Rotating Hot X topic: BTC price action, liquidations and ETF flows.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: ruch BTC, likwidacje i przepływy ETF.",
            "image": "https://commons.wikimedia.org/wiki/Special:FilePath/Bitcoin.svg",
        },
    ],
    [
        {
            "category": "economy",
            "label_pl": "MAKRO",
            "label_en": "MACRO",
            "query": "CPI inflation jobs report NFP unemployment dollar Reuters Bloomberg",
            "fallback_title_en": "Inflation, jobs and dollar reaction",
            "fallback_title_pl": "Inflacja, rynek pracy i reakcja dolara",
            "fallback_summary_en": "Rotating Hot X topic: inflation data, labour market and the dollar.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: dane inflacyjne, rynek pracy i dolar.",
            "image": "/assets/hot-x/fed-market.svg",
        },
        {
            "category": "energy",
            "label_pl": "ENERGIA",
            "label_en": "ENERGY",
            "query": "oil prices OPEC energy market Middle East Reuters Bloomberg",
            "fallback_title_en": "Oil, OPEC and energy risk",
            "fallback_title_pl": "Ropa, OPEC i ryzyko energii",
            "fallback_summary_en": "Rotating Hot X topic: crude oil, OPEC and energy market risk.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: ropa, OPEC i ryzyko rynku energii.",
            "image": "/assets/hot-x/fed-market.svg",
        },
        {
            "category": "tech",
            "label_pl": "TECH",
            "label_en": "TECH",
            "query": "AI regulation OpenAI Google Microsoft chips Reuters The Verge",
            "fallback_title_en": "AI, chips and regulation",
            "fallback_title_pl": "AI, chipy i regulacje",
            "fallback_summary_en": "Rotating Hot X topic: AI companies, chips and regulation.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: firmy AI, chipy i regulacje.",
            "image": "/assets/hot-x/fed-market.svg",
        },
    ],
    [
        {
            "category": "central banks",
            "label_pl": "BANKI CENTRALNE",
            "label_en": "CENTRAL BANKS",
            "query": "ECB Lagarde euro inflation rates bond yields Reuters Bloomberg",
            "fallback_title_en": "ECB, euro and bond yields",
            "fallback_title_pl": "ECB, euro i rentowności obligacji",
            "fallback_summary_en": "Rotating Hot X topic: ECB policy, euro, inflation and bond yields.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: polityka ECB, euro, inflacja i rentowności.",
            "image": "/assets/hot-x/fed-market.svg",
        },
        {
            "category": "world",
            "label_pl": "ŚWIAT",
            "label_en": "WORLD",
            "query": "China economy tariffs trade tensions Reuters BBC Bloomberg",
            "fallback_title_en": "China, trade and tariffs",
            "fallback_title_pl": "Chiny, handel i cła",
            "fallback_summary_en": "Rotating Hot X topic: China, global trade and tariff risk.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: Chiny, handel globalny i ryzyko ceł.",
            "image": "/assets/hot-x/us-court-politics.svg",
        },
        {
            "category": "crypto",
            "label_pl": "KRYPTO",
            "label_en": "CRYPTO",
            "query": "stablecoins crypto regulation SEC Bitcoin Ethereum Reuters CoinDesk",
            "fallback_title_en": "Crypto regulation and stablecoins",
            "fallback_title_pl": "Regulacje krypto i stablecoiny",
            "fallback_summary_en": "Rotating Hot X topic: crypto regulation, stablecoins, BTC and ETH.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: regulacje krypto, stablecoiny, BTC i ETH.",
            "image": "https://commons.wikimedia.org/wiki/Special:FilePath/Bitcoin.svg",
        },
    ],
    [
        {
            "category": "business",
            "label_pl": "BIZNES",
            "label_en": "BUSINESS",
            "query": "earnings guidance big tech banks markets Reuters Bloomberg",
            "fallback_title_en": "Earnings, guidance and market reaction",
            "fallback_title_pl": "Wyniki, prognozy i reakcja rynku",
            "fallback_summary_en": "Rotating Hot X topic: earnings, guidance and cross-market reaction.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: wyniki spółek, guidance i reakcja rynku.",
            "image": "/assets/hot-x/fed-market.svg",
        },
        {
            "category": "policy",
            "label_pl": "POLITYKA",
            "label_en": "POLICY",
            "query": "election polls policy markets risk Reuters BBC",
            "fallback_title_en": "Elections, policy and market risk",
            "fallback_title_pl": "Wybory, polityka i ryzyko rynkowe",
            "fallback_summary_en": "Rotating Hot X topic: elections, policy shifts and market risk.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: wybory, zmiany polityczne i ryzyko rynkowe.",
            "image": "/assets/hot-x/us-court-politics.svg",
        },
        {
            "category": "crypto",
            "label_pl": "KRYPTO",
            "label_en": "CRYPTO",
            "query": "crypto treasury Bitcoin companies miners ETF CoinDesk Reuters",
            "fallback_title_en": "Crypto treasuries and miners",
            "fallback_title_pl": "Krypto-treasury i kopalnie BTC",
            "fallback_summary_en": "Rotating Hot X topic: Bitcoin treasuries, miners and ETFs.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: bitcoinowe rezerwy firm, kopalnie i ETF-y.",
            "image": "https://commons.wikimedia.org/wiki/Special:FilePath/Bitcoin.svg",
        },
    ],
    [
        {
            "category": "risk",
            "label_pl": "RYZYKO",
            "label_en": "RISK",
            "query": "VIX risk off dollar yen gold market volatility Reuters Bloomberg",
            "fallback_title_en": "Volatility, dollar, yen and gold",
            "fallback_title_pl": "Zmienność, dolar, jen i złoto",
            "fallback_summary_en": "Rotating Hot X topic: risk-off signals, volatility, dollar, yen and gold.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: risk-off, zmienność, dolar, jen i złoto.",
            "image": "/assets/hot-x/fed-market.svg",
        },
        {
            "category": "geopolitics",
            "label_pl": "GEOPOLITYKA",
            "label_en": "GEOPOLITICS",
            "query": "Middle East security oil shipping risk Reuters BBC",
            "fallback_title_en": "Middle East, oil and shipping risk",
            "fallback_title_pl": "Bliski Wschód, ropa i ryzyko żeglugi",
            "fallback_summary_en": "Rotating Hot X topic: Middle East security, oil and shipping routes.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: bezpieczeństwo na Bliskim Wschodzie, ropa i szlaki morskie.",
            "image": "/assets/hot-x/us-court-politics.svg",
        },
        {
            "category": "crypto",
            "label_pl": "KRYPTO",
            "label_en": "CRYPTO",
            "query": "Solana Ethereum Bitcoin DeFi crypto market CoinDesk",
            "fallback_title_en": "Crypto market: BTC, ETH, SOL and DeFi",
            "fallback_title_pl": "Rynek krypto: BTC, ETH, SOL i DeFi",
            "fallback_summary_en": "Rotating Hot X topic: major crypto assets and DeFi market discussion.",
            "fallback_summary_pl": "Rotacyjny Hot X topic: główne aktywa krypto i dyskusja o DeFi.",
            "image": "https://commons.wikimedia.org/wiki/Special:FilePath/Bitcoin.svg",
        },
    ],
]


def now_dt() -> datetime:
    return datetime.now(WARSAW)


def now_iso() -> str:
    return now_dt().isoformat(timespec="seconds")


def current_slot_index() -> int:
    now = now_dt()
    # Six 4-hour windows per day, rotating through six distinct topic sets.
    return (now.hour // SLOT_HOURS) % len(TOPIC_SLOTS)


def clean_text(text: str, limit: int = 170) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def http_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 18) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers=headers or {"User-Agent": "BriefRoomsHotX/2.0"})
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
        items = root.findall("./channel/item")
        # Use a deterministic pick inside the current 4h slot to avoid the same top result forever.
        if not items:
            return None
        slot = current_slot_index()
        item = items[min(slot, len(items) - 1)]
        title = clean_text(item.findtext("title") or "")
        desc = clean_text(item.findtext("description") or "")
        source = clean_text(item.findtext("source") or item.findtext("link") or "News / X")
        if title:
            return title, desc, source
    except Exception as exc:
        print(f"WARN RSS parse failed for {query}: {exc}", file=sys.stderr)
    return None


def x_api_pick(topic: Dict[str, str]) -> Optional[Dict[str, Any]]:
    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        return None
    query = topic["query"] + " lang:en -is:retweet"
    params = {
        "query": query,
        "max_results": "25",
        "tweet.fields": "created_at,public_metrics,author_id",
        "expansions": "author_id",
        "user.fields": "username,name,verified",
    }
    url = "https://api.x.com/2/tweets/search/recent?" + urllib.parse.urlencode(params)
    raw = http_get(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "BriefRoomsHotX/2.0"})
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
        ranked = sorted(tweets, key=score, reverse=True)
        # Change the chosen tweet within the 4h slot if several strong candidates exist.
        t = ranked[min(current_slot_index(), len(ranked) - 1)]
        user = users.get(t.get("author_id"), {})
        username = user.get("username") or "i"
        tweet_id = t.get("id")
        text = clean_text(t.get("text") or "", 190)
        return {
            "tweet_url": f"https://x.com/{username}/status/{tweet_id}",
            "search_url": x_search_url(topic["query"]),
            "summary_en": text,
            "summary_pl": text,
            "source_en": f"X / @{username}",
            "source_pl": f"X / @{username}",
            "selected_by": "x-api-rotating-4h",
        }
    except Exception as exc:
        print(f"WARN X API parse failed: {exc}", file=sys.stderr)
    return None


def build_item(topic: Dict[str, str], slot: int) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "category": topic["category"],
        "label_pl": topic["label_pl"],
        "label_en": topic["label_en"],
        "tweet_url": "",
        "search_url": x_search_url(topic["query"]),
        "image": topic["image"],
        "selected_at": now_iso(),
        "rotation_slot": slot,
        "refresh_interval_hours": SLOT_HOURS,
    }
    api = x_api_pick(topic)
    if api:
        item.update(api)
        item["title_en"] = topic["fallback_title_en"]
        item["title_pl"] = topic["fallback_title_pl"]
        return item
    picked = bing_news_pick(topic["query"])
    if picked:
        title, desc, source = picked
        item.update({
            "title_en": title,
            "title_pl": title,
            "summary_en": desc or topic["fallback_summary_en"],
            "summary_pl": desc or topic["fallback_summary_pl"],
            "source_en": f"{source} / X search",
            "source_pl": f"{source} / wyszukiwanie X",
            "selected_by": "rotating-news-to-x-search-4h",
        })
        return item
    item.update({
        "title_en": topic["fallback_title_en"],
        "title_pl": topic["fallback_title_pl"],
        "summary_en": topic["fallback_summary_en"],
        "summary_pl": topic["fallback_summary_pl"],
        "source_en": "Automatic rotating topic / X search",
        "source_pl": "Automatyczny rotacyjny temat / wyszukiwanie X",
        "selected_by": "rotating-fallback-4h",
    })
    return item


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    slot = current_slot_index()
    topics = TOPIC_SLOTS[slot]
    items = [build_item(topic, slot) for topic in topics]
    mode = "automatic-4h-x-api-rotating" if os.environ.get("X_BEARER_TOKEN") else "automatic-4h-rotating-x-search"
    payload = {
        "updated_at": now_iso(),
        "mode": mode,
        "refresh_interval_hours": SLOT_HOURS,
        "rotation_slot": slot,
        "rotation_slots_total": len(TOPIC_SLOTS),
        "method_pl": "Automatycznie co 4 godziny. Każde okno 4h ma inny zestaw tematów. Jeśli dostępny jest X_BEARER_TOKEN, skrypt wybiera konkretne tweety z X API. Bez tokenu wybiera gorące newsy/RSS i prowadzi do wyników X dla dokładnego tematu.",
        "method_en": "Automatically every 4 hours. Each 4h window uses a different topic set. If X_BEARER_TOKEN is available, the script selects concrete tweets through X API. Without the token, it selects hot news/RSS topics and links to X results for the exact topic.",
        "items": items,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {OUT} with {len(items)} rotating Hot X topics in slot={slot} mode={mode}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Direct Hot X API fetcher.

Runs after the normal Hot X builder. If the repository secret X_BEARER_TOKEN can
access X API recent search, this script replaces Hot X cards with concrete X
posts and stores exact post text 1:1 in x_post_text_raw / x_post_text.

If X API is unavailable, it leaves existing cards in place but writes safe
non-secret diagnostics into data/hot_tweets.json so the reason is visible.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

PATH = Path("data/hot_tweets.json")
WARSAW = timezone(timedelta(hours=2))

TOPICS = [
    {
        "category": "tech",
        "label_pl": "TECH",
        "label_en": "TECH",
        "title_pl": "Post z X: tech",
        "title_en": "X post: tech",
        "query": "OpenAI lang:en -is:retweet -is:reply",
        "image": "/assets/hot-x/topic-ai-tech.svg",
    },
    {
        "category": "energy",
        "label_pl": "ENERGIA",
        "label_en": "ENERGY",
        "title_pl": "Post z X: energia",
        "title_en": "X post: energy",
        "query": "OPEC oil lang:en -is:retweet -is:reply",
        "image": "/assets/hot-x/topic-energy-oil.svg",
    },
    {
        "category": "macro",
        "label_pl": "MAKRO",
        "label_en": "MACRO",
        "title_pl": "Post z X: makro",
        "title_en": "X post: macro",
        "query": "inflation jobs dollar lang:en -is:retweet -is:reply",
        "image": "/assets/hot-x/topic-macro-rates.svg",
    },
]


def now_iso() -> str:
    return datetime.now(WARSAW).isoformat(timespec="seconds")


def x_search_url(query: str) -> str:
    return "https://x.com/search?q=" + urllib.parse.quote(query) + "&src=typed_query&f=top"


def read_existing() -> dict[str, Any]:
    if PATH.exists():
        try:
            return json.loads(PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"items": []}


def request_json(url: str, token: str) -> tuple[int, dict[str, Any], str]:
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "User-Agent": "BriefRoomsHotXDirect/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read().decode("utf-8", errors="replace")
            return int(r.status), json.loads(raw), "ok"
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:500]
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            body = {"raw": raw}
        return int(exc.code), body, "http_error"
    except Exception as exc:
        return 0, {"error": str(exc)[:300]}, "network_or_parse_error"


def exact_text(tweet: dict[str, Any]) -> str:
    note = tweet.get("note_tweet") or {}
    return str(note.get("text") or tweet.get("text") or "")


def score(tweet: dict[str, Any]) -> int:
    m = tweet.get("public_metrics") or {}
    return int(m.get("like_count") or 0) + 2 * int(m.get("retweet_count") or 0) + int(m.get("reply_count") or 0)


def fetch_topic(topic: dict[str, str], token: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    params = {
        "query": topic["query"],
        "max_results": "10",
        "tweet.fields": "created_at,public_metrics,author_id,note_tweet",
        "expansions": "author_id",
        "user.fields": "username,name,verified",
    }
    url = "https://api.x.com/2/tweets/search/recent?" + urllib.parse.urlencode(params)
    status, body, result = request_json(url, token)
    diag: dict[str, Any] = {
        "query": topic["query"],
        "status": status,
        "result": result,
        "endpoint": "tweets/search/recent",
    }
    if status != 200:
        # Safe diagnostic only. Do not include token or headers.
        diag["error"] = body.get("title") or body.get("detail") or body.get("error") or body.get("raw") or "X API request failed"
        return None, diag

    tweets = body.get("data") or []
    users = {u.get("id"): u for u in (body.get("includes", {}) or {}).get("users", [])}
    diag["returned"] = len(tweets)
    if not tweets:
        diag["error"] = "no_tweets_returned"
        return None, diag

    tweet = sorted(tweets, key=score, reverse=True)[0]
    text = exact_text(tweet)
    if not text:
        diag["error"] = "tweet_text_empty"
        return None, diag

    user = users.get(tweet.get("author_id"), {})
    username = user.get("username") or "i"
    tweet_id = tweet.get("id")
    item = {
        "category": topic["category"],
        "label_pl": topic["label_pl"],
        "label_en": topic["label_en"],
        "title_pl": topic["title_pl"],
        "title_en": topic["title_en"],
        "tweet_url": f"https://x.com/{username}/status/{tweet_id}",
        "search_url": x_search_url(topic["query"]),
        "image": topic["image"],
        "selected_at": now_iso(),
        "x_post_text_raw": text,
        "x_post_text": text,
        "summary_pl": text,
        "summary_en": text,
        "source_pl": f"X / @{username}",
        "source_en": f"X / @{username}",
        "selected_by": "direct-x-api-exact-post",
        "hot_x_comment_mode": "exact_full_x_post",
        "hot_x_source_rule": "tweet_url_is_primary_source",
        "x_query": topic["query"],
    }
    diag["selected_tweet_url"] = item["tweet_url"]
    diag["selected_text_chars"] = len(text)
    return item, diag


def main() -> None:
    data = read_existing()
    token = os.environ.get("X_BEARER_TOKEN")
    diagnostics: list[dict[str, Any]] = []

    if not token:
        data["x_api_status"] = "missing_X_BEARER_TOKEN"
        data["x_api_diagnostics"] = [{"error": "GitHub secret X_BEARER_TOKEN is missing or unavailable to this workflow"}]
        PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("X_BEARER_TOKEN missing")
        return

    items: list[dict[str, Any]] = []
    for topic in TOPICS:
        item, diag = fetch_topic(topic, token)
        diagnostics.append(diag)
        if item:
            items.append(item)
        time.sleep(0.4)

    data["x_api_diagnostics"] = diagnostics
    data["x_api_checked_at"] = now_iso()
    if items:
        data["updated_at"] = now_iso()
        data["mode"] = "direct-x-api-exact-post"
        data["items"] = items
        data["method_pl"] = "Hot X: pełny tekst posta z X jest zapisany 1:1 w x_post_text_raw. Długi tekst jest zwijany tylko wizualnie i można go rozwinąć."
        data["method_en"] = "Hot X: the full X post is stored 1:1 in x_post_text_raw. Long text is only visually collapsed and can be expanded."
        data["hot_x_comment_policy"] = "exact_full_x_post_1to1"
        data["x_api_status"] = "ok_exact_posts"
        print(f"Fetched {len(items)} exact X posts")
    else:
        data["x_api_status"] = "no_exact_posts_from_x_api"
        print("No exact X posts fetched; diagnostics written")

    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

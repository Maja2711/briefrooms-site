#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hot X post quality guard.

Visible rule:
- If a real X post is available, show the full post when it is short.
- If the post is long, show a concise summary/excerpt.
- Keep the public card linked to the concrete X post whenever possible.
- If no concrete post is available, keep an exact X search link and show a useful
  source/news summary instead of generic filler.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PATH = Path("data/hot_tweets.json")
SHORT_POST_LIMIT = 520
LONG_SUMMARY_LIMIT = 360
TCO_RE = re.compile(r"https?://t\.co/\S+", re.I)
WS_RE = re.compile(r"\s+")
GENERIC_RE = re.compile(r"rotating hot x topic|rotacyjny hot x topic|monitorowany jest konkretny news|pełny wątek otwiera link", re.I)
WIRE_RE = re.compile(r"^(?:By\s+[A-Z][^–—-]{2,120}\s+)?(?:LONDON|BERLIN|FRANKFURT|NEW YORK|WASHINGTON|BRUSSELS|SAN FRANCISCO),?\s+(?:Jan|Feb|Mar|Apr|May|Jun|June|Jul|July|Aug|Sep|Oct|Nov|Dec)[^–—-]{0,60}\s+\(Reuters\)\s*[-–—]\s*", re.I)


def clean(text: str) -> str:
    text = str(text or "").replace("\u2060", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    text = TCO_RE.sub("", text)
    text = WIRE_RE.sub("", text)
    text = WS_RE.sub(" ", text).strip(" -–—")
    return text


def clip(text: str, limit: int) -> str:
    text = clean(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(".,;: ") + "…"


def tweet_ids(items: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for item in items:
        url = str(item.get("tweet_url") or "")
        m = re.search(r"/status/(\d+)", url)
        if m:
            ids.append(m.group(1))
    return ids


def fetch_x_texts(ids: list[str]) -> dict[str, str]:
    token = os.environ.get("X_BEARER_TOKEN")
    if not token or not ids:
        return {}
    params = urllib.parse.urlencode({
        "ids": ",".join(ids),
        "tweet.fields": "note_tweet,created_at,author_id",
    })
    req = urllib.request.Request(
        "https://api.x.com/2/tweets?" + params,
        headers={"Authorization": f"Bearer {token}", "User-Agent": "BriefRoomsHotXPostGuard/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        print(f"WARN: could not enrich X posts: {exc}")
        return {}
    out: dict[str, str] = {}
    for t in data.get("data") or []:
        text = ((t.get("note_tweet") or {}).get("text") or t.get("text") or "")
        if text:
            out[str(t.get("id"))] = clean(text)
    return out


def polish_title(item: dict[str, Any]) -> str:
    label = str(item.get("label_pl") or item.get("category") or "temat").lower()
    if item.get("tweet_url"):
        return f"Post z X: {label}"
    return str(item.get("title_pl") or item.get("title_en") or f"Temat z X: {label}")


def english_title(item: dict[str, Any]) -> str:
    label = str(item.get("label_en") or item.get("category") or "topic")
    if item.get("tweet_url"):
        return f"X post: {label}"
    return str(item.get("title_en") or item.get("title_pl") or f"X topic: {label}")


def pl_source_summary(item: dict[str, Any], en_summary: str) -> str:
    blob = " ".join(str(item.get(k, "")) for k in ("title_en", "summary_en", "label_en", "category")).lower()
    if "openai" in blob and ("chip" in blob or "broadcom" in blob):
        return "Streszczenie źródła/X: OpenAI pokazał własny chip AI projektowany z Broadcom. Temat dotyczy infrastruktury AI i uniezależniania się od zewnętrznych dostawców mocy obliczeniowej."
    if "opec" in blob or "saudi" in blob or "oil" in blob or "crude" in blob:
        return "Streszczenie źródła/X: Sygnały z OPEC i Arabii Saudyjskiej wskazują możliwy kierunek dla rynku ropy. Link prowadzi do dokładnego wyszukiwania tego tematu na X."
    if "inflation" in blob or "cpi" in blob or "jobs" in blob or "dollar" in blob or "fed" in blob:
        return "Streszczenie źródła/X: Temat dotyczy danych inflacyjnych, rynku pracy, dolara lub oczekiwań wobec Fed. Link prowadzi do bieżącej dyskusji i źródeł na X."
    if "bitcoin" in blob or "crypto" in blob or "ethereum" in blob or "stablecoin" in blob:
        return "Streszczenie źródła/X: Temat dotyczy rynku krypto, przepływów, regulacji lub nastrojów wokół głównych aktywów cyfrowych. Link prowadzi do źródeł na X."
    if "china" in blob or "tariff" in blob or "trade" in blob:
        return "Streszczenie źródła/X: Temat dotyczy handlu, ceł lub relacji gospodarczych z Chinami. Link prowadzi do bieżących źródeł i dyskusji na X."
    label = str(item.get("label_pl") or "temat").lower()
    if en_summary:
        return f"Streszczenie źródła/X: {en_summary}"
    return f"Streszczenie źródła/X: monitorowany jest konkretny temat z kategorii {label}. Link prowadzi do źródła lub dokładnego wyszukiwania na X."


def set_x_post_comment(item: dict[str, Any], post_text: str) -> None:
    post = clean(post_text)
    if not post:
        return
    item["x_post_text"] = post
    item["title_en"] = english_title(item)
    item["title_pl"] = polish_title(item)
    if len(post) <= SHORT_POST_LIMIT:
        item["summary_en"] = f"X post: {post}"
        item["summary_pl"] = f"Post z X — oryginał: {post}"
        item["hot_x_comment_mode"] = "full_x_post"
    else:
        excerpt = clip(post, LONG_SUMMARY_LIMIT)
        item["summary_en"] = f"Summary of X post: {excerpt}"
        item["summary_pl"] = f"Streszczenie posta z X: {excerpt}"
        item["hot_x_comment_mode"] = "x_post_summary"
    item["source_en"] = item.get("source_en") or "X"
    item["source_pl"] = item.get("source_pl") or "X"
    item["hot_x_source_rule"] = "tweet_url_is_primary_source"


def set_x_search_comment(item: dict[str, Any]) -> None:
    summary_en = clean(item.get("summary_en") or "")
    title_en = clean(item.get("title_en") or "")
    if not summary_en or GENERIC_RE.search(summary_en):
        summary_en = title_en
    summary_en = clip(summary_en, 360)

    summary_pl = clean(item.get("summary_pl") or "")
    if not summary_pl or GENERIC_RE.search(summary_pl) or re.search(r"\b(the|with|market|inflation|jobs|dollar|OpenAI|OPEC|crude|Reuters|By\s+[A-Z])\b", summary_pl, re.I):
        summary_pl = pl_source_summary(item, summary_en)
    else:
        summary_pl = "Streszczenie: " + re.sub(r"^Streszczenie:\s*", "", summary_pl, flags=re.I)

    item["summary_en"] = "Summary: " + re.sub(r"^Summary:\s*", "", summary_en, flags=re.I)
    item["summary_pl"] = summary_pl
    item["tweet_url"] = item.get("tweet_url") or ""
    item["search_url"] = item.get("search_url") or "https://x.com/search?q=" + urllib.parse.quote(title_en or str(item.get("title_pl") or "BriefRooms"))
    item["source_en"] = "X — exact source search"
    item["source_pl"] = "X — wyszukiwanie źródła"
    item["hot_x_comment_mode"] = "source_summary_x_search"
    item["hot_x_source_rule"] = "search_url_is_x_source_link"


def process() -> bool:
    if not PATH.exists():
        return False
    data = json.loads(PATH.read_text(encoding="utf-8"))
    items = data.get("items") or []
    before = json.dumps(data, ensure_ascii=False, sort_keys=True)
    fetched = fetch_x_texts(tweet_ids(items))

    for item in items:
        post = clean(item.get("x_post_text") or "")
        if not post:
            url = str(item.get("tweet_url") or "")
            m = re.search(r"/status/(\d+)", url)
            if m:
                post = fetched.get(m.group(1), "") or clean(item.get("summary_en") or "")
        if item.get("tweet_url") and post:
            set_x_post_comment(item, post)
        else:
            set_x_search_comment(item)

    data["method_pl"] = "Hot X: priorytetem jest pełny tekst posta z X, jeśli jest dostępny i niedługi. Przy dłuższym poście pokazujemy streszczenie. Karta prowadzi do posta na X albo do dokładnego wyszukiwania źródła na X."
    data["method_en"] = "Hot X: the full X post is prioritized when available and short. Longer posts are summarized. The card links to the X post or an exact X source search."
    data["hot_x_comment_policy"] = "prefer_full_x_post_else_summary_else_exact_x_search"

    after = json.dumps(data, ensure_ascii=False, sort_keys=True)
    if after != before:
        PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    return False


if __name__ == "__main__":
    print("Hot X comments/source links updated" if process() else "Hot X comments already OK")

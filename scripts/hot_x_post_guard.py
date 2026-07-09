#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hot X post quality guard.

Rule for real X posts:
- Keep the exact post text 1:1 in x_post_text_raw / x_post_text.
- Do not translate, clean, strip t.co links, normalize whitespace or summarize it.
- The front-end decides whether to collapse/expand long text, but the stored text
  remains complete.
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
GENERIC_RE = re.compile(r"rotating hot x topic|rotacyjny hot x topic|monitorowany jest konkretny news|pełny wątek otwiera link", re.I)
WIRE_RE = re.compile(r"^(?:By\s+[A-Z][^–—-]{2,120}\s+)?(?:LONDON|BERLIN|FRANKFURT|NEW YORK|WASHINGTON|BRUSSELS|SAN FRANCISCO),?\s+(?:Jan|Feb|Mar|Apr|May|Jun|June|Jul|July|Aug|Sep|Oct|Nov|Dec)[^–—-]{0,60}\s+\(Reuters\)\s*[-–—]\s*", re.I)
WS_RE = re.compile(r"\s+")


def clean_source_text(text: str) -> str:
    text = str(text or "").replace("\u2060", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    text = WIRE_RE.sub("", text)
    return WS_RE.sub(" ", text).strip(" -–—")


def clip(text: str, limit: int = 360) -> str:
    text = clean_source_text(text)
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
        headers={"Authorization": f"Bearer {token}", "User-Agent": "BriefRoomsHotXPostGuard/2.0"},
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
            out[str(t.get("id"))] = str(text)
    return out


def blob(item: dict[str, Any]) -> str:
    return " ".join(str(item.get(k, "")) for k in ("title_en", "summary_en", "label_en", "category")).lower()


def pl_source_title(item: dict[str, Any]) -> str:
    b = blob(item)
    if "openai" in b and ("chip" in b or "broadcom" in b):
        return "OpenAI pokazuje własny chip AI"
    if "opec" in b or "saudi" in b or "oil" in b or "crude" in b:
        return "OPEC i sygnał dla rynku ropy"
    if "ai rally" in b or ("cpi" in b and "jobs" in b):
        return "USA: CPI i rynek pracy testem dla rajdu AI"
    if "inflation" in b or "cpi" in b or "jobs" in b or "dollar" in b or "fed" in b:
        return "Inflacja, rynek pracy i reakcja dolara"
    if "bitcoin" in b or "crypto" in b or "ethereum" in b or "stablecoin" in b:
        return "Rynek krypto: najważniejszy temat z X"
    if "china" in b or "tariff" in b or "trade" in b:
        return "Handel, cła i Chiny w centrum dyskusji"
    label = str(item.get("label_pl") or "temat").lower()
    return f"Temat z X: {label}"


def pl_source_summary(item: dict[str, Any], en_summary: str) -> str:
    b = blob(item)
    if "openai" in b and ("chip" in b or "broadcom" in b):
        return "Streszczenie źródła/X: OpenAI pokazał własny chip AI projektowany z Broadcom. Temat dotyczy infrastruktury AI i uniezależniania się od zewnętrznych dostawców mocy obliczeniowej."
    if "opec" in b or "saudi" in b or "oil" in b or "crude" in b:
        return "Streszczenie źródła/X: Sygnały z OPEC i Arabii Saudyjskiej wskazują możliwy kierunek dla rynku ropy. Link prowadzi do dokładnego wyszukiwania tego tematu na X."
    if "ai rally" in b or ("cpi" in b and "jobs" in b):
        return "Streszczenie źródła/X: Po słabym raporcie z rynku pracy inwestorzy patrzą na dane CPI z USA. Temat dotyczy reakcji rynku akcji, spółek technologicznych i rajdu AI."
    if "inflation" in b or "cpi" in b or "jobs" in b or "dollar" in b or "fed" in b:
        return "Streszczenie źródła/X: Temat dotyczy danych inflacyjnych, rynku pracy, dolara lub oczekiwań wobec Fed. Link prowadzi do bieżącej dyskusji i źródeł na X."
    if "bitcoin" in b or "crypto" in b or "ethereum" in b or "stablecoin" in b:
        return "Streszczenie źródła/X: Temat dotyczy rynku krypto, przepływów, regulacji lub nastrojów wokół głównych aktywów cyfrowych. Link prowadzi do źródeł na X."
    if "china" in b or "tariff" in b or "trade" in b:
        return "Streszczenie źródła/X: Temat dotyczy handlu, ceł lub relacji gospodarczych z Chinami. Link prowadzi do bieżących źródeł i dyskusji na X."
    if en_summary:
        return f"Streszczenie źródła/X: {en_summary}"
    label = str(item.get("label_pl") or "temat").lower()
    return f"Streszczenie źródła/X: monitorowany jest konkretny temat z kategorii {label}. Link prowadzi do źródła lub dokładnego wyszukiwania na X."


def set_exact_x_post(item: dict[str, Any], raw_post: str) -> None:
    # Exact copy only: no cleanup, no trimming, no translation.
    item["x_post_text_raw"] = raw_post
    item["x_post_text"] = raw_post
    label_en = str(item.get("label_en") or "X")
    label_pl = str(item.get("label_pl") or "X").lower()
    item["title_en"] = f"X post: {label_en}"
    item["title_pl"] = f"Post z X: {label_pl}"
    item["summary_en"] = raw_post
    item["summary_pl"] = raw_post
    item["hot_x_comment_mode"] = "exact_full_x_post"
    item["hot_x_source_rule"] = "tweet_url_is_primary_source"
    item["source_en"] = item.get("source_en") or "X"
    item["source_pl"] = item.get("source_pl") or "X"


def set_x_search_comment(item: dict[str, Any]) -> None:
    summary_en = clean_source_text(item.get("summary_en") or "")
    title_en = clean_source_text(item.get("title_en") or "")
    if not summary_en or GENERIC_RE.search(summary_en):
        summary_en = title_en
    summary_en = clip(summary_en, 360)
    item["title_pl"] = pl_source_title(item)
    item["summary_pl"] = pl_source_summary(item, summary_en)
    item["summary_en"] = "Summary: " + re.sub(r"^Summary:\s*", "", summary_en, flags=re.I)
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
        raw_post = str(item.get("x_post_text_raw") or item.get("x_post_text") or "")
        if not raw_post:
            url = str(item.get("tweet_url") or "")
            m = re.search(r"/status/(\d+)", url)
            if m:
                raw_post = fetched.get(m.group(1), "")
        if item.get("tweet_url") and raw_post:
            set_exact_x_post(item, raw_post)
        else:
            set_x_search_comment(item)

    data["method_pl"] = "Hot X: jeśli jest konkretny post z X, zapisujemy i pokazujemy pełny tekst 1:1. Długi post jest zwinięty wizualnie, ale po kliknięciu Rozwiń cały post użytkownik czyta całość. Bez konkretnego posta pokazujemy dokładny link do źródła na X."
    data["method_en"] = "Hot X: if a concrete X post is available, the full text is stored and shown 1:1. Long posts are visually collapsed, but Expand full post reveals the complete text. Without a concrete post, an exact X source link is shown."
    data["hot_x_comment_policy"] = "exact_full_x_post_1to1_else_x_source_search"

    after = json.dumps(data, ensure_ascii=False, sort_keys=True)
    if after != before:
        PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    return False


if __name__ == "__main__":
    print("Hot X exact post text applied" if process() else "Hot X already OK")

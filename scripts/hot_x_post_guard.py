#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

PATH = Path("data/hot_tweets.json")
URL_RE = re.compile(r"https?://\S+", re.I)
WS_RE = re.compile(r"\s+")
GENERIC_RE = re.compile(r"rotating|rotacyjny|automatic|pełny wątek", re.I)


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    text = WS_RE.sub(" ", text).strip(" -–—")
    return text


def clip(text: str, n: int = 380) -> str:
    text = clean(text)
    if len(text) <= n:
        return text
    return text[: n - 1].rsplit(" ", 1)[0].rstrip(".,;: ") + "…"


def query(text: str) -> str:
    text = URL_RE.sub("", str(text or ""))
    text = text.replace("…", "")
    text = WS_RE.sub(" ", text).strip(" -–—")[:180]
    if " " in text and not text.startswith('"'):
        text = f'"{text}"'
    return text or "BriefRooms"


def x_search(text: str) -> str:
    return "https://x.com/search?q=" + urllib.parse.quote(query(text)) + "&src=typed_query&f=top"


def pl_title(item: dict[str, Any]) -> str:
    b = " ".join(str(item.get(k, "")) for k in ("title_en", "summary_en", "category", "label_en")).lower()
    if "opec" in b or "oil" in b or "crude" in b:
        return "OPEC i rynek ropy"
    if "openai" in b or "google" in b or "ai" in b:
        return "AI i technologia w centrum dyskusji"
    if "cpi" in b or "inflation" in b or "jobs" in b or "dollar" in b:
        return "Inflacja, rynek pracy i reakcja dolara"
    return "Temat z X: " + str(item.get("label_pl") or item.get("category") or "news").lower()


def pl_summary(item: dict[str, Any], en: str) -> str:
    b = " ".join(str(item.get(k, "")) for k in ("title_en", "summary_en", "category", "label_en")).lower()
    if "opec" in b or "oil" in b or "crude" in b:
        return "Streszczenie źródła/X: temat dotyczy OPEC, podaży ropy i możliwego wpływu na ceny energii. Link prowadzi do czystego wyszukiwania źródeł na X."
    if "openai" in b or "google" in b or "ai" in b:
        return "Streszczenie źródła/X: temat dotyczy AI, technologii lub infrastruktury cyfrowej. Link prowadzi do czystego wyszukiwania źródeł na X."
    if "cpi" in b or "inflation" in b or "jobs" in b or "dollar" in b:
        return "Streszczenie źródła/X: temat dotyczy inflacji, rynku pracy, dolara lub oczekiwań wobec Fed. Link prowadzi do czystego wyszukiwania źródeł na X."
    return "Streszczenie źródła/X: " + (en or "wybrany temat jest monitorowany jako aktualny wątek informacyjny.")


def process_item(item: dict[str, Any]) -> None:
    title = clean(item.get("title_en") or item.get("title_pl") or item.get("x_query") or "BriefRooms")
    en = clean(item.get("summary_en") or title)
    en = re.sub(r"^Summary:\s*", "", en, flags=re.I)
    if GENERIC_RE.search(en):
        en = title
    en = clip(en)
    item.pop("x_post_text", None)
    item.pop("x_post_text_raw", None)
    item["tweet_url"] = ""
    item["search_url"] = x_search(title)
    item["title_pl"] = pl_title(item)
    item["summary_en"] = "Summary: " + en
    item["summary_pl"] = pl_summary(item, en)
    item["source_en"] = "X — source search"
    item["source_pl"] = "X — wyszukiwanie źródła"
    item["hot_x_comment_mode"] = "source_summary_x_search"
    item["hot_x_source_rule"] = "search_url_is_x_source_link"


def main() -> None:
    if not PATH.exists():
        return
    data = json.loads(PATH.read_text(encoding="utf-8"))
    for item in data.get("items") or []:
        process_item(item)
    for key in ["x_api_diagnostics", "x_api_checked_at", "x_api_status"]:
        data.pop(key, None)
    data["mode"] = "source-summary-plus-x-search"
    data["method_pl"] = "Hot X: treściwe streszczenie + czysty link do wyszukiwania źródła na X."
    data["method_en"] = "Hot X: concise summary + clean X source-search link."
    data["hot_x_comment_policy"] = "source_summary_plus_clean_x_search"
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Hot X source-summary mode applied")


if __name__ == "__main__":
    main()

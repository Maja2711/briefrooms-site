#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
META_NAME = "briefrooms-live-news-marker"


def esc(value: Any, *, quote: bool = True) -> str:
    return html.escape(str(value or ""), quote=quote)


def card(story: dict[str, Any], lang: str) -> str:
    source_label = "Źródło" if lang == "pl" else "Source"
    return (
        f'<li><a class="news-main-link" href="{esc(story.get("link"))}" '
        'target="_blank" rel="noopener noreferrer external">'
        f'<span class="news-thumb has-image"><img src="{esc(story.get("image"))}" alt="" '
        'loading="lazy" decoding="async" referrerpolicy="no-referrer"></span>'
        '<span class="news-title-wrap">'
        f'<span class="news-text">{esc(story.get("title"), quote=False)}</span>'
        f'<span class="source-line">{source_label}: {esc(story.get("source"), quote=False)}</span>'
        '</span></a></li>'
    )


def format_label(value: str, lang: str) -> str:
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if lang == "pl":
        return date.astimezone().strftime("Ostatnia aktualizacja: %d.%m.%Y, %H:%M")
    return date.astimezone().strftime("Last updated: %d/%m/%Y, %H:%M")


def upsert_meta(source: str, name: str, content: str) -> str:
    pattern = re.compile(
        rf'<meta\s+name=["\']{re.escape(name)}["\']\s+content=["\'][^"\']*["\']\s*/?>',
        re.I,
    )
    tag = f'<meta name="{name}" content="{esc(content)}">'
    if pattern.search(source):
        return pattern.sub(tag, source, count=1)
    return source.replace("</head>", f"  {tag}\n</head>", 1)


def render_news_page(lang: str, payload: dict[str, Any]) -> None:
    path = ROOT / ("pl/aktualnosci.html" if lang == "pl" else "en/news.html")
    source = path.read_text(encoding="utf-8")
    for section_id, stories in payload.get("sections", {}).items():
        pattern = re.compile(
            rf'(<section\s+class=["\']card["\']\s+id=["\']{re.escape(section_id)}["\'][^>]*>.*?<ul\s+class=["\']news["\']>).*?(</ul>)',
            re.I | re.S,
        )
        replacement = rf'\1{"".join(card(item, lang) for item in stories)}\2'
        source, count = pattern.subn(replacement, source, count=1)
        if count != 1:
            raise RuntimeError(f"section {lang}/{section_id} not found in {path}")

    source = upsert_meta(source, META_NAME, str(payload.get("marker") or ""))
    source = upsert_meta(source, "briefrooms-news-updated-at", str(payload.get("generated_at") or ""))
    label = format_label(str(payload["generated_at"]), lang)
    source, count = re.subn(
        r'<p\s+class=["\']sub["\']>.*?</p>',
        f'<p class="sub">{esc(label, quote=False)}</p>',
        source,
        count=1,
        flags=re.I | re.S,
    )
    if count != 1:
        raise RuntimeError(f"update label not found in {path}")
    path.write_text(source, encoding="utf-8", newline="\n")


def mark_homepage(lang: str, payload: dict[str, Any]) -> None:
    path = ROOT / lang / "index.html"
    source = path.read_text(encoding="utf-8")
    source = upsert_meta(source, META_NAME, str(payload.get("marker") or ""))
    source = upsert_meta(source, "briefrooms-news-updated-at", str(payload.get("generated_at") or ""))
    path.write_text(source, encoding="utf-8", newline="\n")


def main() -> None:
    for lang in ("pl", "en"):
        payload = json.loads((ROOT / "data" / "news" / f"{lang}.json").read_text(encoding="utf-8"))
        if payload.get("language") != lang or payload.get("schema_version") != "news-live-v2":
            raise RuntimeError(f"invalid {lang} live news payload")
        render_news_page(lang, payload)
        mark_homepage(lang, payload)
        print(f"rendered static {lang} news fallback")


if __name__ == "__main__":
    main()

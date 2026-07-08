#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BriefRooms editorial methodology for article briefs.

One rule: extract the sense of the source article and summarise it. Article
briefs should contain 3-6 source-derived sentences when enough source material
is available. Do not pad the brief with generic category/source/meta sentences.
Do not build a brief from the title alone. Do not add unsupported facts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

FILES = [
    (Path("pl/home_brief.json"), "pl"),
    (Path("en/home_brief.json"), "en"),
]

BOILERPLATE = re.compile(
    r"briefrooms|pełnego tekstu źródłowego|publikacja źródłowa|otwórz pełny artykuł|"
    r"źródłem wpisu jest|najważniejszy sygnał z materiału mieści się w kategorii|"
    r"artykuł dotyczy tematu:|pełne tło i szczegóły są w artykule źródłowym|"
    r"source publication|full source text|open the full article|homepage skip|accessibility help|more menu|search bbc|"
    r"the source is|the main signal belongs to the|this article is about:|full context and supporting details are in the original article",
    re.I,
)


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip(" -–—·•/\t\n\r")
    return text


def ensure_period(text: str) -> str:
    text = clean(text)
    return text if not text or text[-1] in ".!?…" else text + "."


def split_sentences(text: str) -> list[str]:
    text = clean(text).replace("…", ".")
    out: list[str] = []
    for part in re.findall(r"[^.!?]+[.!?]+|[^.!?]+$", text):
        sentence = ensure_period(part)
        if len(sentence) >= 28 and not BOILERPLATE.search(sentence):
            out.append(sentence)
    return out


def unique(sentences: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for sentence in sentences:
        key = re.sub(r"\W+", "", sentence.lower())[:90]
        if key and key not in seen:
            seen.add(key)
            out.append(sentence)
    return out


def build_full_brief(item: dict, lang: str) -> str:
    material = " ".join(clean(item.get(k) or "") for k in ("full_brief", "details", "summary", "why"))
    sentences = unique(split_sentences(material))
    # No generic padding and no title-only fallback. If the article text was not
    # readable and the feed gives too little material, keep only real source text
    # until the reader can extract a richer article body.
    return " ".join(sentences[:6])


def process_file(path: Path, lang: str) -> bool:
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for section in ("latest", "radar"):
        for item in data.get(section, []) or []:
            full = build_full_brief(item, lang)
            if full and item.get("full_brief") != full:
                item["full_brief"] = full
                changed = True
            elif not full and item.get("full_brief"):
                item.pop("full_brief", None)
                changed = True
    if changed:
        data["brief_methodology"] = {
            "pl": "Jedna zasada: przeczytać dostępny tekst artykułu i streścić jego sens. Brief ma mieć 3–6 zdań, gdy materiał źródłowy na to pozwala. Nie wolno budować komentarza z samego tytułu ani dopisywać ogólników o kategorii, źródle czy pełnym artykule.",
            "en": "One rule: read the available article text and summarise its meaning. The brief should contain 3–6 sentences when the source material supports it. Do not build comments from the title alone or add generic category, source or full-article filler.",
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def main() -> None:
    changed = False
    for path, lang in FILES:
        changed = process_file(path, lang) or changed
    print("Brief meaning-only methodology applied" if changed else "Brief methodology already satisfied")


if __name__ == "__main__":
    main()

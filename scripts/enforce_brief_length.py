#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BriefRooms editorial methodology for article briefs.

Article-level comments shown on /pl/brief.html and /en/brief.html must not be
one-sentence teasers. The full brief should contain 3-6 sentences that reflect
only the available source material: title, RSS summary, extracted article text
and source metadata. Do not add unsupported facts.
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
    r"source publication|full source text|open the full article|homepage skip|accessibility help|more menu|search bbc",
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
    title = clean(item.get("title") or "")
    source = clean(item.get("source") or ("Źródło" if lang == "pl" else "Source"))
    category = clean(item.get("category") or ("Brief" if lang == "en" else "Brief"))
    material = " ".join(clean(item.get(k) or "") for k in ("full_brief", "details", "summary", "why"))
    sentences = unique(split_sentences(material))

    # If the publisher feed gives only a short abstract, still show a proper
    # article brief, but keep every sentence tied to visible source material.
    if title and len(sentences) < 3:
        if lang == "pl":
            fallback = [
                f"Artykuł dotyczy tematu: {title}.",
                f"Najważniejszy sygnał z materiału mieści się w kategorii {category}.",
                f"Źródłem wpisu jest {source}, a pełne tło i szczegóły są w artykule źródłowym.",
            ]
        else:
            fallback = [
                f"This article is about: {title}.",
                f"The main signal belongs to the {category} category.",
                f"The source is {source}; the full context and supporting details are in the original article.",
            ]
        sentences = unique(sentences + fallback)

    if not sentences:
        if lang == "pl":
            sentences = [
                "Ten brief wymaga otwarcia artykułu źródłowego.",
                "Aktualny plik danych nie zawiera wystarczającego opisu z kanału źródłowego.",
                "BriefRooms nie dopisuje faktów, których nie ma w materiale wejściowym.",
            ]
        else:
            sentences = [
                "This brief requires opening the original source article.",
                "The current data file does not contain enough source description.",
                "BriefRooms does not add facts that are not present in the input material.",
            ]

    return " ".join(sentences[:6])


def process_file(path: Path, lang: str) -> bool:
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for section in ("latest", "radar"):
        for item in data.get(section, []) or []:
            full = build_full_brief(item, lang)
            if item.get("full_brief") != full:
                item["full_brief"] = full
                changed = True
    if changed:
        data["brief_methodology"] = {
            "pl": "Brief artykułu ma mieć co najmniej 3 i maksymalnie 6 zdań. Ma oddawać treść dostępnego materiału źródłowego i nie dopisywać faktów spoza tekstu.",
            "en": "Article briefs must contain at least 3 and at most 6 sentences. They must reflect the available source material and must not add unsupported facts.",
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def main() -> None:
    changed = False
    for path, lang in FILES:
        changed = process_file(path, lang) or changed
    print("Brief length methodology applied" if changed else "Brief length methodology already satisfied")


if __name__ == "__main__":
    main()

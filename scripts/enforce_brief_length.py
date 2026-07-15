#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BriefRooms editorial methodology for article briefs.

One rule: read the available article text and summarise its meaning. Comments
must be strict article summaries, not prompts, source notes or copied UI text.
Do not build a brief from the title alone. Do not add unsupported facts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from comment_quality import validate_comment

FILES = [
    (Path("pl/home_brief.json"), "pl"),
    (Path("en/home_brief.json"), "en"),
]

BOILERPLATE = re.compile(
    r"briefrooms|pełnego tekstu źródłowego|publikacja źródłowa|otwórz pełny artykuł|"
    r"źródłem wpisu jest|najważniejszy sygnał z materiału mieści się w kategorii|"
    r"artykuł dotyczy tematu:|pełne tło i szczegóły są w artykule źródłowym|"
    r"skom(entuj|entował|entowała|entowali|entowała)|powiedz|napisz|wyślij|fotonews|pap\b|"
    r"grzegorz krzyżewski|autor:|oprac\.|redakcja|czytaj także|zobacz także|"
    r"source publication|full source text|open the full article|homepage skip|accessibility help|more menu|search bbc|"
    r"the source is|the main signal belongs to the|this article is about:|full context and supporting details are in the original article",
    re.I,
)
BAD_START = re.compile(
    r"^(?:[.,;:!?%‰/\\)\]}]|zł\b|tys\.\b|mln\b|mld\b|proc\.\b|usd\b|eur\b|pln\b|"
    r"skom(entuj|entował|entowała|entowali|entowała)\b|powiedz\b|napisz\b|"
    r"and\b|or\b|but\b|because\b|which\b|that\b|za\b|dla\b|oraz\b|a\b|i\b)",
    re.I,
)
GOOD_START = re.compile(r"^[A-ZĄĆĘŁŃÓŚŹŻ0-9\"„'’]")


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip(" -–—·•/\t\n\r")
    return text


def ensure_period(text: str) -> str:
    text = clean(text)
    return text if not text or text[-1] in ".!?…" else text + "."


def logical_sentence(sentence: str) -> bool:
    sentence = clean(sentence)
    return bool(
        len(sentence) >= 45
        and GOOD_START.search(sentence)
        and not BAD_START.search(sentence)
        and not BOILERPLATE.search(sentence)
    )


def split_sentences(text: str) -> list[str]:
    text = clean(text).replace("…", ".")
    out: list[str] = []
    for part in re.findall(r"[^.!?]+[.!?]+|[^.!?]+$", text):
        sentence = ensure_period(part)
        if logical_sentence(sentence):
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
    # The AI-reviewed full_brief is the only approved source. Appending details,
    # summary or why here would mutate the comment after independent review.
    result = validate_comment(str(item.get("full_brief") or ""), lang)
    return result.text if result.valid else ""


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
            "pl": "Jedna zasada: przeczytać dostępny tekst artykułu i streścić jego sens. Komentarz musi być ścisłym streszczeniem artykułu. Nie wolno przepisywać poleceń redakcyjnych typu „Skomentuj”, podpisów, źródeł, kategorii ani urwanych fragmentów.",
            "en": "One rule: read the available article text and summarise its meaning. The comment must be a strict article summary. Do not copy editorial commands, bylines, source notes, categories or clipped fragments.",
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def main() -> None:
    changed = False
    for path, lang in FILES:
        changed = process_file(path, lang) or changed
    print("Strict article-only methodology applied" if changed else "Brief methodology already satisfied")


if __name__ == "__main__":
    main()

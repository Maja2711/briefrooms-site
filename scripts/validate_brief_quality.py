#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Final quality gate before publishing BriefRooms comments.

A comment is published only when it is readable, grammatical enough for display,
free of mojibake, and built from complete sentences. Broken Polish such as
"rz du", "mwi", "koz w", "obowi zek" is rejected instead of being shown.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

FILES = [(Path("pl/home_brief.json"), "pl"), (Path("en/home_brief.json"), "en")]

REPLACEMENTS = {
    "Å": "ł", "Å": "Ł", "Å¼": "ż", "Å»": "Ż", "Åº": "ź", "Å¹": "Ź",
    "Å": "ś", "Åš": "Ś", "Å„": "ń", "Å": "ń", "Ã³": "ó", "Ã": "Ó",
    "Ä": "ę", "Ä": "Ę", "Ä…": "ą", "Ä„": "Ą", "Ä": "ć", "Ä": "Ć",
    "â": "–", "â": "—", "â": "„", "â": "”", "â": "“", "â": "’", "Â": "",
}
MOJIBAKE = re.compile(r"[ÅÄÃÂâ€\x80-\x9f]")
BROKEN_PL = re.compile(
    r"\b(?:rz du|mwi|m wi|koz w|bd\b|b d|obowi zek|rozwi za|dotycz cych|"
    r"wiadcze|p ac|kilkadziesi t|przed ministr\b|ochron zdrowia|szukanie koz w|"
    r"zosta y|zosta o|przekaza a|podkre li|mo liwo|g os|ród o|w ród|"
    r"Å|Ä|Ã|Â|â)\b",
    re.I,
)
BAD_START_PL = re.compile(
    r"^(?:[.,;:!?%‰/\\)\]}]|zł\b|tys\.\b|mln\b|mld\b|proc\.\b|za\b|dla\b|oraz\b|a\b|i\b|"
    r"dodał\b|dodała\b|dodali\b|zaznaczył\b|zaznaczyła\b|powiedział\b|powiedziała\b|skomentuj\b)",
    re.I,
)
BAD_START_EN = re.compile(r"^(?:[.,;:!?%‰/\\)\]}]|and\b|or\b|but\b|because\b|which\b|that\b|usd\b|eur\b|gbp\b)", re.I)
BAD_FRAGMENT = re.compile(r"fotonews|autor:|oprac\.|czytaj także|zobacz także|skom(entuj|entował|entowała)|homepage skip|image source|image caption", re.I)


def repair(text: str) -> str:
    out = str(text or "")
    for bad, good in REPLACEMENTS.items():
        out = out.replace(bad, good)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def split_sentences(text: str) -> list[str]:
    text = repair(text).replace("…", ".")
    parts = re.findall(r"[^.!?]+[.!?]+|[^.!?]+$", text)
    out = []
    for part in parts:
        s = repair(part)
        if s and s[-1] not in ".!?":
            s += "."
        out.append(s)
    return out


def sentence_ok(sentence: str, lang: str) -> bool:
    s = repair(sentence)
    if len(s) < 35:
        return False
    if MOJIBAKE.search(s) or BAD_FRAGMENT.search(s):
        return False
    if lang == "pl":
        if BROKEN_PL.search(s) or BAD_START_PL.search(s):
            return False
        if not re.match(r"^[A-ZĄĆĘŁŃÓŚŹŻ0-9„\"'’]", s):
            return False
    else:
        if BAD_START_EN.search(s):
            return False
        if not re.match(r"^[A-Z0-9\"'’]", s):
            return False
    return True


def clean_comment(text: str, lang: str, max_sentences: int) -> str:
    good = []
    seen = set()
    for s in split_sentences(text):
        if not sentence_ok(s, lang):
            continue
        key = re.sub(r"\W+", "", s.lower())[:100]
        if key in seen:
            continue
        seen.add(key)
        good.append(s)
    return " ".join(good[:max_sentences])


def process(path: Path, lang: str) -> bool:
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    rejected = 0
    for section in ("latest", "radar"):
        for item in data.get(section, []) or []:
            for plain_key in ("title", "source", "category"):
                if isinstance(item.get(plain_key), str):
                    fixed = repair(item[plain_key])
                    if fixed != item[plain_key]:
                        item[plain_key] = fixed
                        changed = True
            for key, max_sents in (("summary", 2), ("details", 4), ("full_brief", 6)):
                old = item.get(key)
                if not isinstance(old, str):
                    continue
                cleaned = clean_comment(old, lang, max_sents)
                if cleaned:
                    if cleaned != old:
                        item[key] = cleaned
                        changed = True
                else:
                    item.pop(key, None)
                    item["comment_quality_status"] = "rejected_before_publish"
                    rejected += 1
                    changed = True
            # Do not let the article page fall back from a rejected full_brief to broken details/summary.
            basis = item.get("full_brief") or item.get("details") or item.get("summary")
            if not basis:
                item["comment_quality_status"] = "no_clean_comment_available"
                changed = True
    data["comment_quality_gate"] = {
        "pl": "Przed publikacją komentarz jest sprawdzany. Jeśli zawiera krzaki kodowania, urwane polskie słowa, fragmenty redakcyjne albo nielogiczny początek, nie jest wklejany.",
        "en": "Before publication, every comment is checked. If it contains mojibake, clipped words, editorial fragments or an illogical start, it is not inserted.",
        "rejected_count": rejected,
    }
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def main() -> None:
    changed = False
    for path, lang in FILES:
        changed = process(path, lang) or changed
    print("Brief quality gate applied" if changed else "Brief comments already passed quality gate")


if __name__ == "__main__":
    main()

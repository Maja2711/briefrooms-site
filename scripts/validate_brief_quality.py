#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Final quality gate before publishing BriefRooms comments.

Saved editorial rule:
- Read the available article text first.
- Publish only a coherent, grammatical, article-derived comment.
- Article-page comments must have at least 3 clean sentences and at most 6.
- Do not publish mojibake, broken Polish, clipped words, orphan reporting verbs,
  editorial fragments, or short unclear comments.
- If a comment does not pass, reject it instead of showing a worse fallback.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

FILES = [(Path("pl/home_brief.json"), "pl"), (Path("en/home_brief.json"), "en")]
MIN_ARTICLE_SENTENCES = 3
MAX_ARTICLE_SENTENCES = 6

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
    r"niektrz|take nie|tak e|takze niektr|poko sie|pok osie|pokłosie ultimatum postawionego|"
    r"odpowiada za konkretne sytuacje w konkretnych szpitalach|"
    r"Å|Ä|Ã|Â|â)\b",
    re.I,
)
BAD_START_PL = re.compile(
    r"^(?:[.,;:!?%‰/\\)\]}]|zł\b|tys\.\b|mln\b|mld\b|proc\.\b|za\b|dla\b|oraz\b|a\b|i\b|"
    r"dodał\b|dodała\b|dodali\b|zaznaczył\b|zaznaczyła\b|powiedział\b|powiedziała\b|"
    r"stwierdził\b|stwierdziła\b|ocenił\b|oceniła\b|wskazał\b|wskazała\b|skomentuj\b)",
    re.I,
)
BAD_START_EN = re.compile(
    r"^(?:[.,;:!?%‰/\\)\]}]|and\b|or\b|but\b|because\b|which\b|that\b|usd\b|eur\b|gbp\b|"
    r"he added\b|she added\b|they added\b|he said\b|she said\b)",
    re.I,
)
BAD_FRAGMENT = re.compile(
    r"fotonews|autor:|oprac\.|czytaj także|zobacz także|skom(entuj|entował|entowała)|"
    r"homepage skip|image source|image caption|pełne tło|źródłem wpisu|najważniejszy sygnał|"
    r"the source is|full context|main signal belongs",
    re.I,
)


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
        if s:
            out.append(s)
    return out


def sentence_ok(sentence: str, lang: str) -> bool:
    s = repair(sentence)
    if len(s) < 45:
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


def clean_sentences(text: str, lang: str) -> list[str]:
    good = []
    seen = set()
    for s in split_sentences(text):
        if not sentence_ok(s, lang):
            continue
        key = re.sub(r"\W+", "", s.lower())[:110]
        if key and key not in seen:
            seen.add(key)
            good.append(s)
    return good


def clean_comment(text: str, lang: str, max_sentences: int, min_sentences: int) -> str:
    good = clean_sentences(text, lang)
    if len(good) < min_sentences:
        return ""
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

            # Homepage card summary may be short, but it still must be clean.
            if isinstance(item.get("summary"), str):
                cleaned = clean_comment(item["summary"], lang, max_sentences=2, min_sentences=1)
                if cleaned:
                    if cleaned != item["summary"]:
                        item["summary"] = cleaned
                        changed = True
                else:
                    item.pop("summary", None)
                    changed = True

            # Article-page text must be a real comment: 3-6 clean sentences.
            for key in ("details", "full_brief"):
                old = item.get(key)
                if not isinstance(old, str):
                    continue
                cleaned = clean_comment(old, lang, max_sentences=MAX_ARTICLE_SENTENCES, min_sentences=MIN_ARTICLE_SENTENCES)
                if cleaned:
                    if cleaned != old:
                        item[key] = cleaned
                        changed = True
                else:
                    item.pop(key, None)
                    item["comment_quality_status"] = "rejected_before_publish"
                    rejected += 1
                    changed = True

            if not item.get("full_brief") and not item.get("details"):
                item["comment_quality_status"] = "no_clean_article_comment_available"
                changed = True
    data["comment_quality_gate"] = {
        "pl": "Przed publikacją komentarz jest sprawdzany. Komentarz pod artykułem musi mieć 3–6 zdań, być gramatyczny, logiczny i zrozumiały. Jeśli zawiera krzaki kodowania, urwane polskie słowa, fragmenty redakcyjne, nielogiczny początek albo mniej niż 3 czyste zdania, nie jest wklejany.",
        "en": "Before publication, every article comment is checked. It must have 3–6 sentences and be grammatical, logical and understandable. If it contains mojibake, clipped words, editorial fragments, an illogical start or fewer than 3 clean sentences, it is not inserted.",
        "min_article_sentences": MIN_ARTICLE_SENTENCES,
        "max_article_sentences": MAX_ARTICLE_SENTENCES,
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

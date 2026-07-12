#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final editorial gate for BriefRooms article comments.

Permanent rule:
- a homepage news card is publishable only when it has a coherent article-derived
  comment containing 3-6 clean sentences;
- bylines, photo credits, clipped fragments, broken encoding and vague orphan
  sentences are removed before publication;
- the card summary is rebuilt from the accepted full comment;
- if no publishable comment remains, the entire news card is removed. A short
  headline/RSS fragment must never be used as the article-page comment.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

FILES = [(Path("pl/home_brief.json"), "pl"), (Path("en/home_brief.json"), "en")]
MIN_ARTICLE_SENTENCES = 3
MAX_ARTICLE_SENTENCES = 6
MAX_VISIBLE_ITEMS = 12

REPLACEMENTS = {
    "Å": "ł", "Å": "Ł", "Å¼": "ż", "Å»": "Ż", "Åº": "ź", "Å¹": "Ź",
    "Å": "ś", "Åš": "Ś", "Å„": "ń", "Å": "ń", "Ã³": "ó", "Ã": "Ó",
    "Ä": "ę", "Ä": "Ę", "Ä…": "ą", "Ä„": "Ą", "Ä": "ć", "Ä": "Ć",
    "â": "–", "â": "—", "â": "„", "â": "”", "â": "“", "â": "’", "Â": "",
}
MOJIBAKE = re.compile(r"[ÅÄÃÂâ€\x80-\x9f]")
BROKEN_PL = re.compile(
    r"\b(?:wygl da|zatrzyma si|wy cznie|w rod|poinformowa|wyl dowaniu|znajdowa si|ju wcze?niej|"
    r"zaznaczy, e|zostaa|wysana|zgodnie z prob|caej|za czam|zdjcie|przesiad si|pokadzie|ktrej|"
    r"wrci|stanw|rdo|moliwo|operacj|przewodnicz cy|wyjani|zagraaj cym|yciu|dziaania|urzd\w*|"
    r"poudniow\w*|ledztw\w*|konkretw|projektw|aktw|caociow\w*|zupenie|wiadcze|mo liwo|"
    r"tumaczy|rnych|dostpn\w*|okrelenie|wraenie|e mamy|jak alternatyw|udowodnienie, e|co dziaa|"
    r"mudna droga|probwki|zwierztach|ludziach|porwnawcze|wyleczony t metod|rz du|mwi|m wi|"
    r"koz w|bd\b|b d|obowi zek|rozwi za|dotycz cych|p ac|kilkadziesi t|przed ministr\b|"
    r"ochron zdrowia|szukanie koz w|zosta y|zosta o|przekaza a|podkre li|g os|w ród|niektrz|"
    r"take nie|tak e|takze niektr|poko sie|pok osie|pokłosie ultimatum postawionego|Å|Ä|Ã|Â|â)\b",
    re.I,
)
BAD_START_PL = re.compile(
    r"^(?:[.,;:!?%‰/\\)\]}]|zł\b|tys\.\b|mln\b|mld\b|proc\.\b|za\b|dla\b|oraz\b|a\b|i\b|"
    r"dodał\b|dodała\b|dodali\b|zaznaczył\b|zaznaczyła\b|powiedział\b|powiedziała\b|"
    r"stwierdził\b|stwierdziła\b|ocenił\b|oceniła\b|wskazał\b|wskazała\b|skomentuj\b|"
    r"jak podkreślono\b|jak zaznaczono\b|jak poinformowano\b|jak przekazano\b|"
    r"placówka zaznaczyła\b)",
    re.I,
)
BAD_START_EN = re.compile(
    r"^(?:[.,;:!?%‰/\\)\]}]|and\b|or\b|but\b|because\b|which\b|that\b|usd\b|eur\b|gbp\b|"
    r"he added\b|she added\b|they added\b|he said\b|she said\b|according to him\b|according to her\b)",
    re.I,
)
BAD_FRAGMENT = re.compile(
    r"fotonews|autor:|oprac\.|czytaj także|zobacz także|skom(entuj|entował|entowała)|"
    r"homepage skip|image source|image caption|pełne tło|źródłem wpisu|najważniejszy sygnał|"
    r"the source is|full context|main signal belongs|shutterstock|(?:^|\s)pap(?:\s|$)",
    re.I,
)
# Typical publisher byline/photo-credit fragments, e.g. "Agnieszka Loosen / ...".
BYLINE_PL = re.compile(
    r"^(?:(?:[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż-]+|[A-ZĄĆĘŁŃÓŚŹŻ]\.)(?:\s+|$)){2,5}\s*/+",
    re.I,
)
BYLINE_EN = re.compile(r"^(?:By\s+)?(?:[A-Z][a-z-]+\s+){1,4}[A-Z][a-z-]+\s*/+", re.I)
PL_MARKS = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")


def repair(text: str) -> str:
    out = str(text or "")
    for bad, good in REPLACEMENTS.items():
        out = out.replace(bad, good)
    return re.sub(r"\s+", " ", out).strip(" -–—·•/\t\n\r")


def looks_broken_pl(text: str) -> bool:
    s = repair(text)
    if BROKEN_PL.search(s):
        return True
    if len(s) > 180 and not PL_MARKS.search(s) and re.search(
        r"\b(jest|oraz|przez|zosta|będzie|polsk|prezydent|szpital|minister|rynek)\b", s, re.I
    ):
        return True
    bad_tokens = len(re.findall(r"\b(?:si|e|ktre|ktra|ktry|rdo|ju|te|moe|bdzie|wicej|zostaa|jeli)\b", s, re.I))
    return bad_tokens >= 3


def split_sentences(text: str) -> list[str]:
    text = repair(text).replace("…", ".")
    out: list[str] = []
    for part in re.findall(r"[^.!?]+[.!?]+|[^.!?]+$", text):
        sentence = repair(part)
        if sentence and sentence[-1] not in ".!?":
            sentence += "."
        if sentence:
            out.append(sentence)
    return out


def sentence_ok(sentence: str, lang: str) -> bool:
    s = repair(sentence)
    if len(s) < 45 or MOJIBAKE.search(s) or BAD_FRAGMENT.search(s):
        return False
    if lang == "pl":
        if BYLINE_PL.search(s) or BAD_START_PL.search(s) or looks_broken_pl(s):
            return False
        if not re.match(r"^[A-ZĄĆĘŁŃÓŚŹŻ0-9„\"'’]", s):
            return False
    else:
        if BYLINE_EN.search(s) or BAD_START_EN.search(s):
            return False
        if not re.match(r"^[A-Z0-9\"'’]", s):
            return False
    return True


def clean_sentences(text: str, lang: str) -> list[str]:
    good: list[str] = []
    seen: set[str] = set()
    # A single bad sentence must not destroy the remaining good article text.
    for sentence in split_sentences(text):
        if not sentence_ok(sentence, lang):
            continue
        key = re.sub(r"\W+", "", sentence.lower())[:110]
        if key and key not in seen:
            seen.add(key)
            good.append(sentence)
    return good


def publishable_comment(item: dict, lang: str) -> str:
    for key in ("full_brief", "details"):
        value = item.get(key)
        if not isinstance(value, str):
            continue
        sentences = clean_sentences(value, lang)
        if len(sentences) >= MIN_ARTICLE_SENTENCES:
            return " ".join(sentences[:MAX_ARTICLE_SENTENCES])
    return ""


def process(path: Path, lang: str) -> bool:
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    rejected: list[dict] = []

    for section in ("latest", "radar"):
        old_items = data.get(section, []) or []
        kept: list[dict] = []
        for item in old_items:
            for key in ("title", "source", "category"):
                if isinstance(item.get(key), str):
                    item[key] = repair(item[key])

            comment = publishable_comment(item, lang)
            if not comment:
                rejected.append({"source": item.get("source", ""), "title": item.get("title", "")})
                continue

            item["full_brief"] = comment
            item["details"] = comment
            comment_sentences = split_sentences(comment)
            item["summary"] = " ".join(comment_sentences[:2])
            item["comment_quality_status"] = "passed_3_to_6_sentences"
            kept.append(item)

        kept = kept[:MAX_VISIBLE_ITEMS]
        if kept != old_items:
            data[section] = kept
            changed = True

    data["count"] = len(data.get("latest", []) or [])
    data["comment_quality_gate"] = {
        "pl": "Karta jest publikowana tylko wtedy, gdy posiada logiczny, gramatyczny komentarz złożony z 3–6 zdań. Nazwiska autorów, podpisy zdjęć, urwane fragmenty i RSS-owe strzępy są usuwane. Jeśli po kontroli nie ma pełnego komentarza, cały news jest usuwany ze strony.",
        "en": "A card is published only when it has a coherent, grammatical 3–6 sentence comment. Bylines, photo credits, clipped fragments and RSS scraps are removed. If no full comment remains, the whole news item is removed.",
        "min_article_sentences": MIN_ARTICLE_SENTENCES,
        "max_article_sentences": MAX_ARTICLE_SENTENCES,
        "rejected_count": len(rejected),
        "rejected_examples": rejected[:8],
        "headline_or_short_summary_fallback": "forbidden",
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def main() -> None:
    changed = False
    for path, lang in FILES:
        changed = process(path, lang) or changed
    print("Brief quality gate applied; unpublishable cards removed" if changed else "Brief quality gate unchanged")


if __name__ == "__main__":
    main()

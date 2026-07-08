#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Urgent/breaking is a ranking signal, not a visible homepage card category.

This keeps urgent=True and priority_reason in home_brief.json, but replaces
visible category labels "Pilne" / "Breaking" with the real editorial category.
It also repairs mojibake and removes broken/card-unfriendly summary fragments.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PL = Path("pl/home_brief.json")
EN = Path("en/home_brief.json")

MOJIBAKE = re.compile(r"[ÅÄÃÂâ€\x80-\x9f]")
BAD_PL_START = re.compile(r"^(zł\b|tys\.\b|mln\b|mld\b|proc\.\b|[.,;:!?%‰/\\)\]}]|za\b|dla\b|oraz\b|a\b|i\b|dodał\b|dodała\b|zaznaczył\b|skomentuj\b)", re.I)
BAD_EN_START = re.compile(r"^(usd\b|eur\b|gbp\b|[.,;:!?%‰/\\)\]}]|and\b|or\b|but\b|because\b|which\b|that\b)", re.I)
BAD_FRAGMENT = re.compile(r"fotonews|pap\b|autor:|oprac\.|czytaj także|zobacz także|skom(entuj|entował|entowała)", re.I)

REPLACEMENTS = {
    "Å": "ł", "Å": "Ł", "Å¼": "ż", "Å»": "Ż", "Åº": "ź", "Å¹": "Ź",
    "Å": "ś", "Åš": "Ś", "Å„": "ń", "Å": "ń", "Ã³": "ó", "Ã": "Ó",
    "Ä": "ę", "Ä": "Ę", "Ä…": "ą", "Ä„": "Ą", "Ä": "ć", "Ä": "Ć",
    "â": "–", "â": "—", "â": "„", "â": "”", "â": "“", "â": "’", "Â": "",
}


def repair_text(value: str) -> str:
    text = str(value or "")
    for bad, good in REPLACEMENTS.items():
        text = text.replace(bad, good)
    if MOJIBAKE.search(text):
        try:
            fixed = text.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
            if fixed and len(MOJIBAKE.findall(fixed)) < len(MOJIBAKE.findall(text)):
                text = fixed
        except Exception:
            pass
    text = re.sub(r"\s+", " ", text).strip()
    return text


def split_sentences(text: str) -> list[str]:
    text = repair_text(text).replace("…", ".")
    out: list[str] = []
    for part in re.findall(r"[^.!?]+[.!?]+|[^.!?]+$", text):
        s = repair_text(part)
        if s and s[-1] not in ".!?":
            s += "."
        out.append(s)
    return out


def logical_sentences(text: str, lang: str) -> list[str]:
    bad_start = BAD_PL_START if lang == "pl" else BAD_EN_START
    out: list[str] = []
    seen: set[str] = set()
    for s in split_sentences(text):
        key = re.sub(r"\W+", "", s.lower())[:90]
        if not key or key in seen:
            continue
        seen.add(key)
        if len(s) < 35 or bad_start.search(s) or BAD_FRAGMENT.search(s):
            continue
        if lang == "pl" and not re.match(r"^[A-ZĄĆĘŁŃÓŚŹŻ0-9„\"'’]", s):
            continue
        if lang == "en" and not re.match(r"^[A-Z0-9\"'’]", s):
            continue
        out.append(s)
    return out


def clean_field(item: dict, key: str, lang: str, limit: int | None = None) -> bool:
    old = item.get(key)
    if not isinstance(old, str):
        return False
    text = repair_text(old)
    if key in {"summary", "details", "full_brief"}:
        sents = logical_sentences(text, lang)
        if sents:
            max_sents = 2 if key == "summary" else 6
            text = " ".join(sents[:max_sents])
        elif key == "summary":
            text = repair_text(item.get("title") or "")
    if limit and len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].strip() + "…"
    if text != old:
        item[key] = text
        return True
    return False


def text_of(item: dict) -> str:
    return " ".join(str(item.get(k, "")) for k in ("title", "summary", "details", "full_brief")).lower()


def classify_pl(item: dict) -> str:
    text = text_of(item)
    if re.search(r"nato|ukrain|rosj|wojn|patriot|ormuz|iran|usa|trump|chiny|sankcj|cła|okręt|obron|budanow|bbn|zacharowa", text):
        return "Geopolityka"
    if re.search(r"nfz|szpital|zdrow|lek|epidem|pacjent|lekarz|medyk|ortoped|anestezjolog", text):
        return "Zdrowie"
    if re.search(r"nauk|badani|kosmos|technolog|ai|sztuczn|atom|elektrown", text):
        return "Nauka"
    if re.search(r"inflacj|stopy|giełd|bank|ropa|gaz|złoty|dolar|pkb|spółk|rynek|walmart|prom|port|inwest|etf", text):
        return "Ekonomia"
    return "Aktualności"


def classify_en(item: dict) -> str:
    text = text_of(item)
    if re.search(r"nato|ukraine|russia|war|iran|china|trump|sanction|tariff|defen[cs]e|military|gaza|israel", text):
        return "Geopolitics"
    if re.search(r"fed|inflation|rates|stocks|markets|bond|dollar|oil|gas|earnings|recession|crypto|bitcoin", text):
        return "Business / markets"
    if re.search(r"ai|chips|cyber|technology|software|semiconductor|nvidia|samsung", text):
        return "Technology"
    if re.search(r"health|hospital|patient|doctor|drug|vaccine|who|nih|cdc|disease", text):
        return "Health"
    if re.search(r"science|nasa|esa|space|climate|research|study", text):
        return "Science"
    return "World / news"


def process(path: Path, lang: str) -> bool:
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for section in ("latest", "radar"):
        for item in data.get(section, []) or []:
            for key, limit in (("title", 120), ("summary", 260), ("details", 700), ("full_brief", 1200), ("source", 80), ("category", 60)):
                changed = clean_field(item, key, lang, limit) or changed
            cat = str(item.get("category") or "")
            if cat.lower() in {"pilne", "breaking", "urgent", "alert"}:
                item["category"] = classify_pl(item) if lang == "pl" else classify_en(item)
                item["urgent"] = True
                item["priority_reason"] = item.get("priority_reason") or "publisher_marked_urgent"
                changed = True
    data["urgent_display_methodology"] = (
        "Pilne/Breaking jest sygnałem priorytetu w sortowaniu, ale nie jest pokazywane jako etykieta na zdjęciu karty. Karta pokazuje realną kategorię redakcyjną tematu."
        if lang == "pl"
        else "Urgent/Breaking is used as a ranking signal, but it is not shown as the photo badge. The card shows the real editorial category."
    )
    data["text_sanitizer"] = "mojibake-repair-and-logical-sentence-filter-v1"
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def main() -> None:
    changed = process(PL, "pl") or process(EN, "en")
    print("Homepage labels/text sanitized" if changed else "Homepage labels/text already clean")


if __name__ == "__main__":
    main()

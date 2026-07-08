#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Urgent/breaking is a ranking signal, not a visible homepage card category.

This keeps urgent=True and priority_reason in home_brief.json, but replaces
visible category labels "Pilne" / "Breaking" with the real editorial category,
so no urgent label is printed on the photo badge.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PL = Path("pl/home_brief.json")
EN = Path("en/home_brief.json")


def text_of(item: dict) -> str:
    return " ".join(str(item.get(k, "")) for k in ("title", "summary", "details", "full_brief")).lower()


def classify_pl(item: dict) -> str:
    text = text_of(item)
    if re.search(r"nato|ukrain|rosj|wojn|patriot|ormuz|iran|usa|trump|chiny|sankcj|cła|okręt|obron|budanow|bbn", text):
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
            cat = str(item.get("category") or "")
            if cat.lower() in {"pilne", "breaking"}:
                item["category"] = classify_pl(item) if lang == "pl" else classify_en(item)
                item["urgent"] = True
                item["priority_reason"] = item.get("priority_reason") or "publisher_marked_urgent"
                changed = True
    data["urgent_display_methodology"] = (
        "Pilne/Breaking jest sygnałem priorytetu w sortowaniu, ale nie jest pokazywane jako etykieta na zdjęciu karty. "
        "Karta pokazuje realną kategorię redakcyjną tematu."
        if lang == "pl"
        else "Urgent/Breaking is used as a ranking signal, but it is not shown as the photo badge. The card shows the real editorial category."
    )
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def main() -> None:
    changed = process(PL, "pl") or process(EN, "en")
    print("Urgent badge categories removed" if changed else "No urgent badge categories found")


if __name__ == "__main__":
    main()

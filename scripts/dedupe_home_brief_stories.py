#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Final same-story dedupe gate for homepage briefs.

Rule: one underlying event = one homepage card, even when several publishers
write about it with different titles and URLs. This runs after summaries and
category cleanup, because the full title + summary + details give better story
matching than the raw RSS title alone.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

FILES = [Path("pl/home_brief.json"), Path("en/home_brief.json")]

STOPWORDS = set("""
a about after albo ale all also an and are as at aby albo ale oraz albo bez been being by czy dla do from has have her his ich in into is it its jak jako jest jej jego just more na nie no not od of on or oraz po pod przez przy się sie than that the their this to was we were what when where which who will with w we za ze z
bankier bbc cnn forum google image news pap polsat reuters shutterstock source tvn tvn24 twitter x zdjęcie zdjecie dzis dzisiaj today yesterday tomorrow czytaj zobacz także also
""".split())
TRANSLATE = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")

ENTITY_PATTERNS = {
    "lindsey_graham": r"\blindsey\s+graham\b|\bsenator(?:a|em|ze|owi)?\s+graham\b|\bgraham\b",
    "donald_trump": r"\bdonald\s+trump\b|\btrump\b",
    "ukraine": r"ukrain|zelensk|kijow|kyiv|wołyn|wolyn",
    "russia": r"rosj|kreml|moskw|putin|federacj[ai] rosyjsk",
    "china": r"chin|pekin|beijing",
    "iran": r"iran|ormuz|hormuz",
    "nato": r"\bnato\b|sojusz",
    "nfz": r"\bnfz\b|szpital|ochron[ay]? zdrowia",
    "mogilno_hospital": r"mogiln|szpitala w mogilnie|szpital w mogilnie",
    "electric_scooter_teen": r"hulajnog|15\s*latek|piętnastolet|predkoscia ponad 60|prędkością ponad 60",
}

EVENT_PATTERNS = {
    "death": r"zmar|śmier|smier|nie żyje|nie zyje|odszed|dead|death|dies|died",
    "reaction": r"reakcj|grzmi|koment|respond|reaction|reacts",
    "hospital_scandal": r"szpital|kontrol|prokuratur|nfz|skok na kase|skok na kasę",
    "accident": r"uderz|wypad|ratownik|agresywn|policj|scooter|hulajnog",
    "trade_tariff": r"cła|cla|tariff|trade|handel",
    "war_policy": r"wojn|war|nato|sankcj|obron|militar|missile|patriot",
}


def strip_accents(text: str) -> str:
    text = str(text or "").translate(TRANSLATE)
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def clean(text: str) -> str:
    text = strip_accents(text).lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def blob(item: dict) -> str:
    fields = ("title", "summary", "details", "full_brief", "source", "category")
    return clean(" ".join(str(item.get(k, "")) for k in fields))


def entities(item: dict) -> set[str]:
    b = blob(item)
    return {key for key, pattern in ENTITY_PATTERNS.items() if re.search(pattern, b, re.I)}


def events(item: dict) -> set[str]:
    b = blob(item)
    return {key for key, pattern in EVENT_PATTERNS.items() if re.search(pattern, b, re.I)}


def tokens(item: dict) -> set[str]:
    b = blob(item)
    words = {w for w in b.split() if len(w) >= 4 and w not in STOPWORDS and not w.isdigit()}
    words |= entities(item)
    words |= events(item)
    return words


def link_tokens(url: str) -> set[str]:
    try:
        p = urlparse(str(url or ""))
        path = clean(p.path)
        return {w for w in path.split() if len(w) >= 4 and w not in STOPWORDS and not w.isdigit()}
    except Exception:
        return set()


def strong_event_key(item: dict) -> str:
    ent = entities(item)
    ev = events(item)
    if "lindsey_graham" in ent and "death" in ev:
        return "person_death:lindsey_graham"
    if "mogilno_hospital" in ent and "hospital_scandal" in ev:
        return "local_hospital:m在ogilno".replace("在", "")
    if "electric_scooter_teen" in ent and "accident" in ev:
        return "accident:electric_scooter_teen"
    # General event key for named entities when the event type is also shared.
    useful_ent = sorted(e for e in ent if e not in {"donald_trump"})
    useful_ev = sorted(ev)
    if useful_ent and useful_ev:
        return "event:" + "|".join(useful_ent[:3]) + ":" + "|".join(useful_ev[:2])
    return ""


def same_story(a: dict, b: dict) -> bool:
    if a.get("link") and a.get("link") == b.get("link"):
        return True

    ak, bk = strong_event_key(a), strong_event_key(b)
    if ak and ak == bk:
        return True

    ae, be = entities(a), entities(b)
    av, bv = events(a), events(b)
    at, bt = tokens(a), tokens(b)
    if not at or not bt:
        return False

    overlap = len(at & bt) / max(1, min(len(at), len(bt)))
    shared_entities = ae & be
    shared_events = av & bv

    if shared_entities and shared_events and overlap >= 0.28:
        return True
    if shared_entities and overlap >= 0.42:
        return True
    if overlap >= 0.62:
        return True

    la, lb = link_tokens(a.get("link", "")), link_tokens(b.get("link", ""))
    if la and lb:
        link_overlap = len(la & lb) / max(1, min(len(la), len(lb)))
        if link_overlap >= 0.5 and (shared_entities or shared_events or overlap >= 0.25):
            return True

    return False


def item_rank(item: dict) -> tuple[int, int, int]:
    # Keep the stronger/currently earlier editorial card when duplicates collide.
    title = str(item.get("title") or "")
    details = str(item.get("details") or item.get("full_brief") or item.get("summary") or "")
    img = str(item.get("image") or "")
    real_img = 0 if img.startswith("data:image") or not img else 1
    return (1 if item.get("urgent") else 0, real_img, len(title) + min(len(details), 900))


def dedupe_list(items: list[dict]) -> tuple[list[dict], list[dict]]:
    selected: list[dict] = []
    skipped: list[dict] = []
    for item in items:
        dupe_index = next((idx for idx, prev in enumerate(selected) if same_story(item, prev)), None)
        if dupe_index is None:
            selected.append(item)
            continue
        prev = selected[dupe_index]
        if item_rank(item) > item_rank(prev):
            selected[dupe_index] = item
            skipped.append(prev)
        else:
            skipped.append(item)
    return selected, skipped


def process(path: Path) -> bool:
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    total_skipped: list[dict] = []
    for section in ("latest", "radar"):
        items = data.get(section)
        if not isinstance(items, list):
            continue
        deduped, skipped = dedupe_list(items)
        if len(deduped) != len(items) or deduped != items:
            data[section] = deduped
            total_skipped.extend(skipped)
            changed = True
    if total_skipped:
        data["same_story_dedupe"] = {
            "status": "applied",
            "rule": "One underlying event gets one homepage card, even if multiple publishers cover it with different links.",
            "skipped_count": len(total_skipped),
            "skipped_examples": [
                {"source": x.get("source", ""), "title": x.get("title", "")} for x in total_skipped[:8]
            ],
        }
    else:
        data["same_story_dedupe"] = {
            "status": "checked",
            "rule": "One underlying event gets one homepage card, even if multiple publishers cover it with different links.",
            "skipped_count": 0,
        }
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def main() -> None:
    changed = False
    for path in FILES:
        changed = process(path) or changed
    print("Homepage same-story dedupe applied" if changed else "Homepage same-story dedupe already clean")


if __name__ == "__main__":
    main()

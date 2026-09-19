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
from datetime import datetime, timezone
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

TOPIC_GENERIC = {
    "aktualn", "analiz", "badani", "ekspert", "informac", "miast", "now", "raport",
    "spraw", "temat", "wynik", "zmian", "polsk", "swiat", "today", "report", "study",
    "expert", "city", "cities", "change", "changes", "new", "latest",
}
TOPIC_SUFFIXES = (
    "owego", "owej", "owych", "ami", "ach", "anie", "enie", "owie", "ego", "emu",
    "owa", "owe", "owi", "om", "ow", "em", "ie", "y", "a", "u",
    "ingly", "ments", "ment", "ation", "ions", "ing", "ers", "ies", "ed", "es",
)


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
        return "local_hospital:mogilno"
    if "electric_scooter_teen" in ent and "accident" in ev:
        return "accident:electric_scooter_teen"
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


def _topic_stem(word: str) -> str:
    token = clean(word)
    if not token:
        return ""
    for suffix in TOPIC_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def topic_tokens(item: dict) -> tuple[set[str], set[str]]:
    """Return headline topic terms and the subset used as common-noun anchors."""
    title = str(item.get("title") or "")
    raw_words = re.findall(r"[^\\W\\d_]+", title, flags=re.UNICODE)
    all_terms: set[str] = set()
    lowercase_terms: set[str] = set()
    for raw in raw_words:
        folded = clean(raw)
        if len(folded) < 4 or folded in STOPWORDS:
            continue
        stem = _topic_stem(folded)
        if len(stem) < 4 or stem in TOPIC_GENERIC:
            continue
        all_terms.add(stem)
        # A lower-case occurrence is a useful signal that this is a topic noun
        # rather than merely the same person/company name in two unrelated stories.
        if raw == raw.lower():
            lowercase_terms.add(stem)
    return all_terms, lowercase_terms


def _published_epoch(item: dict) -> float | None:
    raw = item.get("published_at") or item.get("timestamp")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


def same_topic(a: dict, b: dict) -> bool:
    """Homepage-level guard: one reader-visible topic/event gets one card."""
    link_a = str(a.get("link") or "").strip()
    link_b = str(b.get("link") or "").strip()
    if link_a and link_b and link_a == link_b:
        return True

    event_a = str(a.get("canonical_event_id") or "")
    event_b = str(b.get("canonical_event_id") or "")
    if event_a and event_b and event_a == event_b:
        return True

    at, alower = topic_tokens(a)
    bt, blower = topic_tokens(b)
    if not at or not bt:
        return False
    shared = at & bt
    overlap = len(shared) / max(1, min(len(at), len(bt)))
    if len(shared) >= 2 and overlap >= 0.45:
        return True

    same_source = bool(a.get("source")) and str(a.get("source")) == str(b.get("source"))
    category_a = str(a.get("category") or a.get("_homepage_section_id") or "")
    category_b = str(b.get("category") or b.get("_homepage_section_id") or "")
    same_category = bool(category_a) and category_a == category_b
    ta, tb = _published_epoch(a), _published_epoch(b)
    close_in_time = ta is not None and tb is not None and abs(ta - tb) <= 18 * 3600
    common_noun_anchor = {
        token for token in shared
        if len(token) >= 5 and token not in TOPIC_GENERIC and (token in alower or token in blower)
    }
    return bool(same_source and same_category and close_in_time and common_noun_anchor)


def item_rank(item: dict) -> tuple[int, int, int]:
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

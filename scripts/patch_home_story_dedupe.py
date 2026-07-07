#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
PL_BUILDER = ROOT / "scripts" / "build_home_brief_pl.py"
EN_BUILDER = ROOT / "scripts" / "build_home_brief_en.py"
PL_JSON = ROOT / "pl" / "home_brief.json"
EN_JSON = ROOT / "en" / "home_brief.json"

PL_HELPERS = r'''

# STORY_DEDUPE_V4: remove multiple cards about the same underlying story.
PL_DEDUPE_STOPWORDS = set("""
a albo ale aby oraz czyli dla jako jest jego jej ich sie się nie na do od po pod nad przed przez przy bez ze z w we i o u za to ten ta te tym tych tego temu jak juz już czy oraz albo albo
mamy maja mają moze może powinien powinna powinno zostal została zostalo zostało powiedzial powiedziała powiedziala wedlug według podaje pisze chodzi chodzić sprawie ws
bankier pl tvn tvn24 reuters pap forum shutterstock twitter x zdjecie zdjęcie image source caption dzis dzisiaj jutro wczoraj
""".split())
PL_ENTITY_PATTERNS = {
    "trump": r"\btrump\b|\bdonald\s+trump\b",
    "grenlandia": r"grenland",
    "nato": r"\bnato\b|sojusz",
    "ukraina": r"ukrain|zelensk|budanow|kijow|kyiv",
    "rosja": r"rosj|kreml|moskw|putin",
    "lpg_auta_rosja": r"lpg|instalacj.*gaz|przerob.*samochod|kolejk.*wrzesn|rafiner",
    "patriot": r"patriot|pac-3|lockheed",
    "nbp_zloto": r"nbp|zloto|złoto|uncj",
    "polska": r"polsk|tusk|nawrock|prezydent",
    "chiny": r"chin|pekin",
    "iran": r"iran|ormuz",
}
PL_TRANSLATE = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")

def dedupe_norm(text: str) -> str:
    text = clean_text(text or "", 2500).lower().translate(PL_TRANSLATE)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\b(reuters|pap|forum|shutterstock|bankier|tvn24|tvn|polsat|google news)\b", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def story_blob(item: dict) -> str:
    return dedupe_norm(" ".join(str(item.get(k, "")) for k in ("title", "summary", "details")))

def story_entities(item: dict) -> set[str]:
    blob = story_blob(item)
    return {key for key, pat in PL_ENTITY_PATTERNS.items() if re.search(pat, blob, re.I)}

def story_tokens(item: dict) -> set[str]:
    blob = story_blob(item)
    toks = {t for t in blob.split() if len(t) >= 4 and t not in PL_DEDUPE_STOPWORDS and not t.isdigit()}
    toks |= story_entities(item)
    return toks

def link_fingerprint(url: str) -> str:
    try:
        p = urlparse(url or "")
        path = re.sub(r"[-_/]+", " ", p.path.lower())
        path = re.sub(r"\d+", " ", path)
        return dedupe_norm(path)
    except Exception:
        return ""

def story_key(item: dict) -> str:
    ents = sorted(story_entities(item))
    if len(ents) >= 2:
        return "entities:" + "|".join(ents[:6])
    toks = sorted(story_tokens(item))[:7]
    return "tokens:" + "|".join(toks)

def same_story(a: dict, b: dict) -> bool:
    if a.get("link") and a.get("link") == b.get("link"):
        return True
    ak, bk = story_key(a), story_key(b)
    if ak.startswith("entities:") and ak == bk:
        return True
    a_link, b_link = link_fingerprint(a.get("link", "")), link_fingerprint(b.get("link", ""))
    if a_link and b_link:
        la, lb = set(a_link.split()), set(b_link.split())
        if la and lb and len(la & lb) / min(len(la), len(lb)) >= 0.55:
            return True
    at, bt = story_tokens(a), story_tokens(b)
    if not at or not bt:
        return False
    overlap = len(at & bt) / min(len(at), len(bt))
    shared_entities = bool(story_entities(a) & story_entities(b))
    return overlap >= 0.55 or (shared_entities and overlap >= 0.35)

def is_duplicate_story(item: dict, selected: list[dict]) -> bool:
    return any(same_story(item, prev) for prev in selected)
'''

EN_HELPERS = r'''

# STORY_DEDUPE_V4: remove multiple cards about the same underlying story.
EN_DEDUPE_STOPWORDS = set("""
the a an and or but for from into with without about over under after before this that these those is are was were be been being has have had will would could should may might can not
reuters ap bloomberg bbc guardian cnbc wsj marketwatch google news says said told report reports source image photo caption today yesterday tomorrow
""".split())
EN_ENTITY_PATTERNS = {
    "trump": r"\btrump\b|\bdonald\s+trump\b",
    "greenland": r"greenland",
    "nato": r"\bnato\b|alliance",
    "ukraine": r"ukrain|zelensky|budanov|kyiv|kiev",
    "russia": r"russia|russian|kremlin|moscow|putin",
    "oil_gas": r"oil|gas|lpg|refiner|refinery",
    "patriot": r"patriot|pac-3|lockheed",
    "china": r"china|beijing",
    "iran": r"iran|hormuz",
    "fed_rates": r"fed|rates|inflation|treasury|yields",
    "bitcoin": r"bitcoin|btc|crypto",
}

def dedupe_norm(text: str) -> str:
    text = clean_text(text or "", 2500).lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\b(reuters|ap|bloomberg|bbc|guardian|cnbc|wsj|marketwatch|google news)\b", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def story_blob(item: dict) -> str:
    return dedupe_norm(" ".join(str(item.get(k, "")) for k in ("title", "summary", "details")))

def story_entities(item: dict) -> set[str]:
    blob = story_blob(item)
    return {key for key, pat in EN_ENTITY_PATTERNS.items() if re.search(pat, blob, re.I)}

def story_tokens(item: dict) -> set[str]:
    blob = story_blob(item)
    toks = {t for t in blob.split() if len(t) >= 4 and t not in EN_DEDUPE_STOPWORDS and not t.isdigit()}
    toks |= story_entities(item)
    return toks

def link_fingerprint(url: str) -> str:
    try:
        p = urlparse(url or "")
        path = re.sub(r"[-_/]+", " ", p.path.lower())
        path = re.sub(r"\d+", " ", path)
        return dedupe_norm(path)
    except Exception:
        return ""

def story_key(item: dict) -> str:
    ents = sorted(story_entities(item))
    if len(ents) >= 2:
        return "entities:" + "|".join(ents[:6])
    toks = sorted(story_tokens(item))[:7]
    return "tokens:" + "|".join(toks)

def same_story(a: dict, b: dict) -> bool:
    if a.get("link") and a.get("link") == b.get("link"):
        return True
    ak, bk = story_key(a), story_key(b)
    if ak.startswith("entities:") and ak == bk:
        return True
    a_link, b_link = link_fingerprint(a.get("link", "")), link_fingerprint(b.get("link", ""))
    if a_link and b_link:
        la, lb = set(a_link.split()), set(b_link.split())
        if la and lb and len(la & lb) / min(len(la), len(lb)) >= 0.55:
            return True
    at, bt = story_tokens(a), story_tokens(b)
    if not at or not bt:
        return False
    overlap = len(at & bt) / min(len(at), len(bt))
    shared_entities = bool(story_entities(a) & story_entities(b))
    return overlap >= 0.55 or (shared_entities and overlap >= 0.35)

def is_duplicate_story(item: dict, selected: list[dict]) -> bool:
    return any(same_story(item, prev) for prev in selected)
'''

PL_BUILD_PAYLOAD = r'''def build_payload(items: list[dict]) -> dict:
    latest, used_links = [], set()
    for item in items:
        if item["link"] in used_links:
            continue
        if is_duplicate_story(item, latest):
            print(f"[INFO] duplicate story skipped: {item.get('source')} | {item.get('title')[:90]}", file=sys.stderr)
            continue
        latest.append(item)
        used_links.add(item["link"])
        if len(latest) >= MAX_ITEMS:
            break
    return {
        "language": "pl",
        "updated_at": datetime.now().astimezone().isoformat(timespec="minutes"),
        "quality_mode": "important-news-v4-same-story-dedupe",
        "count": len(latest),
        "latest": strip_internal(latest),
        "radar": [],
    }
'''

EN_BUILD_PAYLOAD = r'''def build_payload(items):
    latest = []
    seen = set()
    for item in items:
        if item["link"] in seen:
            continue
        if is_duplicate_story(item, latest):
            print(f"[INFO] duplicate story skipped: {item.get('source')} | {item.get('title')[:90]}")
            continue
        latest.append(item)
        seen.add(item["link"])
        if len(latest) >= MAX_ITEMS:
            break
    return {"language": "en", "updated_at": datetime.now().astimezone().isoformat(timespec="minutes"), "quality_mode": "important-news-v4-same-story-dedupe", "count": len(latest), "latest": strip_internal(latest), "radar": []}
'''

# Minimal standalone JSON dedupe for current payloads.
STANDALONE_STOPWORDS = set("""
the a an and or ale albo oraz dla from with about jest are was were said says podaje wedlug według reuters pap bankier tvn24 forum shutterstock
""".split())
STANDALONE_TRANSLATE = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
STANDALONE_PATTERNS = {
    "trump": r"\btrump\b|donald\s+trump",
    "greenland": r"grenland|greenland",
    "russia": r"rosj|russia|russian|kreml|moscow|moskw",
    "ukraine": r"ukrain|zelensk|budanow|budanov|kyiv|kiev",
    "lpg_cars": r"lpg|instalacj.*gaz|przerob.*samochod|kolejk.*wrzesn|rafiner",
    "patriot": r"patriot|pac-3|lockheed",
    "nato": r"\bnato\b|sojusz|alliance",
    "gold_nbp": r"nbp|zloto|złoto|gold|uncj",
}

def patch_builder(path: Path, helper: str, build_payload: str, typed: bool) -> None:
    text = path.read_text(encoding="utf-8")
    if "STORY_DEDUPE_V4" not in text:
        text = text.replace("\ndef build_payload", helper + "\n\ndef build_payload")
    if typed:
        text = re.sub(r"def build_payload\(items: list\[dict\]\) -> dict:[\s\S]*?\n\ndef main\(\):", build_payload + "\n\ndef main():", text)
    else:
        text = re.sub(r"def build_payload\(items\):[\s\S]*?\n\ndef main\(\):", build_payload + "\n\ndef main():", text)
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"patched {path}")

def norm_text(text: str) -> str:
    text = (text or "").lower().translate(STANDALONE_TRANSLATE)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def standalone_blob(item: dict) -> str:
    return norm_text(" ".join(str(item.get(k, "")) for k in ("title", "summary", "details", "link")))

def standalone_entities(item: dict) -> set[str]:
    blob = standalone_blob(item)
    return {k for k, p in STANDALONE_PATTERNS.items() if re.search(p, blob, re.I)}

def standalone_tokens(item: dict) -> set[str]:
    blob = standalone_blob(item)
    toks = {t for t in blob.split() if len(t) >= 4 and t not in STANDALONE_STOPWORDS and not t.isdigit()}
    toks |= standalone_entities(item)
    return toks

def standalone_same_story(a: dict, b: dict) -> bool:
    if a.get("link") and a.get("link") == b.get("link"):
        return True
    ae, be = standalone_entities(a), standalone_entities(b)
    if len(ae) >= 2 and ae == be:
        return True
    at, bt = standalone_tokens(a), standalone_tokens(b)
    if not at or not bt:
        return False
    overlap = len(at & bt) / min(len(at), len(bt))
    return overlap >= 0.55 or (bool(ae & be) and overlap >= 0.35)

def dedupe_json(path: Path) -> None:
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    latest = data.get("latest") or []
    out = []
    for item in latest:
        if any(standalone_same_story(item, prev) for prev in out):
            print(f"removed duplicate from {path}: {item.get('title')}")
            continue
        out.append(item)
    if len(out) != len(latest):
        data["latest"] = out
        data["count"] = len(out)
        data["quality_mode"] = "important-news-v4-same-story-dedupe"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

def main() -> None:
    patch_builder(PL_BUILDER, PL_HELPERS, PL_BUILD_PAYLOAD, typed=True)
    patch_builder(EN_BUILDER, EN_HELPERS, EN_BUILD_PAYLOAD, typed=False)
    dedupe_json(PL_JSON)
    dedupe_json(EN_JSON)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build /pl/home_brief.json with stricter editorial quality.

Rules:
- homepage is for important news, not low-value programme announcements;
- priority is given to items explicitly marked by publishers as urgent/breaking
  ("Pilne", "alert", "z ostatniej chwili", etc.);
- prefer PAP/Reuters/Stooq/Bankier-style factual wires and market/news sources;
- always provide an image, using an editorial placeholder only as fallback;
- summaries must be short, clean and readable, not raw RSS/Twitter/photo-credit text.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlparse

import feedparser
import requests

OUT_PATH = "pl/home_brief.json"
TIMEOUT = 9
USER_AGENT = "BriefRoomsBot/2.0 (+https://briefrooms.com)"
AI_MODEL = os.getenv("NEWS_AI_MODEL") or os.getenv("BRIEFROOMS_AI_MODEL") or "gpt-4o-mini"
AI_CACHE_PATH = ".cache/home_brief_pl_ai.json"
MAX_ITEMS = 12

# Direct feeds + Google News RSS searches that often surface PAP/Reuters/Stooq/Bankier items.
# "Pilne" is first on purpose: publishers' urgent/breaking labels get first chance and extra score.
FEEDS = {
    "Pilne": [
        "https://news.google.com/rss/search?q=" + quote_plus('("pilne" OR "z ostatniej chwili" OR "alert" OR "ważne") (PAP OR Reuters OR TVN24 OR Polsat News OR Bankier OR Stooq)') + "&hl=pl&gl=PL&ceid=PL:pl",
        "https://news.google.com/rss/search?q=" + quote_plus('("Pilne" OR "Alert") Ukraina Rosja NATO USA Chiny Iran ropa inflacja stopy giełda') + "&hl=pl&gl=PL&ceid=PL:pl",
        "https://tvn24.pl/najnowsze.xml",
        "https://www.polsatnews.pl/rss/wszystkie.xml",
    ],
    "Wiadomości": [
        "https://www.pap.pl/rss.xml",
        "https://tvn24.pl/najnowsze.xml",
        "https://tvn24.pl/polska.xml",
        "https://www.polsatnews.pl/rss/wszystkie.xml",
    ],
    "Ekonomia": [
        "https://www.bankier.pl/rss/wiadomosci.xml",
        "https://www.bankier.pl/rss/gospodarka.xml",
        "https://news.google.com/rss/search?q=" + quote_plus("site:stooq.pl gospodarka OR giełda OR PAP OR Reuters") + "&hl=pl&gl=PL&ceid=PL:pl",
        "https://news.google.com/rss/search?q=" + quote_plus("site:bankier.pl Reuters OR PAP OR inflacja OR stopy") + "&hl=pl&gl=PL&ceid=PL:pl",
    ],
    "Geopolityka": [
        "https://news.google.com/rss/search?q=" + quote_plus("PAP Reuters Ukraina NATO Rosja Bliski Wschód") + "&hl=pl&gl=PL&ceid=PL:pl",
        "https://news.google.com/rss/search?q=" + quote_plus("site:stooq.pl Ukraina NATO ropa sankcje Reuters") + "&hl=pl&gl=PL&ceid=PL:pl",
    ],
    "Zdrowie": [
        "https://news.google.com/rss/search?q=" + quote_plus("PAP zdrowie NFZ szpitale leki epidemia") + "&hl=pl&gl=PL&ceid=PL:pl",
        "https://www.who.int/feeds/entity/mediacentre/news/en/rss.xml",
    ],
    "Nauka": [
        "https://news.google.com/rss/search?q=" + quote_plus("PAP nauka technologia badania kosmos") + "&hl=pl&gl=PL&ceid=PL:pl",
        "https://www.esa.int/rssfeed/Our_Activities/Space_Science",
        "https://www.nasa.gov/news-release/feed/",
    ],
}

SOURCE_NAMES = [
    (r"stooq\.pl", "Stooq"),
    (r"pap\.pl", "PAP"),
    (r"reuters\.com|reuters", "Reuters"),
    (r"bankier\.pl", "Bankier.pl"),
    (r"tvn24\.pl", "TVN24"),
    (r"polsatnews\.pl", "Polsat News"),
    (r"bbc\.", "BBC"),
    (r"cnn\.com", "CNN"),
    (r"who\.int", "WHO"),
    (r"nasa\.gov", "NASA"),
    (r"esa\.int", "ESA"),
]

BAN = re.compile(
    r"horoskop|quiz|galeria|plotk|sponsorowany|lotto|eurojackpot|kupon|promocja|"
    r"na żywo|relacja live|transmisja|będzie gościem|gościem programu|w programie|"
    r"graffiti|rozmowa dnia|poranna rozmowa|wywiad w|zapraszamy na|wcześniejsze odcinki|"
    r"wpis anny lewandowskiej|okiem diabła|sport|plusliga|siatkówka|piłka nożna",
    re.I,
)
LOW_VALUE = re.compile(r"będzie gościem|transmisja|programu w polsacie|wywiad|zapraszamy|obejrzyj|wideo|w studiu", re.I)
NOISE = re.compile(
    r"cookie|cookies|reklama|advertisement|subskryb|newsletter|zaloguj|privacy|rodo|"
    r"wyrażam zgodę|czytaj także|zobacz także|prześlij zdjęcie|przyślij zdjęcie|wrzutnia@|"
    r"wcześniejsze odcinki|materiał partnera",
    re.I,
)
PHOTO_CREDIT = re.compile(
    r"^(?:[A-Z0-9_@./\-\s]{2,80}|[\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ .-]{2,80})\s*/\s*/\s*"
    r"(?:Reuters|Forum|Shutterstock|Twitter|X|Wikipedia|PAP|AP|Getty|Bloomberg)?\s*",
    re.I,
)
MOJIBAKE = re.compile(r"[ÅÄÂÃâ€™â€œâ€\x9c\x9d\x80\x99]")
IMG_META = re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)["\']', re.I)
IMG_META_ALT = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']', re.I)
URGENT = re.compile(
    r"\b(pilne|alert|ważne|z ostatniej chwili|wiadomość z ostatniej chwili|breaking|urgent|just in|flash)\b|"
    r"^\s*(pilne|alert|ważne)\s*[:\-–—]",
    re.I,
)
IMPORTANT = re.compile(
    r"wojna|nato|ukraina|rosja|trump|usa|chiny|iran|ormuz|ropa|gaz|sankcj|cła|"
    r"inflacj|stopy|fed|ecb|nbp|giełd|złoty|dolar|rentowno|recesj|pkb|"
    r"nfz|szpital|lek|epidem|cyber|atak|bezpieczeń|patriot|okręt|obrona",
    re.I,
)
SOURCE_WEIGHT = {"Reuters": 42, "PAP": 40, "Stooq": 38, "Bankier.pl": 26, "TVN24": 20, "BBC": 18, "WHO": 18, "NASA": 16, "ESA": 16, "Polsat News": 8}


def load_cache(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_cache(path: str, cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception as ex:
        print(f"[WARN] cache write failed: {ex}", file=sys.stderr)


CACHE = load_cache(AI_CACHE_PATH)


def fix_encoding(value: str) -> str:
    value = value or ""
    if MOJIBAKE.search(value):
        for src in ("latin1", "cp1252"):
            try:
                fixed = value.encode(src, errors="ignore").decode("utf-8", errors="ignore")
                if fixed and len(MOJIBAKE.findall(fixed)) < len(MOJIBAKE.findall(value)):
                    value = fixed
                    break
            except Exception:
                pass
    return value.replace("Â", "")


def clean_text(value: str, max_len: int = 190) -> str:
    value = fix_encoding(value)
    value = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"\s*/\s*/\s*", " / ", value)
    value = PHOTO_CREDIT.sub("", value).strip()
    value = re.sub(r"^@?[A-Z0-9_]+\s*/\s*(?:Twitter|X)?\s*", "", value, flags=re.I)
    value = re.sub(r"\b(PRZYŚLIJ|PRZESLIJ|PRZEŚLIJ)\b[\s\S]*$", "", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip(" -–—·•/\t\n\r")
    if len(value) <= max_len:
        return value
    cut = value[: max_len + 1]
    end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    if end > 80:
        return cut[: end + 1].strip()
    return cut[:max_len].rsplit(" ", 1)[0].strip() + "…"


def ensure_period(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text + "." if text and text[-1] not in ".!?…" else text


def is_urgent_text(*parts: str) -> bool:
    return bool(URGENT.search(" ".join(str(p or "") for p in parts)))


def source_name(url: str, raw: str = "") -> str:
    text = f"{url} {raw}"
    host = urlparse(url).netloc.replace("www.", "")
    for pattern, name in SOURCE_NAMES:
        if re.search(pattern, text, re.I):
            return name
    if "news.google." in host:
        return "Google News"
    return host or "Źródło"


def final_url(link: str) -> str:
    # Google News links are acceptable as source links, but direct publisher links are preferred.
    return link or ""


def sentences(text: str) -> list[str]:
    text = clean_text(text, 2200).replace("…", ".")
    parts = re.findall(r"[^.!?]+[.!?]+|[^.!?]+$", text)
    out = []
    for p in parts:
        p = ensure_period(clean_text(p, 260))
        if len(p) >= 45 and not NOISE.search(p) and not LOW_VALUE.search(p):
            out.append(p)
    return out


def html_to_article_text(raw_html: str) -> str:
    raw_html = fix_encoding(raw_html[:280000])
    raw_html = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<noscript[\s\S]*?</noscript>", " ", raw_html, flags=re.I)
    paras = re.findall(r"<p[^>]*>([\s\S]*?)</p>", raw_html, flags=re.I)
    cleaned = []
    for p in paras[:34]:
        t = clean_text(p, 800)
        if len(t) >= 55 and not NOISE.search(t) and not LOW_VALUE.search(t):
            cleaned.append(t)
        if len(cleaned) >= 9:
            break
    return " ".join(cleaned)


def article_excerpt(url: str) -> str:
    if not url.startswith("http"):
        return ""
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if not r.ok:
            return ""
        return html_to_article_text(r.text)
    except Exception:
        return ""


def fallback_image(category: str, title: str) -> str:
    color = "38d6c9"
    if "geo" in category.lower() or "wiadomo" in category.lower() or "pilne" in category.lower(): color = "ffd15e"
    if "zdrow" in category.lower(): color = "86ffb7"
    if "nauk" in category.lower(): color = "7fc8ff"
    label = html.escape(category[:18] or "Brief")
    svg = f"<svg xmlns='http://www.w3.org/2000/svg' width='1200' height='720'><defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop stop-color='%23061526'/><stop offset='1' stop-color='%23{color}' stop-opacity='.55'/></linearGradient></defs><rect width='1200' height='720' fill='url(%23g)'/><circle cx='220' cy='160' r='150' fill='%23{color}' opacity='.22'/><path d='M120 520 C320 320 520 440 720 250 S990 180 1120 300' stroke='%23{color}' stroke-width='20' fill='none' opacity='.65'/><text x='82' y='620' font-family='Arial' font-size='62' font-weight='800' fill='white'>{label}</text></svg>"
    return "data:image/svg+xml;charset=utf-8," + quote_plus(svg)


def is_image_url(url: str) -> bool:
    if not url:
        return False
    low = url.lower().split("?", 1)[0]
    return low.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg")) or "image" in low or "galeria.bankier.pl" in low


def article_og_image(url: str) -> str:
    if not url.startswith("http"):
        return ""
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if not r.ok:
            return ""
        head = r.text[:180000]
        m = IMG_META.search(head) or IMG_META_ALT.search(head)
        return urljoin(url, html.unescape(m.group(1).strip())) if m else ""
    except Exception:
        return ""


def entry_image(entry, article_url: str, category: str, title: str) -> str:
    for key in ("media_content", "media_thumbnail"):
        for item in entry.get(key) or []:
            url = item.get("url", "") if isinstance(item, dict) else ""
            if is_image_url(url):
                return url
    for enc in entry.get("enclosures") or []:
        url = enc.get("href") or enc.get("url") or ""
        mime = enc.get("type") or ""
        if is_image_url(url) or mime.startswith("image/"):
            return url
    for field in (entry.get("summary", ""), entry.get("description", "")):
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', field or "", re.I)
        if m and is_image_url(m.group(1)):
            return urljoin(article_url, m.group(1))
    return article_og_image(article_url) or fallback_image(category, title)


def ai_summary(title: str, category: str, source: str, text: str, link: str) -> str:
    base = clean_text(text, 1800)
    if not base:
        return ""
    key = hashlib.sha256(f"home-pl-quality-v3|{title}|{link}|{base[:400]}".encode("utf-8")).hexdigest()[:40]
    cached = CACHE.get(key)
    if isinstance(cached, dict) and cached.get("summary"):
        return cached["summary"]
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return ""
    prompt = f"""Zredaguj krótki opis do karty BriefRooms. Zwróć wyłącznie JSON: {{"summary":"..."}}.
Zasady: po polsku; 1–2 proste zdania; maks. 230 znaków; sedno sprawy; jeśli tekst jest oznaczony jako Pilne/Alert, zachowaj wagę informacji, ale nie twórz sensacji; bez surowych nicków typu @MON_GOV_PL; bez zapowiedzi programu/wywiadu; nie dopisuj faktów spoza tekstu.
Kategoria: {category}
Źródło: {source}
Tytuł: {title}
Tekst: {base}
"""
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": AI_MODEL, "messages": [{"role": "system", "content": "Jesteś redaktorem depesz BriefRooms. Zwracasz wyłącznie JSON."}, {"role": "user", "content": prompt}], "temperature": 0.1, "max_tokens": 180},
            timeout=28,
        )
        resp.raise_for_status()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", resp.json()["choices"][0]["message"]["content"].strip(), flags=re.I | re.S)
        data = json.loads(raw)
        summary = ensure_period(clean_text(str(data.get("summary", "")), 240))
        if summary and not LOW_VALUE.search(summary) and not MOJIBAKE.search(summary):
            CACHE[key] = {"summary": summary, "model": AI_MODEL}
            save_cache(AI_CACHE_PATH, CACHE)
            return summary
    except Exception as ex:
        print(f"[WARN] AI summary failed: {source} | {title[:80]} :: {ex}", file=sys.stderr)
    return ""


def editorial_summary(title: str, article_text: str, rss_summary: str, category: str, source: str, link: str) -> str:
    source_text = article_text or rss_summary or title
    ai = ai_summary(title, category, source, source_text, link)
    if ai:
        return ai
    sents = sentences(source_text)
    if sents:
        summary = " ".join(sents[:2])
    else:
        summary = clean_text(source_text, 230)
    summary = ensure_period(clean_text(summary, 240))
    if summary.lower().startswith(("twitter ", "x ", "@")):
        summary = re.sub(r"^@?\w+\s*/?\s*(?:Twitter|X)?\s*", "", summary, flags=re.I)
    return summary or "Krótko: sprawa ma znaczenie publiczne i warto przeczytać źródło."


def details_summary(text: str, fallback: str) -> str:
    sents = sentences(text or fallback)
    if not sents:
        return ensure_period(clean_text(fallback, 480))
    return clean_text(" ".join(sents[:4]), 620)


def assign_category(original: str, title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()
    if original == "Pilne":
        return "Pilne"
    if re.search(r"nato|ukrain|rosj|wojn|patriot|ormuz|iran|usa|trump|chiny|sankcj|cła|okręt|obron", text):
        return "Geopolityka"
    if re.search(r"nfz|szpital|zdrow|lek|epidem|pacjent", text):
        return "Zdrowie"
    if re.search(r"nauk|badani|kosmos|technolog|ai|sztuczn", text):
        return "Nauka"
    if re.search(r"inflacj|stopy|giełd|bank|ropa|gaz|złoty|dolar|pkb|spółk|rynek|walmart|prom|port", text):
        return "Ekonomia"
    return original if original != "Sport" else "Wiadomości"


def entry_timestamp(entry) -> float:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc).timestamp()
        except Exception:
            pass
    return time.time()


def quality_score(item: dict) -> int:
    text = f"{item.get('title','')} {item.get('summary','')} {item.get('details','')}"
    src = item.get("source", "")
    score = SOURCE_WEIGHT.get(src, 10)
    if item.get("urgent") or is_urgent_text(text):
        score += 90
    if IMPORTANT.search(text):
        score += 35
    if item.get("image") and not str(item.get("image", "")).startswith("data:image"):
        score += 8
    if item.get("category") in {"Pilne", "Geopolityka", "Ekonomia", "Zdrowie"}:
        score += 10
    if LOW_VALUE.search(text) or BAN.search(text):
        score -= 100
    if MOJIBAKE.search(text):
        score -= 80
    if len(clean_text(item.get("summary", ""), 500)) < 60:
        score -= 15
    return score


def make_item(entry, category: str) -> dict | None:
    title = clean_text(entry.get("title", ""), 96).rstrip(".")
    link = final_url(entry.get("link", ""))
    rss_raw = entry.get("summary", "") or entry.get("description", "") or title
    rss_summary = clean_text(rss_raw, 260)
    if not title or not link:
        return None
    if BAN.search(f"{title} {rss_summary}") or LOW_VALUE.search(f"{title} {rss_summary}"):
        return None
    article_text = article_excerpt(link)
    source = source_name(link, f"{title} {rss_summary}")
    urgent = category == "Pilne" or is_urgent_text(title, rss_summary, article_text)
    category = assign_category(category, title, article_text or rss_summary)
    if urgent:
        category = "Pilne"
    summary = editorial_summary(title, article_text, rss_summary, category, source, link)
    details = details_summary(article_text, summary)
    if BAN.search(f"{title} {summary} {details}") or LOW_VALUE.search(f"{title} {summary} {details}"):
        return None
    item = {
        "category": category,
        "title": title,
        "summary": summary,
        "details": details,
        "source": source,
        "link": link,
        "image": entry_image(entry, link, category, title),
        "time": "dzisiaj",
        "urgent": urgent,
        "priority_reason": "publisher_marked_urgent" if urgent else "standard_importance",
        "ts": entry_timestamp(entry),
    }
    item["quality_score"] = quality_score(item)
    if item["quality_score"] < 15:
        return None
    return item


def collect_items() -> list[dict]:
    seen, items = set(), []
    for category, feeds in FEEDS.items():
        per_feed_limit = 28 if category == "Pilne" else 18
        for feed_url in feeds:
            try:
                parsed = feedparser.parse(feed_url)
            except Exception as ex:
                print(f"[WARN] feed failed {feed_url}: {ex}", file=sys.stderr)
                continue
            for entry in parsed.entries[:per_feed_limit]:
                item = make_item(entry, category)
                if not item:
                    continue
                norm = re.sub(r"\W+", "", item["title"].lower())[:90]
                if norm in seen:
                    continue
                seen.add(norm)
                items.append(item)
    # Priority order: publisher-marked urgent first, then score, then recency.
    items.sort(key=lambda x: (1 if x.get("urgent") else 0, x.get("quality_score", 0), x.get("ts", 0)), reverse=True)
    return items


def strip_internal(items: list[dict]) -> list[dict]:
    out = []
    for item in items:
        x = dict(item)
        x.pop("ts", None)
        x.pop("quality_score", None)
        out.append(x)
    return out


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


def build_payload(items: list[dict]) -> dict:
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
        "quality_mode": "important-news-v5-urgent-priority-same-story-dedupe",
        "urgent_methodology": "Najpierw pobierane i punktowane są wpisy oznaczone przez wydawców jako Pilne, Alert, Ważne, Z ostatniej chwili, Breaking lub podobne. Takie newsy dostają priorytet przed zwykłymi wpisami, o ile nie są liveblogiem, zapowiedzią programu ani treścią niskiej wartości.",
        "count": len(latest),
        "latest": strip_internal(latest),
        "radar": [],
    }


def main():
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    payload = build_payload(collect_items())
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"✓ Wygenerowano {OUT_PATH}: {len(payload['latest'])} ważnych briefów, priorytet pilnych newsów aktywny")


if __name__ == "__main__":
    main()

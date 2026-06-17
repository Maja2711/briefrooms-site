#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build /pl/home_brief.json for the Polish BriefRooms homepage.

Rules for the Polish page:
- every visible title and summary in /pl must be Polish;
- English-language RSS sources may be used only after translation to Polish;
- if OPENAI_API_KEY is missing or translation fails, the English item is filtered out;
- /en is not touched by this script.
"""

import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import feedparser
import requests

OUT_PATH = "pl/home_brief.json"
TIMEOUT = 8
USER_AGENT = "BriefRoomsBot/1.0 (+https://briefrooms.com)"
AI_MODEL = os.getenv("NEWS_AI_MODEL") or os.getenv("BRIEFROOMS_AI_MODEL") or "gpt-4o-mini"
AI_CACHE_PATH = ".cache/home_brief_pl_ai.json"

FEEDS = {
    "Polityka / kraj": [
        "https://tvn24.pl/najnowsze.xml",
        "https://tvn24.pl/polska.xml",
        "https://www.polsatnews.pl/rss/wszystkie.xml",
        "https://www.pap.pl/rss.xml",
    ],
    "Ekonomia": [
        "https://www.bankier.pl/rss/wiadomosci.xml",
        "https://www.bankier.pl/rss/gospodarka.xml",
        "https://www.pap.pl/rss.xml",
    ],
    "Sport": [
        "https://www.polsatsport.pl/rss/wszystkie.xml",
        "https://sport.tvp.pl/rss",
        "https://przegladsportowy.onet.pl/.feed",
        "https://sportowefakty.wp.pl/rss.xml",
    ],
    "Nauka": [
        "https://www.esa.int/rssfeed/Our_Activities/Space_Science",
        "https://www.nasa.gov/news-release/feed/",
        "https://www.sciencedaily.com/rss/top/science.xml",
    ],
    "Zdrowie": [
        "https://www.who.int/feeds/entity/mediacentre/news/en/rss.xml",
        "https://www.cdc.gov/media/rss.htm",
        "https://www.nhs.uk/news/feed/",
    ],
}

SOURCE_NAMES = [
    (r"tvn24\.pl", "TVN24"),
    (r"polsatnews\.pl", "Polsat News"),
    (r"pap\.pl", "PAP"),
    (r"bankier\.pl", "Bankier.pl"),
    (r"polsatsport\.pl", "Polsat Sport"),
    (r"sport\.tvp\.pl", "TVP Sport"),
    (r"przegladsportowy\.onet\.pl|sport\.onet\.pl", "Przegląd Sportowy"),
    (r"sportowefakty\.wp\.pl", "SportoweFakty WP"),
    (r"nasa\.gov", "NASA"),
    (r"esa\.int", "ESA"),
    (r"sciencedaily\.com", "ScienceDaily"),
    (r"who\.int", "WHO"),
    (r"cdc\.gov", "CDC"),
    (r"nhs\.uk", "NHS"),
]

BAN = re.compile(r"horoskop|quiz|galeria|plotk|sponsorowany|lotto|eurojackpot|na żywo|relacja live", re.I)
IMG_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
IMG_META_RE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']',
    re.I,
)

EN_HOST_RE = re.compile(
    r"(nasa\.gov|esa\.int|sciencedaily\.com|who\.int|cdc\.gov|nhs\.uk|reuters\.com|bbc\.|apnews\.com|theguardian\.com)",
    re.I,
)
PL_CHARS_RE = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
COMMON_EN_RE = re.compile(
    r"\b(the|and|with|after|before|over|under|from|for|to|of|in|on|as|by|will|is|are|was|were|has|have|says|said|new|world|study|studies|research|scientists|science|space|galaxy|galaxies|star|stars|planet|planets|hubble|webb|nasa|reveals|shows|sees|finds|mission|health|disease|patients|risk)\b",
    re.I,
)
COMMON_PL_RE = re.compile(
    r"\b(i|oraz|że|się|jest|są|był|była|będzie|dla|przez|polska|polski|nauka|badanie|badania|kosmos|zdrowie|pacjent|ryzyko|pokazuje|odkrycie|naukowcy|galaktyk|misja)\b",
    re.I,
)


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
        print(f"[WARN] Nie udało się zapisać cache tłumaczeń: {ex}", file=sys.stderr)


CACHE = load_cache(AI_CACHE_PATH)


def clean_text(value: str, max_len: int = 190) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= max_len:
        return value
    cut = value[: max_len + 1]
    end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    if end > 80:
        return cut[: end + 1].strip()
    return cut[:max_len].rsplit(" ", 1)[0].strip() + "…"


def source_name(url: str) -> str:
    host = urlparse(url).netloc.replace("www.", "")
    for pattern, name in SOURCE_NAMES:
        if re.search(pattern, host, re.I):
            return name
    return host or "Źródło"


def ensure_period(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return text
    if text[-1] not in ".!?…":
        return text + "."
    return text


def translation_cache_key(title: str, link: str) -> str:
    raw = f"home-pl-v2|{title}|{link}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def likely_english_item(item: dict) -> bool:
    title = item.get("title", "") or ""
    summary = item.get("summary", "") or ""
    link = item.get("link", "") or ""
    text = f"{title} {summary}"
    host = urlparse(link).netloc.lower()

    if EN_HOST_RE.search(host):
        return True
    if PL_CHARS_RE.search(text) or COMMON_PL_RE.search(text):
        return False
    en_hits = len(COMMON_EN_RE.findall(text))
    pl_hits = len(COMMON_PL_RE.findall(text))
    return en_hits >= 2 and pl_hits == 0


def still_looks_english(text: str) -> bool:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return True
    if PL_CHARS_RE.search(text) or COMMON_PL_RE.search(text):
        return False
    en_hits = len(COMMON_EN_RE.findall(text))
    pl_hits = len(COMMON_PL_RE.findall(text))
    return en_hits >= 2 and pl_hits == 0


def apply_translation(item: dict, title_pl: str, summary_pl: str) -> dict | None:
    title_pl = clean_text(title_pl, 92).rstrip(".")
    summary_pl = ensure_period(clean_text(summary_pl, 150))
    if not title_pl or not summary_pl:
        return None
    if still_looks_english(title_pl) or still_looks_english(summary_pl):
        return None

    out = dict(item)
    out["title"] = title_pl
    out["summary"] = summary_pl
    if "brief po polsku" not in out.get("source", ""):
        out["source"] = f"{out.get('source', 'Źródło')} · brief po polsku"
    return out


def translate_english_item_to_polish(item: dict) -> dict | None:
    title = item.get("title", "") or ""
    summary = item.get("summary", "") or ""
    link = item.get("link", "") or ""
    source = item.get("source", "") or "Źródło"
    cache_key = translation_cache_key(title, link)

    cached = CACHE.get(cache_key)
    if isinstance(cached, dict):
        translated = apply_translation(item, cached.get("title_pl", ""), cached.get("summary_pl", ""))
        if translated:
            return translated

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print(f"[WARN] Pomijam anglojęzyczny wpis w PL, brak OPENAI_API_KEY: {source} | {title[:90]}", file=sys.stderr)
        return None

    prompt = f"""Przetłumacz i zredaguj anglojęzyczny wpis RSS do polskiej strony BriefRooms.
Zwróć wyłącznie poprawny JSON bez Markdown:
{{
  "title_pl": "krótki tytuł po polsku, maksymalnie 92 znaki",
  "summary_pl": "jedno krótkie zdanie po polsku, maksymalnie 150 znaków, z sednem informacji"
}}

Zasady:
- Wszystko, co zobaczy użytkownik, musi być po polsku.
- Nie zostawiaj angielskiego tytułu ani angielskiego opisu.
- Nie dopisuj faktów spoza tytułu i opisu RSS.
- Zachowaj nazwy własne, np. NASA, Hubble, ESA, ale resztę przetłumacz.
- Styl: krótko, informacyjnie, bez clickbaitu.

Kategoria: {item.get('category', '')}
Źródło: {source}
Tytuł oryginalny: {title}
Opis RSS: {summary}
URL: {link}
"""

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": AI_MODEL,
                "messages": [
                    {"role": "system", "content": "Jesteś polskim redaktorem BriefRooms. Zwracasz wyłącznie poprawny JSON."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 260,
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
        data = json.loads(raw)
        title_pl = str(data.get("title_pl", "")).strip()
        summary_pl = str(data.get("summary_pl", "")).strip()
        translated = apply_translation(item, title_pl, summary_pl)
        if not translated:
            print(f"[WARN] Tłumaczenie nadal wygląda na niepolskie, pomijam: {source} | {title[:90]}", file=sys.stderr)
            return None
        CACHE[cache_key] = {"title_pl": translated["title"], "summary_pl": translated["summary"], "model": AI_MODEL}
        save_cache(AI_CACHE_PATH, CACHE)
        return translated
    except Exception as ex:
        print(f"[WARN] Nie udało się przetłumaczyć wpisu PL: {source} | {title[:90]} -> {ex}", file=sys.stderr)
        return None


def entry_timestamp(entry) -> float:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc).timestamp()
        except Exception:
            pass
    return time.time()


def entry_image(entry, article_url: str) -> str:
    # RSS media extensions: feedparser normalizes many variants here.
    for key in ("media_content", "media_thumbnail"):
        media = entry.get(key) or []
        if isinstance(media, list):
            for item in media:
                url = item.get("url") if isinstance(item, dict) else ""
                if is_image_url(url):
                    return url

    # Enclosures.
    for enc in entry.get("enclosures") or []:
        url = enc.get("href") or enc.get("url") or ""
        mime = enc.get("type") or ""
        if is_image_url(url) or mime.startswith("image/"):
            return url

    # Some feeds keep the image inside summary/content HTML.
    for html_field in (entry.get("summary", ""), entry.get("description", "")):
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html_field or "", re.I)
        if m and is_image_url(m.group(1)):
            return urljoin(article_url, m.group(1))

    # Last resort: article page Open Graph image.
    return article_og_image(article_url)


def is_image_url(url: str) -> bool:
    if not url:
        return False
    low = url.lower().split("?", 1)[0]
    return low.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")) or "image" in low


def article_og_image(url: str) -> str:
    if not url.startswith("http"):
        return ""
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if not r.ok:
            return ""
        head = r.text[:180000]
        m = IMG_META_RE.search(head) or IMG_META_RE_ALT.search(head)
        if not m:
            return ""
        return urljoin(url, html.unescape(m.group(1).strip()))
    except Exception:
        return ""


def make_item(entry, category: str) -> dict | None:
    title = clean_text(entry.get("title", ""), 92)
    link = entry.get("link", "")
    summary_raw = entry.get("summary", "") or entry.get("description", "") or title
    summary = clean_text(summary_raw, 150)
    if not title or not link or BAN.search(f"{title} {summary}"):
        return None

    item = {
        "category": category,
        "title": title,
        "summary": summary or "Krótki kontekst i link do źródła.",
        "source": source_name(link),
        "link": link,
        "image": entry_image(entry, link),
        "time": "dzisiaj",
        "ts": entry_timestamp(entry),
    }

    if likely_english_item(item):
        return translate_english_item_to_polish(item)
    return item


def collect_items() -> list[dict]:
    seen = set()
    items = []
    for category, feeds in FEEDS.items():
        for feed_url in feeds:
            try:
                parsed = feedparser.parse(feed_url)
            except Exception:
                continue
            for entry in parsed.entries[:12]:
                item = make_item(entry, category)
                if not item:
                    continue
                norm = re.sub(r"\W+", "", item["title"].lower())[:80]
                if norm in seen:
                    continue
                seen.add(norm)
                items.append(item)
    items.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return items


def build_payload(items: list[dict]) -> dict:
    latest = []
    used_categories = set()
    for item in items:
        if item["category"] not in used_categories:
            latest.append(item)
            used_categories.add(item["category"])
        if len(latest) >= 4:
            break
    if len(latest) < 4:
        for item in items:
            if item not in latest:
                latest.append(item)
            if len(latest) >= 4:
                break

    radar = [item for item in items if item not in latest][:4]
    return {
        "updated_at": datetime.now().astimezone().isoformat(timespec="minutes"),
        "count": min(len(items), 24),
        "latest": strip_ts(latest),
        "radar": strip_ts(radar),
    }


def strip_ts(items: list[dict]) -> list[dict]:
    out = []
    for item in items:
        x = dict(item)
        x.pop("ts", None)
        out.append(x)
    return out


def main():
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    payload = build_payload(collect_items())
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"✓ Wygenerowano {OUT_PATH}: {len(payload['latest'])} briefów + {len(payload['radar'])} radar")


if __name__ == "__main__":
    main()

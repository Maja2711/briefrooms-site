#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import re
import json
import html
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
from dateutil import tz
import requests

# =========================
# STREFA CZASOWA
# =========================
TZ = tz.gettz("Europe/Warsaw")

# =========================
# KONFIG
# =========================
MAX_PER_SECTION = 5
MAX_PER_HOST = 2
MAX_ITEM_AGE_DAYS = 10
HOTBAR_LIMIT = 12

AI_ENABLED = bool(os.getenv("OPENAI_API_KEY"))
AI_MODEL = os.getenv("NEWS_AI_MODEL", "gpt-4o-mini")

# osobny plik na cache AI
AI_CACHE_PATH = ".cache/ai_cache_pl.json"
# plik, z którego czyta hotbar.js
HOTBAR_JSON_PATH = ".cache/news_summaries_pl.json"
CLOUDFLARE_WEB_ANALYTICS = (
    "<!-- Cloudflare Web Analytics --><script defer src='https://static.cloudflareinsights.com/beacon.min.js' "
    "data-cf-beacon='{\"token\": \"9adde99e330a4b0d991627986ac34246\"}'></script><!-- End Cloudflare Web Analytics -->"
)

SOURCE_PROFILES = {
    "pap.pl": {"name": "PAP", "score": 60, "sections": {"polityka", "biznes", "sport", "zdrowie", "nauka"}},
    "polsatnews.pl": {"name": "Polsat News", "score": 52, "sections": {"polityka", "biznes", "sport", "zdrowie", "nauka"}},
    "polsatsport.pl": {"name": "Polsat Sport", "score": 46, "sections": {"sport"}},
    "tvn24.pl": {"name": "TVN24", "score": 50, "sections": {"polityka", "biznes", "sport", "zdrowie", "nauka"}},
    "reuters.com": {"name": "Reuters", "score": 48, "sections": {"polityka", "biznes", "sport", "zdrowie", "nauka"}},
    "bloomberg.com": {"name": "Bloomberg", "score": 46, "sections": {"polityka", "biznes", "nauka"}},
    "gov.pl": {"name": "gov.pl", "score": 44, "sections": {"polityka", "biznes", "zdrowie", "nauka"}},
    "stat.gov.pl": {"name": "GUS", "score": 46, "sections": {"biznes"}},
    "nbp.pl": {"name": "NBP", "score": 46, "sections": {"biznes"}},
    "mf.gov.pl": {"name": "Ministerstwo Finansów", "score": 44, "sections": {"biznes"}},
}

# Feedy do doboru newsów (PL)
FEEDS = {
    "polityka": [
        "https://www.pap.pl/rss.xml",
        "https://www.polsatnews.pl/rss/kraj.xml",
        "https://www.polsatnews.pl/rss/polska.xml",
        "https://www.polsatnews.pl/rss/swiat.xml",
        "https://tvn24.pl/polska.xml",
        "https://tvn24.pl/swiat.xml",
        "https://tvn24.pl/najnowsze.xml",
        "https://feeds.reuters.com/reuters/worldNews",
        "https://feeds.reuters.com/reuters/politicsNews",
        "https://feeds.bloomberg.com/politics/news.rss",
    ],
    "biznes": [
        "https://www.pap.pl/rss.xml",
        "https://www.polsatnews.pl/rss/biznes.xml",
        "https://www.polsatnews.pl/rss/kraj.xml",
        "https://tvn24.pl/biznes.xml",
        "https://tvn24.pl/najnowsze.xml",
        "https://feeds.reuters.com/reuters/businessNews",
        "https://feeds.bloomberg.com/markets/news.rss",
        "https://feeds.bloomberg.com/economics/news.rss",
        "https://www.nbp.pl/rss/aktualnosci.xml",
    ],
    "sport": [
        "https://www.pap.pl/rss.xml",
        "https://www.polsatnews.pl/rss/sport.xml",
        "https://tvn24.pl/sport.xml",
        "https://tvn24.pl/najnowsze.xml",
        "https://feeds.reuters.com/reuters/sportsNews",
    ],
    "zdrowie": [
        "https://www.pap.pl/rss.xml",
        "https://www.polsatnews.pl/rss/wszystkie.xml",
        "https://tvn24.pl/zdrowie.xml",
        "https://feeds.reuters.com/reuters/healthNews",
    ],
    "nauka": [
        "https://www.pap.pl/rss.xml",
        "https://www.polsatnews.pl/rss/nauka.xml",
        "https://www.polsatnews.pl/rss/wszystkie.xml",
        "https://tvn24.pl/nauka.xml",
        "https://tvn24.pl/najnowsze.xml",
        "https://feeds.reuters.com/reuters/scienceNews",
        "https://feeds.bloomberg.com/technology/news.rss",
    ],
}

# Zaufane źródła do korelacji (ogólne)
TRUSTED_CORRO_FEEDS = [
    "https://www.pap.pl/rss.xml",
    "https://feeds.reuters.com/reuters/worldNews",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/uk/rss.xml",
    "https://apnews.com/hub/apf-topnews?utm_source=apnews.com&utm_medium=referral&utm_campaign=rss",
]

# Zaufane źródła do zdrowia/nauki
OFFICIAL_SCIENCE_FEEDS = [
    "https://www.who.int/feeds/entity/mediacentre/news/en/rss.xml",
    "https://www.cdc.gov/media/rss.htm",
    "https://www.nhs.uk/news/feed/",
    "https://www.cochrane.org/news-feed.xml",
]

# Preferencje źródeł
SOURCE_PRIORITY = [
    (re.compile(r"pap\.pl", re.I), 25),
    (re.compile(r"polsatnews\.pl", re.I), 18),
    (re.compile(r"tvn24\.pl", re.I), 15),
    (re.compile(r"reuters\.com", re.I), 12),
    (re.compile(r"bloomberg\.com", re.I), 12),
    (re.compile(r"gov\.pl|stat\.gov\.pl|nbp\.pl|mf\.gov\.pl", re.I), 10),
]

# Wzmocnienia tematyczne
BOOST = {
    "polityka": [
        (re.compile(r"Polska|kraj|Sejm|Senat|prezydent|premier|ustawa|minister|rząd|samorząd|Trybunał|TK|SN|UE|KE|budżet|PKW", re.I), 30),
        (re.compile(r"inflacja|NBP|RPP|podatek|ZUS|emerytur|Orlen|PGE|LOT|PKP", re.I), 18),
    ],
    "biznes": [
        (re.compile(r"NBP|RPP|PKB|inflacja|stopy|obligacj|kredyt|VAT|CIT|PIT|GPW|WIG|paliw|energia|MWh", re.I), 28),
    ],
    "sport": [
        (re.compile(r"Iga Świątek|Swiatek|Hurkacz|Lewandowski|Zielińsk|Zielinsk|Stoch|Żyła|Zyla|Reprezentacja|Legia|Raków|Rakow|Lech|Skorupski", re.I), 40),
    ],
    "zdrowie": [
        (re.compile(r"zdrow|szpital|lekarz|pacjent|NFZ|lek|chorob|wirus|szczep|terapi|badanie kliniczne|WHO", re.I), 28),
    ],
    "nauka": [
        (re.compile(r"nauka|badanie|odkry|kosmos|AI|sztuczna inteligencja|klimat|technolog|energia|NASA|uczelnia", re.I), 28),
    ],
}

# Filtry
BAN_PATTERNS = [
    # śmieci / rozrywka / niska wartość informacyjna
    re.compile(r"horoskop|plotk|quiz|sponsorowany|galeria|zobacz zdjęcia|clickbait", re.I),

    # losowania, loterie, wyniki gier liczbowych
    re.compile(r"lotto|lotek|eurojackpot|ekstra pensja|ekstra premia|kaskada|mini lotto|multi multi|wyniki losowania", re.I),

    # celebryci / show-biznes / miękkie newsy
    re.compile(r"gwiazda|celebryt|piosenkarz|aktor|aktorka|influencer|tiktoker|youtuber|patostreamer", re.I),

    # sport/rozrywka poza sekcją sportową, jeśli trafi do polityki/kraju
    re.compile(r"taniec z gwiazdami|the voice|serial|film|koncert", re.I),
]
ROUNDUP_OR_LIVE_RE = re.compile(
    r"\b(live|na żywo|relacja live|relacja na żywo|wynik na żywo|minuta po minucie|transmisja)\b|"
    r"newsletter|podsumowanie|najważniejsze informacje|co wiemy|dzień w skrócie|roundup|"
    r"wszystko, co musisz wiedzieć|najważniejsze wydarzenia|gdzie obejrzeć|stream online|terminarz",
    re.I,
)
MULTI_TOPIC_MARKER_RE = re.compile(
    r"\b(tymczasem|jednocześnie|z kolei|w innym temacie|ponadto|oprócz tego|również dziś|w osobnej sprawie)\b",
    re.I,
)
CATEGORY_SEGMENTS = {
    "tag", "tagi", "temat", "tematy", "kategoria", "category", "rss", "wiadomosci",
    "najnowsze", "polska", "swiat", "biznes", "sport", "zdrowie", "nauka", "live",
}
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
POLITICS_TOPIC_RE = re.compile(
    r"Polska|kraj|Sejm|Senat|prezydent|premier|rząd|minister|ustawa|wybory|koalicj|opozycj|"
    r"\b(?:NATO|UE|USA|TK|SN)\b|Komisj[ai] Europejsk|Ukrain|Rosj|Chin|Trump|Zełensk|Putin|wojna|sankcj|"
    r"bezpieczeństw|dyplomacj|granica|migracj|sąd|prokuratur|Trybunał|polityk|parti",
    re.I,
)
BUSINESS_TOPIC_RE = re.compile(
    r"gospodark|biznes|rynk|giełd|\b(?:GPW|WIG|NBP|RPP|PKB|CIT|PIT|VAT|ZUS|CLO|ETF)\b|inflacj|stopy|budżet|deficyt|dług|"
    r"podat|bank|kredyt|obligacj|rentowno|energia|ropa|gaz|paliw|ceny|"
    r"spółk|firma|przemysł|handel|eksport|import|inwestycj|private debt|rates|credit",
    re.I,
)
SPORT_TOPIC_RE = re.compile(
    r"sport|mecz|liga|piłk|tenis|siatk|koszyk|F1|Formula|Grand Prix|UFC|MMA|NBA|ATP|WTA|Le Mans|"
    r"Lewandowski|Świątek|Swiatek|Hurkacz|Majchrzak|reprezentacj|Ekstraklas|Legia|Lech|Raków|Rakow|"
    r"zawodnik|trener|kajakar|mistrzostw|turniej|wyścig|Panthers|Wrocław",
    re.I,
)
HEALTH_TOPIC_RE = re.compile(
    r"zdrow|szpital|lekarz|pacjent|NFZ|lek\b|leki|chorob|wirus|epidemi|szczep|terapi|"
    r"badanie kliniczne|WHO|medyczn|farmaceut|diagnost|onkolog|serc|cukrzyc",
    re.I,
)
SCIENCE_TOPIC_RE = re.compile(
    r"nauka|badanie|odkryci|odkryli|odkryto|kosmos|NASA|ESA|technolog|\bAI\b|sztuczna inteligencja|klimat|energia|"
    r"robot|chip|półprzewodnik|quantum|kwant|uczelnia|naukow|laborator|biotechnolog|archeolog",
    re.I,
)
ACCIDENT_OR_CRIME_RE = re.compile(
    r"wypadek|kolizj|drogow|samochod|zderzeni|katastrof|zgin|śmierć|zabój|areszt|zatrzyman|pożar|tragedi|atak|rann",
    re.I,
)
SECURITY_TOPIC_RE = re.compile(
    r"wywiad|wojna|Ukrain|Rosj|żołnierz|sankcj|atak rakiet|armia|front|dementuje|bezpieczeństw",
    re.I,
)

# =========================
# TOKENIZACJA / PODOBIEŃSTWO
# =========================
STOP_PL = {
    "i","oraz","a","w","we","z","za","do","dla","na","o","u","od","po","pod","nad","przed",
    "jest","są","był","była","było","będzie","to","ten","ta","te","tych","tym","tą","że",
    "jak","kiedy","który","która","które","których","którym","którego","której","czy",
    "się","ze","go","jej","ich","jego","nią","nim","lub","albo","też","również",
    "–","—","-","\"","„","”","'", "…"
}
TOKEN_RE = re.compile(r"[A-Za-zĄąĆćĘęŁłŃńÓóŚśŹźŻż0-9]+")

def tokens_pl(text: str):
    toks = [t.lower() for t in TOKEN_RE.findall(text or "")]
    toks = [t for t in toks if t not in STOP_PL and len(t) > 2 and not t.isdigit()]
    return set(toks)

def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0

SIMILARITY_THRESHOLD = 0.70

# =========================
# HELPERY
# =========================
def norm_title(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"&\w+;|&#\d+;", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def host_of(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return ""

def clean_rss_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^<]+?>", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def source_profile_for(link: str, section_key: str = ""):
    host = host_of(link).lower().lstrip("www.")
    for domain in sorted(SOURCE_PROFILES, key=len, reverse=True):
        if host == domain or host.endswith("." + domain):
            profile = SOURCE_PROFILES[domain]
            if not section_key or section_key in profile["sections"]:
                return profile
    return None

def source_name(link: str, section_key: str = "") -> str:
    profile = source_profile_for(link, section_key)
    if profile:
        return profile["name"]
    return host_of(link).lower().lstrip("www.") or "Źródło"

def source_quality_score(link: str, section_key: str) -> int:
    profile = source_profile_for(link, section_key)
    return int(profile["score"]) if profile else 0

def is_concrete_article_url(link: str) -> bool:
    try:
        parsed = urlparse(link)
    except Exception:
        return False
    path = (parsed.path or "").strip("/")
    if not path:
        return False
    normalized_path = path.lower().replace("-", " ").replace("_", " ")
    normalized_query = (parsed.query or "").lower().replace("-", " ").replace("_", " ")
    if ROUNDUP_OR_LIVE_RE.search(normalized_path) or ROUNDUP_OR_LIVE_RE.search(normalized_query):
        return False
    segments = [seg.lower() for seg in path.split("/") if seg]
    if not segments:
        return False
    if len(segments) == 1 and segments[0] in CATEGORY_SEGMENTS:
        return False
    if segments[-1] in CATEGORY_SEGMENTS:
        return False
    if any(seg in {"tag", "tagi", "temat", "tematy", "kategoria", "category", "newsletter", "live"} for seg in segments):
        return False
    return True

def split_sentences(text: str):
    return [s.strip() for s in SENTENCE_RE.split(clean_rss_text(text)) if s.strip()]

def first_sentence(text: str) -> str:
    sentences = split_sentences(text)
    return sentences[0] if sentences else clean_rss_text(text)

def is_roundup_or_live(title: str, summary: str, link: str) -> bool:
    parsed = urlparse(link)
    path = parsed.path.replace("-", " ").replace("_", " ")
    query = parsed.query.replace("-", " ").replace("_", " ")
    blob = " ".join([title or "", summary or "", path, query])
    return bool(ROUNDUP_OR_LIVE_RE.search(blob))

def is_multitopic_summary(title: str, summary: str) -> bool:
    text = clean_rss_text(summary)
    if not text:
        return False
    sentences = split_sentences(text)
    if len(sentences) <= 1:
        return False
    if len(sentences) >= 2 and MULTI_TOPIC_MARKER_RE.search(text):
        return True
    if len(sentences) < 3:
        return False
    title_tokens = tokens_pl(title)
    unrelated = 0
    for sentence in sentences[1:]:
        sent_tokens = tokens_pl(sentence)
        if len(sent_tokens) >= 5 and jaccard(title_tokens, sent_tokens) < 0.08:
            unrelated += 1
    return unrelated >= 2

def topic_safe_snippet(title: str, summary: str) -> str:
    text = clean_rss_text(summary)
    if not text:
        return ""
    sentences = split_sentences(text)
    if is_multitopic_summary(title, text):
        return first_sentence(text)
    if len(sentences) > 2:
        return " ".join(sentences[:2])
    return text

def topic_tokens(title: str, summary: str):
    return tokens_pl(" ".join([title or "", first_sentence(summary or "")]))

def normalize_link_for_dedupe(link: str) -> str:
    parsed = urlparse(link or "")
    host = parsed.netloc.lower().lstrip("www.")
    path = (parsed.path or "").rstrip("/")
    return f"{host}{path}"

def is_recent_enough(published_parsed) -> bool:
    if not published_parsed:
        return True
    try:
        dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
    except Exception:
        return True
    age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    return -1 <= age_days <= MAX_ITEM_AGE_DAYS

def matches_section_topic(title: str, summary: str, link: str, section_key: str) -> bool:
    path = urlparse(link).path.replace("-", " ").replace("_", " ")
    blob = " ".join([title or "", summary or "", path])
    if section_key == "polityka":
        return bool(POLITICS_TOPIC_RE.search(blob)) and not SPORT_TOPIC_RE.search(blob)
    if section_key == "biznes":
        return bool(BUSINESS_TOPIC_RE.search(blob)) and not (SPORT_TOPIC_RE.search(blob) or ACCIDENT_OR_CRIME_RE.search(blob))
    if section_key == "sport":
        return bool(SPORT_TOPIC_RE.search(blob))
    if section_key == "zdrowie":
        return bool(HEALTH_TOPIC_RE.search(blob)) and not (SPORT_TOPIC_RE.search(blob) or ACCIDENT_OR_CRIME_RE.search(blob))
    if section_key == "nauka":
        if SECURITY_TOPIC_RE.search(blob) and not re.search(r"\bAI\b|cyber|technolog|chip|półprzewodnik", blob, re.I):
            return False
        return bool(SCIENCE_TOPIC_RE.search(blob)) and not SPORT_TOPIC_RE.search(blob)
    return True

def dedupe_sections(sections: dict) -> dict:
    seen = set()
    out = {}
    for key in ("polityka", "biznes", "sport", "zdrowie", "nauka"):
        kept = []
        for it in sections.get(key, []):
            marker = normalize_link_for_dedupe(it.get("link", ""))
            if marker in seen:
                continue
            seen.add(marker)
            kept.append(it)
        out[key] = kept
    return out

def should_keep_item(title: str, link: str, summary: str, section_key: str) -> bool:
    if source_profile_for(link, section_key) is None:
        return False
    if not is_concrete_article_url(link):
        return False
    if is_roundup_or_live(title, summary, link):
        return False
    if any(rx.search(title) or rx.search(summary) for rx in BAN_PATTERNS):
        return False
    if is_multitopic_summary(title, summary):
        return False
    if not matches_section_topic(title, summary, link, section_key):
        return False
    return True

def today_str() -> str:
    now = datetime.now(TZ)
    return now.strftime("%Y-%m-%d")


def today_str_pl() -> str:
    return datetime.now(TZ).strftime("%d.%m.%Y")

def inject_cloudflare_analytics(html_text: str) -> str:
    if "static.cloudflareinsights.com/beacon.min.js" in html_text:
        return html_text
    body_close = html_text.lower().rfind("</body>")
    if body_close != -1:
        return html_text[:body_close] + CLOUDFLARE_WEB_ANALYTICS + "\n" + html_text[body_close:]
    return html_text.rstrip() + "\n" + CLOUDFLARE_WEB_ANALYTICS + "\n</body>\n</html>"

def esc(s: str) -> str:
    return html.escape(s or "", quote=True)

def ensure_period(txt: str) -> str:
    txt = (txt or "").strip()
    if not txt:
        return txt
    if txt[-1] not in ".!?…":
        txt += "."
    return txt

def ensure_full_sentence(text: str, max_chars: int = 320) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    if len(t) > max_chars:
        t = t[:max_chars+1]
    end = max(t.rfind("."), t.rfind("!"), t.rfind("?"))
    if end != -1:
        t = t[:end+1]
    return t.strip()

def fallback_why_pl(title: str, snippet: str) -> str:
    basis = first_sentence(snippet) or title
    basis = ensure_full_sentence(basis, 190)
    if not basis:
        return "To ważne, bo artykuł dotyczy jednego konkretnego tematu wybranego z priorytetowego źródła."
    return ensure_full_sentence(f"To ważne, bo artykuł pokazuje konkretny kontekst tego wątku: {basis}", 280)

def score_item(item, section_key: str) -> float:
    score = 0.0
    source_score = source_quality_score(item.get("link", ""), section_key)
    score += source_score
    published_parsed = item.get("published_parsed") or item.get("updated_parsed")
    if published_parsed:
        dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        score += max(0.0, 36.0 - age_h)
    t = item.get("title", "") or ""
    for rx, pts in BOOST.get(section_key, []):
        if rx.search(t):
            score += pts
    h = host_of(item.get("link", "") or "")
    for rx, pts in SOURCE_PRIORITY:
        if rx.search(h):
            score += pts
    if source_score >= 50:
        score += 8
    if len(t) > 140:
        score -= 5
    if not item.get("summary_raw"):
        score -= 8
    return score

# =========================
# CACHE KOMENTARZY AI
# =========================
def load_cache(path: str):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_cache(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

CACHE = load_cache(AI_CACHE_PATH)

def ai_summarize_pl(title: str, snippet: str, url: str) -> dict:
    """
    Zwraca pola dla komentarza: Najważniejsze + Dlaczego to ważne.
    """
    key = os.getenv("OPENAI_API_KEY")
    cache_key = f"{norm_title(title)}|{today_str()}"
    if cache_key in CACHE:
        cached = CACHE[cache_key]
        cached.setdefault("key_point", cached.get("summary", ""))
        cached.setdefault("why_it_matters", fallback_why_pl(title, snippet))
        return cached

    safe_snippet = topic_safe_snippet(title, snippet)

    if not key:
        key_point = ensure_full_sentence((safe_snippet or title or "")[:320], 320)
        out = {
            "summary": key_point,
            "key_point": key_point,
            "why_it_matters": fallback_why_pl(title, safe_snippet),
            "uncertain": "",
            "model": "fallback"
        }
        CACHE[cache_key] = out
        save_cache(AI_CACHE_PATH, CACHE)
        return out

    prompt = f"""Napisz po polsku krótki komentarz do jednego konkretnego artykułu.
Użyj wyłącznie tytułu i bezpośredniego opisu RSS poniżej.
Zwróć dokładnie dwie linie:
Najważniejsze: ...
Dlaczego to ważne: ...

Tytuł: {title}
Opis RSS: {safe_snippet}

Zasady:
- Opisz tylko jeden temat z tego artykułu.
- Nie mieszaj kilku newsów ani kilku wątków.
- Jeśli opis RSS wygląda wielotematycznie, użyj tylko tytułu i pierwszego zdania opisu.
- Bądź konkretny, zwięzły i neutralny.
- Kończ zdania kropką.
- Nie dopisuj faktów spoza tytułu/opisu.
- Zwróć czysty tekst, linia po linii (bez formatowania)."""

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": AI_MODEL,
                "messages": [
                    {"role": "system", "content": "Jesteś rzetelnym i zwięzłym asystentem prasowym. Zawsze kończ zdania kropką."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 220,
            },
            timeout=25,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        key_point, why, warn_line = "", "", ""
        for ln in lines:
            if ln.lower().startswith("uwaga:"):
                warn_line = ln
            elif ln.lower().startswith("najważniejsze:") or ln.lower().startswith("najwazniejsze:"):
                key_point = ln.split(":", 1)[1].strip()
            elif ln.lower().startswith("dlaczego to ważne:") or ln.lower().startswith("dlaczego to wazne:"):
                why = ln.split(":", 1)[1].strip()
            else:
                key_point = key_point or ln

        summary = ensure_full_sentence(key_point or safe_snippet or title, 320)
        why = ensure_full_sentence(why, 280) or fallback_why_pl(title, safe_snippet)
        warn_line = ensure_period(warn_line) if warn_line else ""

        out = {"summary": summary, "key_point": summary, "why_it_matters": why, "uncertain": warn_line}
        CACHE[cache_key] = out
        save_cache(AI_CACHE_PATH, CACHE)
        return out
    except Exception:
        key_point = ensure_full_sentence((safe_snippet or title or "")[:320], 320)
        return {
            "summary": key_point,
            "key_point": key_point,
            "why_it_matters": fallback_why_pl(title, safe_snippet),
            "uncertain": ""
        }

# =========================
# WARSTWA WERYFIKACJI (PL)
# =========================
# „Mocne” słowa – wymagają potwierdzeń
STRONG_CLAIM_RE = re.compile(
    r"(skazany|skazana|skazano|aresztowano|udowodni|fałszerstw|rekordow|zginął|zginęła|ofiary|katastrof|wyciek|bankructw|zdrad[ay]|korupcj|manipulacj|złamał prawo|złamali prawo)",
    re.I
)

# Reguły prawne (PL)
LEGAL_RULES_PL = [
    (re.compile(r"(musi|będzie musiał|będzie musiała)\s+udowodnić\s+(swoją|swoja|swą|swa)?\s*niewinność", re.I),
     "w postępowaniu karnym obowiązuje domniemanie niewinności — to prokurator musi wykazać winę, nie oskarżony swoją niewinność."),
    (re.compile(r"\bskazany\b|\bskazana\b", re.I),
     "termin „skazany” dotyczy prawomocnego wyroku; wcześniejsze etapy to zatrzymanie, przedstawienie zarzutów lub akt oskarżenia."),
]

# Czerwone flagi nauka/zdrowie (PL)
SCIENCE_RED_FLAGS_PL = [
    (re.compile(r"\b(100%|gwarantuje|cudowny lek|zero ryzyka)\b", re.I),
     "absolutne twierdzenia („100%”, „cudowny lek”, „zero ryzyka”) rzadko mają solidne potwierdzenie."),
    (re.compile(r"\b(powoduje|udowodniono)\b", re.I),
     "stwierdzenie przyczynowości/„udowodniono” wymaga silnego projektu badania; nagłówki często opisują korelację."),
    (re.compile(r"\bpreprint\b|\bmedrxiv\b|\bbiorxiv\b", re.I),
     "preprinty nie są recenzowane; wnioski mogą się zmienić po recenzji."),
    (re.compile(r"\b(n=\s?\d{1,2}\b|\bpróba\s+\d{1,2}\b)", re.I),
     "bardzo mała próba ogranicza wiarygodność i uogólnianie wyników."),
]

def _apply_rules(rules, title: str, snippet: str):
    notes = []
    for rx, msg in rules:
        if rx.search(title) or rx.search(snippet):
            notes.append(msg)
    return notes

def _search_trusted_sources(title: str, feeds, min_sim=0.58, max_look=60):
    base_tok = tokens_pl(title)
    hits = []
    for f in feeds:
        try:
            p = feedparser.parse(f)
            for e in p.entries[:max_look]:
                t = (e.get("title") or "").strip()
                if not t:
                    continue
                sc = jaccard(base_tok, tokens_pl(t))
                if sc >= min_sim:
                    hits.append((host_of(e.get("link","")), sc))
        except Exception as ex:
            print(f"[WARN] verification RSS error: {f} -> {ex}", file=sys.stderr)
    hits.sort(key=lambda x: x[1], reverse=True)
    return [h[0] for h in hits[:3]]

def verify_note_pl(title: str, snippet: str) -> str:
    """
    Zwraca jedno krótkie ostrzeżenie „Uwaga: …” TYLKO gdy:
    - naruszone są podstawowe zasady prawa (domniemanie niewinności, nadużycie „skazany”),
    - występują czerwone flagi naukowe/zdrowotne (absoluty, preprinty, bardzo małe próby),
    - mocna teza nie znajduje potwierdzenia w PAP/Reuters/BBC/AP,
    - naukowo-zdrowotna teza nie znajduje nagłówków w WHO/CDC/NHS/Cochrane.
    """
    notes = []

    # 1) Reguły prawne
    notes += _apply_rules(LEGAL_RULES_PL, title, snippet)

    # 2) Red flags nauka/zdrowie
    science_hits = _apply_rules(SCIENCE_RED_FLAGS_PL, title, snippet)
    if science_hits:
        notes += science_hits
        sci_srcs = _search_trusted_sources(title, OFFICIAL_SCIENCE_FEEDS)
        if not sci_srcs:
            notes.append("nie znaleziono zbieżnych nagłówków w WHO/CDC/NHS/Cochrane.")

    # 3) Mocna teza — oczekujemy co najmniej jednego echa w zaufanych źródłach
    if STRONG_CLAIM_RE.search(title) or STRONG_CLAIM_RE.search(snippet):
        gen_srcs = _search_trusted_sources(title, TRUSTED_CORRO_FEEDS)
        if not gen_srcs:
            notes.append("brak potwierdzenia w nagłówkach PAP/Reuters/BBC/AP — potraktuj informację ostrożnie.")

    if not notes:
        return ""

    text = "Uwaga: " + " ".join(sorted(set(notes)))
    text = text.strip()
    if text[-1] not in ".!?…":
        text += "."
    return text

# =========================
# POBIERANIE + DEDUPE
# =========================
def fetch_section(section_key: str):
    items = []
    for feed_url in FEEDS[section_key]:
        try:
            parsed = feedparser.parse(feed_url)
            for e in parsed.entries:
                title = e.get("title", "") or ""
                link  = e.get("link", "") or ""
                if not title or not link:
                    continue
                snippet = e.get("summary", "") or e.get("description", "") or ""
                summary_raw = clean_rss_text(snippet)
                published_parsed = e.get("published_parsed") or e.get("updated_parsed")
                if not is_recent_enough(published_parsed):
                    continue
                if not should_keep_item(title, link, summary_raw, section_key):
                    continue
                items.append({
                    "title": title.strip(),
                    "link":  link.strip(),
                    "summary_raw": topic_safe_snippet(title, summary_raw),
                    "published_parsed": published_parsed,
                })
        except Exception as ex:
            print(f"[WARN] RSS error: {feed_url} -> {ex}", file=sys.stderr)

    # scoring + tokeny
    for it in items:
        it["_source_score"] = source_quality_score(it["link"], section_key)
        it["source_name"] = source_name(it["link"], section_key)
        it["_score"] = score_item(it, section_key)
        it["_tok"] = topic_tokens(it["title"], it.get("summary_raw", ""))

    # sortuj po score
    items.sort(key=lambda x: x["_score"], reverse=True)

    # deduplikacja semantyczna (Jaccard na tokenach tytułu)
    kept = []
    for it in items:
        duplicate_idx = None
        for idx, got in enumerate(kept):
            if jaccard(it["_tok"], got["_tok"]) >= SIMILARITY_THRESHOLD:
                duplicate_idx = idx
                break
        if duplicate_idx is None:
            kept.append(it)
            continue
        got = kept[duplicate_idx]
        candidate_rank = (it.get("_source_score", 0), it.get("_score", 0), len(it.get("summary_raw", "")))
        current_rank = (got.get("_source_score", 0), got.get("_score", 0), len(got.get("summary_raw", "")))
        if candidate_rank > current_rank:
            kept[duplicate_idx] = it

    # limit na host + total
    per_host = {}
    pool = []
    for it in kept:
        h = it.get("source_name") or host_of(it["link"])
        per_host[h] = per_host.get(h, 0)
        if per_host[h] >= MAX_PER_HOST:
            continue
        per_host[h] += 1
        pool.append(it)

    # SPORT: dywersyfikacja dyscyplin, bez liveblogów i relacji minutowych
    if section_key == "sport":
        picked = []
        def tag(t):
            t=t.lower()
            if any(k in t for k in ["tenis","wimbledon","us open","australian open"]): return "tenis"
            if any(k in t for k in ["piłka nożna","ekstraklasa","liga konferencji","liga europy","mecz","legia","lech","raków","rakow"]): return "pilka"
            if any(k in t for k in ["siatkówka","siatkar"]): return "siatkowka"
            if any(k in t for k in ["koszykówka","nba"]): return "kosz"
            if any(k in t for k in ["f1","formula","grand prix"]): return "f1"
            return "inne"

        seen_tags = set()
        for it in pool:
            tg = tag(it["title"])
            if tg not in seen_tags:
                picked.append(it); seen_tags.add(tg)
                if len(picked) >= MAX_PER_SECTION: 
                    break

        # 3) domknij do limitu
        for it in pool:
            if len(picked) >= MAX_PER_SECTION: 
                break
            if it not in picked:
                picked.append(it)
    else:
        picked = pool[:MAX_PER_SECTION]

    # AI + weryfikacja (ostrzeżenie tylko przy realnym powodzie)
    for it in picked:
        s = ai_summarize_pl(it["title"], it.get("summary_raw", ""), it["link"])
        verify = verify_note_pl(it["title"], it.get("summary_raw",""))
        final_warn = verify or s.get("uncertain","")
        it["ai_key_point"] = ensure_period(s.get("key_point") or s.get("summary", ""))
        it["ai_why_it_matters"] = ensure_period(s.get("why_it_matters", "")) if s.get("why_it_matters") else ""
        it["ai_summary"] = it["ai_key_point"]
        it["ai_uncertain"] = ensure_period(final_warn) if final_warn else ""
        it["ai_model"] = s.get("model","")

    return picked

# =========================
# HOTBAR JSON (dla paska)
# =========================
def build_hotbar_json(sections: dict) -> dict:
    """
    Buduje słownik:
    {
      "v2|Tytuł|2025-11-17": "https://link-do-artykulu",
      ...
    }
    używany przez /scripts/hotbar.js
    """
    all_items = []
    for sec_key, items in sections.items():
        for it in items:
            all_items.append(it)

    # sortujemy globalnie po _score (najważniejsze na końcu listy)
    all_items.sort(key=lambda it: it.get("_score", 0.0), reverse=True)

    out = {}
    taken = 0
    for it in all_items:
        if taken >= HOTBAR_LIMIT:
            break
        title = (it.get("title") or "").strip()
        link = (it.get("link") or "").strip()
        if not title or not link:
            continue

        pp = it.get("published_parsed")
        if pp:
            dt = datetime(*pp[:6], tzinfo=timezone.utc).astimezone(TZ)
            dstr = dt.strftime("%Y-%m-%d")
        else:
            dstr = today_str()

        key = f"v2|{title}|{dstr}"
        # ZAMIANA: zapisujemy URL zamiast True
        out[key] = link
        taken += 1

    return out

# =========================
# RENDER HTML
# =========================
def render_html(sections: dict) -> str:
    extra_css = """
    ul.news{ list-style:none; padding-left:0; }
    ul.news li{ margin:13px 0 18px; }
    ul.news li .news-main-link{
      display:flex; align-items:center; gap:10px;
      color:#fdf3e3; text-decoration:none; line-height:1.25;
    }
    ul.news li .news-main-link:hover{ color:#ffffff; text-decoration:underline; }
    .news-thumb{
      width:78px; min-width:78px; height:50px; border-radius:12px;
      background:linear-gradient(135deg,#f59e0b 0%,#d97706 48%,#1f2937 100%);
      border:1px solid rgba(255,255,255,.22);
      display:flex; flex-direction:column; justify-content:center; align-items:flex-start;
      gap:3px; padding:6px 9px 6px 11px; box-shadow:0 10px 22px rgba(0,0,0,.32);
    }
    .news-thumb .dot{
      width:14px; height:14px; border-radius:999px; background:rgba(7,89,133,1);
      border:2px solid rgba(255,255,255,.6); box-shadow:0 0 8px rgba(255,255,255,.3); margin-bottom:1px;
    }
    .news-thumb .title{ font-size:.58rem; font-weight:800; letter-spacing:0; color:#fff; line-height:1.05; }
    .news-thumb .sub{ font-size:.48rem; color:rgba(244,246,255,.86); line-height:1.05; white-space:nowrap; }
    .news-text{
      font-size:1.02rem; font-weight:720; letter-spacing:0; color:#fff7ed;
    }
    .source-line{
      margin:5px 0 0 88px; color:#9fb3cb; font-size:.82rem; line-height:1.3;
    }

    .ai-note{
      margin:8px 0 0 88px;
      font-size:.92rem; color:#dfe7f1; line-height:1.38;
      background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.08);
      padding:10px 12px; border-radius:10px;
    }
    .ai-head{ display:flex; align-items:center; gap:8px; margin-bottom:5px; font-weight:700; color:#fdf3e3; }
    .ai-badge{ display:inline-flex; align-items:center; gap:6px; padding:3px 8px; border-radius:999px;
      background:linear-gradient(135deg,#0ea5e9,#7c3aed); font-size:.73rem; color:#fff; border:1px solid rgba(255,255,255,.35); }
    .ai-dot{ width:8px; height:8px; border-radius:999px; background:#fff; box-shadow:0 0 6px rgba(255,255,255,.7); }
    .sec{ margin-top:3px; }
    .empty-note{ color:#9fb3cb; margin:10px 0 4px; font-size:.92rem; }
    .note{ color:#9fb3cb; font-size:.92rem }
    """

    def badge(source):
        return (
            '<span class="news-thumb">'
            '<span class="dot"></span>'
            f'<span class="title">{esc(source)}</span>'
            '<span class="sub">Źródło</span>'
            '</span>'
        )

    def make_li(it):
        source = it.get("source_name") or source_name(it.get("link", ""))
        warn_html = f'<div class="sec"><strong>Uwaga:</strong> {esc(it["ai_uncertain"])}</div>' if it.get("ai_uncertain") else ""
        why_html = f'<div class="sec"><strong>Dlaczego to ważne:</strong> {esc(it.get("ai_why_it_matters",""))}</div>' if it.get("ai_why_it_matters") else ""
        return f'''<li>
  <a class="news-main-link" href="{esc(it["link"])}" target="_blank" rel="noopener">
    {badge(source)}
    <span class="news-text">{esc(it["title"])}</span>
  </a>
  <div class="source-line">Źródło: {esc(source)}</div>
  <div class="ai-note">
    <div class="ai-head"><span class="ai-badge"><span class="ai-dot"></span> BriefRooms • AI komentarz</span></div>
    <div class="sec"><strong>Najważniejsze:</strong> {esc(it.get("ai_key_point") or it.get("ai_summary",""))}</div>
    {why_html}
    {warn_html}
  </div>
</li>'''

    def make_section(title, items):
        if not items:
            return f"""
<section class="card">
  <h2>{esc(title)}</h2>
  <p class="empty-note">Brak pojedynczych artykułów spełniających kryteria w tej aktualizacji.</p>
</section>"""
        lis = "\n".join(make_li(it) for it in items)
        return f"""
<section class="card">
  <h2>{esc(title)}</h2>
  <ul class="news">
    {lis}
  </ul>
</section>"""

    html_out = f"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Aktualności — BriefRooms</title>
  <meta name="description" content="Automatycznie odświeżane aktualności BriefRooms: polityka i kraj, ekonomia i biznes, sport, zdrowie oraz nauka." />
  <link rel="icon" href="/assets/favicon.svg" />
  <link rel="stylesheet" href="/assets/site.css?v=news5" />
  <style>
    header{{ text-align:center; padding:24px 12px 6px }}
    .sub{{ color:#b9c5d8 }}
    main{{ max-width:980px; margin:0 auto; padding:0 16px 48px }}
    .card{{
      background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.02));
      border:1px solid rgba(255,255,255,.08);
      border-radius:16px; padding:18px 20px; margin:14px 0;
      box-shadow:inset 0 1px 0 rgba(255,255,255,.04), 0 10px 30px rgba(0,0,0,.25)
    }}
    h2{{ margin:8px 0 6px; color:#d7e6ff }}
    {extra_css}
  </style>
</head>
<body data-page="news">
<header>
  <h1>Aktualności</h1>
  <p class="sub">Ostatnia aktualizacja: {today_str_pl()}</p>
</header>
<main>
{make_section("Polityka/Kraj", sections["polityka"])}
{make_section("Ekonomia/Biznes", sections["biznes"])}
{make_section("Sport", sections["sport"])}
{make_section("Zdrowie", sections["zdrowie"])}
{make_section("Nauka", sections["nauka"])}

<p class="note">Automatyczny skrót (RSS). Linki prowadzą do wydawców. Strona nadpisywana automatycznie.</p>
</main>
<footer style="text-align:center; opacity:.55; padding:18px">© BriefRooms</footer>
</body>
</html>"""
    return inject_cloudflare_analytics(html_out)

# =========================
# MAIN
# =========================
def main():
    sections = {
        "polityka": fetch_section("polityka"),
        "biznes":   fetch_section("biznes"),
        "sport":    fetch_section("sport"),
        "zdrowie":  fetch_section("zdrowie"),
        "nauka":    fetch_section("nauka"),
    }
    sections = dedupe_sections(sections)

    # 1) HTML /pl/aktualnosci.html
    html_str = render_html(sections)
    os.makedirs("pl", exist_ok=True)
    with open("pl/aktualnosci.html", "w", encoding="utf-8") as f:
        f.write(html_str)

    # 2) JSON dla hotbara (klikalne linki)
    hotbar_data = build_hotbar_json(sections)
    os.makedirs(os.path.dirname(HOTBAR_JSON_PATH), exist_ok=True)
    with open(HOTBAR_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(hotbar_data, f, ensure_ascii=False, indent=2)

    print("✓ Wygenerowano pl/aktualnosci.html +", HOTBAR_JSON_PATH, "(AI:", "ON" if AI_ENABLED else "OFF", ")")

if __name__ == "__main__":
    main()

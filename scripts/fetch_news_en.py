#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import re
import json
import html
import hashlib

from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import feedparser
from dateutil import tz
import requests

from comment_quality import (
    QUALITY_STATUS,
    QUALITY_VERSION,
    decode_http_response,
    get_ai_runtime,
    independent_ai_review,
    request_json_completion,
    validate_news_comment,
)
from news_comment_batch import summarize_news_items

# =========================
# TIMEZONE (UTC dla wersji EN jest bezpieczniejsze)
# =========================
TZ = timezone.utc

# =========================
# CONFIG
# =========================
MAX_PER_SECTION = 5
MAX_PER_HOST = 2
HOTBAR_LIMIT = 15 # Trochę więcej newsów w pasku

AI_ENABLED = get_ai_runtime().available
AI_MODEL = os.getenv("NEWS_AI_MODEL", "gpt-4o-mini")

# Ścieżki plików
AI_CACHE_PATH = ".cache/ai_cache_en.json"
HOTBAR_JSON_PATH = ".cache/news_summaries_en.json" # To czyta hotbar.js
HTML_OUTPUT_PATH = "en/news.html" # Generowana strona zbiorcza
CLOUDFLARE_WEB_ANALYTICS = (
    "<!-- Cloudflare Web Analytics --><script defer src='https://static.cloudflareinsights.com/beacon.min.js' "
    "data-cf-beacon='{\"token\": \"9adde99e330a4b0d991627986ac34246\"}'></script><!-- End Cloudflare Web Analytics -->"
)

SOURCE_PROFILES = {
    "reuters.com": {"name": "Reuters", "score": 60, "sections": {"world", "asia_pacific", "europe", "middle_east", "business", "science", "health", "sport"}},
    "apnews.com": {"name": "AP", "score": 58, "sections": {"world", "asia_pacific", "europe", "middle_east", "business", "science", "health", "sport"}},
    "bloomberg.com": {"name": "Bloomberg", "score": 58, "sections": {"world", "asia_pacific", "europe", "middle_east", "business"}},
    "bbci.co.uk": {"name": "BBC News", "score": 50, "sections": {"world", "asia_pacific", "europe", "middle_east", "business", "science", "health", "sport"}},
    "bbc.co.uk": {"name": "BBC News", "score": 50, "sections": {"world", "asia_pacific", "europe", "middle_east", "business", "science", "health", "sport"}},
    "bbc.com": {"name": "BBC News", "score": 50, "sections": {"world", "asia_pacific", "europe", "middle_east", "business", "science", "health", "sport"}},
    "theguardian.com": {"name": "The Guardian", "score": 46, "sections": {"world", "europe", "middle_east", "business", "science", "health", "sport"}},
    "politico.com": {"name": "Politico", "score": 44, "sections": {"world", "europe", "business"}},
    "cnbc.com": {"name": "CNBC", "score": 42, "sections": {"business"}},
    "federalreserve.gov": {"name": "Federal Reserve", "score": 52, "sections": {"business"}},
    "treasury.gov": {"name": "U.S. Treasury", "score": 50, "sections": {"business"}},
    "bls.gov": {"name": "BLS", "score": 50, "sections": {"business"}},
    "bea.gov": {"name": "BEA", "score": 50, "sections": {"business"}},
    "imf.org": {"name": "IMF", "score": 48, "sections": {"business"}},
    "oecd.org": {"name": "OECD", "score": 48, "sections": {"business"}},
    "worldbank.org": {"name": "World Bank", "score": 48, "sections": {"business"}},
    "ecb.europa.eu": {"name": "ECB", "score": 50, "sections": {"business"}},
    "commission.europa.eu": {"name": "European Commission", "score": 48, "sections": {"europe", "business"}},
    "ec.europa.eu": {"name": "European Commission", "score": 48, "sections": {"europe", "business"}},
    "nato.int": {"name": "NATO", "score": 48, "sections": {"europe"}},
    "who.int": {"name": "WHO", "score": 50, "sections": {"health"}},
    "nasa.gov": {"name": "NASA", "score": 48, "sections": {"science"}},
    "esa.int": {"name": "ESA", "score": 46, "sections": {"science"}},
    "espn.com": {"name": "ESPN", "score": 44, "sections": {"sport"}},
    "skysports.com": {"name": "Sky Sports", "score": 42, "sections": {"sport"}},
    "formula1.com": {"name": "Formula1.com", "score": 44, "sections": {"sport"}},
    "uefa.com": {"name": "UEFA", "score": 42, "sections": {"sport"}},
    "fifa.com": {"name": "FIFA", "score": 42, "sections": {"sport"}},
    "nba.com": {"name": "NBA", "score": 42, "sections": {"sport"}},
    "nfl.com": {"name": "NFL", "score": 42, "sections": {"sport"}},
    "nhl.com": {"name": "NHL", "score": 42, "sections": {"sport"}},
    "mlb.com": {"name": "MLB", "score": 42, "sections": {"sport"}},
    "aljazeera.com": {"name": "Al Jazeera", "score": 45, "sections": {"middle_east"}},
    "thenationalnews.com": {"name": "The National", "score": 43, "sections": {"middle_east"}},
    "asia.nikkei.com": {"name": "Nikkei Asia", "score": 47, "sections": {"asia_pacific"}},
    "scmp.com": {"name": "South China Morning Post", "score": 43, "sections": {"asia_pacific"}},
    "channelnewsasia.com": {"name": "CNA", "score": 43, "sections": {"asia_pacific"}},
    "japantimes.co.jp": {"name": "Japan Times", "score": 42, "sections": {"asia_pacific"}},
    "kyodonews.net": {"name": "Kyodo News", "score": 44, "sections": {"asia_pacific"}},
    "yna.co.kr": {"name": "Yonhap", "score": 44, "sections": {"asia_pacific"}},
}

CATEGORY_SEGMENTS = {
    "business", "category", "economy", "health", "home", "hub", "hubs",
    "latest", "live", "markets", "news", "newsletter", "newsletters",
    "opinion", "politics", "science", "sport", "sports", "tag", "tags",
    "technology", "topic", "topics", "update", "updates", "video", "videos", "world",
}
ROUNDUP_OR_LIVE_RE = re.compile(
    r"\b(live|liveblog|liveblogs|live blog|live blogs|live updates?|rolling coverage|as it happened)\b|"
    r"minute-by-minute|minute by minute|roundup|daily briefing|newsletter|latest news|latest updates?|"
    r"what we know|key takeaways|everything you need to know|catch up|"
    r"morning brief|evening brief",
    re.I,
)
MULTI_TOPIC_MARKER_RE = re.compile(
    r"\b(meanwhile|separately|elsewhere|in other news|also today|at the same time|"
    r"additionally|in a separate development|on another front)\b",
    re.I,
)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
SPORT_TOPIC_RE = re.compile(
    r"\b(World Cup|FIFA|UEFA|football|soccer|basketball|NBA|NFL|NHL|MLB|Formula 1|F1|Grand Prix|"
    r"tennis|cricket|rugby|golf|match|tournament|championship|league)\b",
    re.I,
)

# ŹRÓDŁA (regional + business + science + health + sport)
FEEDS = {
    "world": [
        "http://feeds.bbci.co.uk/news/world/rss.xml",
        "https://feeds.reuters.com/reuters/worldNews",
        "https://apnews.com/hub/ap-top-news?output=rss",
        "https://feeds.bloomberg.com/politics/news.rss",
        "https://www.theguardian.com/world/rss",
        "https://www.politico.com/rss/politicopicks.xml",
    ],
    "asia_pacific": [
        "https://feeds.reuters.com/reuters/worldNews",
        "https://apnews.com/hub/ap-top-news?output=rss",
        "https://feeds.bloomberg.com/politics/news.rss",
        "http://feeds.bbci.co.uk/news/world/asia/rss.xml",
        "https://asia.nikkei.com/rss/feed/nar",
        "https://www.scmp.com/rss/91/feed",
        "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6511",
        "https://www.japantimes.co.jp/news/feed/",
        "https://english.kyodonews.net/rss/news.xml",
        "https://en.yna.co.kr/RSS/news.xml",
    ],
    "europe": [
        "https://feeds.reuters.com/reuters/worldNews",
        "https://apnews.com/hub/ap-top-news?output=rss",
        "https://feeds.bloomberg.com/politics/news.rss",
        "http://feeds.bbci.co.uk/news/world/europe/rss.xml",
        "https://www.theguardian.com/world/europe-news/rss",
        "https://www.politico.com/rss/politicopicks.xml",
        "https://www.nato.int/cps/en/natohq/news.xml",
        "https://commission.europa.eu/node/126/rss_en",
    ],
    "middle_east": [
        "https://feeds.reuters.com/reuters/worldNews",
        "https://apnews.com/hub/ap-top-news?output=rss",
        "https://feeds.bloomberg.com/politics/news.rss",
        "http://feeds.bbci.co.uk/news/world/middle_east/rss.xml",
        "https://www.theguardian.com/world/middleeast/rss",
        "https://www.aljazeera.com/xml/rss/all.xml",
        "https://www.thenationalnews.com/arc/outboundfeeds/rss/?outputType=xml",
    ],
    "business": [
        "http://feeds.bbci.co.uk/news/business/rss.xml",
        "https://feeds.reuters.com/reuters/businessNews",
        "https://apnews.com/hub/business?output=rss",
        "https://feeds.bloomberg.com/markets/news.rss",
        "https://feeds.bloomberg.com/economics/news.rss",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "https://www.politico.com/rss/politicopicks.xml",
        "https://home.treasury.gov/news/press-releases/rss",
        "https://www.federalreserve.gov/feeds/press_all.xml",
        "https://www.bls.gov/feed/news_release.rss",
        "https://www.bea.gov/news/current-releases/rss.xml",
        "https://www.ecb.europa.eu/rss/press.html",
        "https://www.imf.org/en/News/RSS",
        "https://www.oecd.org/newsroom/rss.xml",
        "https://www.worldbank.org/en/news/all?format=rss",
    ],
    "science": [
        "http://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        "https://apnews.com/hub/science?output=rss",
        "https://www.theguardian.com/science/rss",
        "https://www.nasa.gov/rss/dyn/breaking_news.rss",
        "https://www.esa.int/rssfeed/Our_Activities/Space_News",
    ],
    "health": [
        "http://feeds.bbci.co.uk/news/health/rss.xml",
        "https://feeds.reuters.com/reuters/healthNews",
        "https://apnews.com/hub/health?output=rss",
        "https://www.theguardian.com/society/health/rss",
        "https://www.who.int/feeds/entity/mediacentre/news/en/rss.xml",
    ],
    "sport": [
        "http://feeds.bbci.co.uk/sport/rss.xml?edition=int",
        "https://www.espn.com/espn/rss/news",
        "https://www.skysports.com/rss/12040",
        "https://feeds.reuters.com/reuters/sportsNews",
        "https://apnews.com/hub/sports?output=rss",
        "https://www.formula1.com/en/latest/all.xml",
        "https://www.uefa.com/rssfeed/news/rss.xml",
        "https://www.fifa.com/fifaplus/en/rss",
        "https://www.nba.com/news/rss.xml",
        "https://www.nfl.com/rss/rsslanding?searchString=home",
        "https://www.nhl.com/rss/news.xml",
        "https://www.mlb.com/feeds/news/rss.xml",
    ],
}

# Słowa kluczowe do podbijania ważności
BOOST = {
    "world": [
        (re.compile(r"US|USA|China|Russia|Ukraine|G7|G20|NATO|UN|White House|trade|war|security|sanctions|climate", re.I), 24),
    ],
    "asia_pacific": [
        (re.compile(r"China|Taiwan|Japan|South Korea|North Korea|India|Southeast Asia|South China Sea|chips|semiconductor|trade|security", re.I), 26),
    ],
    "europe": [
        (re.compile(r"Europe|EU|European Union|NATO|Ukraine|Russia|UK|France|Germany|Poland|Brussels|Kyiv|Moscow|sanctions|security", re.I), 26),
    ],
    "middle_east": [
        (re.compile(r"Iran|Israel|Gaza|Palestinian|Lebanon|Syria|Iraq|Yemen|Saudi|Qatar|UAE|Hormuz|Hamas|Hezbollah|Houthis|oil", re.I), 26),
    ],
    "business": [
        (re.compile(r"market|stocks|inflation|fed|rate|ECB|crypto|bitcoin|AI|tech|Nvidia|Apple|Tesla", re.I), 25),
    ],
    "science": [
        (re.compile(r"space|nasa|moon|mars|climate|discovery|study|research|cancer|brain", re.I), 25),
    ],
    "health": [
        (re.compile(r"WHO|FDA|vaccine|drug|trial|disease|hospital|public health", re.I), 18),
    ],
    "sport": [
        (re.compile(r"Formula 1|F1|World Cup|Champions League|NBA|NFL|NHL|MLB|Grand Prix|UEFA|FIFA", re.I), 24),
    ],
}

MIDDLE_EAST_TOPIC_RE = re.compile(
    r"\b(Middle East|Iran|Israel|Israeli|Gaza|Palestin(?:e|ian)|West Bank|Lebanon|Lebanese|Syria|Syrian|"
    r"Iraq|Iraqi|Yemen|Yemeni|Saudi|Riyadh|UAE|Emirati|Qatar|Doha|Oman|Kuwait|Jordan|Egypt|Turkey|"
    r"Hormuz|Hamas|Hezbollah|Houthi|Houthis|Red Sea|Gulf|Tehran|Jerusalem|Tel Aviv)\b",
    re.I,
)
ASIA_PACIFIC_TOPIC_RE = re.compile(
    r"\b(China|Chinese|Taiwan|Taiwanese|Japan|Japanese|South Korea|Korean|North Korea|Pyongyang|Seoul|"
    r"India|Indian|Southeast Asia|ASEAN|South China Sea|Philippines|Vietnam|Indonesia|Thailand|Malaysia|"
    r"Singapore|chips?|semiconductors?|trade|tariffs?|security|defen[cs]e|Pacific)\b",
    re.I,
)
EUROPE_TOPIC_RE = re.compile(
    r"\b(Europe|European|EU|European Union|Eurozone|NATO|Ukraine|Ukrainian|Russia|Russian|Kyiv|Moscow|"
    r"UK|Britain|British|France|French|Germany|German|Poland|Polish|Italy|Spain|Brussels|Baltic|"
    r"sanctions?|defen[cs]e|security|migration)\b",
    re.I,
)

BAN_PATTERNS = [
    # low-value / soft content
    re.compile(r"horoscope|celeb|quiz|sponsored|gallery|clickbait", re.I),

    # lotteries / games / trivial finance-light items
    re.compile(r"lotto|lottery|jackpot|powerball|mega millions|winning numbers|draw results", re.I),

    # celebrity / entertainment / influencer noise
    re.compile(r"celebrity|influencer|youtuber|tiktoker|reality show|showbiz", re.I),

    # soft entertainment
    re.compile(r"tv show|movie trailer|red carpet|music awards", re.I),
]
# =========================
# TOKENIZATION / HELPER
# =========================
STOP_EN = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "from",
    "up", "down", "is", "are", "was", "were", "be", "been", "being", "it", "its", "he", "his", "she", "her",
    "daily", "briefing", "live", "updates"
}
TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def tokens_en(text: str):
    toks = [t.lower() for t in TOKEN_RE.findall(text or "")]
    toks = [t for t in toks if t not in STOP_EN and len(t) > 2 and not t.isdigit()]
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

def source_profile_for(link: str, section_key: str):
    host = host_of(link).lower().lstrip("www.")
    for domain in sorted(SOURCE_PROFILES, key=len, reverse=True):
        if host == domain or host.endswith("." + domain):
            profile = SOURCE_PROFILES[domain]
            if section_key in profile["sections"]:
                return profile
    return None

def source_quality_score(link: str, section_key: str) -> int:
    profile = source_profile_for(link, section_key)
    return int(profile["score"]) if profile else 0

def source_name(link: str, section_key: str) -> str:
    profile = source_profile_for(link, section_key)
    if profile:
        host = host_of(link).lower().lstrip("www.")
        if section_key == "sport" and (host.endswith("bbci.co.uk") or host.endswith("bbc.co.uk") or host.endswith("bbc.com")):
            return "BBC Sport"
        if section_key == "sport" and host.endswith("reuters.com"):
            return "Reuters Sports"
        if section_key == "sport" and host.endswith("apnews.com"):
            return "AP Sports"
        return profile["name"]
    return host_of(link).lower().lstrip("www.") or "source"

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
    if any(seg in {"hub", "hubs", "tag", "tags", "topic", "topics", "category", "newsletter", "newsletters", "video", "videos", "newsfeed"} for seg in segments):
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
    title_tokens = tokens_en(title)
    unrelated = 0
    for sentence in sentences[1:]:
        sent_tokens = tokens_en(sentence)
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
    return tokens_en(" ".join([title or "", first_sentence(summary or "")]))

def normalize_link_for_dedupe(link: str) -> str:
    parsed = urlparse(link or "")
    host = parsed.netloc.lower().lstrip("www.")
    path = (parsed.path or "").rstrip("/")
    return f"{host}{path}"

def matches_section_topic(title: str, summary: str, link: str, section_key: str) -> bool:
    if section_key not in {"world", "asia_pacific", "europe", "middle_east"}:
        return True
    if section_key == "world":
        return True
    path = urlparse(link).path.replace("-", " ").replace("_", " ")
    blob = " ".join([title or "", summary or "", path])
    if section_key == "asia_pacific":
        return bool(ASIA_PACIFIC_TOPIC_RE.search(blob))
    if section_key == "europe":
        return bool(EUROPE_TOPIC_RE.search(blob))
    return bool(MIDDLE_EAST_TOPIC_RE.search(blob))

def should_keep_item(title: str, link: str, summary: str, section_key: str) -> bool:
    if source_profile_for(link, section_key) is None:
        return False
    if not is_concrete_article_url(link):
        return False
    if is_roundup_or_live(title, summary, link):
        return False
    if any(rx.search(title) or rx.search(summary) for rx in BAN_PATTERNS):
        return False
    if section_key != "sport" and SPORT_TOPIC_RE.search(" ".join([title or "", summary or "", urlparse(link).path.replace("-", " ")])):
        return False
    if is_multitopic_summary(title, summary):
        return False
    if not matches_section_topic(title, summary, link, section_key):
        return False
    return True

def today_str() -> str:
    now = datetime.now(TZ)
    return now.strftime("%Y-%m-%d")

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
    if source_score >= 58:
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

def ai_summarize_en(title: str, snippet: str, url: str) -> dict:
    runtime = get_ai_runtime()
    safe_snippet = topic_safe_snippet(title, snippet)
    if len(clean_rss_text(snippet)) < 55:
        return {"summary": "", "key_point": "", "why_it_matters": "", "uncertain": "", "model": "insufficient_source", "reviewed": False}
    digest = hashlib.sha256(f"{title}|{url}|{safe_snippet}".encode("utf-8")).hexdigest()[:20]
    cache_key = f"strict-v{QUALITY_VERSION}|{digest}"
    if cache_key in CACHE:
        cached = CACHE[cache_key]
        quality = validate_news_comment(str(cached.get("summary") or ""), "en")
        if (
            cached.get("reviewed") is True
            and cached.get("quality_version") == QUALITY_VERSION
            and quality.valid
        ):
            cached["key_point"] = quality.text
            return cached

    if not runtime.available:
        return {
            "summary": "",
            "key_point": "",
            "why_it_matters": "",
            "uncertain": "",
            "model": "unavailable",
            "reviewed": False,
        }

    prompt = f"""Write a concise English news comment using only the title and RSS description.
Return only JSON: {{"summary":"..."}}.
The comment must contain 1-2 complete, concrete sentences and 55-320 characters.

Title: {title}
RSS description: {safe_snippet}

Rules:
- Use clear, neutral and grammatical English that is understandable without extra context.
- Do not copy damaged characters, split words, bylines, publisher UI or editorial commands.
- Do not add facts that are absent from the title or description.
- If the material is damaged or cannot be summarized with confidence, return an empty summary."""
    try:
        payload = request_json_completion(
            post=requests.post,
            runtime=runtime,
            messages=[
                {"role": "system", "content": "You are a strict news editor. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=220,
            temperature=0.1,
            timeout=25,
        )
        quality = validate_news_comment(str(payload.get("summary") or ""), "en")
        if not quality.valid:
            print(f"[WARN] EN news comment rejected: {title[:80]} :: {','.join(quality.reasons)}", file=sys.stderr)
            return {"summary": "", "key_point": "", "why_it_matters": "", "uncertain": "", "model": AI_MODEL, "reviewed": False}
        reviewed, reason = independent_ai_review(
            post=requests.post,
            runtime=runtime,
            title=title,
            source_text=safe_snippet,
            summary=quality.text,
            lang="en",
        )
        if not reviewed:
            print(f"[WARN] EN news AI review rejected: {title[:80]} :: {reason[:160]}", file=sys.stderr)
            return {"summary": "", "key_point": "", "why_it_matters": "", "uncertain": "", "model": AI_MODEL, "reviewed": False}
        out = {
            "summary": quality.text,
            "key_point": quality.text,
            "why_it_matters": "",
            "uncertain": "",
            "model": runtime.generation_model,
            "review_model": runtime.review_model,
            "provider": runtime.provider,
            "reviewed": True,
            "quality_status": QUALITY_STATUS,
            "quality_version": QUALITY_VERSION,
        }
        CACHE[cache_key] = out
        save_cache(AI_CACHE_PATH, CACHE)
        return out
    except Exception as ex:
        print(f"[WARN] EN news AI generation failed: {title[:80]} :: {ex}", file=sys.stderr)
        return {
            "summary": "",
            "key_point": "",
            "why_it_matters": "",
            "uncertain": "",
            "model": AI_MODEL,
            "reviewed": False,
        }

# =========================
# WARSTWA WERYFIKACJI (EN)
# =========================
# Dla uproszczenia (przywrócenia funkcjonalności) przyjmujemy, że to jest ok.
def verify_note_en(title: str, snippet: str) -> str:
    return ""


def _valid_image_url(value: str, base_url: str = "") -> str:
    candidate = html.unescape(str(value or "")).strip()
    if not candidate:
        return ""
    candidate = urljoin(base_url, candidate)
    if not candidate.lower().startswith(("http://", "https://")):
        return ""
    if re.search(r"(?:pixel|tracking|spacer|blank)\.(?:gif|png)(?:\?|$)", candidate, re.I):
        return ""
    return candidate


def entry_image(entry, feed_url: str) -> str:
    """Return the best real image exposed directly by an RSS entry."""
    candidates = []
    for key in ("media_content", "media_thumbnail"):
        for media in entry.get(key, []) or []:
            if not isinstance(media, dict):
                continue
            url = _valid_image_url(media.get("url") or media.get("href"), feed_url)
            if not url:
                continue
            try:
                width = int(media.get("width") or 0)
            except (TypeError, ValueError):
                width = 0
            candidates.append((width, url))

    for enclosure in (entry.get("enclosures", []) or []) + (entry.get("links", []) or []):
        if not isinstance(enclosure, dict):
            continue
        media_type = str(enclosure.get("type") or "").lower()
        rel = str(enclosure.get("rel") or "").lower()
        if media_type.startswith("image/") or rel == "enclosure":
            url = _valid_image_url(enclosure.get("href") or enclosure.get("url"), feed_url)
            if url:
                candidates.append((0, url))

    raw_html = str(entry.get("summary") or entry.get("description") or "")
    match = re.search(r"<img[^>]+src=[\"']([^\"']+)", raw_html, re.I)
    if match:
        url = _valid_image_url(match.group(1), feed_url)
        if url:
            candidates.append((0, url))

    return max(candidates, default=(0, ""), key=lambda item: item[0])[1]


def article_image(link: str) -> str:
    """Use article metadata when the RSS feed does not include an image."""
    if not str(link or "").lower().startswith(("http://", "https://")):
        return ""
    try:
        response = requests.get(
            link,
            headers={
                "User-Agent": "BriefRoomsBot/2.1 (+https://briefrooms.com)",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=6,
            allow_redirects=True,
        )
        if not response.ok:
            return ""
        page = decode_http_response(response)[:600000]
    except Exception:
        return ""

    patterns = (
        r"""<meta[^>]+(?:property|name)=["']og:image(?::secure_url)?["'][^>]+content=["']([^"']+)""",
        r"""<meta[^>]+content=["']([^"']+)["'][^>]+(?:property|name)=["']og:image(?::secure_url)?["']""",
        r"""<meta[^>]+(?:property|name)=["']twitter:image(?::src)?["'][^>]+content=["']([^"']+)""",
        r"""<meta[^>]+content=["']([^"']+)["'][^>]+(?:property|name)=["']twitter:image(?::src)?["']""",
        r"""<link[^>]+rel=["']image_src["'][^>]+href=["']([^"']+)""",
    )
    for pattern in patterns:
        match = re.search(pattern, page, re.I)
        if match:
            image_url = _valid_image_url(match.group(1), link)
            if image_url:
                return image_url
    return ""


# =========================
# POBIERANIE + DEDUPE
# =========================
def fetch_section(section_key: str, excluded_links=None, excluded_topics=None, summarize: bool = True):
    excluded_links = set(excluded_links or [])
    excluded_topics = list(excluded_topics or [])
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
                if not should_keep_item(title, link, summary_raw, section_key):
                    continue
                items.append({
                    "title": title.strip(),
                    "link":  link.strip(),
                    "summary_raw": topic_safe_snippet(title, summary_raw),
                    "thumbnail_url": entry_image(e, feed_url),
                    "published_parsed": e.get("published_parsed") or e.get("updated_parsed"),
                })
        except Exception as ex:
            print(f"[WARN] RSS error: {feed_url} -> {ex}", file=sys.stderr)

    # Scoring, tokenizacja, deduplikacja i limitowanie (przywrócone)
    for it in items:
        it["_source_score"] = source_quality_score(it["link"], section_key)
        it["source_name"] = source_name(it["link"], section_key)
        it["_score"] = score_item(it, section_key)
        it["_tok"] = topic_tokens(it["title"], it.get("summary_raw", ""))

    items.sort(key=lambda x: x["_score"], reverse=True)

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

    per_host = {}
    pool = []
    for it in kept:
        if normalize_link_for_dedupe(it["link"]) in excluded_links:
            continue
        if any(jaccard(it["_tok"], seen) >= SIMILARITY_THRESHOLD for seen in excluded_topics):
            continue
        h = it.get("source_name") or host_of(it["link"])
        per_host[h] = per_host.get(h, 0)
        if per_host[h] >= MAX_PER_HOST:
            continue
        per_host[h] += 1
        pool.append(it)

    picked = []
    for it in pool:
        if not it.get("thumbnail_url"):
            it["thumbnail_url"] = article_image(it.get("link", ""))
        if not it.get("thumbnail_url"):
            continue
        picked.append(it)
        if len(picked) >= MAX_PER_SECTION:
            break

    if not summarize:
        return picked

    # AI + weryfikacja
    for it in picked:
        s = ai_summarize_en(it["title"], it.get("summary_raw", ""), it["link"])
        verify = verify_note_en(it["title"], it.get("summary_raw",""))
        final_warn = verify or s.get("uncertain","")
        it["ai_key_point"] = ensure_period(s.get("key_point") or s.get("summary", ""))
        it["ai_why_it_matters"] = ensure_period(s.get("why_it_matters", "")) if s.get("why_it_matters") else ""
        it["ai_summary"] = it["ai_key_point"]
        it["ai_uncertain"] = ensure_period(final_warn) if final_warn else ""
        it["ai_model"] = s.get("model","")
        it["comment_quality_status"] = s.get("quality_status", "")
        it["comment_quality_version"] = s.get("quality_version")
        it["comment_generation_status"] = "ai_review_approved" if s.get("reviewed") is True else "rejected_or_unavailable"

    return picked


def summarize_sections_en(sections: dict) -> None:
    all_items = [item for items in sections.values() for item in items]
    results = summarize_news_items(items=all_items, lang="en", cache=CACHE, post=requests.post)
    for item in all_items:
        result = results.get(str(item.get("_comment_batch_id") or ""), {})
        summary = str(result.get("summary") or "")
        item["ai_key_point"] = ensure_period(summary) if summary else ""
        item["ai_why_it_matters"] = ""
        item["ai_summary"] = item["ai_key_point"]
        item["ai_uncertain"] = ""
        item["ai_model"] = result.get("model", "")
        item["comment_quality_status"] = result.get("quality_status", "")
        item["comment_quality_version"] = result.get("quality_version")
        item["comment_generation_status"] = "ai_review_approved" if result.get("reviewed") is True else "rejected_or_unavailable"
    save_cache(AI_CACHE_PATH, CACHE)


def finalize_sections(sections: dict) -> dict:
    return sections


# =========================
# HOTBAR JSON (dla paska)
# =========================
# ... (pozostała część funkcji build_hotbar_json bez zmian) ...
def build_hotbar_json(sections: dict) -> dict:
    all_items = []
    for sec_key, items in sections.items():
        for it in items:
            all_items.append(it)

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
        out[key] = link
        taken += 1

    return out

# =========================
# RENDER HTML (en/news.html)
# =========================
def render_html(sections: dict) -> str:
    extra_css = """
    ul.news{ list-style:none; padding-left:0; }
    ul.news li{ margin:18px 0 24px; }
    ul.news li a{
      display:flex; align-items:center; gap:10px;
      color:#fdf3e3; text-decoration:none; line-height:1.25;
    }
    ul.news li a:hover{ color:#ffffff; text-decoration:underline; }
    .news-thumb{
      width:78px; min-width:78px; height:54px; border-radius:14px;
      background:radial-gradient(circle at 10% 10%, #ffcf71 0%, #f7a34b 35%, #0f172a 100%);
      border:1px solid rgba(255,255,255,.28);
      display:flex; flex-direction:column; justify-content:center; align-items:flex-start;
      gap:3px; padding:6px 10px 6px 12px; box-shadow:0 10px 24px rgba(0,0,0,.35);
    }
    .news-thumb .dot{
      width:14px; height:14px; border-radius:999px; background:rgba(7,89,133,1);
      border:2px solid rgba(255,255,255,.6); box-shadow:0 0 8px rgba(255,255,255,.3); margin-bottom:1px;
    }
    .news-thumb .title{ font-size:.56rem; font-weight:700; letter-spacing:.03em; color:#fff; line-height:1; }
    .news-thumb .sub{ font-size:.47rem; color:rgba(244,246,255,.85); line-height:1.05; white-space:nowrap; }
    .news-thumb.has-image{padding:0;overflow:hidden;background:rgba(255,255,255,.06);align-items:stretch;justify-content:stretch;}
    .news-thumb.has-image img{width:100%;height:100%;display:block;object-fit:cover;}

    .ai-note{
      margin:10px 0 0 88px;
      font-size:.95rem; color:#dfe7f1; line-height:1.4;
      background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.08);
      padding:12px 14px; border-radius:12px;
    }
    .ai-head{ display:flex; align-items:center; gap:8px; margin-bottom:6px; font-weight:700; color:#fdf3e3; }
    .ai-badge{ display:inline-flex; align-items:center; gap:6px; padding:3px 8px; border-radius:999px;
      background:linear-gradient(135deg,#0ea5e9,#7c3aed); font-size:.75rem; color:#fff; border:1px solid rgba(255,255,255,.35); }
    .ai-dot{ width:8px; height:8px; border-radius:999px; background:#fff; box-shadow:0 0 6px rgba(255,255,255,.7); }
    .sec{ margin-top:4px; }
    .source-line{ margin:8px 0 0 88px; color:#9fb3cb; font-size:.82rem; }
    .note{ color:#9fb3cb; font-size:.92rem }
    """

    def badge(source_label: str, image_url: str = ""):
        if image_url:
            return (
                '<span class="news-thumb has-image">'
                f'<img src="{esc(image_url)}" alt="" loading="lazy" decoding="async" '
                'referrerpolicy="no-referrer" width="78" height="54" />'
                '</span>'
            )
        source_label = esc(source_label or "Source")
        return (
            '<span class="news-thumb">'
            '<span class="dot"></span>'
            f'<span class="title">{source_label}</span>'
            '<span class="sub">Article</span>'
            '</span>'
        )

    def make_li(it):
        warn_html = f'<div class="sec"><strong>Warning:</strong> {esc(it["ai_uncertain"])}</div>' if it.get("ai_uncertain") else ""
        why_html = f'<div class="sec"><strong>Why it matters:</strong> {esc(it.get("ai_why_it_matters",""))}</div>' if it.get("ai_why_it_matters") else ""
        raw_source = it.get("source_name") or host_of(it.get("link", "")) or "source"
        source = esc(raw_source)
        return f'''<li>
  <a class="news-main-link" href="{esc(it["link"])}" target="_blank" rel="noopener">
    {badge(raw_source, it.get("thumbnail_url", ""))}
    <span class="news-text">{esc(it["title"])}</span>
  </a>
  <div class="source-line">Source: {source}</div>
  <div class="ai-note">
    <div class="ai-head"><span class="ai-badge"><span class="ai-dot"></span> BriefRooms • AI comment</span></div>
    <div class="sec"><strong>Key point:</strong> {esc(it.get("ai_key_point") or it.get("ai_summary",""))}</div>
    {why_html}
    {warn_html}
  </div>
</li>'''
    def make_section(title, items):
        lis = "\n".join(make_li(it) for it in items)
        return f"""
<section class="card">
  <h2>{esc(title)}</h2>
  <ul class="news">
    {lis}
  </ul>
</section>"""
    
    # Mapowanie na sekcje EN
    sections_map = {
        "World News": sections["world"],
        "Asia-Pacific": sections["asia_pacific"],
        "Europe": sections["europe"],
        "Middle East": sections["middle_east"],
        "Business": sections["business"],
        "Science": sections["science"],
        "Health": sections["health"],
        "Sport": sections["sport"],
    }
    
    sections_html = "\n".join(make_section(title, items) for title, items in sections_map.items())

    html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>News — BriefRooms</title>
  <meta name="description" content="Automatically refreshed single-source news summaries: World News, Asia-Pacific, Europe, Middle East, Business, Science, Health and Sport." />
  <link rel="icon" href="/assets/favicon.svg" />
  <link rel="stylesheet" href="/assets/site.css" />
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

    /* EN: override source CTA text */
    body[data-page="news"] ul.news > li > a::after{{
      content:"Read source" !important;
    }}

    @media (max-width:560px){{
      body[data-page="news"] ul.news > li > a::after{{
        content:"Source" !important;
      }}
    }}
  </style>
</head>
<body data-page="news">
<header>
  <h1>News</h1>
  <p class="sub">Last updated: {today_str()}</p>
</header>
<main>
{sections_html}

</main>
<footer style="text-align:center; opacity:.55; padding:18px">© BriefRooms</footer>
</body>
</html>"""
    return inject_cloudflare_analytics(html_out)

# =========================
# MAIN
# =========================
def main():
    sections = {}
    seen_links = set()
    seen_topics = []
    for section_key in ("world", "asia_pacific", "europe", "middle_east", "business", "science", "health", "sport"):
        items = fetch_section(section_key, seen_links, seen_topics, summarize=False)
        sections[section_key] = items
        for it in items:
            seen_links.add(normalize_link_for_dedupe(it.get("link", "")))
            seen_topics.append(it.get("_tok", set()))

    summarize_sections_en(sections)
    sections = finalize_sections(sections)

    # 1) HTML /en/news.html
    html_str = render_html(sections)
    os.makedirs(os.path.dirname(HTML_OUTPUT_PATH), exist_ok=True)
    with open(HTML_OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html_str)

    # 2) JSON dla hotbara (klikalne linki)
    hotbar_data = build_hotbar_json(sections)
    os.makedirs(os.path.dirname(HOTBAR_JSON_PATH), exist_ok=True)
    with open(HOTBAR_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(hotbar_data, f, ensure_ascii=False, indent=2)

    print("✓ Generated", HTML_OUTPUT_PATH, "+", HOTBAR_JSON_PATH, "(AI:", "ON" if AI_ENABLED else "OFF", ")")

if __name__ == "__main__":
    main()

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
    get_ai_runtime,
    independent_ai_review,
    request_json_completion,
    validate_news_comment,
)
from news_comment_batch import summarize_news_items

# =========================
# STREFA CZASOWA
# =========================
TZ = tz.gettz("Europe/Warsaw")

# =========================
# KONFIG
# =========================
MAX_PER_SECTION = 12
MAX_PER_HOST = 6
SECTION_LIMITS = {
    "polityka": 14,
    "biznes": 10,
    "zdrowie": 10,
    "nauka": 10,
    "sport": 18,
}
SECTION_PUBLISH_BOUNDS = {
    "polityka": (5, 10),
    "biznes": (3, 6),
    "zdrowie": (3, 5),
    "nauka": (3, 5),
    "sport": (5, 10),
}
SECTION_MAX_PER_HOST = {
    "biznes": 2,
    "sport": 3,
}
HOTBAR_LIMIT = 12

AI_ENABLED = get_ai_runtime().available
AI_MODEL = os.getenv("NEWS_AI_MODEL", "gpt-4o-mini")

# osobny plik na cache AI
AI_CACHE_PATH = ".cache/ai_cache_pl.json"
# plik, z którego czyta hotbar.js
HOTBAR_JSON_PATH = ".cache/news_summaries_pl.json"

CLOUDFLARE_WEB_ANALYTICS = (
    "<!-- Cloudflare Web Analytics --><script defer src='https://static.cloudflareinsights.com/beacon.min.js' "
    "data-cf-beacon='{\"token\": \"9adde99e330a4b0d991627986ac34246\"}'></script><!-- End Cloudflare Web Analytics -->"
)
# Feedy do doboru newsów (PL)
FEEDS = {
    "polityka": [
        "https://tvn24.pl/najnowsze.xml",
        "https://tvn24.pl/polska.xml",
        "https://tvn24.pl/swiat.xml",
        {"url": "https://www.polsatnews.pl/rss/polska.xml", "source": "Polsat News"},
        "https://www.polsatnews.pl/rss/wszystkie.xml",
        {"url": "https://www.rmf24.pl/fakty/polityka/feed", "source": "RMF24"},
        {"url": "https://www.rmf24.pl/fakty/polska/feed", "source": "RMF24"},
        {"url": "https://www.rmf24.pl/fakty/swiat/feed", "source": "RMF24"},
        "https://www.pap.pl/rss.xml",
        "https://feeds.reuters.com/reuters/worldNews",
        "https://feeds.reuters.com/reuters/politicsNews",
    ],
    "biznes": [
        "https://www.bankier.pl/rss/wiadomosci.xml",
        "https://www.bankier.pl/rss/gospodarka.xml",
        {"url": "https://businessinsider.com.pl/.feed", "source": "Business Insider Polska"},
        {"url": "https://www.polsatnews.pl/rss/biznes.xml", "source": "Polsat News"},
        {"url": "https://www.rmf24.pl/ekonomia/feed", "source": "RMF24"},
        "https://www.pap.pl/rss.xml",
        "https://feeds.reuters.com/reuters/businessNews",
        {"url": "https://feeds.bbci.co.uk/news/business/rss.xml", "source": "BBC Business"},
        {"url": "https://apnews.com/hub/business?output=rss", "source": "AP Business"},
        {"url": "https://www.theguardian.com/uk/business/rss", "source": "The Guardian Business"},
    ],
    "zdrowie": [
        {"url": "https://naukawpolsce.pl/zdrowie/rss.xml", "source": "Nauka w Polsce"},
        {"url": "https://www.rmf24.pl/zdrowie/feed", "source": "RMF24"},
        "http://feeds.bbci.co.uk/news/health/rss.xml",
        "https://feeds.reuters.com/reuters/healthNews",
        "https://apnews.com/hub/health?output=rss",
        "https://www.theguardian.com/society/health/rss",
        "https://www.who.int/feeds/entity/mediacentre/news/en/rss.xml",
    ],
    "nauka": [
        {"url": "https://naukawpolsce.pl/naukowy/rss.xml", "source": "Nauka w Polsce"},
        {"url": "https://www.rmf24.pl/nauka/feed", "source": "RMF24"},
        {"url": "https://www.polsatnews.pl/rss/technologie.xml", "source": "Polsat News"},
        "http://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        "https://apnews.com/hub/science?output=rss",
        "https://www.theguardian.com/science/rss",
        "https://www.nasa.gov/feed/",
        "https://www.esa.int/rssfeed/Our_Activities/Space_News",
    ],
    "sport": [
        {"url": "https://www.pap.pl/rss.xml", "source": "PAP Sport"},
        "https://www.polsatsport.pl/rss/wszystkie.xml",
        {"url": "https://www.polsatnews.pl/rss/sport.xml", "source": "Polsat Sport"},
        {"url": "https://www.rmf24.pl/sport/feed", "source": "RMF24 Sport"},
        {"url": "https://sport.tvp.pl/rss", "source": "TVP Sport"},
        {"url": "https://przegladsportowy.onet.pl/.feed", "source": "Przegląd Sportowy / Onet Sport"},
        {"url": "https://sportowefakty.wp.pl/rss.xml", "source": "SportoweFakty WP"},
        {"url": "https://eurosport.tvn24.pl/rss.xml", "source": "Eurosport Polska"},
        {"url": "https://www.laczynaspilka.pl/rss", "source": "PZPN / Łączy nas piłka"},
        {"url": "https://www.atptour.com/en/news/rss-feed", "source": "ATP Tour"},
        {"url": "https://www.wtatennis.com/rss", "source": "WTA"},
        {"url": "https://www.fifa.com/fifaplus/en/rss", "source": "FIFA"},
        {"url": "https://www.uefa.com/rssfeed/news/rss.xml", "source": "UEFA"},
        "https://feeds.bbci.co.uk/sport/rss.xml?edition=int",
        "https://feeds.bbci.co.uk/sport/tennis/rss.xml",
        "https://feeds.reuters.com/reuters/sportsNews",
        {"url": "https://apnews.com/hub/apf-sports?utm_source=apnews.com&utm_medium=referral&utm_campaign=rss", "source": "AP Sports"},
        {"url": "https://www.espn.com/espn/rss/news", "source": "ESPN"},
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

# Zaufane źródła do weryfikacji zdrowia i nauki
OFFICIAL_HEALTH_FEEDS = [
    "https://www.who.int/feeds/entity/mediacentre/news/en/rss.xml",
    "https://www.cdc.gov/media/rss.htm",
    "https://www.nhs.uk/news/feed/",
    "https://www.cochrane.org/news-feed.xml",
]
OFFICIAL_SCIENCE_FEEDS = [
    "https://naukawpolsce.pl/naukowy/rss.xml",
    "http://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "https://apnews.com/hub/science?output=rss",
    "https://www.nasa.gov/feed/",
    "https://www.esa.int/rssfeed/Our_Activities/Space_News",
]

# Preferencje źródeł
SOURCE_PRIORITY = [
    (re.compile(r"pap\.pl", re.I), 25),
    (re.compile(r"polsatnews\.pl", re.I), 18),
    (re.compile(r"tvn24\.pl", re.I), 15),
    (re.compile(r"bankier\.pl", re.I), 12),
    (re.compile(r"businessinsider\.com\.pl", re.I), 20),
    (re.compile(r"rmf24\.pl", re.I), 18),
    (re.compile(r"naukawpolsce\.pl", re.I), 25),
    (re.compile(r"who\.int", re.I), 24),
    (re.compile(r"nasa\.gov|esa\.int", re.I), 22),
    (re.compile(r"theguardian\.com", re.I), 16),
    (re.compile(r"reuters\.com", re.I), 24),
    (re.compile(r"sport\.tvp\.pl", re.I), 23),
    (re.compile(r"polsatsport\.pl", re.I), 22),
    (re.compile(r"przegladsportowy\.onet\.pl|sport\.onet\.pl", re.I), 22),
    (re.compile(r"sportowefakty\.wp\.pl", re.I), 22),
    (re.compile(r"eurosport\.tvn24\.pl", re.I), 21),
    (re.compile(r"laczynaspilka\.pl|pzpn\.pl", re.I), 21),
    (re.compile(r"atptour\.com|wtatennis\.com|fifa\.com|uefa\.com", re.I), 18),
    (re.compile(r"apnews\.com", re.I), 20),
    (re.compile(r"espn\.", re.I), 12),
    (re.compile(r"bbc\.", re.I), 20),
]

SOURCE_NAME_RULES = [
    (re.compile(r"pap\.pl", re.I), "PAP"),
    (re.compile(r"naukawpolsce\.pl", re.I), "Nauka w Polsce"),
    (re.compile(r"who\.int", re.I), "WHO"),
    (re.compile(r"nasa\.gov", re.I), "NASA"),
    (re.compile(r"esa\.int", re.I), "ESA"),
    (re.compile(r"theguardian\.com", re.I), "The Guardian"),
    (re.compile(r"polsatsport\.pl", re.I), "Polsat Sport"),
    (re.compile(r"sport\.tvp\.pl", re.I), "TVP Sport"),
    (re.compile(r"przegladsportowy\.onet\.pl|sport\.onet\.pl", re.I), "Przegląd Sportowy / Onet Sport"),
    (re.compile(r"sportowefakty\.wp\.pl", re.I), "SportoweFakty WP"),
    (re.compile(r"eurosport\.tvn24\.pl", re.I), "Eurosport Polska"),
    (re.compile(r"laczynaspilka\.pl|pzpn\.pl", re.I), "PZPN / Łączy nas piłka"),
    (re.compile(r"atptour\.com", re.I), "ATP Tour"),
    (re.compile(r"wtatennis\.com", re.I), "WTA"),
    (re.compile(r"fifa\.com", re.I), "FIFA"),
    (re.compile(r"uefa\.com", re.I), "UEFA"),
    (re.compile(r"reuters\.com", re.I), "Reuters"),
    (re.compile(r"apnews\.com", re.I), "AP"),
    (re.compile(r"bbc\.", re.I), "BBC News"),
    (re.compile(r"espn\.", re.I), "ESPN"),
    (re.compile(r"tvn24\.pl", re.I), "TVN24"),
    (re.compile(r"polsatnews\.pl", re.I), "Polsat News"),
    (re.compile(r"bankier\.pl", re.I), "Bankier.pl"),
    (re.compile(r"businessinsider\.com\.pl", re.I), "Business Insider Polska"),
    (re.compile(r"rmf24\.pl", re.I), "RMF24"),
]

SOURCE_BADGE_SHORT = {
    "Przegląd Sportowy / Onet Sport": "Przegląd Sportowy",
    "PZPN / Łączy nas piłka": "Łączy nas piłka",
    "Eurosport Polska": "Eurosport",
    "Reuters Sports": "Reuters",
    "AP Sports": "AP",
}

POLISH_NATIVE_SOURCES = {
    "PAP",
    "Nauka w Polsce",
    "Polsat News",
    "Bankier.pl",
    "Business Insider Polska",
    "RMF24",
    "Polsat Sport",
    "TVP Sport",
    "Przegląd Sportowy / Onet Sport",
    "SportoweFakty WP",
    "Eurosport Polska",
    "PZPN / Łączy nas piłka",
    "RMF24 Sport",
}
POLISH_CP1250_AS_LATIN2 = str.maketrans({
    "Ľ": "Ą",
    "š": "ą",
    "\x8c": "Ś",
    "\x8f": "Ź",
    "\x9c": "ś",
    "\x9f": "ź",
})

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
        (re.compile(r"Polska|Polacy|Polki|Polak|Polka|polski|polska|polscy|reprezentacja Polski|kadry Polski|biało-czerwoni", re.I), 22),
        (re.compile(r"mistrzostwa świata|mistrzostwa Europy|igrzyska|Grand Slam|ATP|WTA|FIFA World Cup|UEFA|Liga Mistrzów", re.I), 20),
    ],
    "zdrowie": [
        (re.compile(r"WHO|NFZ|szpital|pacjent|lek|szczep|chorob|epidem|zdrowie publiczne|badanie kliniczne|nowotwor", re.I), 24),
    ],
    "nauka": [
        (re.compile(r"badani|nauk|odkry|kosmos|NASA|ESA|klimat|archeolog|astronom|technolog", re.I), 24),
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
LIVE_RE = re.compile(r"\b(live|na żywo|relacja live|wynik na żywo|minuta po minucie)\b", re.I)
SPORT_REJECT_RE = re.compile(
    r"\b(live|na żywo|relacja live|wynik na żywo|minuta po minucie|newsletter|typy|kursy|transmisja|stream online|gdzie oglądać|gdzie obejrzeć|program tv|zapowiedź transmisji)\b",
    re.I,
)
SPORT_ROUNDUP_RE = re.compile(
    r"\b(najważniejsze informacje|sportowy skrót|podsumowanie dnia|co dziś|dzień w sporcie|transfery live|plotki transferowe|wyniki meczów|wyniki i skróty|terminarz)\b",
    re.I,
)
URL_REJECT_RE = re.compile(
    r"/(tag|tags|temat|tematy|kategoria|category|newsletter|liveblog|liveblogs|program-tv)(/|$)|relacja-live|wynik-na-zywo|wyniki-na-zywo|live-stream|transmisja-na-zywo|stream-online|gdzie-obejrzec",
    re.I,
)
SPORT_SOURCE_HOST_RE = re.compile(
    r"polsatsport\.pl|rmf24\.pl|sport\.tvp\.pl|przegladsportowy\.onet\.pl|sportowefakty\.wp\.pl|eurosport\.tvn24\.pl|laczynaspilka\.pl|pzpn\.pl|atptour\.com|wtatennis\.com|fifa\.com|uefa\.com|reuters\.com|apnews\.com|bbc\.|espn\.",
    re.I,
)
SPORT_TOPIC_RE = re.compile(
    r"mecz|turniej|liga|finał|półfinał|ćwierćfinał|awans|medal|rekord|mistrzostw|igrzysk|tenis|ATP|WTA|Grand Slam|FIFA|UEFA|piłk|siatk|koszyk|hokej|F1|Formula 1|Grand Prix|lekkoatlety|olimpij|reprezentacja|klub|zawodnik|zawodniczka|trener|ranking",
    re.I,
)
POLISH_SPORT_RE = re.compile(
    r"\b(Polska|Polacy|Polki|Polak|Polka|polski|polska|polscy|polskie|reprezentacja Polski|kadra Polski|kadry Polski|biało-czerwoni|biało-czerwone)\b",
    re.I,
)
HIGH_RESULT_RE = re.compile(
    r"\b(wygrywa|wygrał|wygrała|wygrali|zwycięstwo|zwycięża|pokonał|pokonała|pokonali|awans|awansuje|finał|finale|półfinał|tytuł|mistrz|mistrzostwo|medal|złoto|srebro|brąz|rekord|triumf|wins|beats|final|title|medal|record|qualifies|advances)\b",
    re.I,
)
MAJOR_EVENT_RE = re.compile(
    r"mistrzostwa świata|mistrzostw świata|mistrzostwa Europy|mistrzostw Europy|igrzyska|olimpijskie|FIFA World Cup|World Cup|Euro 20\d{2}|Grand Slam|Liga Mistrzów|Champions League",
    re.I,
)
TENNIS_EVENT_RE = re.compile(r"\b(tenis|ATP|WTA|Grand Slam|Wimbledon|Roland Garros|US Open|Australian Open)\b", re.I)
NATIONAL_TEAM_RE = re.compile(r"reprezentacja Polski|kadra Polski|kadry Polski|biało-czerwoni|biało-czerwone", re.I)
CORE_POLISH_SPORT_RE = re.compile(r"piłk|siatk|olimpij|sporty olimpijskie", re.I)
GLOBAL_SPORT_RE = re.compile(r"\b(F1|Formula 1|Grand Prix|NBA|NHL|lekkoatlety|Premier League|La Liga|Serie A|Bundesliga|Liga Mistrzów|Champions League)\b", re.I)
HEALTH_TOPIC_RE = re.compile(
    r"\b(zdrow|medycz|medycyn|lekar|pacjent|szpital|chorob|zakaż|szczep|lek(?:i|u|ów)?|NFZ|WHO|"
    r"epidem|profilakty|diagno|terapi|nowotwor|rak(?:a|iem)?|health|medical|medicine|doctor|patient|"
    r"hospital|disease|infection|vaccine|drug|clinical trial|public health|cancer|FDA)\b",
    re.I,
)
SCIENCE_TOPIC_RE = re.compile(
    r"\b(nauk|badani|odkry|eksperyment|kosmos|astronom|planeta|galakty|NASA|ESA|klimat|archeolog|"
    r"biolog|fizyk|chem|technolog|science|scientist|study|research|discovery|space|moon|mars|climate|"
    r"archaeolog|species|telescope|physics|biology)\b",
    re.I,
)
BUSINESS_TOPIC_RE = re.compile(
    r"(?:gospodar\w*|ekonom\w*|biznes\w*|firm\w*|spółk\w*|przedsiębior\w*|rynk\w*|giełd\w*|GPW|WIG|akcj\w*|obligacj\w*|"
    r"bank\w*|kredyt\w*|pożycz\w*|hipotek\w*|finans\w*|walut\w*|złot\w*|dolar\w*|euro|inflac\w*|deflac\w*|PKB|NBP|RPP|"
    r"stopy procent\w*|podat\w*|VAT|CIT|PIT|budżet\w*|deficyt\w*|dług publicz\w*|hand\w*|eksport\w*|import\w*|"
    r"produkc\w*|przemysł\w*|energi\w*|paliw\w*|ropa|gaz|prac\w*|bezroboc\w*|płac\w*|wynagrod\w*|emerytur\w*|ZUS|"
    r"econom\w*|business\w*|compan\w*|market\w*|stock\w*|share\w*|bond\w*|banking|credit\w*|"
    r"inflation|GDP|interest rate\w*|tax\w*|trade|export\w*|import\w*|industr\w*|energy|oil|gas|jobs?)",
    re.I,
)
GEOPOLITICS_ONLY_RE = re.compile(
    r"(?:wojn\w*|atak\w*|ostrzał\w*|pocisk\w*|dron\w*|front\w*|żołnier\w*|armi\w*|ofiar\w*|zabit\w*|rann\w*|Ukrain\w*|Rosj\w*|"
    r"NATO|konflikt\w*|rozejm\w*|sankcj\w*|war|attack\w*|missile\w*|drone\w*|troop\w*|army|killed|wounded|"
    r"Ukraine|Russia|NATO|conflict\w*|ceasefire|sanction\w*)",
    re.I,
)
BUSINESS_IMPACT_RE = re.compile(
    r"(?:cen\w*|koszt\w*|kurs\w*|rynk\w*|giełd\w*|hand\w*|eksport\w*|import\w*|dostaw\w*|surowc\w*|ropa|gaz|energi\w*|"
    r"inwest\w*|finans\w*|bank\w*|PKB|inflac\w*|budżet\w*|sankcj\w+ gospodar\w*|price\w*|cost\w*|market\w*|trade|"
    r"supply|commodit\w*|oil|gas|energy|investment\w*|finance|banking|GDP|inflation|economic sanction\w*)",
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

def today_str() -> str:
    now = datetime.now(TZ)
    return now.strftime("%Y-%m-%d")


def today_str_pl() -> str:
    return datetime.now(TZ).strftime("%d.%m.%Y")

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

def feed_url(feed):
    return feed.get("url") if isinstance(feed, dict) else feed

def feed_source(feed):
    return feed.get("source", "") if isinstance(feed, dict) else ""

def source_name_for(link: str, fallback: str = "", section_key: str = "") -> str:
    if fallback:
        return fallback
    h = host_of(link)
    for rx, name in SOURCE_NAME_RULES:
        if rx.search(h):
            if section_key == "sport":
                return {
                    "PAP": "PAP Sport",
                    "Reuters": "Reuters Sports",
                    "AP": "AP Sports",
                    "BBC News": "BBC Sport",
                }.get(name, name)
            return name
    return h.replace("www.", "") or "Źródło"

def source_badge_for(source: str) -> str:
    return SOURCE_BADGE_SHORT.get(source, source)


def repair_polish_feed_encoding(value: str) -> str:
    return str(value or "").translate(POLISH_CP1250_AS_LATIN2)

def is_sport_related(title: str, snippet: str, link: str, source: str = "") -> bool:
    if source and source != "PAP Sport":
        return True
    if SPORT_SOURCE_HOST_RE.search(host_of(link)):
        return True
    return bool(SPORT_TOPIC_RE.search(f"{title} {snippet}"))

def is_rejected_item(section_key: str, title: str, snippet: str, link: str, source: str = "") -> bool:
    text = f"{title} {snippet}"
    if any(rx.search(text) for rx in BAN_PATTERNS):
        return True
    path = urlparse(link).path or "/"
    if path in ("", "/") or URL_REJECT_RE.search(path):
        return True
    if section_key == "biznes" and not BUSINESS_TOPIC_RE.search(f"{text} {link}"):
        return True
    topic_filter = {"zdrowie": HEALTH_TOPIC_RE, "nauka": SCIENCE_TOPIC_RE}.get(section_key)
    if topic_filter and not topic_filter.search(f"{text} {link} {source}"):
        return True
    if section_key == "biznes" and GEOPOLITICS_ONLY_RE.search(text) and not BUSINESS_IMPACT_RE.search(text):
        return True
    if section_key == "sport":
        if len([seg for seg in path.split("/") if seg]) <= 1:
            return True
        if not is_sport_related(title, snippet, link, source):
            return True
        if SPORT_REJECT_RE.search(text) or SPORT_ROUNDUP_RE.search(text) or LIVE_RE.search(path):
            return True
    return False

def sport_priority_points(title: str, snippet: str, link: str) -> int:
    text = f"{title} {snippet}"
    points = 0
    if POLISH_SPORT_RE.search(text) and HIGH_RESULT_RE.search(text):
        points += 5
    if MAJOR_EVENT_RE.search(text):
        points += 5
    if TENNIS_EVENT_RE.search(text) and POLISH_SPORT_RE.search(text):
        points += 4
    if NATIONAL_TEAM_RE.search(text) or CORE_POLISH_SPORT_RE.search(text):
        points += 4
    if GLOBAL_SPORT_RE.search(text):
        points += 3
    return points

def sport_tag(title: str, snippet: str = "") -> str:
    t = f"{title} {snippet}".lower()
    if re.search(r"tenis|atp|wta|grand slam|wimbledon|roland garros|us open|australian open", t):
        return "tenis"
    if re.search(r"piłk|football|fifa|uefa|liga mistrzów|champions league|world cup", t):
        return "pilka"
    if re.search(r"siatk|volleyball|liga narodów", t):
        return "siatkowka"
    if re.search(r"\bf1\b|formula 1|grand prix", t):
        return "f1"
    if re.search(r"koszyk|nba", t):
        return "kosz"
    if re.search(r"hokej|nhl", t):
        return "hokej"
    if re.search(r"lekkoatlety|athletics", t):
        return "lekkoatletyka"
    if re.search(r"igrzysk|olimpij|olympic", t):
        return "olimpijskie"
    return "inne"

def why_it_matters_pl(section_key: str, title: str, snippet: str) -> str:
    text = f"{title} {snippet}"
    if section_key == "sport":
        if POLISH_SPORT_RE.search(text) and HIGH_RESULT_RE.search(text):
            return "To ważny wynik z polskiej perspektywy, bo może wpływać na prestiż, ranking, awans albo pozycję reprezentacji, klubu lub zawodnika."
        if MAJOR_EVENT_RE.search(text):
            return "To wydarzenie wysokiej rangi, więc jego wynik porządkuje szerszy obraz rywalizacji międzynarodowej."
        if TENNIS_EVENT_RE.search(text):
            return "Tenisowe turnieje ATP, WTA i Grand Slam szybko zmieniają rankingi oraz układ kolejnych rund."
        return "To temat sportowy z wiarygodnego źródła, który pomaga śledzić najważniejsze rozstrzygnięcia i ich kontekst."
    if section_key == "biznes":
        return "Ten temat może mieć znaczenie dla cen, firm, rynku pracy albo decyzji finansowych gospodarstw domowych."
    if section_key == "zdrowie":
        return "W zdrowiu kluczowe są skala zjawiska, grupa ryzyka oraz to, czy informacja zmienia zalecenia dla pacjentów lub lekarzy."
    if section_key == "nauka":
        return "W nauce najważniejsze są metoda, jakość danych i stopień potwierdzenia wyniku, a nie sam efektowny nagłówek."
    return "To istotne, bo wpływa na decyzje publiczne, bezpieczeństwo albo codzienne życie obywateli."

def score_item(item, section_key: str) -> float:
    score = 0.0
    published_parsed = item.get("published_parsed") or item.get("updated_parsed")
    if published_parsed:
        dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        score += max(0.0, 36.0 - age_h)
    t = item.get("title", "") or ""
    for rx, pts in BOOST.get(section_key, []):
        if rx.search(t):
            score += pts
    if section_key == "sport":
        score += sport_priority_points(t, item.get("summary_raw", ""), item.get("link", "")) * 8
    h = host_of(item.get("link", "") or "")
    for rx, pts in SOURCE_PRIORITY:
        if rx.search(h):
            score += pts
    if len(t) > 140:
        score -= 5
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

def ai_summarize_pl(title: str, snippet: str, url: str, section_key: str = "") -> dict:
    """
    Zwraca: {"summary": "...", "why": "...", "uncertain": ""} - 'uncertain' puste, gdy brak ostrzeżenia.
    """
    runtime = get_ai_runtime()
    source_text = re.sub(r"\s+", " ", (snippet or "").strip())
    if len(source_text) < 55:
        return {"summary": "", "why": "", "uncertain": "", "model": "insufficient_source", "reviewed": False}
    digest = hashlib.sha256(f"{title}|{url}|{source_text}".encode("utf-8")).hexdigest()[:20]
    cache_key = f"strict-v{QUALITY_VERSION}|{digest}"
    if cache_key in CACHE:
        cached = CACHE[cache_key]
        quality = validate_news_comment(str(cached.get("summary") or ""), "pl")
        if (
            cached.get("reviewed") is True
            and cached.get("quality_version") == QUALITY_VERSION
            and quality.valid
        ):
            return cached

    if not runtime.available:
        return {
            "summary": "",
            "why": "",
            "uncertain": "",
            "model": "unavailable",
            "reviewed": False,
        }

    prompt = f"""Napisz po polsku krótki komentarz do newsa wyłącznie na podstawie tytułu i opisu RSS.
Zwróć wyłącznie JSON: {{"summary":"..."}}.
Komentarz ma mieć 1-2 pełne, konkretne zdania i 55-320 znaków.

Tytuł: {title}
Opis RSS: {source_text}
Zasady:
- Bądź zwięzły, neutralny, poprawny językowo i zrozumiały bez dodatkowego kontekstu.
- Nie kopiuj uszkodzonych znaków, porozcinanych wyrazów, podpisów, fragmentów interfejsu ani poleceń wydawcy.
- Nie dopisuj faktów spoza tytułu/opisu.
- Jeśli materiał jest uszkodzony albo nie pozwala na pewne streszczenie, zwróć pusty summary.
- Nie używaj formatowania Markdown."""

    try:
        payload = request_json_completion(
            post=requests.post,
            runtime=runtime,
            messages=[
                {"role": "system", "content": "Jesteś rygorystycznym redaktorem newsów. Zwracasz wyłącznie poprawny JSON."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=220,
            temperature=0.1,
            timeout=25,
        )
        quality = validate_news_comment(str(payload.get("summary") or ""), "pl")
        if not quality.valid:
            print(f"[WARN] PL news comment rejected: {title[:80]} :: {','.join(quality.reasons)}", file=sys.stderr)
            return {"summary": "", "why": "", "uncertain": "", "model": AI_MODEL, "reviewed": False}
        reviewed, reason = independent_ai_review(
            post=requests.post,
            runtime=runtime,
            title=title,
            source_text=source_text,
            summary=quality.text,
            lang="pl",
        )
        if not reviewed:
            print(f"[WARN] PL news AI review rejected: {title[:80]} :: {reason[:160]}", file=sys.stderr)
            return {"summary": "", "why": "", "uncertain": "", "model": AI_MODEL, "reviewed": False}
        out = {
            "summary": quality.text,
            "why": "",
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
        print(f"[WARN] PL news AI generation failed: {title[:80]} :: {ex}", file=sys.stderr)
        return {
            "summary": "",
            "why": "",
            "uncertain": "",
            "model": AI_MODEL,
            "reviewed": False,
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

def verify_note_pl(title: str, snippet: str, section_key: str = "") -> str:
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
        verification_feeds = OFFICIAL_HEALTH_FEEDS if section_key == "zdrowie" else OFFICIAL_SCIENCE_FEEDS
        sci_srcs = _search_trusted_sources(title, verification_feeds)
        if not sci_srcs:
            if section_key == "zdrowie":
                notes.append("nie znaleziono zbieżnych nagłówków w WHO/CDC/NHS/Cochrane.")
            else:
                notes.append("nie znaleziono zbieżnych nagłówków w zaufanych źródłach naukowych.")

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
def entry_image(entry, source_url: str) -> str:
    for key in ("media_thumbnail", "media_content"):
        media = entry.get(key) or []
        if isinstance(media, dict):
            media = [media]
        for item in media:
            image_url = (item or {}).get("url")
            if image_url:
                return urljoin(source_url, image_url)
    for enclosure in entry.get("enclosures") or []:
        image_url = (enclosure or {}).get("href") or (enclosure or {}).get("url")
        content_type = ((enclosure or {}).get("type") or "").lower()
        if image_url and content_type.startswith("image/"):
            return urljoin(source_url, image_url)
    return ""


def article_image(link: str) -> str:
    """Read the article metadata when the RSS feed has no thumbnail."""
    try:
        response = requests.get(
            link,
            headers={"User-Agent": "BriefRoomsBot/2.0 (+https://briefrooms.com)"},
            timeout=7,
        )
        if response.status_code >= 400:
            return ""
        page = response.text[:350000]
    except Exception:
        return ""

    patterns = (
        r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, page, re.I)
        if not match:
            continue
        image_url = urljoin(link, html.unescape(match.group(1)).strip())
        if image_url.lower().startswith(("http://", "https://")):
            return image_url
    return ""


def fetch_section(section_key: str, summarize: bool = True):
    items = []
    for feed in FEEDS[section_key]:
        f_url = feed_url(feed)
        f_source = feed_source(feed)
        try:
            parsed = feedparser.parse(f_url)
            for e in parsed.entries:
                title = e.get("title", "") or ""
                link  = e.get("link", "") or ""
                if not title or not link:
                    continue
                snippet = e.get("summary", "") or e.get("description", "") or ""
                clean_snippet = re.sub("<[^<]+?>", "", snippet).strip()
                source_name = source_name_for(link, f_source, section_key)
                if source_name in POLISH_NATIVE_SOURCES:
                    title = repair_polish_feed_encoding(title)
                    clean_snippet = repair_polish_feed_encoding(clean_snippet)
                if is_rejected_item(section_key, title, clean_snippet, link, source_name):
                    continue
                items.append({
                    "title": title.strip(),
                    "link":  link.strip(),
                    "source_name": source_name,
                    "source_badge": source_badge_for(source_name),
                    "summary_raw": clean_snippet,
                    "thumbnail_url": entry_image(e, f_url),
                    "published_parsed": e.get("published_parsed") or e.get("updated_parsed"),
                })
        except Exception as ex:
            print(f"[WARN] RSS error: {f_url} -> {ex}", file=sys.stderr)

    # scoring + tokeny
    for it in items:
        it["_score"] = score_item(it, section_key)
        it["_tok"] = tokens_pl(it["title"])

    # sortuj po score
    items.sort(key=lambda x: x["_score"], reverse=True)

    # deduplikacja semantyczna (Jaccard na tokenach tytułu)
    kept = []
    for it in items:
        if not any(jaccard(it["_tok"], got["_tok"]) >= SIMILARITY_THRESHOLD for got in kept):
            kept.append(it)

    # limit na host + total
    per_host = {}
    pool = []
    host_limit = SECTION_MAX_PER_HOST.get(section_key, MAX_PER_HOST)
    for it in kept:
        h = host_of(it["link"])
        per_host[h] = per_host.get(h, 0)
        if per_host[h] >= host_limit:
            continue
        per_host[h] += 1
        pool.append(it)

    limit = SECTION_LIMITS.get(section_key, MAX_PER_SECTION)

    # SPORT: dywersyfikacja źródeł i dyscyplin bez stałych nazwisk
    if section_key == "sport":
        picked = []

        def add_item(it):
            if it in picked or len(picked) >= limit:
                return False
            picked.append(it)
            return True

        seen_sources = set()
        for it in pool:
            src = it.get("source_name", "")
            if src and src not in seen_sources and add_item(it):
                seen_sources.add(src)
            if len(picked) >= limit:
                break

        seen_tags = {sport_tag(x["title"], x.get("summary_raw", "")) for x in picked}
        for it in pool:
            if it in picked:
                continue
            tg = sport_tag(it["title"], it.get("summary_raw", ""))
            if tg not in seen_tags:
                add_item(it)
                seen_tags.add(tg)
            if len(picked) >= limit:
                break

        for it in pool:
            if len(picked) >= limit:
                break
            add_item(it)
    elif section_key == "biznes":
        # Najpierw po jednym materiale z każdego źródła; dopiero potem uzupełnij sekcję.
        picked = []
        seen_sources = set()
        for it in pool:
            src = it.get("source_name") or host_of(it.get("link", ""))
            if src in seen_sources:
                continue
            picked.append(it)
            seen_sources.add(src)
            if len(picked) >= limit:
                break
        for it in pool:
            if len(picked) >= limit:
                break
            if it not in picked:
                picked.append(it)
    else:
        picked = pool[:limit]

    if section_key in ("polityka", "biznes"):
        for item in picked:
            if not item.get("thumbnail_url"):
                item["thumbnail_url"] = article_image(item.get("link", ""))

    if not summarize:
        return picked

    # AI + weryfikacja (ostrzeżenie tylko przy realnym powodzie)
    for it in picked:
        s = ai_summarize_pl(it["title"], it.get("summary_raw", ""), it["link"], section_key)
        verify = verify_note_pl(it["title"], it.get("summary_raw",""), section_key)
        final_warn = verify or s.get("uncertain","")
        it["ai_summary"] = ensure_period(s["summary"])
        it["ai_why"] = ensure_period(s.get("why") or why_it_matters_pl(section_key, it["title"], it.get("summary_raw", "")))
        it["ai_uncertain"] = ensure_period(final_warn) if final_warn else ""
        it["ai_model"] = s.get("model","")
        it["comment_quality_status"] = s.get("quality_status", "")
        it["comment_quality_version"] = s.get("quality_version")
        it["comment_generation_status"] = "ai_review_approved" if s.get("reviewed") is True else "rejected_or_unavailable"

    return picked


def summarize_sections_pl(sections: dict) -> None:
    all_items = [item for items in sections.values() for item in items]
    results = summarize_news_items(items=all_items, lang="pl", cache=CACHE, post=requests.post)
    for item in all_items:
        result = results.get(str(item.get("_comment_batch_id") or ""), {})
        summary = str(result.get("summary") or "")
        if result.get("title_pl"):
            item["title"] = str(result["title_pl"])
        section_key = str(item.get("_section_key") or "")
        verify = verify_note_pl(item["title"], item.get("summary_raw", ""), section_key)
        item["ai_summary"] = ensure_period(summary) if summary else ""
        item["ai_why"] = ""
        item["ai_uncertain"] = ensure_period(verify) if verify else ""
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
    updated_at = datetime.now(TZ)
    extra_css = """
    ul.news{ list-style:none; padding-left:0; }
    ul.news li{ margin:18px 0 24px; }
    .news-main-link{
      display:flex; align-items:center; gap:10px;
      color:#fdf3e3; text-decoration:none; line-height:1.25;
    }
    .news-main-link:hover{ color:#ffffff; text-decoration:underline; }
    .news-main-link:focus-visible,.read-source:focus-visible{ outline:3px solid #f8c97a; outline-offset:4px; border-radius:6px; }
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
    .news-thumb .title{ max-width:58px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:.54rem; font-weight:700; letter-spacing:0; color:#fff; line-height:1; }
    .news-thumb .sub{ font-size:.47rem; color:rgba(244,246,255,.85); line-height:1.05; white-space:nowrap; }
    .news-thumb.has-image{ padding:0; overflow:hidden; background:rgba(255,255,255,.06); align-items:stretch; }
    .news-thumb.has-image img{ width:100%; height:100%; display:block; object-fit:cover; }
    .news-title-wrap{ display:flex; flex-direction:column; gap:4px; min-width:0; }
    .news-text{ font-weight:700; }
    .source-line{ color:#9fb3cb; font-size:.88rem; }

    .ai-note{
      margin:10px 0 0 88px;
      font-size:.95rem; color:#dfe7f1; line-height:1.4;
      background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.08);
      padding:12px 14px; border-radius:12px;
    }
    .ai-head{ display:flex; align-items:center; gap:8px; margin-bottom:6px; font-weight:700; color:#fdf3e3; }
    .ai-badge{ display:inline-flex; align-items:center; gap:6px; padding:3px 8px; border-radius:999px;
      background:rgba(255,255,255,.07); font-size:.75rem; color:#fff; border:1px solid rgba(255,255,255,.16); }
    .ai-dot{ width:8px; height:8px; border-radius:999px; background:#fff; box-shadow:0 0 6px rgba(255,255,255,.7); }
    .sec{ margin-top:4px; }
    .read-source{ display:inline-flex; margin-top:10px; color:#f8c97a; text-decoration:none; font-weight:700; }
    .read-source:hover{ color:#ffffff; text-decoration:underline; }
    .note{ color:#9fb3cb; font-size:.92rem }
    .empty-state{ color:#b9c5d8; padding:12px 0; }
    @media(max-width:640px){
      .news-main-link{ align-items:flex-start; }
      .news-thumb{ width:70px; min-width:70px; height:50px; }
      .ai-note{ margin-left:0; }
    }
    """

    def badge(it, source: str):
        thumbnail_url = (it.get("thumbnail_url") or "").strip()
        if thumbnail_url:
            return (
                '<span class="news-thumb has-image">'
                f'<img src="{esc(thumbnail_url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" width="78" height="54" />'
                '</span>'
            )
        return (
            '<span class="news-thumb">'
            '<span class="dot"></span>'
            f'<span class="title">{esc(source_badge_for(source))}</span>'
            '<span class="sub">źródło</span>'
            '</span>'
        )

    def make_li(it):
        source = it.get("source_name") or source_name_for(it.get("link", ""))
        warn_html = f'<div class="sec"><strong>Uwaga:</strong> {esc(it["ai_uncertain"])}</div>' if it.get("ai_uncertain") else ""
        return f'''<li>
  <a class="news-main-link" href="{esc(it["link"])}" target="_blank" rel="noopener">
    {badge(it, source)}
    <span class="news-title-wrap">
      <span class="news-text">{esc(it["title"])}</span>
      <span class="source-line">Źródło: {esc(source)}</span>
    </span>
  </a>
  <div class="ai-note">
    <div class="ai-head"><span class="ai-badge"><span class="ai-dot"></span> Komentarz</span></div>
    <div class="sec"><strong>Najważniejsze:</strong> {esc(it.get("ai_summary",""))}</div>
    <div class="sec"><strong>Dlaczego to ważne:</strong> {esc(it.get("ai_why",""))}</div>
    {warn_html}
  </div>
</li>'''

    def make_section(title, items):
        lis = "\n".join(make_li(it) for it in items)
        if not lis:
            lis = '<li class="empty-state">Brak nowych materiałów spełniających kryteria jakości. Sekcja zostanie sprawdzona ponownie przy najbliższej aktualizacji.</li>'
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
  <meta name="description" content="Automatycznie odświeżane aktualności z ostatnich godzin: polityka, ekonomia, zdrowie, nauka i sport." />
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
  <link rel="stylesheet" href="/assets/site-header.css?v=20260719-1" />
  <script src="/scripts/site-header.js?v=20260719-1" defer></script>
</head>
<body data-page="news">
<header id="site-header"></header>
<header>
  <h1>Aktualności</h1>
  <p class="sub">Ostatnia aktualizacja: <time datetime="{updated_at.isoformat(timespec='minutes')}">{updated_at.strftime('%d.%m.%Y, %H:%M')}</time></p>
</header>
<main>
{make_section("Polityka / Kraj", sections["polityka"])}
{make_section("Ekonomia / Biznes", sections["biznes"])}
{make_section("Zdrowie", sections["zdrowie"])}
{make_section("Nauka", sections["nauka"])}
{make_section("Sport", sections["sport"])}

</main>
<footer style="text-align:center; opacity:.55; padding:18px">© {updated_at.year} BriefRooms</footer>
<!-- Cloudflare Web Analytics --><script defer src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{{"token": "9adde99e330a4b0d991627986ac34246"}}'></script><!-- End Cloudflare Web Analytics -->
</body>
</html>"""
    return html_out

# =========================
# MAIN
# =========================
def main():
    sections = {
        "polityka": fetch_section("polityka", summarize=False),
        "biznes":   fetch_section("biznes", summarize=False),
        "zdrowie":  fetch_section("zdrowie", summarize=False),
        "nauka":    fetch_section("nauka", summarize=False),
        "sport":    fetch_section("sport", summarize=False),
    }
    for section_key, items in sections.items():
        for item in items:
            item["_section_key"] = section_key
    summarize_sections_pl(sections)
    sections = finalize_sections(sections)

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

    print("[OK] Wygenerowano pl/aktualnosci.html +", HOTBAR_JSON_PATH, "(AI:", "ON" if AI_ENABLED else "OFF", ")")

if __name__ == "__main__":
    main()

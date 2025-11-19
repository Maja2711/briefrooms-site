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
MAX_PER_SECTION = 6
MAX_PER_HOST = 6
HOTBAR_LIMIT = 12

AI_ENABLED = bool(os.getenv("OPENAI_API_KEY"))
AI_MODEL = os.getenv("NEWS_AI_MODEL", "gpt-4o-mini")

# osobny plik na cache AI
AI_CACHE_PATH = ".cache/ai_cache_pl.json"
# plik, z którego czyta hotbar.js
HOTBAR_JSON_PATH = ".cache/news_summaries_pl.json"

# Feedy do doboru newsów (PL)
FEEDS = {
    "polityka": [
        "https://tvn24.pl/najnowsze.xml",
        "https://tvn24.pl/polska.xml",
        "https://tvn24.pl/swiat.xml",
        "https://www.polsatnews.pl/rss/wszystkie.xml",
        "https://www.pap.pl/rss.xml",
        "https://feeds.reuters.com/reuters/worldNews",
        "https://feeds.reuters.com/reuters/politicsNews",
    ],
    "biznes": [
        "https://www.bankier.pl/rss/wiadomosci.xml",
        "https://www.bankier.pl/rss/gospodarka.xml",
        "https://www.pap.pl/rss.xml",
        "https://feeds.reuters.com/reuters/businessNews",
    ],
    "sport": [
        "https://www.polsatsport.pl/rss/wszystkie.xml",
        "https://tvn24.pl/sport.xml",
        "https://www.pap.pl/rss.xml",
        "https://feeds.bbci.co.uk/sport/rss.xml?edition=int",
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
    (re.compile(r"bankier\.pl", re.I), 20),
    (re.compile(r"reuters\.com", re.I), 12),
    (re.compile(r"polsatsport\.pl", re.I), 22),
    (re.compile(r"bbc\.", re.I), 10),
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
        (re.compile(r"(LIVE|na żywo|relacja live|transmisja)", re.I), 34),
    ],
}

# Filtry
BAN_PATTERNS = [
    re.compile(r"horoskop|plotk|quiz|sponsorowany|galeria|zobacz zdjęcia|clickbait", re.I),
]
LIVE_RE = re.compile(r"(LIVE|na żywo|relacja live|transmisja)", re.I)

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

def ai_summarize_pl(title: str, snippet: str, url: str) -> dict:
    """
    Zwraca: {"summary": "...", "uncertain": ""} – 'uncertain' puste, gdy brak ostrzeżenia.
    """
    key = os.getenv("OPENAI_API_KEY")
    cache_key = f"{norm_title(title)}|{today_str()}"
    if cache_key in CACHE:
        return CACHE[cache_key]

    if not key:
        out = {
            "summary": ensure_full_sentence((snippet or title or "")[:320], 320),
            "uncertain": "",
            "model": "fallback"
        }
        CACHE[cache_key] = out
        save_cache(AI_CACHE_PATH, CACHE)
        return out

    prompt = f"""Streść po polsku w maksymalnie 2 krótkich zdaniach najważniejsze informacje z tytułu i opisu RSS poniżej.
Jeśli widać element niepewny/sporny lub łatwy do błędnej interpretacji, dodaj JEDNO krótkie zdanie zaczynające się od "Uwaga:".
Jeśli nie ma potrzeby ostrzeżenia, NIE dodawaj tej linii.
Tytuł: {title}
Opis RSS: {snippet}
Zasady:
- Bądź zwięzły i neutralny.
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
        summary_lines, warn_line = [], ""
        for ln in lines:
            if ln.lower().startswith("uwaga:"):
                warn_line = ln
            else:
                summary_lines.append(ln)

        summary = ensure_full_sentence(" ".join(summary_lines), 320)
        warn_line = ensure_period(warn_line) if warn_line else ""

        out = {"summary": summary, "uncertain": warn_line}
        CACHE[cache_key] = out
        save_cache(AI_CACHE_PATH, CACHE)
        return out
    except Exception:
        return {
            "summary": ensure_full_sentence((snippet or title)[:320], 320),
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
                if any(rx.search(title) for rx in BAN_PATTERNS):
                    continue
                snippet = e.get("summary", "") or e.get("description", "") or ""
                items.append({
                    "title": title.strip(),
                    "link":  link.strip(),
                    "summary_raw": re.sub("<[^<]+?>", "", snippet).strip(),
                    "published_parsed": e.get("published_parsed") or e.get("updated_parsed"),
                })
        except Exception as ex:
            print(f"[WARN] RSS error: {feed_url} -> {ex}", file=sys.stderr)

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
    for it in kept:
        h = host_of(it["link"])
        per_host[h] = per_host.get(h, 0)
        if per_host[h] >= MAX_PER_HOST:
            continue
        per_host[h] += 1
        pool.append(it)

    # SPORT: LIVE + dywersyfikacja dyscyplin
    if section_key == "sport":
        picked = []
        # 1) do 2 wpisów LIVE lub z PL gwiazdami
        for it in pool:
            t = it["title"]
            if LIVE_RE.search(t) or re.search(r"Świątek|Swiatek|Hurkacz|Lewandowski|Zielińsk|Zielinsk|Stoch|Żyła|Zyla|Legia|Raków|Rakow|Lech", t, re.I):
                picked.append(it)
                if len(picked) >= 2:
                    break

        # 2) dywersyfikacja
        def tag(t):
            t=t.lower()
            if any(k in t for k in ["tenis","wimbledon","us open","australian open"]): return "tenis"
            if any(k in t for k in ["piłka nożna","ekstraklasa","liga konferencji","liga europy","mecz","legia","lech","raków","rakow"]): return "pilka"
            if any(k in t for k in ["siatkówka","siatkar"]): return "siatkowka"
            if any(k in t for k in ["koszykówka","nba"]): return "kosz"
            if any(k in t for k in ["f1","formula","grand prix"]): return "f1"
            return "inne"

        seen_tags = {tag(x["title"]) for x in picked}
        for it in pool:
            if it in picked: 
                continue
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
        it["ai_summary"] = ensure_period(s["summary"])
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
    .note{ color:#9fb3cb; font-size:.92rem }
    """

    def badge():
        return (
            '<span class="news-thumb">'
            '<span class="dot"></span>'
            '<span class="title">BriefRooms</span>'
            '<span class="sub">powered by AI</span>'
            '</span>'
        )

    def make_li(it):
        warn_html = f'<div class="sec"><strong>Uwaga:</strong> {esc(it["ai_uncertain"])}</div>' if it.get("ai_uncertain") else ""
        return f'''<li>
  <a href="{esc(it["link"])}" target="_blank" rel="noopener">
    {badge()}
    <span class="news-text">{esc(it["title"])}</span>
  </a>
  <div class="ai-note">
    <div class="ai-head"><span class="ai-badge"><span class="ai-dot"></span> BriefRooms • AI komentarz</span></div>
    <div class="sec"><strong>Najważniejsze:</strong> {esc(it.get("ai_summary",""))}</div>
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

    html_out = f"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Aktualności — BriefRooms</title>
  <meta name="description" content="Automatycznie odświeżane aktualności z ostatnich godzin: polityka, ekonomia, sport." />
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
  </style>
</head>
<body>
<header>
  <h1>Aktualności</h1>
  <p class="sub">Ostatnie ~36 godzin • {today_str()}</p>
</header>
<main>
{make_section("Polityka / Kraj", sections["polityka"])}
{make_section("Ekonomia / Biznes", sections["biznes"])}
{make_section("Sport", sections["sport"])}

<p class="note">Automatyczny skrót (RSS). Linki prowadzą do wydawców. Strona nadpisywana automatycznie.</p>
</main>
<footer style="text-align:center; opacity:.55; padding:18px">© BriefRooms</footer>
</body>
</html>"""
    return html_out

# =========================
# MAIN
# =========================
def main():
    sections = {
        "polityka": fetch_section("polityka"),
        "biznes":   fetch_section("biznes"),
        "sport":    fetch_section("sport"),
    }

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

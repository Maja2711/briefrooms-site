#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import html
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
from dateutil import tz
import requests

# ===== USTAWIENIA CZASU =====
TZ = tz.gettz("Europe/Warsaw")

# ===== KONFIG OGÓLNY =====
MAX_PER_SECTION = 6
MAX_PER_HOST = 6

AI_ENABLED = bool(os.getenv("OPENAI_API_KEY"))
AI_MODEL   = os.getenv("NEWS_AI_MODEL", "gpt-4o-mini")

# Każda zmiana tej wersji „przebija” cache i wymusza nowe skróty.
SUMMARY_VERSION = "2025-11-07b"
CACHE_PATH = ".cache/news_summaries_pl.json"

# ===== REGUŁY WYBORU FEEDÓW =====
FEEDS = {
    "polityka": [
        "https://tvn24.pl/najnowsze.xml",
        "https://tvn24.pl/polska.xml",
        "https://tvn24.pl/swiat.xml",
        "https://www.polsatnews.pl/rss/wszystkie.xml",
        "https://feeds.reuters.com/reuters/worldNews",
        "https://feeds.reuters.com/reuters/politicsNews",
    ],
    "biznes": [
        "https://www.bankier.pl/rss/wiadomosci.xml",
        "https://www.bankier.pl/rss/gospodarka.xml",
        "https://feeds.reuters.com/reuters/businessNews",
    ],
    "sport": [
        "https://www.polsatsport.pl/rss/wszystkie.xml",
        "https://tvn24.pl/sport.xml",
        "https://feeds.bbci.co.uk/sport/rss.xml?edition=int",  # dywersyfikacja
    ],
}

# ===== PRIORYTETY ŹRÓDEŁ =====
def rx(d): return re.compile(d, re.I)
SOURCE_PRIORITY = [
    (rx(r"pap\.pl"),          25),
    (rx(r"polsatnews\.pl"),   18),
    (rx(r"tvn24\.pl"),        15),
    (rx(r"bankier\.pl"),      20),
    (rx(r"reuters\.com"),     12),
    (rx(r"polsatsport\.pl"),  22),
    (rx(r"bbc\."),            10),
]

# ===== DOPALACZE TEMATYCZNE =====
BOOST = {
    "polityka": [
        (re.compile(r"Polska|kraj|Sejm|Senat|premier|prezydent|rząd|samorząd|ustawa|Trybunał|UE|budżet|PKW", re.I), 30),
        (re.compile(r"inflacja|NBP|RPP|podatek|ZUS|emerytur|Orlen|PGE|LOT|PKP", re.I), 18),
    ],
    "biznes": [
        (re.compile(r"NBP|RPP|PKB|inflacja|stopy|obligacj|kredyt|VAT|CIT|PIT|GPW|WIG|paliw|energia|MWh", re.I), 28),
    ],
    "sport": [
        # promuj polskich sportowców/brandów i LIVE
        (re.compile(r"Polak|Polska|Polski|Świątek|Swiatek|Hurkacz|Lewandowski|Zielińsk|Zielinsk|Stoch|Żyła|Zyla|"
                    r"Siatkar|Reprezentacja|Legia|Raków|Rakow|Lech|Iga|Hubert", re.I), 40),
        (re.compile(r"(LIVE|na żywo|relacja live|transmisja)", re.I), 34),
    ],
}

# ===== FILTRY =====
BAN_PATTERNS = [
    re.compile(r"horoskop|plotk|quiz|sponsorowany|galeria|zobacz zdjęcia|clickbait", re.I),
]
LIVE_RE = re.compile(r"(LIVE|na żywo|relacja live|transmisja)", re.I)

# ===== NARZĘDZIA =====
def host_of(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return ""

def esc(s: str) -> str:
    return html.escape(s or "", quote=True)

def today_str() -> str:
    now = datetime.now(TZ)
    return now.strftime("%Y-%m-%d")

def norm_title(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"&\w+;|&#\d+;", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def ensure_full_sentence(text: str, max_chars: int = 320) -> str:
    """Przytnij do <= max_chars i zakończ na pełnym zdaniu (. ! ?)."""
    t = (text or "").strip()
    if not t:
        return ""
    if len(t) > max_chars:
        t = t[:max_chars+1]
    end = max(t.rfind("."), t.rfind("!"), t.rfind("?"))
    if end != -1:
        t = t[:end+1]
    return t.strip()

# ===== SCORING =====
def score_item(item, section_key: str) -> float:
    score = 0.0
    published_parsed = item.get("published_parsed") or item.get("updated_parsed")
    if published_parsed:
        dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        score += max(0.0, 36.0 - age_h)

    t = item.get("title", "") or ""
    for rxp, pts in BOOST.get(section_key, []):
        if rxp.search(t):
            score += pts

    h = host_of(item.get("link", "") or "")
    for rxp, pts in SOURCE_PRIORITY:
        if rxp.search(h):
            score += pts

    if len(t) > 140:
        score -= 5
    return score

# ===== CACHE =====
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

CACHE = load_cache(CACHE_PATH)

# ===== AI PODSUMOWANIA (PL) =====
def ai_summarize_pl(title: str, snippet: str) -> dict:
    """
    Zwraca: {"summary": "...", "uncertain": ""} – 'uncertain' pusta, jeśli brak niepewności.
    """
    cache_key = f"{SUMMARY_VERSION}|{norm_title(title)}|{today_str()}"
    if cache_key in CACHE:
        return CACHE[cache_key]

    key = os.getenv("OPENAI_API_KEY")
    if not key:
        out = {
            "summary": ensure_full_sentence((snippet or title)[:320], 320),
            "uncertain": ""
        }
        CACHE[cache_key] = out
        save_cache(CACHE_PATH, CACHE)
        return out

    prompt = f"""Streść po polsku w maksymalnie 2 krótkich zdaniach najważniejsze informacje z tytułu i fragmentu RSS poniżej.
Następnie – tylko jeśli to zasadne – dodaj jedno zdanie zaczynające się od „Niepewne / sporne:”, opisujące to, co niejasne,
wstępne lub potencjalnie mylące dla czytelnika. Jeśli nic takiego nie występuje, NIE dodawaj tej linii.

Tytuł: {title}
Opis RSS: {snippet}

Zasady:
- Bądź zwięzły, neutralny i nie spekuluj.
- Każde zdanie zakończ kropką.
- Nie dopisuj faktów spoza tytułu/opisu.
- Zwróć czysty tekst (bez formatowania)."""

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": AI_MODEL,
                "messages": [
                    {"role": "system", "content": "Jesteś rzetelnym i zwięzłym asystentem prasowym."},
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
        summary_lines, uncertain_line = [], ""
        for ln in lines:
            if ln.lower().startswith("niepewne / sporne:"):
                uncertain_line = ln
            else:
                summary_lines.append(ln)

        summary = ensure_full_sentence(" ".join(summary_lines), 320)
        # usuń „niepewne”, jeśli puste
        if not uncertain_line or uncertain_line.lower() in {"niepewne / sporne:", "niepewne/sporne:", "niepewne: brak", "niepewne / sporne: brak"}:
            uncertain_line = ""

        out = {"summary": summary, "uncertain": uncertain_line}
        CACHE[cache_key] = out
        save_cache(CACHE_PATH, CACHE)
        return out
    except Exception:
        return {
            "summary": ensure_full_sentence((snippet or title)[:320], 320),
            "uncertain": ""
        }

# ===== POBIERANIE I WYBÓR =====
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
        except Exception:
            pass

    for it in items:
        it["_score"] = score_item(it, section_key)
    items.sort(key=lambda x: x["_score"], reverse=True)

    seen = set()
    deduped = []
    for it in items:
        key = norm_title(it["title"])
        if key and key not in seen:
            seen.add(key)
            deduped.append(it)

    per_host = {}
    pool = []
    for it in deduped:
        h = host_of(it["link"])
        per_host[h] = per_host.get(h, 0)
        if per_host[h] >= MAX_PER_HOST:
            continue
        per_host[h] += 1
        pool.append(it)

    if section_key == "sport":
        picked = []
        # 1) preferuj LIVE lub PL gwiazdy – weź do 2
        for it in pool:
            t = it["title"]
            if LIVE_RE.search(t) or re.search(r"Świątek|Swiatek|Hurkacz|Lewandowski|Zielińsk|Zielinsk|Legia|Raków|Rakow|Lech", t, re.I):
                picked.append(it)
                if len(picked) >= 2:
                    break

        # 2) dywersyfikacja dyscyplin
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
            if it in picked: continue
            tg = tag(it["title"])
            if tg not in seen_tags:
                picked.append(it); seen_tags.add(tg)
                if len(picked) >= MAX_PER_SECTION: break

        # 3) uzupełnij do limitu najwyżej punktowanymi
        for it in pool:
            if len(picked) >= MAX_PER_SECTION: break
            if it not in picked:
                picked.append(it)
    else:
        picked = pool[:MAX_PER_SECTION]

    # AI skróty
    for it in picked:
        s = ai_summarize_pl(it["title"], it.get("summary_raw", ""))
        it["ai_summary"]   = s["summary"]
        it["ai_uncertain"] = s["uncertain"]

    return picked

# ===== RENDER =====
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
      background:radial-gradient(circle at 12% 10%, #ffd089 0%, #f59e0b 36%, #0f172a 100%);
      border:1px solid rgba(255,255,255,.28);
      display:flex; flex-direction:column; justify-content:center; align-items:flex-start;
      gap:3px; padding:6px 10px 6px 12px; box-shadow:0 10px 24px rgba(0,0,0,.35);
    }
    .news-thumb .dot{
      width:14px; height:14px; border-radius:999px; background:rgba(14,165,233,1);
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
        uncertain = it.get("ai_uncertain","").strip()
        uncertain_html = f'<div class="sec">{esc(uncertain)}</div>' if uncertain else ""
        return f'''<li>
  <a href="{esc(it["link"])}" target="_blank" rel="noopener">
    {badge()}
    <span class="news-text">{esc(it["title"])}</span>
  </a>
  <div class="ai-note">
    <div class="ai-head"><span class="ai-badge"><span class="ai-dot"></span> BriefRooms • AI komentarz</span></div>
    <div class="sec"><strong>Najważniejsze:</strong> {esc(it.get("ai_summary",""))}</div>
    {uncertain_html}
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

def main():
    sections = {
        "polityka": fetch_section("polityka"),
        "biznes":   fetch_section("biznes"),
        "sport":    fetch_section("sport"),
    }
    html_str = render_html(sections)
    with open("pl/aktualnosci.html", "w", encoding="utf-8") as f:
        f.write(html_str)
    print("✓ Wygenerowano pl/aktualnosci.html (AI:", "ON" if AI_ENABLED else "OFF", ")")

if __name__ == "__main__":
    main()


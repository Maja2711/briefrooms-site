#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Generuje pl/aktualnosci.html zgodnie z zasadami:
- Polityka/Kraj: tylko PL; preferuj PAP, Polsat, TVN24, potem Reuters/Bloomberg.
- Ekonomia/Biznes: preferuj PAP/Bankier/Reuters/Bloomberg.
- Sport: priorytet newsów z udziałem polskich zawodników; jeśli jest wydarzenie "LIVE/na żywo"
  z udziałem Polaka – wstaw co najmniej 1 link 'live'.
- 2× dziennie uruchamiane przez GitHub Actions (crony w workflow).
"""

import sys
import re
import html
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser  # runner GitHub ma to preinstalowane; w razie czego dodasz do requirements
from dateutil import tz

TZ = tz.gettz("Europe/Warsaw")

# ---------- KONFIGURACJA ----------

MAX_PER_SECTION = 6
MAX_PER_HOST = 6  # by źródło nie zdominowało sekcji

FEEDS = {
    "polityka": [
        # PL/Kraj – wiarygodne
        "https://tvn24.pl/najnowsze.xml",
        "https://tvn24.pl/polska.xml",
        "https://tvn24.pl/swiat.xml",
        "https://www.polsatnews.pl/rss/wszystkie.xml",
        # Reuters/Bloomberg (mogą nie mieć dedyk. PL RSS – fallback)
        "https://feeds.reuters.com/reuters/worldNews",
        "https://feeds.reuters.com/reuters/businessNews",
    ],
    "biznes": [
        "https://www.bankier.pl/rss/wiadomosci.xml",
        "https://www.bankier.pl/rss/gospodarka.xml",
        "https://feeds.reuters.com/reuters/businessNews",
        "https://www.bloomberg.com/feed/podcast/etf-report.xml",  # fallback (RSS Bloomberga bywają ograniczone)
    ],
    "sport": [
        "https://www.polsatsport.pl/rss/wszystkie.xml",
        # fallback:
        "https://tvn24.pl/sport.xml",
    ],
}

# priorytet źródeł (dopisywane do punktacji)
SOURCE_PRIORITY = [
    (re.compile(r"pap\.pl", re.I), 25),           # jeśli dodasz PAP RSS w przyszłości
    (re.compile(r"polsatnews\.pl", re.I), 18),
    (re.compile(r"tvn24\.pl", re.I), 15),
    (re.compile(r"bankier\.pl", re.I), 20),
    (re.compile(r"reuters\.com", re.I), 12),
    (re.compile(r"bloomberg\.com", re.I), 10),
    (re.compile(r"polsatsport\.pl", re.I), 25),
]

# boost słów (po sekcjach)
BOOST = {
    "polityka": [
        (re.compile(r"Polska|kraj|rząd|Sejm|Senat|prezydent|premier|ustawa|minister|samorząd|Trybunał|TK|SN|KE|UE|budżet|PKW", re.I), 35),
        (re.compile(r"inflacja|NBP|RPP|podatek|ZUS|emerytur|Orlen|PGE|LOT|PKP", re.I), 25),
    ],
    "biznes": [
        (re.compile(r"NBP|RPP|PKB|inflacja|stopy|obligacj|kredyt|ZUS|VAT|CIT|PIT|GPW|WIG|paliw|energia|MWh", re.I), 30),
    ],
    "sport": [
        (re.compile(r"Polak|Polska|Polski|Iga Świątek|Swiatek|Hurkacz|Lewandowski|Zieliński|Zielinski|Stoch|Żyła|Zyla|Siatkar|Reprezentacja|Legia|Raków|Rakow|Lech", re.I), 40),
        (re.compile(r"(LIVE|na żywo|relacja live|transmisja)", re.I), 45),
    ],
}

# ban-słowa (odrzucane globalnie)
BAN_PATTERNS = [
    re.compile(r"horoskop|plotk|quiz|sponsorowany|sponsor|galeria|zobacz zdjęcia|clickbait", re.I),
]

# uznajemy to za "live"
LIVE_RE = re.compile(r"(LIVE|na żywo|relacja live|transmisja)", re.I)

# ---------- FUNKCJE ----------

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

def score_item(item, section_key: str) -> float:
    score = 0.0
    # świeżość (0..36)
    published_parsed = item.get("published_parsed") or item.get("updated_parsed")
    if published_parsed:
        dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        score += max(0.0, 36.0 - age_h)

    # boost słów
    t = item.get("title", "") or ""
    for rx, pts in BOOST.get(section_key, []):
        if rx.search(t):
            score += pts

    # priorytet źródła
    h = host_of(item.get("link", "") or "")
    for rx, pts in SOURCE_PRIORITY:
        if rx.search(h):
            score += pts

    # lekka kara: zbyt długi tytuł
    if len(t) > 140:
        score -= 5

    return score

def fetch_section(section_key: str):
    items = []
    for feed_url in FEEDS[section_key]:
        try:
            parsed = feedparser.parse(feed_url)
            for e in parsed.entries:
                title = e.get("title", "") or ""
                link = e.get("link", "") or ""
                if not title or not link:
                    continue
                # BAN
                if any(rx.search(title) for rx in BAN_PATTERNS):
                    continue
                items.append({
                    "title": title.strip(),
                    "link": link.strip(),
                    "published_parsed": e.get("published_parsed") or e.get("updated_parsed"),
                })
        except Exception as ex:
            print(f"[WARN] RSS error: {feed_url} -> {ex}", file=sys.stderr)

    # scoring
    for it in items:
        it["_score"] = score_item(it, section_key)

    # sort
    items.sort(key=lambda x: x["_score"], reverse=True)

    # dedupe by title
    seen = set()
    deduped = []
    for it in items:
        key = norm_title(it["title"])
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    # cap per host + take N
    per_host = {}
    picked = []
    for it in deduped:
        h = host_of(it["link"])
        per_host[h] = per_host.get(h, 0)
        if per_host[h] >= MAX_PER_HOST:
            continue
        per_host[h] += 1
        picked.append(it)
        if len(picked) >= MAX_PER_SECTION:
            break

    # sport: wymagaj 1 "live" (jeśli dostępny w reszcie)
    if section_key == "sport":
        has_live = any(LIVE_RE.search(x["title"]) for x in picked)
        if not has_live:
            # poszukaj poza TOP-N i ewentualnie podmień ostatni
            for it in deduped:
                if LIVE_RE.search(it["title"]):
                    if it not in picked:
                        if len(picked) == MAX_PER_SECTION:
                            picked[-1] = it
                        else:
                            picked.append(it)
                    break

    return picked

def esc(s: str) -> str:
    return html.escape(s, quote=True)

def today_str() -> str:
    now = datetime.now(TZ)
    return now.strftime("%Y-%m-%d")

def render_html(pl_sections: dict) -> str:
    # wspólne CSS (ciepły biały + PRO badge)
    extra_css = """
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

    def make_section(title, items):
        lis = "\n".join(
            f'''<li>
  <a href="{esc(it["link"])}" target="_blank" rel="noopener">
    {badge()}
    <span class="news-text">{esc(it["title"])}</span>
  </a>
</li>'''
            for it in items
        )
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
{make_section("Polityka / Kraj", pl_sections["polityka"])}
{make_section("Ekonomia / Biznes", pl_sections["biznes"])}
{make_section("Sport", pl_sections["sport"])}

<p class="note">Automatyczny skrót (RSS). Linki prowadzą do wydawców. Strona nadpisywana codziennie.</p>
</main>
<footer style="text-align:center; opacity:.55; padding:18px">© BriefRooms</footer>
</body>
</html>"""
    return html_out

def main():
    sections = {
        "polityka": fetch_section("polityka"),
        "biznes": fetch_section("biznes"),
        "sport": fetch_section("sport"),
    }
    html_str = render_html(sections)
    with open("pl/aktualnosci.html", "w", encoding="utf-8") as f:
        f.write(html_str)
    print("✓ Wygenerowano pl/aktualnosci.html")

if __name__ == "__main__":
    main()

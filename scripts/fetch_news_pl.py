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

# ======= STREFA / CZAS =======
TZ = tz.gettz("Europe/Warsaw")

# ======= KONFIG =======
MAX_PER_SECTION = 6
MAX_PER_HOST = 6
AI_MODEL = os.getenv("NEWS_AI_MODEL", "gpt-4o-mini")
CACHE_PATH = ".cache/news_summaries_pl.json"
CACHE_VERSION = "v2"   # nowy klucz – żeby nie brać starych, uciętych komentarzy

FEEDS = {
    "polityka": [
        "https://tvn24.pl/najnowsze.xml",
        "https://tvn24.pl/polska.xml",
        "https://tvn24.pl/swiat.xml",
        "https://www.polsatnews.pl/rss/wszystkie.xml",
        "https://feeds.reuters.com/reuters/worldNews",
        "https://feeds.reuters.com/reuters/businessNews",  # czasem jest tam polityka
    ],
    "biznes": [
        "https://www.bankier.pl/rss/wiadomosci.xml",
        "https://www.bankier.pl/rss/gospodarka.xml",
        "https://feeds.reuters.com/reuters/businessNews",
    ],
    "sport": [
        "https://www.polsatsport.pl/rss/wszystkie.xml",
        "https://tvn24.pl/sport.xml",
    ],
}

# priorytet domen
SOURCE_PRIORITY = [
    (re.compile(r"pap\.pl", re.I), 25),
    (re.compile(r"polsatnews\.pl", re.I), 18),
    (re.compile(r"tvn24\.pl", re.I), 16),
    (re.compile(r"bankier\.pl", re.I), 20),
    (re.compile(r"reuters\.com", re.I), 12),
    (re.compile(r"polsatsport\.pl", re.I), 18),
]

# dodatkowe boosty treści
BOOST = {
    "polityka": [
        (re.compile(r"Polska|kraj|rząd|Sejm|Senat|prezydent|premier|ustawa|minister|samorząd|Trybunał|TK|SN|UE|budżet|PKW", re.I), 35),
    ],
    "biznes": [
        (re.compile(r"NBP|RPP|inflacja|stopy|obligacj|kredyt|ZUS|VAT|CIT|PIT|GPW|WIG|paliw|energia|MWh", re.I), 30),
    ],
    # sport rozbijamy dalej – tu tylko podstawowe
    "sport": [
        (re.compile(r"(LIVE|na żywo|relacja live|transmisja)", re.I), 35),
    ],
}

# filtry
BAN_PATTERNS = [
    re.compile(r"horoskop|plotk|quiz|sponsorowany|sponsor|galeria|zobacz zdjęcia|clickbait", re.I),
]

# dopalacze dla polskich sportowców i różnych dyscyplin
SPORT_STAR_POL = re.compile(
    r"(Świątek|Swiatek|Hurkacz|Lewandowsk|Zielińsk|Zielinsk|Raków|Rakow|Legia|Lech|Pogoń|Pogon|reprezentacja|siatkówk|żużel|zuzel|skoki|Kamil Stoch|Żyła|Zyla)",
    re.I,
)

LIVE_RE = re.compile(r"(LIVE|na żywo|relacja live|transmisja)", re.I)

# ====== UTILE ======
def tidy_sentence_pl(text: str, limit: int = 420) -> str:
    """Ucina tak, żeby skończyć pełnym zdaniem. Jak się nie da – kończy elipsą."""
    if not text:
        return ""
    # normalizacja
    t = " ".join(text.strip().split())
    t = re.sub(r"^[-–•]\s+", "", t)
    if len(t) <= limit:
        return t if t.endswith(('.', '!', '?')) else t + "."
    cut = t[:limit]
    # szukamy ostatniej kropki/wykrzyknika/pytajnika
    last_end = max(cut.rfind('.'), cut.rfind('!'), cut.rfind('?'))
    if last_end >= 50:  # żeby nie kończyć po 2 słowach
        return cut[:last_end + 1]
    # w ostateczności urwij na słowie
    cut = cut.rsplit(" ", 1)[0]
    return cut + "…"

def clean_uncertain_pl(u: str) -> str:
    """Nie pokazuj generycznych, pustych tekstów."""
    if not u:
        return ""
    u2 = " ".join(u.strip().split())
    if re.search(r"brak analizy|brak|none|n/a|nie dotyczy", u2, re.I):
        return ""
    if re.search(r"rss", u2, re.I):
        return ""
    return u2

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
    published_parsed = item.get("published_parsed") or item.get("updated_parsed")
    if published_parsed:
        dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        score += max(0.0, 36.0 - age_h)
    t = item.get("title", "") or ""
    # boost ogólny
    for rx, pts in BOOST.get(section_key, []):
        if rx.search(t):
            score += pts
    # sport – ekstra za polskich sportowców
    if section_key == "sport" and SPORT_STAR_POL.search(t):
        score += 40
    h = host_of(item.get("link", "") or "")
    for rx, pts in SOURCE_PRIORITY:
        if rx.search(h):
            score += pts
    if len(t) > 140:
        score -= 5
    return score

def esc(s: str) -> str:
    return html.escape(s or "", quote=True)

def today_str() -> str:
    now = datetime.now(TZ)
    return now.strftime("%Y-%m-%d")

# ====== CACHE ======
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

# ====== AI ======
def ai_summarize_pl(title: str, snippet: str, url: str) -> dict:
    """
    Zwraca:
    {
      "summary": "...",
      "uncertain": "",   # puste, jeśli nic nie jest niepewne
      "model": "..."
    }
    """
    key = os.getenv("OPENAI_API_KEY")
    cache_key = f"{CACHE_VERSION}|{norm_title(title)}|{today_str()}"

    if cache_key in CACHE:
        cached = CACHE[cache_key]
        cached["uncertain"] = clean_uncertain_pl(cached.get("uncertain", ""))
        cached["summary"] = tidy_sentence_pl(cached.get("summary", ""))
        return cached

    if not key:
        out = {
            "summary": tidy_sentence_pl((snippet or title or "")[:320]),
            "uncertain": "",
            "model": "fallback",
        }
        CACHE[cache_key] = out; save_cache(CACHE_PATH, CACHE)
        return out

    prompt = f"""Streść bardzo zwięźle (2–3 zdania) najważniejsze fakty z wiadomości.
Jeśli W TYTULE albo w OPISIE widać, że coś jest niepotwierdzone / zależy od decyzji sądu / wynika z przecieków / może się zmienić – dopisz drugą linijkę:
"Niepewne / sporne: …"
Jeśli takiej niepewności nie widać – NIE PISZ tej drugiej linijki.
Tytuł: {title}
Opis (RSS): {snippet}
Nie dopisuj rzeczy spoza tytułu/opisu."""

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": AI_MODEL,
                "messages": [
                    {"role": "system", "content": "Jesteś ostrożnym, rzetelnym asystentem prasowym po polsku. Nie fantazjuj."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.15,
                "max_tokens": 230,
            },
            timeout=25,
        )
        resp.raise_for_status()
        txt = resp.json()["choices"][0]["message"]["content"].strip()

        parts = {"summary": "", "uncertain": ""}
        for line in txt.splitlines():
            l = line.strip()
            if not l:
                continue
            low = l.lower()
            if low.startswith("najważniejsze:"):
                parts["summary"] = l.split(":", 1)[1].strip()
            elif low.startswith("niepewne") or "sporne" in low:
                val = l.split(":", 1)[1].strip() if ":" in l else ""
                parts["uncertain"] = clean_uncertain_pl(val)

        if not parts["summary"]:
            parts["summary"] = (snippet or title or "")[:320]

        out = {
            "summary": tidy_sentence_pl(parts["summary"]),
            "uncertain": tidy_sentence_pl(parts["uncertain"]) if parts["uncertain"] else "",
            "model": AI_MODEL,
        }
        CACHE[cache_key] = out; save_cache(CACHE_PATH, CACHE)
        return out
    except Exception:
        out = {
            "summary": tidy_sentence_pl((snippet or title or "")[:320]),
            "uncertain": "",
            "model": "fallback-error",
        }
        CACHE[cache_key] = out; save_cache(CACHE_PATH, CACHE)
        return out

# ====== POBIERANIE ======
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
                if any(rx.search(title) for rx in BAN_PATTERNS):
                    continue
                snippet = e.get("summary", "") or e.get("description", "") or ""
                items.append({
                    "title": title.strip(),
                    "link": link.strip(),
                    "summary_raw": re.sub("<[^<]+?>", "", snippet).strip(),
                    "published_parsed": e.get("published_parsed") or e.get("updated_parsed"),
                })
        except Exception as ex:
            print(f"[WARN] RSS error: {feed_url} -> {ex}", file=sys.stderr)

    # scoring
    for it in items:
        it["_score"] = score_item(it, section_key)

    # sort
    items.sort(key=lambda x: x["_score"], reverse=True)

    # dedupe po tytule
    seen = set()
    deduped = []
    for it in items:
        key = norm_title(it["title"])
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    # zwykłe sekcje – prosto
    if section_key != "sport":
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
    else:
        # ===== SPECJALNA LOGIKA SPORT =====
        picked = []
        per_host = {}

        # 1) NAJPIERW: max 2 linki live / relacja / mecz z udziałem polskiej drużyny
        for it in deduped:
            if len(picked) >= 2:
                break
            title = it["title"]
            if LIVE_RE.search(title) or SPORT_STAR_POL.search(title):
                h = host_of(it["link"])
                per_host[h] = per_host.get(h, 0)
                if per_host[h] >= MAX_PER_HOST:
                    continue
                per_host[h] += 1
                picked.append(it)

        # 2) POTEM: inne sporty (tenis, siatkówka, reprezentacja, skoki…) – żeby nie było 6x to samo
        for it in deduped:
            if len(picked) >= MAX_PER_SECTION:
                break
            title = it["title"]
            # jeśli to jest identyczny temat co już mamy – pomiń
            if any(norm_title(title) == norm_title(x["title"]) for x in picked):
                continue

            # preferuj te z polskimi nazwiskami/drużynami
            important_polish = bool(SPORT_STAR_POL.search(title))
            # jeśli nie ma polskich, to bierz inny HOST niż dominujący
            h = host_of(it["link"])
            per_host[h] = per_host.get(h, 0)

            if important_polish:
                if per_host[h] < MAX_PER_HOST:
                    per_host[h] += 1
                    picked.append(it)
            else:
                # "inne sporty" – tylko jeśli jeszcze mamy miejsce i nie zdominowaliśmy hostem
                if per_host[h] < 2:   # mniejsze odcięcie
                    per_host[h] += 1
                    picked.append(it)

        # 3) jeśli nadal mniej niż 6 – dobierz cokolwiek innego
        if len(picked) < MAX_PER_SECTION:
            for it in deduped:
                if len(picked) >= MAX_PER_SECTION:
                    break
                if any(norm_title(it["title"]) == norm_title(x["title"]) for x in picked):
                    continue
                h = host_of(it["link"])
                per_host[h] = per_host.get(h, 0)
                if per_host[h] >= MAX_PER_HOST:
                    continue
                per_host[h] += 1
                picked.append(it)

    # AI dla wszystkich wybranych
    for it in picked:
        s = ai_summarize_pl(it["title"], it.get("summary_raw", ""), it["link"])
        it["ai_summary"] = s["summary"]
        it["ai_uncertain"] = s["uncertain"]
        it["ai_model"] = s["model"]

    return picked

# ====== RENDER HTML ======
def render_html(pl_sections: dict) -> str:
    extra_css = """
    ul.news { list-style:none; padding-left:0 }
    ul.news li { margin:0 0 16px 0 }
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
      margin:6px 0 0 88px;
      font-size:.92rem; color:#dfe7f1; line-height:1.35;
      background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.08);
      padding:10px 12px; border-radius:12px;
    }
    .ai-head{ display:flex; align-items:center; gap:8px; margin-bottom:6px; font-weight:700; color:#fdf3e3; }
    .ai-badge{
      display:inline-flex; align-items:center; gap:6px; padding:3px 8px; border-radius:999px;
      background:linear-gradient(135deg,#0ea5e9,#7c3aed); font-size:.75rem; color:#fff;
      border:1px solid rgba(255,255,255,.35);
    }
    .ai-dot{ width:8px; height:8px; border-radius:999px; background:#fff; box-shadow:0 0 6px rgba(255,255,255,.7); }
    .ai-note .sec{ margin-top:4px; }
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
        uncertain = it.get("ai_uncertain", "").strip()
        uncertain_block = (
            f'<div class="sec"><strong>Niepewne / sporne:</strong> {esc(uncertain)}</div>'
            if uncertain else ""
        )
        return f'''<li>
  <a href="{esc(it["link"])}" target="_blank" rel="noopener">
    {badge()}
    <span class="news-text">{esc(it["title"])}</span>
  </a>
  <div class="ai-note">
    <div class="ai-head"><span class="ai-badge"><span class="ai-dot"></span> BriefRooms • AI komentarz</span></div>
    <div class="sec"><strong>Najważniejsze:</strong> {esc(it.get("ai_summary",""))}</div>
    {uncertain_block}
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
{make_section("Polityka / Kraj", pl_sections["polityka"])}
{make_section("Ekonomia / Biznes", pl_sections["biznes"])}
{make_section("Sport", pl_sections["sport"])}
<p class="note">Automatyczny skrót (RSS). Linki prowadzą do wydawców. Strona nadpisywana 2× dziennie.</p>
</main>
<footer style="text-align:center; opacity:.55; padding:18px">© BriefRooms</footer>
</body>
</html>"""
    return html_out

# ====== MAIN ======
def main():
    sections = {
        "polityka": fetch_section("polityka"),
        "biznes": fetch_section("biznes"),
        "sport": fetch_section("sport"),
    }
    html_str = render_html(sections)
    with open("pl/aktualnosci.html", "w", encoding="utf-8") as f:
        f.write(html_str)
    print("✓ Wygenerowano pl/aktualnosci.html (AI: ON)" if os.getenv("OPENAI_API_KEY") else "✓ Wygenerowano pl/aktualnosci.html (AI: OFF)")

if __name__ == "__main__":
    main()

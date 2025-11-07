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
# USTAWIENIA CZASU / STREFY
# =========================
TZ = tz.gettz("Europe/Warsaw")

# =========================
# KONFIG GŁÓWNY
# =========================
MAX_PER_SECTION = 6
MAX_PER_HOST = 6
AI_ENABLED = bool(os.getenv("OPENAI_API_KEY"))
AI_MODEL = os.getenv("NEWS_AI_MODEL", "gpt-4o-mini")  # model dla komentarzy
CACHE_PATH = ".cache/news_summaries_pl.json"

FEEDS = {
    "polityka": [
        "https://tvn24.pl/najnowsze.xml",
        "https://tvn24.pl/polska.xml",
        "https://tvn24.pl/swiat.xml",
        "https://www.polsatnews.pl/rss/wszystkie.xml",
        "https://www.pap.pl/rss.xml",
        "https://feeds.reuters.com/reuters/worldNews",
        "https://feeds.reuters.com/reuters/businessNews",
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
    ],
}

# ŹRÓDŁA: większy punkcik = większa szansa, że wejdzie do listy
SOURCE_PRIORITY = [
    (re.compile(r"pap\.pl", re.I), 25),
    (re.compile(r"polsatnews\.pl", re.I), 18),
    (re.compile(r"tvn24\.pl", re.I), 15),
    (re.compile(r"bankier\.pl", re.I), 20),
    (re.compile(r"reuters\.com", re.I), 12),
    (re.compile(r"polsatsport\.pl", re.I), 25),
]

# BOOST wg sekcji — słowa kluczowe
BOOST = {
    "polityka": [
        (re.compile(r"Polska|kraj|Sejm|Senat|prezydent|premier|ustawa|minister|rząd|samorząd|Trybunał|TK|SN|UE|KE|budżet|PKW", re.I), 35),
        (re.compile(r"inflacja|NBP|RPP|podatek|ZUS|emerytur|Orlen|PGE|LOT|PKP", re.I), 18),
    ],
    "biznes": [
        (re.compile(r"NBP|RPP|PKB|inflacja|stopy|obligacj|kredyt|ZUS|VAT|CIT|PIT|GPW|WIG|paliw|energia|MWh", re.I), 28),
    ],
    "sport": [
        # Polscy sportowcy/drużyny
        (re.compile(r"Iga Świątek|Swiatek|Hurkacz|Lewandowski|Zieliński|Zielinski|Siatkar|Reprezentacja|Legia|Raków|Rakow|Lech", re.I), 40),
        # relacje na żywo
        (re.compile(r"(LIVE|na żywo|relacja live|transmisja)", re.I), 45),
    ],
}

BAN_PATTERNS = [
    re.compile(r"horoskop|plotk|quiz|sponsorowany|sponsor|galeria|zobacz zdjęcia|clickbait", re.I),
]
LIVE_RE = re.compile(r"(LIVE|na żywo|relacja live|transmisja)", re.I)

# =========================
# STOP-SŁOWA + TOKENIZACJA
# =========================
STOP_PL = {
    "i","oraz","a","w","we","z","za","do","dla","na","o","u","od","po","pod","nad","przed",
    "jest","są","był","była","było","będzie","to","ten","ta","te","tych","tym","tą","że",
    "jak","kiedy","który","która","które","których","którym","którego","której","czy",
    "się","ze","go","jej","ich","jego","nią","nią","nim","nią","lub","albo","też","również",
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

SIMILARITY_THRESHOLD = 0.70  # próg: to samo „o tym samym” → odrzuć duplikat

# =========================
# POMOCNICZE
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

def score_item(item, section_key: str) -> float:
    score = 0.0
    # świeżość
    published_parsed = item.get("published_parsed") or item.get("updated_parsed")
    if published_parsed:
        dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        score += max(0.0, 36.0 - age_h)
    # boost z tytułu
    t = item.get("title", "") or ""
    for rx, pts in BOOST.get(section_key, []):
        if rx.search(t):
            score += pts
    # preferencje hosta
    h = host_of(item.get("link", "") or "")
    for rx, pts in SOURCE_PRIORITY:
        if rx.search(h):
            score += pts
    # kosmetyka: bardzo długie tytuły lekko w dół
    if len(t) > 140:
        score -= 5
    return score

# =========================
# KOMENTARZE AI – CACHE
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

CACHE = load_cache(CACHE_PATH)

def ensure_period(txt: str) -> str:
    """Zadbaj, by komentarz kończył się kropką."""
    txt = (txt or "").strip()
    if not txt:
        return txt
    if txt[-1] not in ".!?…":
        txt += "."
    return txt

def ai_summarize_pl(title: str, snippet: str, url: str) -> dict:
    """
    Zwraca dict:
    { "summary": "...", "uncertain": "...", "model": "gpt-4o-mini" }
    'uncertain' pojawia się tylko, gdy model wskaże coś potencjalnie mylnego/niepewnego.
    """
    key = os.getenv("OPENAI_API_KEY")
    cache_key = f"{norm_title(title)}|{today_str()}"
    if cache_key in CACHE:
        return CACHE[cache_key]

    # fallback bez klucza (bez AI) – nie generujemy „uncertain”
    if not key:
        out = {
            "summary": ensure_period((snippet or title or "").strip()[:600]),
            "uncertain": "",
            "model": "fallback"
        }
        CACHE[cache_key] = out
        save_cache(CACHE_PATH, CACHE)
        return out

    prompt = f"""Streść zwięźle po polsku (2–3 pełne zdania) najważniejsze fakty z poniższej wiadomości.
Następnie – tylko jeśli widzisz w tytule/opisie elementy niepewne, dyskusyjne lub łatwe do błędnej interpretacji –
podaj JEDNO krótkie zdanie ostrzegawcze zaczynające się od "Uwaga:".
Jeśli nic takiego nie ma, NIE pisz żadnego ostrzeżenia.
Tytuł: {title}
Opis (RSS/snippet): {snippet}
Nie dodawaj faktów, których nie ma w tytule/opisie.
Zwróć odpowiedź w dwóch liniach:
1) Najważniejsze: …
2) (opcjonalnie) Uwaga: … (tylko jeśli potrzebne)
"""

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
                    {"role": "system", "content": "Jesteś rzetelnym, zwięzłym asystentem prasowym. Zawsze kończ zdanie kropką."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2,
                "max_tokens": 240
            },
            timeout=25
        )
        resp.raise_for_status()
        txt = resp.json()["choices"][0]["message"]["content"].strip()

        summary = ""
        warn = ""
        for line in txt.splitlines():
            l = line.strip()
            if not l:
                continue
            low = l.lower()
            if low.startswith("najważniejsze:"):
                summary = l.split(":", 1)[1].strip()
            elif low.startswith("uwaga:"):
                warn = l

        summary = ensure_period(summary or (snippet or title or ""))
        # ostrzeżenie tylko gdy istnieje (już zawiera "Uwaga:")
        uncertain_out = ensure_period(warn) if warn else ""

        out = {"summary": summary, "uncertain": uncertain_out, "model": AI_MODEL}
        CACHE[cache_key] = out
        save_cache(CACHE_PATH, CACHE)
        return out

    except Exception:
        out = {
            "summary": ensure_period((snippet or title or "").strip()[:600]),
            "uncertain": "",
            "model": "fallback-error"
        }
        CACHE[cache_key] = out
        save_cache(CACHE_PATH, CACHE)
        return out

# =========================
# POBIERANIE I DEDUPE
# =========================
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
        it["_tok"] = tokens_pl(it["title"])

    # sort malejąco po wartości
    items.sort(key=lambda x: x["_score"], reverse=True)

    # deduplikacja „semantyczna” – odrzucaj wiadomości o tym samym temacie
    kept = []
    for it in items:
        is_dup = False
        for got in kept:
            if jaccard(it["_tok"], got["_tok"]) >= SIMILARITY_THRESHOLD:
                is_dup = True
                break
        if not is_dup:
            kept.append(it)

    # limit na host oraz total
    per_host = {}
    picked = []
    for it in kept:
        h = host_of(it["link"])
        per_host[h] = per_host.get(h, 0)
        if per_host[h] >= MAX_PER_HOST:
            continue
        per_host[h] += 1
        picked.append(it)
        if len(picked) >= MAX_PER_SECTION:
            break

    # sport: spróbuj wymusić 1 link LIVE, jeśli żaden nie wszedł
    if section_key == "sport":
        has_live = any(LIVE_RE.search(x["title"]) for x in picked)
        if not has_live:
            for it in kept:
                if LIVE_RE.search(it["title"]) and it not in picked:
                    if len(picked) == MAX_PER_SECTION:
                        picked[-1] = it
                    else:
                        picked.append(it)
                    break

        # prosta dywersyfikacja: spróbuj złamać monotonię (np. same mecze jednej ligi)
        # patrzymy na pierwsze słowo po filtrach – jeśli 4+ tytuły zaczynają się podobnie, podmień ostatni
        if len(picked) >= 4:
            heads = {}
            for it in picked:
                head = next(iter(tokens_pl(it["title"])), "")
                heads[head] = heads.get(head, 0) + 1
            common = [k for k, v in heads.items() if v >= 4]
            if common:
                # znajdź alternatywę różniącą się headem
                for it in kept:
                    head = next(iter(tokens_pl(it["title"])), "")
                    if head not in common and it not in picked:
                        picked[-1] = it
                        break

    # komentarze AI (pełne zdania)
    for it in picked:
        s = ai_summarize_pl(it["title"], it.get("summary_raw", ""), it["link"])
        it["ai_summary"] = ensure_period(s["summary"])
        it["ai_uncertain"] = ensure_period(s["uncertain"]) if s.get("uncertain") else ""
        it["ai_model"] = s["model"]

    return picked

# =========================
# RENDER HTML
# =========================
def render_html(pl_sections: dict) -> str:
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
    .ai-note{
      margin:6px 0 0 88px;
      font-size:.92rem; color:#dfe7f1; line-height:1.35;
      background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.08);
      padding:10px 12px; border-radius:12px;
    }
    .ai-note .ai-head{
      display:flex; align-items:center; gap:8px; margin-bottom:6px; font-weight:700;
      color:#fdf3e3;
    }
    .ai-badge{
      display:inline-flex; align-items:center; gap:6px;
      padding:3px 8px; border-radius:999px;
      background:linear-gradient(135deg,#0ea5e9,#7c3aed);
      font-size:.75rem; color:#fff; border:1px solid rgba(255,255,255,.35);
    }
    .ai-dot{
      width:8px; height:8px; border-radius:999px; background:#fff;
      box-shadow:0 0 6px rgba(255,255,255,.7);
    }
    .ai-note .sec{ margin-top:4px; opacity:.95; }
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
{make_section("Polityka / Kraj", pl_sections["polityka"])}
{make_section("Ekonomia / Biznes", pl_sections["biznes"])}
{make_section("Sport", pl_sections["sport"])}

<p class="note">Automatyczny skrót (RSS). Linki prowadzą do wydawców. Strona nadpisywana codziennie.</p>
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
        "biznes": fetch_section("biznes"),
        "sport": fetch_section("sport"),
    }
    html_str = render_html(sections)
    with open("pl/aktualnosci.html", "w", encoding="utf-8") as f:
        f.write(html_str)
    print("✓ Wygenerowano pl/aktualnosci.html (AI:", "ON" if AI_ENABLED else "OFF", ")")

if __name__ == "__main__":
    main()

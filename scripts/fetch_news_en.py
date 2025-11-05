#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, json, html
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
from dateutil import tz
import requests

TZ = tz.gettz("Europe/London")

# ===== CONFIG =====
MAX_PER_SECTION = 6
MAX_PER_HOST = 6
AI_MODEL = os.getenv("NEWS_AI_MODEL", "gpt-4o-mini")
CACHE_PATH = ".cache/news_summaries_en.json"
CACHE_VERSION = "v2"  # bump to ignore old cached entries with generic "No AI analysis..."

FEEDS = {
    "politics": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://feeds.bbci.co.uk/news/uk/rss.xml",
        "https://feeds.bbci.co.uk/news/politics/rss.xml",
        "https://feeds.reuters.com/reuters/worldNews",
        "https://feeds.reuters.com/reuters/UKdomesticNews",
    ],
    "business": [
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://feeds.reuters.com/reuters/businessNews",
    ],
    "sports": [
        "https://www.espn.com/espn/rss/news",
        "https://feeds.bbci.co.uk/sport/rss.xml",
    ],
}

SOURCE_PRIORITY = [
    (re.compile(r"bbc\.co\.uk|bbc\.com", re.I), 20),
    (re.compile(r"reuters\.com", re.I), 18),
    (re.compile(r"espn\.com", re.I), 16),
    (re.compile(r"bloomberg\.com", re.I), 10),
]

BOOST = {
    "politics": [
        (re.compile(r"election|vote|parliament|congress|white house|downing street|prime minister|president|uk|eu|nato|ceasefire", re.I), 30),
    ],
    "business": [
        (re.compile(r"inflation|rates|fed|ecb|boe|jobs|payrolls|earnings|gdp|oil|energy|stocks|bond|market|ipo|merger|m&a", re.I), 30),
    ],
    "sports": [
        (re.compile(r"live|final|grand slam|world cup|champions league|premier league|nba|nfl|mlb|nhl|f1", re.I), 35),
    ],
}

BAN_PATTERNS = [re.compile(r"quiz|gossip|gallery|photos|sponsored|advertorial", re.I)]
LIVE_RE = re.compile(r"(LIVE|live)", re.I)

# ===== UTIL =====
SENT_LIMIT = 420
GENERIC_UNCERTAIN = [
    re.compile(r"no ai analysis", re.I),
    re.compile(r"brief based on rss", re.I),
    re.compile(r"no analysis", re.I),
    re.compile(r"not analyzed", re.I),
]

def tidy_sentence_block(text: str, limit: int = SENT_LIMIT) -> str:
    """Normalize whitespace, strip bullets, keep full sentence end; add … only if no sentence end in limit."""
    if not text:
        return ""
    t = " ".join(text.strip().split())
    t = re.sub(r"^[-–•]\s+", "", t)
    if len(t) <= limit:
        return t if t.endswith(('.', '!', '?')) else (t + '.')
    cut = t[:limit]
    last_end = max(cut.rfind('.'), cut.rfind('!'), cut.rfind('?'))
    if last_end >= 40:
        return cut[:last_end + 1]
    cut = cut.rsplit(' ', 1)[0]
    return cut + '…'

def clean_uncertain(u: str) -> str:
    """Drop generic / useless uncertainty lines."""
    if not u:
        return ""
    u2 = " ".join(u.strip().split())
    for rx in GENERIC_UNCERTAIN:
        if rx.search(u2):
            return ""
    # remove generic 'brak/none'
    if u2.lower() in {"none", "brak", "n/a"}:
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
    for rx, pts in BOOST.get(section_key, []):
        if rx.search(t): score += pts
    h = host_of(item.get("link", "") or "")
    for rx, pts in SOURCE_PRIORITY:
        if rx.search(h): score += pts
    if len(t) > 140: score -= 5
    return score

def esc(s: str) -> str:
    return html.escape(s or "", quote=True)

def today_str() -> str:
    now = datetime.now(TZ)
    return now.strftime("%Y-%m-%d")

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

# ===== AI SUMMARY (EN) =====
def ai_summarize_en(title: str, snippet: str, url: str) -> dict:
    """
    Returns: { "summary": "...", "uncertain": "", "model": "..." }
    'uncertain' stays EMPTY unless a clear issue is detected.
    """
    key = os.getenv("OPENAI_API_KEY")
    cache_key = f"{CACHE_VERSION}|{norm_title(title)}|{today_str()}"
    # prefer fresh 'v2' entries only
    if cache_key in CACHE:
        cached = CACHE[cache_key]
        cached["uncertain"] = clean_uncertain(cached.get("uncertain", ""))
        return cached

    if not key:
        out = {
            "summary": (snippet or title or "")[:320].strip(),
            "uncertain": "",  # never emit generic text when no key
            "model": "fallback"
        }
        CACHE[cache_key] = out; save_cache(CACHE_PATH, CACHE); return out

    prompt = f"""Summarize concisely in English (max 2–3 sentences) the MOST IMPORTANT facts from the item.
Do not start lines with bullets or dashes.
Only if you see a concrete risk of misunderstanding/uncertainty based on the TITLE and RSS SNIPPET, add a second line:
- 'Uncertain/disputed: …' (be specific; no generic disclaimers).
If nothing notable is uncertain — DO NOT output that second line.
Be conservative; never speculate or add facts outside the title/snippet.
Title: {title}
Snippet (RSS): {snippet}
RESPONSE FORMAT:
Most important: …
[optional] Uncertain/disputed: …
"""

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": AI_MODEL,
                "messages": [
                    {"role":"system","content":"You are a careful, conservative news assistant. Do not invent details."},
                    {"role":"user","content": prompt}
                ],
                "temperature": 0.1,
                "max_tokens": 220
            },
            timeout=25
        )
        resp.raise_for_status()
        txt = resp.json()["choices"][0]["message"]["content"].strip()

        parts = {"summary":"", "uncertain":""}
        for line in txt.splitlines():
            l = line.strip()
            if not l: continue
            low = l.lower()
            if low.startswith("most important:"):
                parts["summary"] = l.split(":",1)[1].strip()
            elif low.startswith("uncertain") or "disputed" in low:
                val = l.split(":",1)[1].strip() if ":" in l else ""
                parts["uncertain"] = clean_uncertain(val)

        if not parts["summary"]:
            parts["summary"] = (snippet or title or "")[:320].strip()

        out = {**parts, "model": AI_MODEL}
        # normalize text for safe rendering
        out["summary"]   = tidy_sentence_block(out["summary"])
        out["uncertain"] = tidy_sentence_block(out["uncertain"]) if out["uncertain"] else ""

        CACHE[cache_key] = out; save_cache(CACHE_PATH, CACHE); return out
    except Exception:
        out = {
            "summary": (snippet or title or "")[:320].strip(),
            "uncertain": "",  # never emit generic text on error
            "model": "fallback-error"
        }
        CACHE[cache_key] = out; save_cache(CACHE_PATH, CACHE); return out

# ===== FETCH & PICK =====
def fetch_section(section_key: str):
    items = []
    for feed_url in FEEDS[section_key]:
        try:
            parsed = feedparser.parse(feed_url)
            for e in parsed.entries:
                title = (e.get("title") or "").strip()
                link  = (e.get("link")  or "").strip()
                if not title or not link: continue
                if any(rx.search(title) for rx in BAN_PATTERNS): continue
                snippet = e.get("summary") or e.get("description") or ""
                items.append({
                    "title": title,
                    "link": link,
                    "summary_raw": re.sub("<[^<]+?>", "", snippet).strip(),
                    "published_parsed": e.get("published_parsed") or e.get("updated_parsed"),
                })
        except Exception as ex:
            print(f"[WARN] RSS error: {feed_url} -> {ex}", file=sys.stderr)

    for it in items: it["_score"] = score_item(it, section_key)
    items.sort(key=lambda x: x["_score"], reverse=True)

    seen = set(); deduped=[]
    for it in items:
        key = norm_title(it["title"])
        if not key or key in seen: continue
        seen.add(key); deduped.append(it)

    per_host={}; picked=[]
    for it in deduped:
        h = host_of(it["link"]); per_host[h] = per_host.get(h,0)
        if per_host[h] >= MAX_PER_HOST: continue
        per_host[h]+=1; picked.append(it)
        if len(picked) >= MAX_PER_SECTION: break

    if section_key=="sports":
        has_live = any(LIVE_RE.search(x["title"]) for x in picked)
        if not has_live:
            for it in deduped:
                if LIVE_RE.search(it["title"]) and it not in picked:
                    if len(picked)==MAX_PER_SECTION: picked[-1]=it
                    else: picked.append(it)
                    break

    # AI pass
    for it in picked:
        s = ai_summarize_en(it["title"], it.get("summary_raw",""), it["link"])
        # s is already cleaned, but keep one more safety net
        it["ai_summary"]   = tidy_sentence_block(s.get("summary",""))
        it["ai_uncertain"] = clean_uncertain(s.get("uncertain",""))
        it["ai_model"]     = s.get("model","")
    return picked

# ===== RENDER =====
def render_html(sections: dict) -> str:
    extra_css = """
    ul.news { list-style: none; padding-left: 0 }
    ul.news li { margin: 0 0 16px 0 }
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
    .news-thumb .dot{ width:14px; height:14px; border-radius:999px; background:rgba(7,89,133,1);
      border:2px solid rgba(255,255,255,.6); box-shadow:0 0 8px rgba(255,255,255,.3); margin-bottom:1px; }
    .news-thumb .title{ font-size:.56rem; font-weight:700; letter-spacing:.03em; color:#fff; line-height:1; }
    .news-thumb .sub{ font-size:.47rem; color:rgba(244,246,255,.85); line-height:1.05; white-space:nowrap; }
    .ai-note{ margin:6px 0 0 88px; font-size:.92rem; color:#dfe7f1; line-height:1.35;
      background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.08);
      padding:10px 12px; border-radius:12px; }
    .ai-note .ai-head{ display:flex; align-items:center; gap:8px; margin-bottom:6px; font-weight:700; color:#fdf3e3; }
    .ai-badge{ display:inline-flex; align-items:center; gap:6px; padding:3px 8px; border-radius:999px;
      background:linear-gradient(135deg,#0ea5e9,#7c3aed); font-size:.75rem; color:#fff; border:1px solid rgba(255,255,255,.35); }
    .ai-dot{ width:8px; height:8px; border-radius:999px; background:#fff; box-shadow:0 0 6px rgba(255,255,255,.7); }
    .ai-note .sec{ margin-top:4px; opacity:.9; }
    .note{ color:#9fb3cb; font-size:.92rem }
    """

    def badge():
        return ('<span class="news-thumb">'
                '<span class="dot"></span>'
                '<span class="title">BriefRooms</span>'
                '<span class="sub">powered by AI</span>'
                '</span>')

    def make_li(it):
        uncertain = it.get("ai_uncertain","").strip()
        uncertain_block = (
            f'<div class="sec"><strong>Uncertain / disputed:</strong> {esc(uncertain)}</div>'
            if uncertain else ""
        )
        return f'''<li>
  <a href="{esc(it["link"])}" target="_blank" rel="noopener">
    {badge()}
    <span class="news-text">{esc(it["title"])}</span>
  </a>
  <div class="ai-note">
    <div class="ai-head"><span class="ai-badge"><span class="ai-dot"></span> BriefRooms • AI comment</span></div>
    <div class="sec"><strong>Most important:</strong> {esc(it.get("ai_summary",""))}</div>
    {uncertain_block}
  </div>
</li>'''

    def section(title, items):
        lis = "\n".join(make_li(it) for it in items)
        return f"""
<section class="card">
  <h2>{esc(title)}</h2>
  <ul class="news">
    {lis}
  </ul>
</section>"""

    html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>News — BriefRooms</title>
  <meta name="description" content="Automatic daily digest: politics/world, business/economy, sports." />
  <link rel="icon" href="/assets/favicon.svg" />
  <link rel="stylesheet" href="/assets/site.css" />
  <style>
    header{{ text-align:center; padding:26px 16px 8px }}
    .sub{{ color:#b9c5d8 }}
    main{{ max-width:980px; margin:0 auto; padding:0 16px 48px }}
    .card{{
      background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.02));
      border:1px solid rgba(255,255,255,.08); border-radius:16px; padding:18px 20px; margin:14px 0;
      box-shadow:inset 0 1px 0 rgba(255,255,255,.04), 0 10px 30px rgba(0,0,0,.25)
    }}
    h2{{ margin:8px 0 6px; color:#d7e6ff }}
    {extra_css}
  </style>
</head>
<body>
<header>
  <h1>News</h1>
  <p class="sub">Last ~36 hours • {today_str()}</p>
</header>
<main>
{section("Politics / World", sections["politics"])}
{section("Business / Economy", sections["business"])}
{section("Sports", sections["sports"])}

<p class="note">Automatic digest (RSS). Links go to original publishers. Page is overwritten daily.</p>
</main>
<footer style="text-align:center; opacity:.6; padding:16px 12px 36px">© BriefRooms</footer>
</body>
</html>"""
    return html_out

def main():
    sections = {
        "politics": fetch_section("politics"),
        "business": fetch_section("business"),
        "sports": fetch_section("sports"),
    }
    html_str = render_html(sections)
    with open("en/news.html", "w", encoding="utf-8") as f:
        f.write(html_str)
    print("✓ Generated en/news.html")

if __name__ == "__main__":
    main()


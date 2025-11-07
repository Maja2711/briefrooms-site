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

# ---------- Timezone ----------
TZ = tz.gettz("Europe/London")

# ---------- Config ----------
MAX_PER_SECTION = 6
MAX_PER_HOST = 6

AI_ENABLED = bool(os.getenv("OPENAI_API_KEY"))
AI_MODEL   = os.getenv("NEWS_AI_MODEL", "gpt-4o-mini")
CACHE_PATH = ".cache/news_summaries_en.json"

# Promote US/UK names (sports heavy; a light touch for politics/business)
SPORT_STAR_US_UK = re.compile(
    r"(LeBron|Curry|Mahomes|Brady|Serena Williams|Coco Gauff|"
    r"Iga Świątek|Hurkacz|Alcaraz|Djokovic|"  # tennis global stars (help ranking)
    r"Harry Kane|Bukayo Saka|Rashford|Jude Bellingham|"
    r"Raheem Sterling|Declan Rice|"
    r"Patrick Mahomes|Travis Kelce|"
    r"Tyson Fury|Anthony Joshua|"
    r"Lewis Hamilton|Lando Norris|"
    r"Rory McIlroy|"
    r"Megan Rapinoe|Lionesses|Team GB)", re.I
)

POLITICS_FIG_US_UK = re.compile(
    r"(Biden|Trump|Kamala Harris|Keir Starmer|Rishi Sunak|Downing Street|White House)", re.I
)

BUSINESS_US_UK = re.compile(
    r"(FTSE|Bank of England|Fed|Federal Reserve|Treasury|HMRC|HM Treasury|Wall Street|S&P|Nasdaq|"
    r"Apple|Microsoft|Amazon|Nvidia|Google|Alphabet|Meta|TSLA|Tesla|"
    r"BP|Shell|Barclays|HSBC)", re.I
)

# ---------- Feeds ----------
FEEDS = {
    "politics": [
        "https://feeds.bbci.co.uk/news/politics/rss.xml",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://feeds.reuters.com/reuters/worldNews",
        "https://feeds.reuters.com/reuters/ukNews",
        "https://feeds.reuters.com/reuters/politicsNews",
    ],
    "business": [
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.bloomberg.com/feeds/podcasts/etf-report.xml",  # bloomberg rss is limited; still a signal
        "https://feeds.reuters.com/reuters/businessNews",
        "https://www.ft.com/?format=rss",  # some entries will be paywalled; filtered by scoring & host limits
    ],
    "sports": [
        "https://www.espn.com/espn/rss/news",
        "https://www.espn.com/espn/rss/nba/news",
        "https://www.espn.com/espn/rss/soccer/news",
        "https://www.espn.com/espn/rss/tennis/news",
        "https://www.skysports.com/rss/12040",       # football
        "https://www.skysports.com/rss/12028",       # tennis
        "https://www.skysports.com/rss/12040",       # football (duplicate ok — host cap handles)
        "https://feeds.bbci.co.uk/sport/rss.xml?edition=uk",  # BBC Sport
    ],
}

# ---------- Source priority ----------
def rx(d): return re.compile(d, re.I)
SOURCE_PRIORITY = [
    (rx(r"bbc\."),        22),
    (rx(r"reuters\.com"), 20),
    (rx(r"espn\.com"),    18),
    (rx(r"skysports\.com"),16),
    (rx(r"bloomberg\.com"),12),
    (rx(r"ft\.com"),      10),
]

# ---------- Thematic boosts ----------
BOOST = {
    "politics": [
        (POLITICS_FIG_US_UK, 26),
    ],
    "business": [
        (BUSINESS_US_UK, 24),
        (re.compile(r"(inflation|interest rate|CPI|jobs report|labour market|oil|energy|GDP)", re.I), 12),
    ],
    "sports": [
        (SPORT_STAR_US_UK, 35),
        (re.compile(r"(LIVE|live blog|as it happens|stream|coverage)", re.I), 30),
    ],
}

# ---------- Filters ----------
BAN_PATTERNS = [
    re.compile(r"(horoscope|quiz|gallery|photo gallery|opinion|sponsored)", re.I),
]
LIVE_RE = re.compile(r"(LIVE|live blog|as it happens|stream|coverage)", re.I)

# ---------- Utils ----------
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
    """Trim to <=max_chars and end on a full sentence (., !, ?)."""
    t = (text or "").strip()
    if not t:
        return ""
    if len(t) <= max_chars:
        pass
    else:
        t = t[:max_chars+1]
    # try cut to last sentence end
    m = re.findall(r"[.!?](?!.*[.!?])", t)
    if m:
        end = max(t.rfind("."), t.rfind("!"), t.rfind("?"))
        if end != -1:
            t = t[:end+1]
    return t.strip()

# ---------- Scoring ----------
def score_item(item, section_key: str) -> float:
    score = 0.0
    # recency (≤36h full credit)
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

# ---------- AI Summaries (English) ----------
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

def ai_summarize_en(title: str, snippet: str) -> dict:
    """
    Returns dict:
    { "summary": "...", "uncertain": "" } — 'uncertain' omitted if not detected.
    """
    key = os.getenv("OPENAI_API_KEY")
    cache_key = f"{norm_title(title)}|{today_str()}"
    if cache_key in CACHE:
        return CACHE[cache_key]

    if not key:
        summary = ensure_full_sentence((snippet or title)[:320], 320)
        out = {"summary": summary, "uncertain": ""}
        CACHE[cache_key] = out
        save_cache(CACHE_PATH, CACHE)
        return out

    prompt = f"""Summarize the key facts in **2 short sentences max** based **only** on the title and RSS snippet below.
Then, **only if warranted**, add one sentence starting with "Uncertain / disputed:" describing something unclear,
contested, preliminary, or possibly misleading to readers. If nothing is unclear, **do not add that line**.

Title: {title}
RSS snippet: {snippet}

Rules:
- Be concise, neutral, and non-speculative.
- End each sentence with a proper period.
- Do not invent details that are not present.
- Output plain text, no markdown.
"""

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": AI_MODEL,
                "messages": [
                    {"role": "system", "content": "You are a careful news assistant. Be concise and precise."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 220,
            },
            timeout=25,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()

        # Split optional uncertainty line
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        summary_lines = []
        uncertain_line = ""
        for ln in lines:
            if ln.lower().startswith("uncertain / disputed:"):
                uncertain_line = ln  # keep as-is
            else:
                summary_lines.append(ln)
        summary = ensure_full_sentence(" ".join(summary_lines), 320)

        out = {"summary": summary, "uncertain": uncertain_line}
        CACHE[cache_key] = out
        save_cache(CACHE_PATH, CACHE)
        return out
    except Exception:
        summary = ensure_full_sentence((snippet or title)[:320], 320)
        return {"summary": summary, "uncertain": ""}

# ---------- Fetch & Pick ----------
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

    # score & sort
    for it in items:
        it["_score"] = score_item(it, section_key)
    items.sort(key=lambda x: x["_score"], reverse=True)

    # dedupe by normalized title
    seen = set()
    deduped = []
    for it in items:
        key = norm_title(it["title"])
        if key and key not in seen:
            seen.add(key)
            deduped.append(it)

    # keep host cap
    per_host = {}
    pool = []
    for it in deduped:
        h = host_of(it["link"])
        per_host[h] = per_host.get(h, 0)
        if per_host[h] >= MAX_PER_HOST:
            continue
        per_host[h] += 1
        pool.append(it)

    # SPORTS: ensure diversity + LIVE preference + US/UK stars
    if section_key == "sports":
        picked = []
        live_added = 0

        # 1) take up to 2 with LIVE or US/UK stars
        for it in pool:
            t = it["title"]
            if LIVE_RE.search(t) or SPORT_STAR_US_UK.search(t):
                picked.append(it)
                live_added += 1 if LIVE_RE.search(t) else 0
                if len(picked) >= 2:
                    break

        # 2) ensure diversity by discipline keywords
        def tag(t):
            t=t.lower()
            if any(k in t for k in ["tennis","wimbledon","us open","australian open"]): return "tennis"
            if any(k in t for k in ["premier league","fa cup","champions league","soccer","football"]): return "football"
            if any(k in t for k in ["nba","basketball"]): return "basketball"
            if any(k in t for k in ["f1","formula 1","grand prix"]): return "f1"
            if any(k in t for k in ["golf","pga","the open"]): return "golf"
            if any(k in t for k in ["boxing","ufc"]): return "fight"
            return "other"

        seen_tags = {tag(x["title"]) for x in picked}
        for it in pool:
            if it in picked: continue
            tg = tag(it["title"])
            if tg not in seen_tags:
                picked.append(it); seen_tags.add(tg)
                if len(picked) >= MAX_PER_SECTION: break

        # 3) fill remaining by score
        for it in pool:
            if len(picked) >= MAX_PER_SECTION: break
            if it not in picked:
                picked.append(it)

    else:
        # politics/business simple top-N
        picked = pool[:MAX_PER_SECTION]

    # AI summarize
    for it in picked:
        s = ai_summarize_en(it["title"], it.get("summary_raw", ""))
        it["ai_summary"] = s["summary"]
        it["ai_uncertain"] = s["uncertain"]

    return picked

# ---------- Render ----------
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
    <div class="ai-head"><span class="ai-badge"><span class="ai-dot"></span> BriefRooms • AI comment</span></div>
    <div class="sec"><strong>Most important:</strong> {esc(it.get("ai_summary",""))}</div>
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
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>News — BriefRooms</title>
  <meta name="description" content="Automatic daily digest: politics/world, business/economy, sports." />
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
  <h1>News</h1>
  <p class="sub">Last ~36 hours • {today_str()}</p>
</header>
<main>
{make_section("Politics / World", sections["politics"])}
{make_section("Business / Economy", sections["business"])}
{make_section("Sports", sections["sports"])}
<p class="note">Automatic digest (RSS). Links go to original publishers. Page is overwritten daily.</p>
</main>
<footer style="text-align:center; opacity:.55; padding:18px">© BriefRooms</footer>
</body>
</html>"""
    return html_out

def main():
    sections = {
        "politics": fetch_section("politics"),
        "business": fetch_section("business"),
        "sports":   fetch_section("sports"),
    }
    html_str = render_html(sections)
    with open("en/news.html", "w", encoding="utf-8") as f:
        f.write(html_str)
    print("✓ Generated en/news.html (AI:", "ON" if AI_ENABLED else "OFF", ")")

if __name__ == "__main__":
    main()

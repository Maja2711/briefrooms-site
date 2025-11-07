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

# =========================
# TIMEZONE
# =========================
TZ = tz.gettz("Europe/London")

# =========================
# CONFIG
# =========================
MAX_PER_SECTION = 6
MAX_PER_HOST = 6
AI_ENABLED = bool(os.getenv("OPENAI_API_KEY"))
AI_MODEL = os.getenv("NEWS_AI_MODEL", "gpt-4o-mini")
CACHE_PATH = ".cache/news_summaries_en.json"

FEEDS = {
    "politics": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://feeds.bbci.co.uk/news/uk/rss.xml",
        "https://www.reuters.com/rss/world",
        "https://www.reuters.com/rss/politicsNews",
        "https://apnews.com/hub/apf-topnews?utm_source=apnews.com&utm_medium=referral&utm_campaign=rss",
    ],
    "business": [
        "https://www.reuters.com/rss/businessNews",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://apnews.com/hub/business?utm_source=apnews.com&utm_medium=referral&utm_campaign=rss",
    ],
    "sports": [
        "https://www.espn.com/espn/rss/news",
        "https://www.bbc.com/sport/rss.xml",
        "https://apnews.com/hub/apf-sports?utm_source=apnews.com&utm_medium=referral&utm_campaign=rss",
    ],
}

# Trusted sources for corroboration
TRUSTED_CORRO_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/uk/rss.xml",
    "https://www.reuters.com/rss/world",
    "https://www.reuters.com/rss/businessNews",
    "https://apnews.com/hub/apf-topnews?utm_source=apnews.com&utm_medium=referral&utm_campaign=rss",
    "https://apnews.com/hub/business?utm_source=apnews.com&utm_medium=referral&utm_campaign=rss",
]

# Trusted science/health sources
OFFICIAL_SCIENCE_FEEDS = [
    "https://www.who.int/feeds/entity/mediacentre/news/en/rss.xml",
    "https://www.cdc.gov/media/rss.htm",
    "https://www.nhs.uk/news/feed/",
    "https://www.cochrane.org/news-feed.xml",
]

SOURCE_PRIORITY = [
    (re.compile(r"bbc\.co\.uk|bbc\.com", re.I), 22),
    (re.compile(r"reuters\.com", re.I), 20),
    (re.compile(r"apnews\.com", re.I), 18),
    (re.compile(r"espn\.com", re.I), 18),
]

BOOST = {
    "politics": [
        (re.compile(r"UK|Britain|British|Prime Minister|Parliament|Commons|Downing Street|No\.?10|Home Office|NHS|Scotland|Wales|Northern Ireland", re.I), 32),
        (re.compile(r"US|United States|White House|Congress|Senate|House of Representatives|Supreme Court|FBI|CIA|IRS", re.I), 24),
    ],
    "business": [
        (re.compile(r"inflation|CPI|interest rates|BoE|Bank of England|Fed|Federal Reserve|GDP|unemployment|NASDAQ|S&P|FTSE|Dow|bond|gilts|Treasur", re.I), 28),
    ],
    "sports": [
        # UK & US stars/teams
        (re.compile(r"LeBron James|Steph Curry|Patrick Mahomes|Tom Brady|Serena Williams|Coco Gauff|Noah Lyles|Simone Biles", re.I), 42),
        (re.compile(r"Lewis Hamilton|Lando Norris|Jude Bellingham|Harry Kane|Bukayo Saka|Marcus Rashford|Emma Raducanu|Andy Murray|Lionesses", re.I), 42),
        (re.compile(r"(LIVE|live blog|live text|as it happens|stream)", re.I), 45),
    ],
}

BAN_PATTERNS = [
    re.compile(r"horoscope|gossip|quiz|sponsored|gallery|in pictures|clickbait", re.I),
]

LIVE_RE = re.compile(r"(LIVE|live blog|live text|as it happens|stream)", re.I)

# =========================
# TOKENIZATION / SIMILARITY
# =========================
STOP_EN = {
    "the","and","or","a","an","of","to","for","in","on","at","by","with","as","from","that","this","these","those",
    "is","are","was","were","be","been","being","it","its","into","over","under","than","then","but","so","if","not",
    "–","—","-","\"","“","”","'","…"
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
# HELPERS
# =========================
def host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
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
# HEALTH TOPIC GATE
# =========================
HEALTH_KEYWORDS = re.compile(
    r"\b(vaccine|vaccination|virus|covid|flu|influenza|health|hospital|doctor|patient|disease|cancer|trial|therapy|treatment|mask|immunity|infection|outbreak)\b",
    re.I
)
HEALTH_DOMAINS = {"who.int","cdc.gov","nhs.uk","cochrane.org"}

def is_health_topic(title: str, snippet: str, url: str = "") -> bool:
    host = host_of(url)
    if any(host.endswith(d) or host == d for d in HEALTH_DOMAINS):
        return True
    text = f"{title} {snippet}"
    return bool(HEALTH_KEYWORDS.search(text))

# =========================
# AI SUMMARIES (CACHE)
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

def ai_summarize_en(title: str, snippet: str, url: str) -> dict:
    """
    Returns: {"summary": "...", "note": ""} – 'note' empty when no warning needed.
    """
    key = os.getenv("OPENAI_API_KEY")
    cache_key = f"{title.lower().strip()}|{today_str()}"
    if cache_key in CACHE:
        return CACHE[cache_key]

    if not key:
        out = {
            "summary": ensure_full_sentence((snippet or title or "")[:320], 320),
            "note": "",
            "model": "fallback"
        }
        CACHE[cache_key] = out
        save_cache(CACHE_PATH, CACHE)
        return out

    prompt = f"""Summarise in English in up to 2 short sentences the key facts from the title and RSS description below.
If there is any potentially misleading/disputed element or something easy to misinterpret, add ONE short sentence starting with "Note:".
If there is no need for a warning, DO NOT add that line.
Title: {title}
RSS description: {snippet}
Rules:
- Be concise and neutral.
- End sentences with a period.
- Do not add facts not present in title/description.
- Return plain text, line by line (no formatting)."""

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": AI_MODEL,
                "messages": [
                    {"role": "system", "content": "You are a reliable, concise news assistant. Always end sentences with a period."},
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
        summary_lines, note_line = [], ""
        for ln in lines:
            if ln.lower().startswith("note:"):
                note_line = ln
            else:
                summary_lines.append(ln)

        summary = ensure_full_sentence(" ".join(summary_lines), 320)
        note_line = ensure_period(note_line) if note_line else ""

        out = {"summary": summary, "note": note_line}
        CACHE[cache_key] = out
        save_cache(CACHE_PATH, CACHE)
        return out
    except Exception:
        return {
            "summary": ensure_full_sentence((snippet or title)[:320], 320),
            "note": ""
        }

# =========================
# VERIFICATION LAYER (EN)
# =========================
# Strong claims need corroboration
STRONG_CLAIM_RE = re.compile(
    r"(convicted|arrested|proven|fraud|record|killed|deaths|catastroph|leak|bankrupt|treason|corruption|manipulation|broke\s+the\s+law)",
    re.I
)

# Legal rules (EN)
LEGAL_RULES_EN = [
    (re.compile(r"(must|will have to)\s+prove\s+(his|her|their)\s+innocence", re.I),
     "criminal cases follow the presumption of innocence — prosecutors must prove guilt, not defendants their innocence."),
    (re.compile(r"\bconvicted\b", re.I),
     "‘convicted’ should be used only after a final judgment; earlier stages are ‘charged’, ‘indicted’ or ‘arrested’."),
]

# Science/health red flags (EN)
SCIENCE_RED_FLAGS_EN = [
    (re.compile(r"\b(100%|guarantee|cure|miracle|zero risk)\b", re.I),
     "absolute claims (‘100%’, ‘cure’, ‘zero risk’) are rarely supported by high-quality evidence."),
    (re.compile(r"\b(causes?|proves?)\b", re.I),
     "causation/proof requires robust study design; many headlines report correlation."),
    (re.compile(r"\bpreprint\b|\bmedrxiv\b|\bbiorxiv\b", re.I),
     "preprints are not peer-reviewed; findings may change after review."),
    (re.compile(r"\b(n=\s?\d{1,2}\b|\b(sample|group)\s+of\s+\d{1,2}\b)", re.I),
     "very small samples limit reliability; results may not generalise."),
]

def _search_trusted_sources(title: str, feeds, min_sim=0.58, max_look=60):
    base_tok = tokens_en(title)
    hits = []
    for f in feeds:
        try:
            p = feedparser.parse(f)
            for e in p.entries[:max_look]:
                t = (e.get("title") or "").strip()
                if not t:
                    continue
                sc = jaccard(base_tok, tokens_en(t))
                if sc >= min_sim:
                    hits.append((host_of(e.get("link","")), sc))
        except Exception as ex:
            print(f"[WARN] verification RSS error: {f} -> {ex}")
    hits.sort(key=lambda x: x[1], reverse=True)
    return [h[0] for h in hits[:3]]

def _apply_rules(rules, title: str, snippet: str):
    notes = []
    for rx, msg in rules:
        if rx.search(title) or rx.search(snippet):
            notes.append(msg)
    return notes

def verify_note_en(title: str, snippet: str, url: str = "") -> str:
    """
    Returns a single concise 'Note: …' only when:
    - legal rule mismatch, or
    - science/health red flags (+ check WHO/CDC/NHS/Cochrane), or
    - strong claim without corroboration in BBC/Reuters/AP.
    """
    notes = []

    # 1) Legal
    notes += _apply_rules(LEGAL_RULES_EN, title, snippet)

    # 2) Science/health — ONLY when it's a health topic
    if is_health_topic(title, snippet, url):
        science_hits = _apply_rules(SCIENCE_RED_FLAGS_EN, title, snippet)
        if science_hits:
            notes += science_hits
            sci_srcs = _search_trusted_sources(title, OFFICIAL_SCIENCE_FEEDS)
            if not sci_srcs:
                notes.append("no matching headlines found in WHO/CDC/NHS/Cochrane RSS.")

    # 3) Strong claims -> require at least one corroboration
    if STRONG_CLAIM_RE.search(title) or STRONG_CLAIM_RE.search(snippet):
        gen_srcs = _search_trusted_sources(title, TRUSTED_CORRO_FEEDS)
        if not gen_srcs:
            notes.append("no corroboration in BBC/Reuters/AP headlines — treat with caution.")

    if not notes:
        return ""
    text = "Note: " + " ".join(sorted(set(notes)))
    text = text.strip()
    if text[-1] not in ".!?…":
        text += "."
    return text

# =========================
# FETCH + DEDUPLICATION
# =========================
def fetch_section(section_key: str):
    items = []
    for feed_url in FEEDS[section_key]:
        try:
            parsed = feedparser.parse(feed_url)
            for e in parsed.entries[:80]:  # light cap for speed
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
            print(f"[WARN] RSS error: {feed_url} -> {ex}")

    # score + tokens
    for it in items:
        it["_score"] = score_item(it, section_key)
        it["_tok"] = tokens_en(it["title"])

    # sort by score
    items.sort(key=lambda x: x["_score"], reverse=True)

    # semantic dedupe (Jaccard on title tokens)
    kept = []
    for it in items:
        dup = any(jaccard(it["_tok"], got["_tok"]) >= SIMILARITY_THRESHOLD for got in kept)
        if not dup:
            kept.append(it)

    # per-host + total limits
    per_host = {}
    pool = []
    for it in kept:
        h = host_of(it["link"])
        per_host[h] = per_host.get(h, 0)
        if per_host[h] >= MAX_PER_HOST:
            continue
        per_host[h] += 1
        pool.append(it)

    # SPORTS: LIVE + diversification across disciplines
    if section_key == "sports":
        picked = []
        # 1) up to 2 LIVE or star-name items first
        for it in pool:
            t = it["title"]
            if LIVE_RE.search(t) or re.search(r"LeBron|Curry|Mahomes|Brady|Serena|Gauff|Hamilton|Norris|Bellingham|Kane|Saka|Rashford|Raducanu|Murray|Lionesses", t, re.I):
                picked.append(it)
                if len(picked) >= 2:
                    break

        # 2) diversify disciplines
        def tag(t):
            t=t.lower()
            if any(k in t for k in ["tennis","wimbledon","us open","australian open"]): return "tennis"
            if any(k in t for k in ["football","premier league","fa cup","champions league","arsenal","chelsea","manchester","liverpool","tottenham","lionesses"]): return "football"
            if "nba" in t or "basketball" in t: return "nba"
            if any(k in t for k in ["f1","formula","grand prix"]): return "f1"
            if any(k in t for k in ["golf","pga","open championship"]): return "golf"
            if any(k in t for k in ["boxing","ufc","mma"]): return "fight"
            return "other"

        seen_tags = {tag(x["title"]) for x in picked}
        for it in pool:
            if it in picked: continue
            tg = tag(it["title"])
            if tg not in seen_tags:
                picked.append(it); seen_tags.add(tg)
                if len(picked) >= MAX_PER_SECTION: break

        # 3) fill to limit
        for it in pool:
            if len(picked) >= MAX_PER_SECTION: break
            if it not in picked:
                picked.append(it)
    else:
        picked = pool[:MAX_PER_SECTION]

    # AI + verification (print 'Note:' only if really needed)
    for it in picked:
        s = ai_summarize_en(it["title"], it.get("summary_raw", ""), it["link"])
        verify = verify_note_en(it["title"], it.get("summary_raw",""), it["link"])
        final_note = verify or s.get("note","")
        it["ai_summary"] = ensure_period(s["summary"])
        it["ai_note"] = ensure_period(final_note) if final_note else ""
        it["ai_model"] = s.get("model","")

    return picked

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
        # avoid "Note: Note:" duplication
        note_text = (it.get("ai_note","") or "").strip()
        if note_text.lower().startswith("note:"):
            note_text = note_text[5:].strip()
        note_html = f'<div class="sec"><strong>Note:</strong> {esc(note_text)}</div>' if note_text else ""
        return f'''<li>
  <a href="{esc(it["link"])}" target="_blank" rel="noopener">
    {badge()}
    <span class="news-text">{esc(it["title"])}</span>
  </a>
  <div class="ai-note">
    <div class="ai-head"><span class="ai-badge"><span class="ai-dot"></span> BriefRooms • AI comment</span></div>
    <div class="sec"><strong>Most important:</strong> {esc(it.get("ai_summary",""))}</div>
    {note_html}
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

<p class="note">Automatic digest (RSS). Links go to original publishers. Page is overwritten automatically.</p>
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
        "politics": fetch_section("politics"),
        "business": fetch_section("business"),
        "sports": fetch_section("sports"),
    }
    html_str = render_html(sections)
    os.makedirs("en", exist_ok=True)
    with open("en/news.html", "w", encoding="utf-8") as f:
        f.write(html_str)
    print("✓ Generated en/news.html (AI:", "ON" if AI_ENABLED else "OFF", ")")

if __name__ == "__main__":
    main()


#!/usr/bin/env python3
"""Macro monitor for BriefRooms investing scenarios.

Checks official central-bank communication feeds and stores a compact macro bias
file for hourly position review. This is informational and educational only.
"""
from __future__ import annotations

import html
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "data" / "investments" / "macro_events.json"
UA = "BriefRoomsBot/1.0 (+https://briefrooms.com)"

SOURCES = [
    {"bank": "FED", "kind": "press", "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
    {"bank": "FED", "kind": "speeches", "url": "https://www.federalreserve.gov/feeds/speeches.xml"},
    {"bank": "ECB", "kind": "press", "url": "https://www.ecb.europa.eu/rss/press.html"},
]

HAWKISH = ["inflation", "price stability", "restrictive", "higher rates", "rate hike", "tightening", "upside risks", "persistent", "2 percent", "2 per cent"]
DOVISH = ["rate cut", "easing", "lower rates", "downside risks", "growth concerns", "weak demand", "disinflation", "accommodative"]
RISK_OFF = ["war", "conflict", "crisis", "financial stability", "bank stress", "market stress", "recession"]


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="ignore")


def parse_items(xml_text: str, meta: Dict[str, str]) -> List[Dict[str, str]]:
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []
    items = []
    for item in root.findall(".//item")[:10]:
        title = clean(item.findtext("title") or "")
        desc = clean(item.findtext("description") or "")
        link = clean(item.findtext("link") or "")
        pub = clean(item.findtext("pubDate") or item.findtext("date") or "")
        if not title:
            continue
        items.append({"bank": meta["bank"], "kind": meta["kind"], "title": title, "summary": desc[:320], "link": link, "published": pub})
    return items


def score_event(event: Dict[str, str]) -> Dict[str, object]:
    text = f"{event.get('title','')} {event.get('summary','')}".lower()
    hawk = sum(1 for w in HAWKISH if w in text)
    dove = sum(1 for w in DOVISH if w in text)
    risk = sum(1 for w in RISK_OFF if w in text)
    eurusd_bias = 0
    reason = []
    if event.get("bank") == "FED":
        eurusd_bias += dove - hawk
    if event.get("bank") == "ECB":
        eurusd_bias += hawk - dove
    if risk:
        eurusd_bias -= 0.5
        reason.append("risk-off language")
    if hawk:
        reason.append(f"hawkish keywords: {hawk}")
    if dove:
        reason.append(f"dovish keywords: {dove}")
    event["scores"] = {"hawkish": hawk, "dovish": dove, "risk_off": risk, "eurusd_bias": eurusd_bias}
    event["note"] = "; ".join(reason) if reason else "no strong macro keyword signal"
    return event


def main() -> None:
    events = []
    errors = []
    for src in SOURCES:
        try:
            events.extend(parse_items(fetch(src["url"]), src))
        except Exception as exc:
            errors.append({"source": src, "error": str(exc)})
    scored = [score_event(e) for e in events[:30]]
    eurusd_bias = sum(float((e.get("scores") or {}).get("eurusd_bias") or 0) for e in scored[:12])
    if eurusd_bias > 1:
        signal = "EUR/USD macro bias: bullish EUR / bearish USD"
    elif eurusd_bias < -1:
        signal = "EUR/USD macro bias: bearish EUR / bullish USD"
    else:
        signal = "EUR/USD macro bias: neutral"
    payload = {"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": "macro-monitor-1.0", "signal": signal, "eurusd_bias_score": round(eurusd_bias, 2), "events": scored[:12], "errors": errors}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Macro monitor wrote {OUT}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

import update_hot_x_topics as hot

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "hot_tweets.json"

RULES = [
    ("ai", re.compile(r"\b(ai|artificial intelligence|openai|anthropic|microsoft|google|nvidia|chip|chips|gpu|llm|deepseek|gemini|copilot|model)\b", re.I), "TECH", "TECH", "/assets/hot-x/topic-ai-tech.svg"),
    ("energy", re.compile(r"\b(oil|brent|wti|opec|opec\+|gas|lng|crude|refinery|ormuz|hormuz|energy|shipping)\b", re.I), "ENERGY", "ENERGIA", "/assets/hot-x/topic-energy-oil.svg"),
    ("crypto", re.compile(r"\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|crypto|blockchain|stablecoin|defi)\b", re.I), "CRYPTO", "KRYPTO", "/assets/hot-x/topic-crypto.svg"),
    ("macro", re.compile(r"\b(fed|fomc|ecb|inflation|cpi|ppi|jobs|labou?r|nfp|payrolls|unemployment|rates|rate cut|yield|treasury|bond|dollar|usd|powell|lagarde)\b", re.I), "MACRO", "MAKRO", "/assets/hot-x/topic-macro-rates.svg"),
    ("risk", re.compile(r"\b(vix|risk[- ]?off|volatility|gold|yen|safe haven|market stress)\b", re.I), "RISK", "RYZYKO", "/assets/hot-x/topic-risk-volatility.svg"),
    ("geopolitics", re.compile(r"\b(ukraine|russia|nato|china|iran|israel|gaza|middle east|trump|tariffs|sanctions|war|missile|defen[cs]e|military|congress|house|senate|election)\b", re.I), "GEOPOLITICS", "GEOPOLITYKA", "/assets/hot-x/topic-geopolitics.svg"),
    ("health", re.compile(r"\b(health|hospital|drug|vaccine|who|disease|virus|medical|medicine|patient|fda)\b", re.I), "HEALTH", "ZDROWIE", "/assets/hot-x/topic-health.svg"),
    ("science", re.compile(r"\b(nasa|esa|space|science|research|astronomy|satellite|rocket|climate)\b", re.I), "SCIENCE", "NAUKA", "/assets/hot-x/topic-science-space.svg"),
    ("markets", re.compile(r"\b(stocks|shares|nasdaq|s&p|sp500|earnings|guidance|markets|capex|profit|losses)\b", re.I), "MARKETS", "RYNKI", "/assets/hot-x/topic-markets-chart.svg"),
]

DEFAULT = ("news", "NEWS", "NEWS", "/assets/hot-x/topic-news.svg")
BAD = re.compile(r"fed-market\.svg|us-court-politics\.svg|Bitcoin\.svg|Special:FilePath/Bitcoin|placeholder|default-hotx", re.I)


def classify(item: dict) -> tuple[str, str, str, str]:
    text = " ".join(str(item.get(k, "")) for k in ("category", "title_en", "title_pl", "summary_en", "summary_pl", "search_url", "source_en", "source_pl"))
    for key, rx, label_en, label_pl, image in RULES:
        if rx.search(text):
            return key, label_en, label_pl, image
    return DEFAULT


def apply_images() -> None:
    data = json.loads(OUT.read_text(encoding="utf-8"))
    for item in data.get("items", []):
        key, label_en, label_pl, image = classify(item)
        item["image_topic"] = key
        item["label_en"] = label_en
        item["label_pl"] = label_pl
        # Replace old generic FED/court/bitcoin art and every mismatched placeholder.
        if not item.get("image") or BAD.search(str(item.get("image"))):
            item["image"] = image
        # Force exact topic image for rotating fallback/search cards, because these have no real tweet image.
        if "x-search" in str(item.get("selected_by", "")) or "fallback" in str(item.get("selected_by", "")):
            item["image"] = image
    data["image_matching"] = "topic-classifier-v1"
    data["method_pl"] = data.get("method_pl", "") + " Obrazy są dopasowywane klasyfikatorem tematu: AI, makro, energia, geopolityka, krypto, ryzyko, zdrowie, nauka albo rynki."
    data["method_en"] = data.get("method_en", "") + " Images are matched by a topic classifier: AI, macro, energy, geopolitics, crypto, risk, health, science or markets."
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    hot.main()
    apply_images()
    print("Hot X topics rebuilt with topic-matched images")


if __name__ == "__main__":
    main()

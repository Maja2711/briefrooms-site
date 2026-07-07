#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

import update_hot_x_topics as hot

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "hot_tweets.json"

RULES = [
    ("ai", re.compile(r"\b(ai|artificial intelligence|openai|anthropic|microsoft|google|nvidia|samsung|chip|chips|gpu|llm|deepseek|gemini|copilot|model)\b", re.I), "TECH", "TECH", "/assets/hot-x/topic-ai-tech.svg"),
    ("energy", re.compile(r"\b(oil|brent|wti|opec|opec\+|gas|lng|crude|refinery|pipeline|ormuz|hormuz|energy|shipping)\b", re.I), "ENERGY", "ENERGIA", "/assets/hot-x/topic-energy-oil.svg"),
    ("crypto", re.compile(r"\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|crypto|blockchain|stablecoin|defi|miners|treasury)\b", re.I), "CRYPTO", "KRYPTO", "/assets/hot-x/topic-crypto.svg"),
    ("macro", re.compile(r"\b(fed|fomc|ecb|inflation|cpi|ppi|jobs|labou?r|nfp|payrolls|unemployment|rates|rate cut|yield|treasury|bond|dollar|usd|powell|lagarde)\b", re.I), "MACRO", "MAKRO", "/assets/hot-x/topic-macro-rates.svg"),
    ("risk", re.compile(r"\b(vix|risk[- ]?off|volatility|gold|yen|safe haven|market stress|blackout|grid)\b", re.I), "RISK", "RYZYKO", "/assets/hot-x/topic-risk-volatility.svg"),
    ("geopolitics", re.compile(r"\b(ukraine|russia|nato|china|iran|israel|gaza|middle east|trump|tariffs|sanctions|war|missile|defen[cs]e|military|congress|house|senate|election)\b", re.I), "GEOPOLITICS", "GEOPOLITYKA", "/assets/hot-x/topic-geopolitics.svg"),
    ("health", re.compile(r"\b(health|hospital|drug|vaccine|who|disease|virus|medical|medicine|patient|fda)\b", re.I), "HEALTH", "ZDROWIE", "/assets/hot-x/topic-health.svg"),
    ("science", re.compile(r"\b(nasa|esa|space|science|research|astronomy|satellite|rocket|climate)\b", re.I), "SCIENCE", "NAUKA", "/assets/hot-x/topic-science-space.svg"),
    ("markets", re.compile(r"\b(stocks|shares|nasdaq|s&p|sp500|earnings|guidance|markets|capex|profit|profits|losses)\b", re.I), "MARKETS", "RYNKI", "/assets/hot-x/topic-markets-chart.svg"),
]

DEFAULT = ("news", "NEWS", "NEWS", "/assets/hot-x/topic-news.svg")
BAD = re.compile(r"fed-market\.svg|us-court-politics\.svg|Bitcoin\.svg|Special:FilePath/Bitcoin|placeholder|default-hotx", re.I)
GENERIC = re.compile(r"^Rotating Hot X topic:|^Rotacyjny Hot X topic:", re.I)

EXACT_PL = {
    "Europe's economic resilience gives ECB greater room to move rates, Lagarde says": (
        "Odporność gospodarki Europy daje ECB większą swobodę przy stopach",
        "Lagarde sugeruje, że strefa euro lepiej znosi wstrząsy. To może dawać ECB większą swobodę przy decyzjach o stopach procentowych.",
    ),
    "EU leaders weigh tougher measures to combat China trade imbalance": (
        "UE rozważa ostrzejsze działania wobec nierównowagi handlowej z Chinami",
        "Przywódcy UE rozważają mocniejsze działania, aby ograniczyć narastającą nierównowagę handlową z Chinami.",
    ),
    "TradFi advisers want stablecoins, tokenization over Bitcoin: Bitwise": (
        "Doradcy finansowi częściej patrzą na stablecoiny i tokenizację niż na Bitcoina",
        "Część doradców z tradycyjnych finansów bardziej interesuje się stablecoinami i tokenizacją niż samym Bitcoinem.",
    ),
}


def x_search(query: str, live: bool = False) -> str:
    mode = "live" if live else "top"
    return "https://x.com/search?q=" + urllib.parse.quote(query) + f"&src=typed_query&f={mode}"


def classify(item: dict) -> tuple[str, str, str, str]:
    text = " ".join(str(item.get(k, "")) for k in ("category", "title_en", "title_pl", "summary_en", "summary_pl", "search_url", "source_en", "source_pl"))
    for key, rx, label_en, label_pl, image in RULES:
        if rx.search(text):
            return key, label_en, label_pl, image
    return DEFAULT


def clean_source(value: str, pl: bool = False) -> str:
    text = str(value or "")
    if "Reuters" in text:
        return "Reuters / X" if not pl else "Reuters / X"
    if "Bloomberg" in text:
        return "Bloomberg / X" if not pl else "Bloomberg / X"
    if "BBC" in text:
        return "BBC / X" if not pl else "BBC / X"
    if "Guardian" in text or "The Guardian" in text:
        return "The Guardian / X" if not pl else "The Guardian / X"
    if "CoinDesk" in text:
        return "CoinDesk / X" if not pl else "CoinDesk / X"
    if "Bitwise" in text:
        return "Bitwise / X" if not pl else "Bitwise / X"
    if "X / @" in text:
        return text
    return "X — konkretne wyszukiwanie newsa" if pl else "X — exact news search"


def is_concrete_news(item: dict) -> bool:
    title = str(item.get("title_en") or "")
    summary = str(item.get("summary_en") or "")
    selected = str(item.get("selected_by") or "")
    if "x-api" in selected:
        return True
    if "news-to-x-search" in selected and title and not GENERIC.search(summary):
        return True
    # also keep manually curated items concrete
    return str(item.get("selected_by") or "").startswith("manual-")


def make_exact_x_link(item: dict) -> None:
    if item.get("tweet_url"):
        return
    title = str(item.get("title_en") or item.get("title_pl") or "").strip()
    source = re.sub(r"\s*/\s*(X search|wyszukiwanie X|X).*", "", str(item.get("source_en") or "")).strip()
    if title and is_concrete_news(item):
        q = f'"{title}" {source}'.strip()
        item["search_url"] = x_search(q, live=False)
        item["x_query"] = q
        item["selected_by"] = "concrete-news-to-x-search-4h"
    elif title:
        item["search_url"] = x_search(title, live=False)
        item["x_query"] = title


def apply_pl_text(item: dict) -> None:
    title_en = str(item.get("title_en") or "")
    summary_en = str(item.get("summary_en") or "")
    if title_en in EXACT_PL:
        item["title_pl"], item["summary_pl"] = EXACT_PL[title_en]
        return
    # Nie zamieniaj konkretnego newsa na ogólny opis. Jeśli nie mamy tłumaczenia,
    # zachowujemy konkretny tytuł i konkretny opis, zamiast tekstu typu "rotacyjny temat".
    if title_en and (item.get("title_pl") == item.get("title_en") or not item.get("title_pl")):
        item["title_pl"] = title_en
    if summary_en and (item.get("summary_pl") == item.get("summary_en") or not item.get("summary_pl") or GENERIC.search(str(item.get("summary_pl") or ""))):
        item["summary_pl"] = summary_en


def apply_images_and_concrete_links() -> None:
    data = json.loads(OUT.read_text(encoding="utf-8"))
    for item in data.get("items", []):
        key, label_en, label_pl, image = classify(item)
        item["image_topic"] = key
        item["label_en"] = label_en
        item["label_pl"] = label_pl
        if not item.get("image") or BAD.search(str(item.get("image"))):
            item["image"] = image
        if "x-search" in str(item.get("selected_by", "")) or "fallback" in str(item.get("selected_by", "")):
            item["image"] = image
        make_exact_x_link(item)
        apply_pl_text(item)
        item["source_en"] = clean_source(str(item.get("source_en") or ""), False)
        item["source_pl"] = clean_source(str(item.get("source_pl") or item.get("source_en") or ""), True)
    data["image_matching"] = "topic-classifier-v2"
    data["mode"] = "concrete-news-to-x-search"
    data["method_pl"] = "Automatycznie co 4 godziny: wybiera konkretny news, tworzy dokładny link do wyszukiwania tego tematu na X i pokazuje krótki opis newsa. Jeśli dostępny jest X_BEARER_TOKEN, linkuje bezpośrednio do wpisu na X."
    data["method_en"] = "Every 4 hours: selects a concrete news item, builds an exact X search link for that story and shows a short description. If X_BEARER_TOKEN is available, it links directly to an X post."
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    hot.main()
    apply_images_and_concrete_links()
    print("Hot X topics rebuilt as concrete news items with exact X links")


if __name__ == "__main__":
    main()

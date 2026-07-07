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

PL_BY_TOPIC = {
    "macro": ("Banki centralne, inflacja i reakcja rynku", "Temat makro: stopy procentowe, inflacja, euro/dolar i rentowności obligacji."),
    "geopolitics": ("Geopolityka, handel i napięcia polityczne", "Temat geopolityczny: decyzje rządów, cła, sankcje lub relacje między mocarstwami."),
    "crypto": ("Krypto: regulacje, stablecoiny i główne aktywa", "Temat krypto: regulacje, stablecoiny, Bitcoin, Ethereum i nastroje inwestorów."),
    "ai": ("AI, chipy i regulacje technologiczne", "Temat technologiczny: AI, półprzewodniki, wielkie spółki i nowe regulacje."),
    "energy": ("Ropa, OPEC i ryzyko energii", "Temat energetyczny: ropa, OPEC, gaz, szlaki transportowe i ceny energii."),
    "markets": ("Rynki, wyniki spółek i reakcja inwestorów", "Temat rynkowy: indeksy, wyniki spółek, rentowności i apetyt na ryzyko."),
    "risk": ("Zmienność, dolar, jen i złoto", "Temat ryzyka: VIX, ucieczka od ryzyka, dolar, jen, złoto i stres rynkowy."),
    "health": ("Zdrowie publiczne i medycyna", "Temat zdrowotny: choroby, leki, badania, szpitale lub decyzje regulatorów."),
    "science": ("Nauka, kosmos i badania", "Temat naukowy: odkrycia, misje kosmiczne, klimat, badania i technologia."),
    "news": ("Gorący temat z X", "Krótki temat z najnowszych wiadomości kierujący do wyszukiwania na X."),
}

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


def classify(item: dict) -> tuple[str, str, str, str]:
    text = " ".join(str(item.get(k, "")) for k in ("category", "title_en", "title_pl", "summary_en", "summary_pl", "search_url", "source_en", "source_pl"))
    for key, rx, label_en, label_pl, image in RULES:
        if rx.search(text):
            return key, label_en, label_pl, image
    return DEFAULT


def clean_source(value: str, pl: bool = False) -> str:
    text = str(value or "")
    if "Reuters" in text:
        return "Reuters / wyszukiwanie X" if pl else "Reuters / X search"
    if "Bloomberg" in text:
        return "Bloomberg / wyszukiwanie X" if pl else "Bloomberg / X search"
    if "Bitwise" in text:
        return "Bitwise / wyszukiwanie X" if pl else "Bitwise / X search"
    return "Wyszukiwanie X" if pl else "X search"


def apply_pl_translation(item: dict, topic_key: str) -> None:
    title_en = str(item.get("title_en") or "")
    if title_en in EXACT_PL:
        item["title_pl"], item["summary_pl"] = EXACT_PL[title_en]
    elif item.get("title_pl") == item.get("title_en") or item.get("summary_pl") == item.get("summary_en"):
        item["title_pl"], item["summary_pl"] = PL_BY_TOPIC.get(topic_key, PL_BY_TOPIC["news"])
    item["source_en"] = clean_source(str(item.get("source_en") or ""), False)
    item["source_pl"] = clean_source(str(item.get("source_pl") or item.get("source_en") or ""), True)


def apply_images() -> None:
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
        apply_pl_translation(item, key)
    data["image_matching"] = "topic-classifier-v1"
    data["method_pl"] = "Automatycznie co 4 godziny. Wersja PL pokazuje polskie tytuły i krótkie polskie opisy. Obrazy są dopasowywane klasyfikatorem tematu."
    data["method_en"] = "Automatically every 4 hours. EN shows English titles and summaries. Images are matched by topic classifier."
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    hot.main()
    apply_images()
    print("Hot X topics rebuilt with topic-matched images and PL text")


if __name__ == "__main__":
    main()

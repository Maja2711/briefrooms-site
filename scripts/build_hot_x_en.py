#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

import update_hot_x_topics as hot

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "hot_tweets.json"
CACHE = ROOT / ".cache" / "hot_x_pl_translations.json"

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

DEFAULT = ("news", "NEWS", "AKTUALNOŚCI", "/assets/hot-x/topic-news.svg")
BAD = re.compile(r"fed-market\.svg|us-court-politics\.svg|Bitcoin\.svg|Special:FilePath/Bitcoin|placeholder|default-hotx", re.I)
GENERIC = re.compile(r"^Rotating Hot X topic:|^Rotacyjny Hot X topic:", re.I)
ENGLISH_LEFTOVERS = re.compile(r"\b(the|with|despite|yields|steady|tensions|eyes on|study shows|market news|trade with|bond|crypto stash|tops|Reuters|London|Berlin|June|July)\b", re.I)
BYLINE = re.compile(r"^(?:By\s+[A-Z][^–—-]{2,120}\s+)?(?:LONDON|BERLIN|NEW YORK|WASHINGTON|BRUSSELS|SINGAPORE|TOKYO),?\s+(?:Jan|Feb|Mar|Apr|May|Jun|June|Jul|July|Aug|Sep|Oct|Nov|Dec)[^–—-]{0,40}\s+\(Reuters\)\s*[-–—]\s*", re.I)

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
    "Euro Zone Bond Yields Steady as Middle East Tensions Ease, Eyes on ECB": (
        "Rentowności obligacji strefy euro stabilne; rynek patrzy na ECB",
        "Reuters opisuje stabilizację rentowności obligacji w strefie euro. Spadek napięć na Bliskim Wschodzie uspokoił rynek, a inwestorzy skupiają się na kolejnych sygnałach z ECB.",
    ),
    "EU trade with US hits record high despite tariff tensions, study shows": (
        "Handel UE z USA osiąga rekord mimo napięć celnych",
        "Reuters opisuje badanie, według którego handel towarami między Unią Europejską i USA wzrósł do rekordowego poziomu. Tematem dyskusji na X jest odporność wymiany handlowej mimo sporów o cła.",
    ),
    "Cryptocurrency Market News: Reddit's Crypto Stash, Bitcoin Tops $53,000": (
        "Rynek krypto: rezerwy Reddita i Bitcoin powyżej 53 tys. dolarów",
        "W centrum tematu jest ujawnienie kryptoaktywów Reddita oraz ponowny wzrost Bitcoina powyżej 53 tys. dolarów. Dyskusja na X dotyczy wpływu dużych marek i politycznych sygnałów na nastroje w krypto.",
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
        return "Reuters / X"
    if "Bloomberg" in text:
        return "Bloomberg / X"
    if "BBC" in text:
        return "BBC / X"
    if "Guardian" in text or "The Guardian" in text:
        return "The Guardian / X"
    if "CoinDesk" in text:
        return "CoinDesk / X"
    if "Bitwise" in text:
        return "Bitwise / X"
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


def strip_wire_noise(text: str) -> str:
    text = str(text or "").replace("⁠", " ")
    text = BYLINE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—")
    return text


def load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ai_translate_to_pl(title_en: str, summary_en: str) -> tuple[str, str] | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    cache = load_cache()
    cache_key = urllib.parse.quote((title_en + "|" + summary_en)[:400], safe="")
    if cache_key in cache:
        item = cache[cache_key]
        return item.get("title_pl", ""), item.get("summary_pl", "")
    prompt = (
        "Przetłumacz na polski tekst do sekcji Hot X na BriefRooms. "
        "Zwróć wyłącznie JSON {\"title_pl\":\"...\",\"summary_pl\":\"...\"}. "
        "Zasady: naturalny polski, konkretnie, bez angielskich zdań, bez lokalizacji i daty z depeszy na początku, bez byline typu Reuters/LONDON. "
        "title_pl maksymalnie 90 znaków, summary_pl 1-2 zdania, maksymalnie 240 znaków. Nie dodawaj faktów spoza tekstu.\n\n"
        f"Tytuł EN: {title_en}\nOpis EN: {summary_en}"
    )
    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps({
                "model": os.getenv("NEWS_AI_MODEL", "gpt-4o-mini"),
                "messages": [
                    {"role": "system", "content": "Jesteś redaktorem BriefRooms. Tłumaczysz krótko, po polsku i zwracasz tylko JSON."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 260,
            }).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S)
        data = json.loads(raw)
        title_pl = str(data.get("title_pl", "")).strip()
        summary_pl = str(data.get("summary_pl", "")).strip()
        if title_pl and summary_pl and not ENGLISH_LEFTOVERS.search(title_pl + " " + summary_pl):
            cache[cache_key] = {"title_pl": title_pl, "summary_pl": summary_pl}
            save_cache(cache)
            return title_pl, summary_pl
    except Exception:
        return None
    return None


def pl_fallback(title_en: str, summary_en: str, label_pl: str) -> tuple[str, str]:
    title = strip_wire_noise(title_en)
    summary = strip_wire_noise(summary_en)
    # This fallback is deliberately Polish; it avoids leaking English text to /pl/.
    title_pl = f"Temat z X: {label_pl.lower()}"
    summary_pl = "Na X monitorowany jest konkretny news z zagranicznego źródła. Pełny wątek otwiera link do wyszukiwania na X."
    if "bond" in title.lower() or "yield" in title.lower() or "ecb" in title.lower():
        title_pl = "Rynek długu: inwestorzy patrzą na ECB"
        summary_pl = "Temat dotyczy rentowności obligacji w strefie euro i oczekiwań wobec ECB. Link prowadzi do bieżącej dyskusji na X."
    elif "trade" in title.lower() and "us" in title.lower():
        title_pl = "Handel UE–USA pod presją ceł"
        summary_pl = "Temat dotyczy relacji handlowych Unii Europejskiej i USA mimo napięć celnych. Link prowadzi do dyskusji na X."
    elif "crypto" in title.lower() or "bitcoin" in title.lower():
        title_pl = "Krypto: Bitcoin i rezerwy firm"
        summary_pl = "Temat dotyczy rynku kryptowalut, Bitcoina oraz roli dużych firm w nastrojach inwestorów. Link prowadzi do dyskusji na X."
    return title_pl, summary_pl


def apply_pl_text(item: dict, label_pl: str) -> None:
    title_en = strip_wire_noise(str(item.get("title_en") or ""))
    summary_en = strip_wire_noise(str(item.get("summary_en") or ""))
    if title_en in EXACT_PL:
        item["title_pl"], item["summary_pl"] = EXACT_PL[title_en]
        return
    translated = ai_translate_to_pl(title_en, summary_en)
    if translated:
        item["title_pl"], item["summary_pl"] = translated
        return
    item["title_pl"], item["summary_pl"] = pl_fallback(title_en, summary_en, label_pl)


def assert_pl_hot_text(data: dict) -> None:
    for item in data.get("items", []):
        pl_text = f"{item.get('title_pl','')} {item.get('summary_pl','')}"
        if ENGLISH_LEFTOVERS.search(pl_text):
            key, label_en, label_pl, image = classify(item)
            item["title_pl"], item["summary_pl"] = pl_fallback(str(item.get("title_en") or ""), str(item.get("summary_en") or ""), label_pl)
            item["pl_translation_status"] = "fallback_no_english_leak"
        else:
            item["pl_translation_status"] = "pl_ok"


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
        apply_pl_text(item, label_pl)
        item["source_en"] = clean_source(str(item.get("source_en") or ""), False)
        item["source_pl"] = clean_source(str(item.get("source_pl") or item.get("source_en") or ""), True)
    assert_pl_hot_text(data)
    data["image_matching"] = "topic-classifier-v2"
    data["mode"] = "concrete-news-to-x-search"
    data["method_pl"] = "Automatycznie co 4 godziny: wybiera konkretny news, tworzy dokładny link do wyszukiwania tego tematu na X i pokazuje krótki opis po polsku. W wersji PL tytuł i opis nie mogą zostawać po angielsku."
    data["method_en"] = "Every 4 hours: selects a concrete news item, builds an exact X search link for that story and shows a short description."
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    hot.main()
    apply_images_and_concrete_links()
    print("Hot X topics rebuilt with Polish text for PL and English text for EN")


if __name__ == "__main__":
    main()

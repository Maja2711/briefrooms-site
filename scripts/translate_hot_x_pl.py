#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Final PL language gate for Hot X.

Rule: on /pl/ every visible Hot X title and description must be in Polish. If a
proper Polish translation is unavailable, do not leak English text; use a short
Polish topic description instead.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PATH = Path("data/hot_tweets.json")
EN_LEFT = re.compile(
    r"\b(the|with|despite|yields|steady|tensions|eyes on|study shows|market news|trade with|bond|crypto stash|tops|"
    r"supports|trading|stablecoins|second-round|inflation fears|ruling party|Reuters|London|Berlin|Frankfurt|June|July|"
    r"seeks public input|regulatory framework|public consultation)\b",
    re.I,
)
WIRE_PREFIX = re.compile(r"^(?:By\s+[A-Z][^–—-]{2,120}\s+)?(?:LONDON|BERLIN|FRANKFURT|NEW YORK|WASHINGTON|BRUSSELS),?\s+(?:Jan|Feb|Mar|Apr|May|Jun|June|Jul|July|Aug|Sep|Oct|Nov|Dec)[^–—-]{0,40}\s+\(Reuters\)\s*[-–—]\s*", re.I)

EXACT = {
    "ECB's Lagarde plays down second-round inflation fears": (
        "Lagarde z ECB studzi obawy o trwałą inflację",
        "Reuters opisuje wypowiedź Christine Lagarde, która umniejsza ryzyko utrwalenia się drugiej rundy inflacji w strefie euro. Temat na X dotyczy tego, jak te słowa wpływają na oczekiwania wobec polityki ECB.",
    ),
    "EU leaders weigh tougher measures to combat China trade imbalance": (
        "UE rozważa ostrzejsze działania wobec nierównowagi handlowej z Chinami",
        "Przywódcy UE rozważają mocniejsze działania, aby ograniczyć narastającą nierównowagę handlową z Chinami. Dyskusja na X dotyczy możliwych konsekwencji dla handlu i relacji z Pekinem.",
    ),
    "Japan's ruling party supports crypto ETF trading, yen-based stablecoins": (
        "Japonia: partia rządząca popiera ETF-y krypto i stablecoiny w jenie",
        "Temat dotyczy propozycji japońskiej partii rządzącej, aby stworzyć ramy dla handlu ETF-ami opartymi na kryptowalutach i promować stablecoiny powiązane z jenem.",
    ),
    "SEC Seeks Public Input on New Crypto ETF Regulatory Framework": (
        "SEC konsultuje nowe zasady dla ETF-ów krypto",
        "Amerykańska SEC zbiera opinie rynku na temat nowych ram regulacyjnych dla ETF-ów krypto. Dyskusja na X dotyczy tego, jak przepisy mogą wpłynąć na inwestowanie w aktywa cyfrowe.",
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
        "W centrum tematu jest ujawnienie kryptoaktywów Reddita oraz wzrost Bitcoina powyżej 53 tys. dolarów. Dyskusja na X dotyczy wpływu dużych marek i politycznych sygnałów na nastroje w krypto.",
    ),
}


def clean(text: str) -> str:
    text = WIRE_PREFIX.sub("", str(text or "").replace("⁠", " "))
    return re.sub(r"\s+", " ", text).strip(" -–—")


def fallback(item: dict) -> tuple[str, str]:
    title = clean(item.get("title_en") or item.get("title_pl") or "")
    blob = (title + " " + clean(item.get("summary_en") or "")).lower()
    label = str(item.get("label_pl") or "temat").lower()
    if "ecb" in blob or "lagarde" in blob or "inflation" in blob or "yield" in blob or "bond" in blob:
        return (
            "ECB i rynek długu w centrum dyskusji",
            "Temat na X dotyczy polityki ECB, inflacji oraz reakcji rynku obligacji. Link prowadzi do bieżącej dyskusji wokół tego newsa.",
        )
    if "china" in blob or "trade" in blob or "tariff" in blob:
        return (
            "Handel i napięcia celne w centrum dyskusji",
            "Temat na X dotyczy relacji handlowych, ceł i możliwych decyzji politycznych. Link prowadzi do bieżącej dyskusji wokół tego newsa.",
        )
    if "sec" in blob and ("etf" in blob or "regulatory" in blob or "framework" in blob):
        return (
            "SEC i regulacje ETF-ów krypto",
            "Temat na X dotyczy konsultacji regulacyjnych SEC wokół ETF-ów krypto. Link prowadzi do bieżącej dyskusji wokół tego newsa.",
        )
    if "crypto" in blob or "bitcoin" in blob or "stablecoin" in blob or "etf" in blob:
        return (
            "Regulacje i rynek krypto w centrum dyskusji",
            "Temat na X dotyczy kryptowalut, ETF-ów, stablecoinów lub zmian regulacyjnych. Link prowadzi do bieżącej dyskusji wokół tego newsa.",
        )
    return (
        f"Temat z X: {label}",
        "Na X monitorowany jest konkretny news z zagranicznego źródła. W wersji polskiej nie pokazujemy angielskiego opisu, jeśli nie ma pełnego tłumaczenia.",
    )


def process() -> bool:
    if not PATH.exists():
        return False
    data = json.loads(PATH.read_text(encoding="utf-8"))
    changed = False
    for item in data.get("items", []) or []:
        title_en = clean(item.get("title_en") or "")
        if title_en in EXACT:
            title_pl, summary_pl = EXACT[title_en]
        else:
            title_pl = clean(item.get("title_pl") or "")
            summary_pl = clean(item.get("summary_pl") or "")
            if not title_pl or not summary_pl or EN_LEFT.search(title_pl + " " + summary_pl):
                title_pl, summary_pl = fallback(item)
        if item.get("title_pl") != title_pl:
            item["title_pl"] = title_pl
            changed = True
        if item.get("summary_pl") != summary_pl:
            item["summary_pl"] = summary_pl
            changed = True
        if item.get("comment_pl") != summary_pl:
            item["comment_pl"] = summary_pl
            changed = True
        item["pl_translation_status"] = "pl_checked_no_english_leak"
    data["method_pl"] = "Automatycznie dwa razy dziennie: wybiera konkretne posty i dyskusje z X oraz pokazuje tytuł i komentarz po polsku. W wersji PL nie wolno publikować angielskiego komentarza jako fallbacku."
    data["pl_language_gate"] = "Hot X PL: title_pl i summary_pl muszą być po polsku; angielski fallback jest blokowany."
    if changed:
        PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


if __name__ == "__main__":
    print("Hot X PL translated" if process() else "Hot X PL already clean")

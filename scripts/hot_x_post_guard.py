#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

PATH = Path("data/hot_tweets.json")
URL_RE = re.compile(r"https?://\S+", re.I)
WS_RE = re.compile(r"\s+")
GENERIC_RE = re.compile(r"rotating|rotacyjny|automatic|pełny wątek|temat dotyczy", re.I)


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    text = WS_RE.sub(" ", text).strip(" -–—")
    return text


def clip(text: str, n: int = 460) -> str:
    text = clean(text)
    if len(text) <= n:
        return text
    return text[: n - 1].rsplit(" ", 1)[0].rstrip(".,;: ") + "…"


def query(text: str) -> str:
    text = URL_RE.sub("", str(text or ""))
    text = text.replace("…", "")
    text = WS_RE.sub(" ", text).strip(" -–—")[:180]
    if " " in text and not text.startswith('"'):
        text = f'"{text}"'
    return text or "BriefRooms"


def x_search(text: str) -> str:
    return "https://x.com/search?q=" + urllib.parse.quote(query(text)) + "&src=typed_query&f=top"


def blob(item: dict[str, Any]) -> str:
    return " ".join(str(item.get(k, "")) for k in ("title_en", "summary_en", "category", "label_en", "x_query")).lower()


def pl_title(item: dict[str, Any]) -> str:
    b = blob(item)
    if "ecb" in b or "lagarde" in b or "euro" in b:
        return "ECB, Lagarde i stopy w strefie euro"
    if "china" in b or "tariff" in b or "trade imbalance" in b:
        return "UE, Chiny i presja handlowa"
    if "stablecoin" in b or "tokenization" in b or "bitwise" in b:
        return "Stablecoiny, tokenizacja i Bitcoin"
    if "openai" in b or "broadcom" in b or "chip" in b:
        return "OpenAI, Broadcom i chipy AI"
    if "opec" in b or "oil" in b or "crude" in b:
        return "OPEC, podaż ropy i ryzyko cen"
    if "cpi" in b or "inflation" in b or "jobs" in b or "dollar" in b:
        return "USA: CPI, rynek pracy i dolar"
    return str(item.get("title_pl") or item.get("title_en") or "Wątek na X")


def pl_summary(item: dict[str, Any], en: str) -> str:
    b = blob(item)
    if "ecb" in b or "lagarde" in b or "euro" in b:
        return "Na X warto szukać reakcji na wypowiedzi Lagarde i debatę o tym, czy odporność gospodarki strefy euro daje ECB więcej swobody w stopach. Kluczowe są komentarze o inflacji bazowej, rentownościach obligacji i kursie euro. Ten wątek może pokazać, czy rynek widzi przestrzeń do dalszego zacieśniania, czy raczej obawia się spowolnienia."
    if "china" in b or "tariff" in b or "trade imbalance" in b:
        return "Na X ten wątek powinien pokazywać spór o rosnącą nierównowagę handlową UE–Chiny. Najważniejsze są reakcje na możliwe cła, ograniczenia importu i odpowiedź Pekinu. To nie jest tylko polityka: takie decyzje mogą uderzać w auta, zielone technologie, łańcuchy dostaw i europejski przemysł."
    if "stablecoin" in b or "tokenization" in b or "bitwise" in b:
        return "Na X warto sprawdzić, czy doradcy finansowi i instytucje faktycznie przesuwają uwagę z samego Bitcoina na stablecoiny i tokenizację aktywów. Jeśli tak, rynek krypto może coraz bardziej iść w stronę infrastruktury płatniczej i finansów tradycyjnych, a nie tylko spekulacji ceną BTC."
    if "openai" in b or "broadcom" in b or "chip" in b:
        return "Na X warto szukać reakcji na potencjalne własne chipy AI OpenAI/Broadcom. Sedno sprawy: firmy AI chcą zmniejszyć zależność od dostawców mocy obliczeniowej i zabezpieczyć koszt trenowania modeli. Komentarze mogą pokazać, czy rynek traktuje to jako realne zagrożenie dla dominujących producentów GPU."
    if "opec" in b or "oil" in b or "crude" in b:
        return "Na X warto śledzić, czy rynek ropy bardziej wierzy w kontrolę podaży przez OPEC, czy w presję na niższe ceny przez spory wewnątrz kartelu. Kluczowe są reakcje traderów na cięcia/wzrost produkcji, zapasy i popyt z Chin. Ten wątek może szybko wpływać na paliwa, inflację i oczekiwania wobec banków centralnych."
    if "cpi" in b or "inflation" in b or "jobs" in b or "dollar" in b:
        return "Na X warto szukać reakcji na dane CPI po słabszym raporcie z rynku pracy. Najważniejsze pytanie: czy inflacja pozwoli Fedowi myśleć o łagodzeniu, czy znowu podbije rentowności i dolara. Dla rynku akcji istotne jest, czy rajd AI utrzyma się mimo danych makro."
    return clip(en, 420)


def process_item(item: dict[str, Any]) -> None:
    title = clean(item.get("title_en") or item.get("title_pl") or item.get("x_query") or "BriefRooms")
    en = re.sub(r"^Summary:\s*", "", clean(item.get("summary_en") or title), flags=re.I)
    if GENERIC_RE.search(en):
        en = title
    en = clip(en)
    item.pop("x_post_text", None)
    item.pop("x_post_text_raw", None)
    item["tweet_url"] = ""
    item["search_url"] = x_search(title)
    item["title_pl"] = pl_title(item)
    item["summary_en"] = "X angle: " + en
    item["summary_pl"] = pl_summary(item, en)
    item["source_en"] = "X — search"
    item["source_pl"] = "X — wyszukiwanie"
    item["hot_x_comment_mode"] = "x_search_summary"
    item["hot_x_source_rule"] = "search_url_is_x_link"


def main() -> None:
    if not PATH.exists():
        return
    data = json.loads(PATH.read_text(encoding="utf-8"))
    for item in data.get("items") or []:
        process_item(item)
    for key in ["x_api_diagnostics", "x_api_checked_at", "x_api_status"]:
        data.pop(key, None)
    data["mode"] = "x-search-with-useful-summary"
    data["method_pl"] = "Hot X: konkretny opis tego, czego szukać na X + czysty link do wyszukiwania."
    data["method_en"] = "Hot X: useful X-focused summary + clean X search link."
    data["hot_x_comment_policy"] = "useful_x_search_summary"
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Hot X useful X summaries applied")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hybrid PL news builder for BriefRooms.

Rule for Polish version:
- user-facing content must be Polish;
- Polish sources are rendered normally;
- English sources may be used only when their visible title and AI comment are translated/summarized in Polish;
- if OPENAI_API_KEY is not available or translation fails, English-language items are filtered out.

This wrapper reuses scripts/fetch_news_pl.py and patches only the language/translation layer,
so existing layout, scoring, filtering, hotbar and HTML generation stay intact.
"""

import json
import os
import re
import sys
from urllib.parse import urlparse

# Import the existing generator as a module.
# It is in the same directory when executed as: python scripts/fetch_news_pl_hybrid.py
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import fetch_news_pl as base  # noqa: E402

EN_HOST_RE = re.compile(
    r"(reuters\.com|bbc\.|apnews\.com|espn\.|atptour\.com|wtatennis\.com|fifa\.com|uefa\.com)",
    re.I,
)
PL_CHARS_RE = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
COMMON_EN_RE = re.compile(
    r"\b(the|and|with|after|before|over|under|from|for|to|of|in|on|as|by|will|is|are|was|were|has|have|says|said|new|world|cup|final|wins|beats|confirms|deal|minister|government|market|stocks)\b",
    re.I,
)
COMMON_PL_RE = re.compile(
    r"\b(i|oraz|że|się|jest|są|był|będzie|dla|przez|polska|polski|rząd|prezydent|minister|rynek|mecz|wygrywa)\b",
    re.I,
)

# --- Context classification for sensible Polish comments --------------------
MACRO_ECON_RE = re.compile(
    r"\b(inflacja|nbp|rpp|stopy procentowe|pkb|bezrobocie|płace|wynagrodzenia|ceny paliw|benzyn|diesl|energia|prąd|gaz|kredyt|raty|obligacj|deficyt|budżet|podat|zus|gospodark|handel|eksport|import|kurs walut|złoty|euro|dolar|giełd|wig|spółk|firm|rynek pracy)\b",
    re.I,
)
PUBLIC_PERSON_RE = re.compile(
    r"\b(oświadczen|majątek|emerytur|uposażen|pensj|wynagrodzen|dieta poselska|poseł|posłanka|senator|minister|prezydent|radny|radna|polityk|macierewicz|morawiecki|tusk|kaczyński|trzaskowski|nawrocki|duda)\b",
    re.I,
)
LOCAL_INCIDENT_RE = re.compile(
    r"\b(wypadek|zderzenie|kolizja|atak|incydent|zatrzyman|areszt|śledztw|prokuratur|policj|straż|sąd|wyrok|zarzut|sesji rady|rada miasta|hulajnod|autobus|pożar)\b",
    re.I,
)
PUBLIC_POLICY_RE = re.compile(
    r"\b(ustawa|projekt ustawy|rozporządzenie|sejm|senat|rząd|ministerstwo|budżet państwa|świadczenie|emerytury|waloryzacj|składk|program|dopłat|refundacj)\b",
    re.I,
)
GENERIC_MACRO_WHY_RE = re.compile(
    r"może mieć znaczenie dla cen, firm, rynku pracy albo decyzji finansowych gospodarstw domowych",
    re.I,
)
GENERIC_PUBLIC_WHY_RE = re.compile(
    r"wpływa na decyzje publiczne, bezpieczeństwo albo codzienne życie obywateli",
    re.I,
)


def context_why_it_matters_pl(section_key: str, title: str, snippet: str) -> str:
    """Dobiera sensowny komentarz do typu newsa. Nie używa ogólników makro do newsów indywidualnych."""
    text = f"{title} {snippet}"

    if section_key == "sport":
        return _original_why_it_matters_pl(section_key, title, snippet)

    if PUBLIC_PERSON_RE.search(text):
        return (
            "To ważne z perspektywy przejrzystości życia publicznego: pokazuje dochody, majątek albo decyzje osób pełniących funkcje publiczne. "
            "Nie jest to jednak samo w sobie sygnał makroekonomiczny."
        )

    if LOCAL_INCIDENT_RE.search(text):
        return (
            "To informacja lokalna lub społeczna: jej znaczenie dotyczy bezpieczeństwa, odpowiedzialności instytucji albo reakcji władz. "
            "Nie należy jej automatycznie łączyć z cenami, firmami ani rynkiem pracy."
        )

    if PUBLIC_POLICY_RE.search(text):
        return (
            "To ważne, bo dotyczy decyzji publicznych, przepisów albo działań instytucji państwa. "
            "Takie sprawy mogą wpływać na obywateli, budżet lub sposób działania administracji."
        )

    if section_key == "biznes" and MACRO_ECON_RE.search(text):
        return (
            "To ważne gospodarczo, bo może wpływać na koszty życia, decyzje firm, raty kredytów, ceny energii, paliw albo nastroje na rynku."
        )

    if section_key == "biznes":
        return (
            "To ważne dla czytelników gospodarczych, jeśli pokazuje decyzje firm, regulacje, finanse publiczne albo zachowania konsumentów. "
            "Komentarz powinien wynikać z treści newsa, a nie z automatycznego schematu makro."
        )

    if section_key == "polityka":
        return (
            "To ważne jako element obserwacji życia publicznego: pokazuje decyzje, konflikty, działania instytucji albo zachowanie osób publicznych. "
            "Znaczenie tej informacji zależy od dalszego kontekstu i źródła."
        )

    return (
        "To ważne, jeśli pomaga zrozumieć bieżący kontekst wydarzenia i wskazuje, co warto sprawdzić w źródle. "
        "Komentarz nie powinien dopisywać wpływu makro, jeśli nie wynika on z treści newsa."
    )


def likely_english_item(item: dict) -> bool:
    """Conservative language/source detection for PL page hygiene."""
    title = item.get("title", "") or ""
    snippet = item.get("summary_raw", "") or ""
    link = item.get("link", "") or ""
    text = f"{title} {snippet}"
    host = urlparse(link).netloc.lower()

    if EN_HOST_RE.search(host):
        return True
    if PL_CHARS_RE.search(text):
        return False
    en_hits = len(COMMON_EN_RE.findall(text))
    pl_hits = len(COMMON_PL_RE.findall(text))
    # If there are clear English markers and no Polish markers, treat as English.
    return en_hits >= 2 and pl_hits == 0


def still_looks_english(text: str) -> bool:
    """Reject AI output that still looks like an English headline/comment."""
    t = (text or "").strip()
    if not t:
        return True
    if PL_CHARS_RE.search(t):
        return False
    en_hits = len(COMMON_EN_RE.findall(t))
    pl_hits = len(COMMON_PL_RE.findall(t))
    return en_hits >= 2 and pl_hits == 0


def translate_english_item_to_polish(item: dict, section_key: str) -> dict | None:
    """Return Polish user-facing fields for an English source item, or None if unsafe."""
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None

    title = item.get("title", "") or ""
    snippet = item.get("summary_raw", "") or ""
    source = item.get("source_name", "") or "źródło"

    cache_key = f"hybrid-pl-v2|{base.norm_title(title)}|{base.today_str()}"
    if cache_key in base.CACHE:
        cached = base.CACHE[cache_key]
        if cached.get("title_pl") and cached.get("summary") and cached.get("why"):
            cached["why"] = context_why_it_matters_pl(section_key, cached.get("title_pl", title), snippet)
            return cached

    prompt = f"""Przetłumacz i opracuj po polsku anglojęzyczny news do polskiej wersji BriefRooms.
Zwróć wyłącznie poprawny JSON bez Markdown, w formacie:
{{
  "title_pl": "krótki tytuł po polsku, maksymalnie 110 znaków",
  "summary": "Najważniejsze: jedno lub dwa krótkie zdania po polsku z sednem informacji",
  "why": "Dlaczego to ważne: dwa krótkie zdania po polsku z kontekstem i konsekwencją",
  "uncertain": "opcjonalna krótka uwaga po polsku albo pusty string"
}}

Zasady:
- Wszystko, co zobaczy użytkownik, musi być po polsku.
- Nie zostawiaj angielskiego tytułu.
- Nie dopisuj faktów spoza tytułu i opisu RSS.
- Zachowaj neutralny, rzeczowy ton.
- Jeśli opis RSS jest krótki, nie zmyślaj szczegółów.
- Nie używaj ogólnika makroekonomicznego przy newsie o jednej osobie, lokalnym incydencie albo sporcie.
- Jeśli news dotyczy osoby publicznej i jej majątku/dochodów, znaczenie opisz przez przejrzystość życia publicznego, nie przez ceny albo rynek pracy.

Sekcja: {section_key}
Źródło: {source}
Tytuł oryginalny: {title}
Opis RSS: {snippet}
"""

    try:
        resp = base.requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": base.AI_MODEL,
                "messages": [
                    {"role": "system", "content": "Jesteś polskim redaktorem newsowym. Zwracasz wyłącznie poprawny JSON i dobierasz komentarz do typu newsa."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.12,
                "max_tokens": 460,
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
        data = json.loads(raw)

        title_pl = base.ensure_full_sentence(str(data.get("title_pl", "")).strip(), 130)
        # Headline should not necessarily end with a period.
        title_pl = title_pl.rstrip(".")
        summary = base.ensure_full_sentence(str(data.get("summary", "")).replace("Najważniejsze:", "").strip(), 360)
        why_ai = base.ensure_full_sentence(str(data.get("why", "")).replace("Dlaczego to ważne:", "").strip(), 320)
        why_rule = context_why_it_matters_pl(section_key, title_pl or title, snippet)
        if GENERIC_MACRO_WHY_RE.search(why_ai) or GENERIC_PUBLIC_WHY_RE.search(why_ai):
            why = why_rule
        else:
            why = why_ai or why_rule
        uncertain = base.ensure_period(str(data.get("uncertain", "")).strip()) if data.get("uncertain") else ""

        if still_looks_english(title_pl) or still_looks_english(summary) or still_looks_english(why):
            return None

        out = {
            "title_pl": title_pl,
            "summary": base.ensure_period(summary),
            "why": base.ensure_period(why),
            "uncertain": uncertain,
            "model": f"{base.AI_MODEL}-hybrid-pl",
        }
        base.CACHE[cache_key] = out
        base.save_cache(base.AI_CACHE_PATH, base.CACHE)
        return out
    except Exception as ex:
        print(f"[WARN] hybrid PL translation failed: {source} | {title[:80]} -> {ex}", file=sys.stderr)
        return None


_original_source_badge_for = base.source_badge_for


def source_badge_for_hybrid(source: str) -> str:
    """Keep small source badges short even when source line contains the hybrid note."""
    return _original_source_badge_for((source or "").split(" · ", 1)[0])


_original_why_it_matters_pl = base.why_it_matters_pl
_original_ai_summarize_pl = base.ai_summarize_pl
_original_fetch_section = base.fetch_section


def why_it_matters_pl_hybrid(section_key: str, title: str, snippet: str) -> str:
    return context_why_it_matters_pl(section_key, title, snippet)


def ai_summarize_pl_hybrid(title: str, snippet: str, url: str, section_key: str = "") -> dict:
    """Post-process summaries so cached/AI fallback comments do not use irrelevant macro boilerplate."""
    out = _original_ai_summarize_pl(title, snippet, url, section_key)
    if not isinstance(out, dict):
        out = {}
    why = out.get("why", "") or ""
    rule_why = context_why_it_matters_pl(section_key, title, snippet)
    text = f"{title} {snippet}"
    mismatch = False

    if GENERIC_MACRO_WHY_RE.search(why):
        mismatch = True
    if GENERIC_PUBLIC_WHY_RE.search(why):
        mismatch = True
    if PUBLIC_PERSON_RE.search(text) and GENERIC_MACRO_WHY_RE.search(why):
        mismatch = True
    if LOCAL_INCIDENT_RE.search(text) and GENERIC_MACRO_WHY_RE.search(why):
        mismatch = True
    if not why.strip():
        mismatch = True

    if mismatch:
        out["why"] = base.ensure_period(rule_why)
        out["model"] = (out.get("model") or "") + "+context-guard"
    return out


def fetch_section_hybrid(section_key: str):
    items = _original_fetch_section(section_key)
    out = []
    for it in items:
        if not likely_english_item(it):
            out.append(it)
            continue

        translated = translate_english_item_to_polish(it, section_key)
        if not translated:
            # PL page must not show English user-facing content.
            continue

        original_source = it.get("source_name", "Źródło")
        it["title"] = translated["title_pl"]
        it["ai_summary"] = translated["summary"]
        it["ai_why"] = translated["why"]
        if translated.get("uncertain"):
            it["ai_uncertain"] = translated["uncertain"]
        it["ai_model"] = translated.get("model", "")
        it["source_name"] = f"{original_source} · Źródło anglojęzyczne — brief po polsku"
        out.append(it)

    return out


# Patch the base module and reuse its main().
base.source_badge_for = source_badge_for_hybrid
base.why_it_matters_pl = why_it_matters_pl_hybrid
base.ai_summarize_pl = ai_summarize_pl_hybrid
base.fetch_section = fetch_section_hybrid

if __name__ == "__main__":
    base.main()

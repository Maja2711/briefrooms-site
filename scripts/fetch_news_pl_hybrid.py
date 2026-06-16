#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hybrid PL news builder for BriefRooms.

Rules for Polish version:
- user-facing content must be Polish;
- Polish sources are rendered normally;
- English sources may be used only when their visible title and AI comment are translated/summarized in Polish;
- if OPENAI_API_KEY is not available or translation fails, English-language items are filtered out;
- "Dlaczego to ważne" must be contextual, not a repeated generic slogan.
"""

import json
import os
import re
import sys
from urllib.parse import urlparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import fetch_news_pl as base  # noqa: E402

EN_HOST_RE = re.compile(
    r"(reuters\.com|bbc\.|apnews\.com|espn\.|atptour\.com|wtatennis\.com|fifa\.com|uefa\.com|bloomberg\.com|theguardian\.com|nytimes\.com|cnbc\.com|nasa\.gov|esa\.int|who\.int)",
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

MACRO_ECON_RE = re.compile(
    r"\b(inflacja|nbp|rpp|stopy procentowe|pkb|bezrobocie|płace|wynagrodzenia|ceny paliw|benzyn|diesl|energia|prąd|gaz|kredyt|raty|obligacj|deficyt|budżet|podat|zus|gospodark|handel|eksport|import|kurs walut|złoty|euro|dolar|giełd|wig|spółk|firm|rynek pracy|sprzedaż detaliczna|produkcja przemysłowa)\b",
    re.I,
)
FUEL_ENERGY_RE = re.compile(r"\b(paliw|benzyn|diesl|ropa|gaz|prąd|energia|ceny maksymalne|taryf)\b", re.I)
PUBLIC_PERSON_RE = re.compile(
    r"\b(oświadczen|majątek|emerytur|uposażen|pensj|wynagrodzen|dieta poselska|poseł|posłanka|senator|minister|prezydent|radny|radna|polityk|macierewicz|morawiecki|tusk|kaczyński|trzaskowski|nawrocki|duda)\b",
    re.I,
)
LOCAL_INCIDENT_RE = re.compile(
    r"\b(wypadek|zderzenie|kolizja|atak|incydent|zatrzyman|areszt|śledztw|prokuratur|policj|straż|sąd|wyrok|zarzut|sesji rady|rada miasta|hulajnod|autobus|pożar|napad|awaria)\b",
    re.I,
)
PUBLIC_POLICY_RE = re.compile(
    r"\b(ustawa|projekt ustawy|rozporządzenie|sejm|senat|rząd|ministerstwo|budżet państwa|świadczenie|emerytury|waloryzacj|składk|program|dopłat|refundacj|limity|zakaz|regulacj)\b",
    re.I,
)
HEALTH_RE = re.compile(r"\b(zdrow|szpital|lekarz|pacjent|chorob|zakaż|szczep|lek|nfz|who|epidem|profilaktyk)\b", re.I)
SCIENCE_RE = re.compile(r"\b(nauk|badani|kosmos|nasa|esa|planeta|galakty|technolog|ai|sztuczna inteligencja|odkryc)\b", re.I)
SPORT_RE = re.compile(r"\b(mecz|wynik|liga|turniej|siatkar|piłkar|tenis|finał|mundial|sport|bramka|wygral|wygrała|pokonał)\b", re.I)

GENERIC_BAD_WHY_RE = re.compile(
    r"(może mieć znaczenie dla cen, firm, rynku pracy albo decyzji finansowych gospodarstw domowych|wpływa na decyzje publiczne, bezpieczeństwo albo codzienne życie obywateli|to istotne, bo wpływa na decyzje publiczne)",
    re.I,
)


def _clip_sentence(text: str, limit: int = 330) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return base.ensure_period(text)
    cut = text[:limit].rsplit(" ", 1)[0]
    return base.ensure_period(cut)


def context_why_it_matters_pl(section_key: str, title: str, snippet: str) -> str:
    """Zwraca konkretny, kontekstowy komentarz. Bez pustych sloganów."""
    text = f"{title} {snippet}"

    if section_key == "sport" or SPORT_RE.search(text):
        return (
            "To jest informacja wynikowa: jej znaczenie zależy od tabeli, formy drużyny albo kontekstu turnieju. "
            "Warto sprawdzić w źródle, czy wynik zmienia układ rywalizacji, awans lub presję na kolejny mecz."
        )

    if PUBLIC_PERSON_RE.search(text):
        return (
            "Ten news jest ważny nie jako sygnał gospodarczy, lecz jako element kontroli życia publicznego. "
            "Pokazuje, jakie dochody, majątek albo przywileje mają osoby pełniące funkcje publiczne i czy odpowiada to oczekiwaniom przejrzystości."
        )

    if LOCAL_INCIDENT_RE.search(text):
        return (
            "Znaczenie tej informacji jest lokalne i instytucjonalne: chodzi o bezpieczeństwo, procedury oraz reakcję służb lub władz. "
            "Najważniejsze pytanie brzmi, czy zdarzenie jest odosobnione, czy pokazuje szerszy problem organizacyjny."
        )

    if FUEL_ENERGY_RE.search(text):
        return (
            "To ma bezpośrednie znaczenie dla codziennych kosztów, bo paliwa i energia szybko przenoszą się na budżety domowe oraz koszty transportu. "
            "Warto patrzeć nie tylko na samą cenę, ale też na to, czy zmiana jest jednorazowa, czy zaczyna trwalszy trend."
        )

    if section_key == "biznes" and MACRO_ECON_RE.search(text):
        return (
            "To jest sygnał makroekonomiczny: może zmieniać ocenę inflacji, popytu, kosztów firm albo przyszłych decyzji banku centralnego. "
            "Kluczowe jest porównanie danych z oczekiwaniami rynku, a nie sama pojedyncza liczba."
        )

    if PUBLIC_POLICY_RE.search(text):
        return (
            "To ważne, bo za decyzją publiczną zwykle idą realne koszty, obowiązki albo prawa obywateli. "
            "Dobra ocena wymaga sprawdzenia, kogo zmiana obejmie, kiedy wejdzie w życie i kto za nią zapłaci."
        )

    if HEALTH_RE.search(text):
        return (
            "W zdrowiu publicznym znaczenie newsa zależy od skali zjawiska i rekomendacji instytucji, nie od samego nagłówka. "
            "Warto sprawdzić, czy informacja zmienia zalecenia dla pacjentów, lekarzy albo systemu ochrony zdrowia."
        )

    if SCIENCE_RE.search(text):
        return (
            "To ważne, jeśli odkrycie lub badanie zmienia dotychczasowe rozumienie problemu, a nie tylko brzmi efektownie w nagłówku. "
            "Najlepiej oceniać je przez metodę, źródło i to, czy wyniki zostały potwierdzone niezależnie."
        )

    if section_key == "polityka":
        return (
            "Znaczenie tej informacji polega na tym, czy odsłania mechanizm działania instytucji, konflikt interesów albo zmianę układu politycznego. "
            "Sam fakt jest punktem wyjścia; ważniejsze jest, jakie decyzje lub konsekwencje może uruchomić."
        )

    if section_key == "biznes":
        return (
            "To warto śledzić, jeśli pokazuje zmianę w zachowaniu firm, konsumentów albo regulatora. "
            "Najważniejsze jest oddzielenie pojedynczej ciekawostki od informacji, która może zmienić decyzje rynku."
        )

    return (
        "To jest krótki sygnał informacyjny: jego wartość zależy od tego, czy pomaga zrozumieć większy proces, czy jest tylko pojedynczym zdarzeniem. "
        "Warto wejść do źródła i sprawdzić szczegóły, zanim wyciągnie się szersze wnioski."
    )


def likely_english_item(item: dict) -> bool:
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
    return en_hits >= 2 and pl_hits == 0


def still_looks_english(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if PL_CHARS_RE.search(t):
        return False
    en_hits = len(COMMON_EN_RE.findall(t))
    pl_hits = len(COMMON_PL_RE.findall(t))
    return en_hits >= 2 and pl_hits == 0


def translate_english_item_to_polish(item: dict, section_key: str) -> dict | None:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None

    title = item.get("title", "") or ""
    snippet = item.get("summary_raw", "") or ""
    source = item.get("source_name", "") or "źródło"

    cache_key = f"hybrid-pl-v3|{base.norm_title(title)}|{base.today_str()}"
    if cache_key in base.CACHE:
        cached = base.CACHE[cache_key]
        if cached.get("title_pl") and cached.get("summary"):
            cached["why"] = context_why_it_matters_pl(section_key, cached.get("title_pl", title), snippet)
            return cached

    prompt = f"""Przetłumacz i opracuj po polsku anglojęzyczny news do polskiej wersji BriefRooms.
Zwróć wyłącznie poprawny JSON bez Markdown:
{{
  "title_pl": "krótki tytuł po polsku, maksymalnie 110 znaków",
  "summary": "jedno lub dwa krótkie zdania po polsku z sednem informacji",
  "why": "jedno lub dwa krótkie zdania po polsku z konkretnym kontekstem, bez ogólników",
  "uncertain": "opcjonalna krótka uwaga po polsku albo pusty string"
}}

Zasady:
- Wszystko, co zobaczy użytkownik, musi być po polsku.
- Nie zostawiaj angielskiego tytułu.
- Nie dopisuj faktów spoza tytułu i opisu RSS.
- Nie używaj pustych sloganów typu: wpływa na decyzje publiczne, bezpieczeństwo albo codzienne życie obywateli.
- Jeśli news dotyczy osoby publicznej i jej majątku/dochodów, znaczenie opisz przez przejrzystość życia publicznego.
- Jeśli news jest lokalnym incydentem, opisz znaczenie przez procedury, bezpieczeństwo i reakcję instytucji.

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
                    {"role": "system", "content": "Jesteś polskim redaktorem newsowym. Zwracasz wyłącznie poprawny JSON. Komentarz musi być konkretny dla danego newsa."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 520,
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
        data = json.loads(raw)

        title_pl = base.ensure_full_sentence(str(data.get("title_pl", "")).strip(), 130).rstrip(".")
        summary = base.ensure_full_sentence(str(data.get("summary", "")).replace("Najważniejsze:", "").strip(), 360)
        why_ai = base.ensure_full_sentence(str(data.get("why", "")).replace("Dlaczego to ważne:", "").strip(), 340)
        why_rule = context_why_it_matters_pl(section_key, title_pl or title, snippet)
        why = why_rule if (not why_ai or GENERIC_BAD_WHY_RE.search(why_ai)) else why_ai
        uncertain = base.ensure_period(str(data.get("uncertain", "")).strip()) if data.get("uncertain") else ""

        if still_looks_english(title_pl) or still_looks_english(summary) or still_looks_english(why):
            return None

        out = {
            "title_pl": title_pl,
            "summary": base.ensure_period(summary),
            "why": _clip_sentence(why, 340),
            "uncertain": uncertain,
            "model": f"{base.AI_MODEL}-hybrid-pl-v3",
        }
        base.CACHE[cache_key] = out
        base.save_cache(base.AI_CACHE_PATH, base.CACHE)
        return out
    except Exception as ex:
        print(f"[WARN] hybrid PL translation failed: {source} | {title[:80]} -> {ex}", file=sys.stderr)
        return None


_original_source_badge_for = base.source_badge_for
_original_ai_summarize_pl = base.ai_summarize_pl
_original_fetch_section = base.fetch_section


def source_badge_for_hybrid(source: str) -> str:
    return _original_source_badge_for((source or "").split(" · ", 1)[0])


def ai_summarize_pl_hybrid(title: str, snippet: str, url: str, section_key: str = "") -> dict:
    out = _original_ai_summarize_pl(title, snippet, url, section_key)
    if not isinstance(out, dict):
        out = {}

    summary = (out.get("summary") or "").replace("Najważniejsze:", "").strip()
    out["summary"] = base.ensure_period(summary) if summary else base.ensure_period(base.ensure_full_sentence(snippet or title, 300))

    # Always replace the old generic/fallback WHY with our contextual editor note.
    out["why"] = _clip_sentence(context_why_it_matters_pl(section_key, title, snippet), 340)
    out["model"] = (out.get("model") or "") + "+contextual-why-v3"
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


base.source_badge_for = source_badge_for_hybrid
base.ai_summarize_pl = ai_summarize_pl_hybrid
base.fetch_section = fetch_section_hybrid

if __name__ == "__main__":
    base.main()

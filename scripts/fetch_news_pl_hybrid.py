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
SPORT_RE = re.compile(r"\b(mecz|wynik|liga|turniej|siatkar|piłkar|tenis|finał|mundial|sport|bramka|wygral|wygrała|wygrał|pokonał|pokonała|awans|runda|set|gem)\b", re.I)

ROUND_RE = re.compile(
    r"\b(finał|półfinał|ćwierćfinał|1/8 finału|1/16 finału|pierwsza runda|druga runda|trzecia runda|czwarta runda|runda grupowa|mecz grupowy|baraż|kwalifikacj[aei]|eliminacj[aei]|play-off|playoff)\b",
    re.I,
)
RESULT_RE = re.compile(r"\b\d{1,2}\s*[:–-]\s*\d{1,2}\b|\b\d{1,2}\s*:\s*\d{1,2}\b")
SET_RESULT_RE = re.compile(r"\b\d{1,2}:\d{1,2},\s*\d{1,2}:\d{1,2}(?:,\s*\d{1,2}:\d{1,2})?\b")

GENERIC_BAD_WHY_RE = re.compile(
    r"(może mieć znaczenie dla cen, firm, rynku pracy albo decyzji finansowych gospodarstw domowych|wpływa na decyzje publiczne, bezpieczeństwo albo codzienne życie obywateli|to istotne, bo wpływa na decyzje publiczne|warto sprawdzić w źródle|warto wejść do źródła|znaczenie zależy od tabeli, formy drużyny albo kontekstu turnieju|sam fakt jest punktem wyjścia|jego wartość zależy od tego)",
    re.I,
)


def _clip_sentence(text: str, limit: int = 330) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return base.ensure_period(text)
    cut = text[:limit].rsplit(" ", 1)[0]
    return base.ensure_period(cut)


def _first_match(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text or "")
    return m.group(0).strip() if m else ""


def _extract_competition(text: str) -> str:
    patterns = [
        r"\b(Liga Narodów|Wimbledon|Roland Garros|US Open|Australian Open|Liga Mistrzów|Ekstraklasa|Mundial|Euro|Puchar Polski|Tour de France)\b",
        r"\b([A-ZŁŚŻŹĆŃÓ][\wąćęłńóśźż-]+(?:\s+[A-ZŁŚŻŹĆŃÓ][\wąćęłńóśźż-]+){0,3})\b",
    ]
    for pat in patterns:
        m = re.search(pat, text or "")
        if m:
            val = m.group(1).strip()
            if len(val) > 2 and val.lower() not in {"to", "dlaczego", "najważniejsze"}:
                return val
    return ""


def sport_context_why_pl(title: str, snippet: str) -> str:
    """Wyciąga możliwie konkretny sens sportowego newsa z tytułu i opisu RSS."""
    text = re.sub(r"\s+", " ", f"{title} {snippet}".strip())
    round_name = _first_match(ROUND_RE, text)
    score = _first_match(SET_RESULT_RE, text) or _first_match(RESULT_RE, text)
    competition = _extract_competition(text)

    details = []
    if round_name:
        details.append(f"etap: {round_name}")
    if score:
        details.append(f"wynik: {score}")
    if competition:
        details.append(f"kontekst: {competition}")

    if details:
        return (
            "Sedno sportowe: " + ", ".join(details) + ". "
            "To pozwala od razu ocenić, czy news dotyczy awansu, odpadnięcia, zmiany presji przed kolejnym spotkaniem albo potwierdzenia aktualnej formy."
        )

    # Bez halucynowania: gdy RSS nie zawiera rundy/wyniku, mówimy konkretnie, czego brakuje, zamiast odsyłać czytelnika.
    return (
        "Sedno sportowe trzeba czytać przez fakty z nagłówka i krótkiego opisu: kto grał, jaki był wynik i na jakim etapie rywalizacji. "
        "Jeśli RSS nie podaje rundy lub rezultatu, brief nie powinien ich dopowiadać na siłę."
    )


def context_why_it_matters_pl(section_key: str, title: str, snippet: str) -> str:
    """Zwraca konkretny, kontekstowy komentarz. Bez pustych sloganów i bez odsyłania do źródła."""
    text = f"{title} {snippet}"

    if section_key == "sport":
        return sport_context_why_pl(title, snippet)

    if section_key == "zdrowie":
        return (
            "W zdrowiu najważniejsze są skala zjawiska, grupa ryzyka i to, czy informacja zmienia realne zalecenia dla pacjentów lub lekarzy. "
            "Komentarz powinien oddzielać ostrzeżenie systemowe od pojedynczego przypadku."
        )

    if section_key == "nauka":
        return (
            "W newsie naukowym liczy się nie efektowność nagłówka, tylko metoda, źródło danych i stopień potwierdzenia wyniku. "
            "Dobra esencja powinna wskazać, co odkrycie zmienia w rozumieniu tematu i czego jeszcze nie przesądza."
        )

    if SPORT_RE.search(text):
        return sport_context_why_pl(title, snippet)

    if PUBLIC_PERSON_RE.search(text):
        return (
            "Sedno sprawy dotyczy przejrzystości życia publicznego: czy ujawnione dochody, majątek lub przywileje osoby publicznej są proporcjonalne do pełnionej funkcji i jasne dla obywateli. "
            "To nie jest sygnał makroekonomiczny, tylko test zaufania do standardów jawności."
        )

    if LOCAL_INCIDENT_RE.search(text):
        return (
            "Najważniejszy kontekst to bezpieczeństwo i odpowiedzialność instytucji: kto zareagował, jakie procedury zadziałały i czy zdarzenie wskazuje na szerszą lukę organizacyjną. "
            "Taki news warto oceniać przez skutki dla mieszkańców, a nie przez ogólne hasła gospodarcze."
        )

    if FUEL_ENERGY_RE.search(text):
        return (
            "Tu kluczowy jest kanał kosztowy: paliwa i energia szybko wpływają na rachunki domowe, transport oraz marże firm. "
            "Najważniejsze jest, czy opisywana zmiana wygląda na jednorazowy skok, czy początek trwalszego trendu cenowego."
        )

    if section_key == "biznes" and MACRO_ECON_RE.search(text):
        return (
            "To jest informacja makro: trzeba ją czytać przez wpływ na inflację, popyt, koszty firm i możliwe decyzje banku centralnego. "
            "Największe znaczenie ma różnica między danymi a oczekiwaniami, bo to ona zwykle porusza rynek."
        )

    if PUBLIC_POLICY_RE.search(text):
        return (
            "Sedno leży w praktycznych skutkach decyzji publicznej: kogo obejmuje zmiana, od kiedy działa i kto ponosi jej koszt. "
            "Dobre podsumowanie powinno pokazać mechanizm, a nie tylko sam fakt przyjęcia lub zapowiedzi regulacji."
        )

    if HEALTH_RE.search(text):
        return (
            "W zdrowiu najważniejsze są skala zjawiska, grupa ryzyka i to, czy informacja zmienia realne zalecenia dla pacjentów lub lekarzy. "
            "Komentarz powinien oddzielać ostrzeżenie systemowe od pojedynczego przypadku."
        )

    if SCIENCE_RE.search(text):
        return (
            "W newsie naukowym liczy się nie efektowność nagłówka, tylko metoda, źródło danych i stopień potwierdzenia wyniku. "
            "Dobra esencja powinna wskazać, co odkrycie zmienia w rozumieniu tematu i czego jeszcze nie przesądza."
        )

    if section_key == "polityka":
        return (
            "Najważniejsze jest to, jaki mechanizm polityczny odsłania news: zmianę układu sił, konflikt interesów, decyzję instytucji albo problem odpowiedzialności. "
            "Komentarz powinien pokazać możliwą konsekwencję, a nie powtarzać sam nagłówek."
        )

    if section_key == "biznes":
        return (
            "W biznesie znaczenie newsa zależy od tego, czy pokazuje zmianę zachowania firm, klientów, regulatora lub kosztów działalności. "
            "Esencją jest wskazanie mechanizmu: co konkretnie może zmienić decyzje uczestników rynku."
        )

    return (
        "Esencja powinna wynikać z samego newsa: kto podjął decyzję, kogo ona dotyczy i jaka jest bezpośrednia konsekwencja. "
        "Jeśli z tytułu i opisu nie da się tego ustalić, komentarz powinien być ostrożny i nie dopisywać sztucznego znaczenia."
    )


def why_is_useful(why: str) -> bool:
    why = re.sub(r"\s+", " ", (why or "").strip())
    if len(why) < 55:
        return False
    if GENERIC_BAD_WHY_RE.search(why):
        return False
    return True


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
            if not why_is_useful(cached.get("why", "")):
                cached["why"] = context_why_it_matters_pl(section_key, cached.get("title_pl", title), snippet)
            return cached

    prompt = f"""Przetłumacz i opracuj po polsku anglojęzyczny news do polskiej wersji BriefRooms.
Zwróć wyłącznie poprawny JSON bez Markdown:
{{
  "title_pl": "krótki tytuł po polsku, maksymalnie 110 znaków",
  "summary": "jedno lub dwa krótkie zdania po polsku z sednem informacji",
  "why": "jedno lub dwa krótkie zdania po polsku z konkretną esencją: co z tego wynika, dla kogo i na jakim etapie sprawy. Nie odsyłaj do źródła.",
  "uncertain": "opcjonalna krótka uwaga po polsku albo pusty string"
}}

Zasady:
- Wszystko, co zobaczy użytkownik, musi być po polsku.
- Nie zostawiaj angielskiego tytułu.
- Nie dopisuj faktów spoza tytułu i opisu RSS.
- Komentarz 'Dlaczego to ważne' ma być esencją artykułu, nie instrukcją typu 'sprawdź w źródle'.
- Nie używaj pustych sloganów typu: wpływa na decyzje publiczne, bezpieczeństwo albo codzienne życie obywateli.
- Jeśli news sportowy zawiera rundę, wynik, etap turnieju, zawodnika lub drużynę — użyj tych konkretów w komentarzu.
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
                    {"role": "system", "content": "Jesteś polskim redaktorem newsowym. Zwracasz wyłącznie poprawny JSON. Komentarz musi podawać esencję newsa, a nie odsyłać czytelnika do źródła."},
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
        why = why_ai if why_is_useful(why_ai) else why_rule
        uncertain = base.ensure_period(str(data.get("uncertain", "")).strip()) if data.get("uncertain") else ""

        if still_looks_english(title_pl) or still_looks_english(summary) or still_looks_english(why):
            return None

        out = {
            "title_pl": title_pl,
            "summary": base.ensure_period(summary),
            "why": _clip_sentence(why, 340),
            "uncertain": uncertain,
            "model": f"{base.AI_MODEL}-hybrid-pl-v4",
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

    why_ai = (out.get("why") or "").replace("Dlaczego to ważne:", "").strip()
    why_rule = context_why_it_matters_pl(section_key, title, snippet)
    out["why"] = _clip_sentence(why_ai if why_is_useful(why_ai) else why_rule, 340)
    out["model"] = (out.get("model") or "") + "+contextual-why-v4"
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

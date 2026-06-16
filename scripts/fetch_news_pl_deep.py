#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deep-context wrapper for the PL BriefRooms news builder.

Goals:
- keep the hybrid PL model from fetch_news_pl_hybrid.py;
- remove low-value weather items;
- reduce overuse of TVN24/Eurosport TVN24;
- show "Dlaczego to ważne" only when it adds concrete value;
- never write vague ranking/prestige/public-life comments when the RSS text does not contain a real consequence.
"""

import re
import sys

import fetch_news_pl_hybrid as hybrid


GENERIC_SPORT_BAD_RE = re.compile(
    r"(może wpływać na prestiż, ranking, awans albo pozycję|prestiż, ranking, awans|warto sprawdzić|zależy od tabeli|potwierdzenia aktualnej formy|presji przed kolejnym spotkaniem|sedno sportowe trzeba czytać|z dostępnego opisu wynika przede wszystkim etap)",
    re.I,
)

LOW_VALUE_WHY_RE = re.compile(
    r"(przejrzystości życia publicznego|test zaufania do standardów jawności|decyzje publiczne, bezpieczeństwo albo codzienne życie obywateli|znaczenie tej informacji zależy od dalszego kontekstu|warto sprawdzić w źródle|nie jest to jednak samo w sobie sygnał makroekonomiczny)",
    re.I,
)

WEATHER_RE = re.compile(
    r"\b(pogod|burz|burza|ulew|deszcz|wiatr|wichur|grad|upał|temperatur|prognoz|IMGW|meteop|alert pogod|wyładowania atmosferyczne|śnieg|mróz)\b",
    re.I,
)

TENNIS_RE = re.compile(r"\b(tenis|tenisista|tenisistka|ATP|WTA|set|gem|runda|turniej|finał|półfinał|ćwierćfinał|ranking|Top\s*100|top\s*100)\b", re.I)
ATP_RANK_RE = re.compile(r"\b(ATP|ranking(?:u)? ATP|WTA|ranking(?:u)? WTA|Top\s*100|top\s*100|pierwszej setki|setki rankingu|awans(?:uje|ował|owała)?(?: do)?\s+\d{1,3}\.?\s*miejsca|\d{1,3}\.?\s*miejsce(?: w rankingu)?)\b", re.I)
PLAYER_RE = re.compile(
    r"\b([A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż-]+\s+[A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż-]+)\b"
)


# Save already patched functions from hybrid/base.
_original_fetch_section_deep = hybrid.base.fetch_section
_original_render_html_deep = hybrid.base.render_html


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _sentence(text: str) -> str:
    return hybrid.base.ensure_period(_clean(text))


def _first(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text or "")
    return m.group(0).strip() if m else ""


def _player(text: str) -> str:
    skip = {"Liga Narodów", "Roland Garros", "Australian Open", "US Open", "Puchar Polski", "BriefRooms"}
    for m in PLAYER_RE.finditer(text or ""):
        val = m.group(1).strip()
        if val not in skip and len(val) > 5:
            return val
    return ""


def is_weather_item(title: str, snippet: str) -> bool:
    return bool(WEATHER_RE.search(f"{title} {snippet}"))


def is_tvn24_item(item: dict) -> bool:
    src = item.get("source_name", "") or ""
    link = item.get("link", "") or ""
    return "TVN24" in src or "tvn24.pl" in link


def deep_sport_context_why_pl(title: str, snippet: str) -> str:
    """Build a concrete sports comment from visible RSS data only. Empty string means: omit the line."""
    text = _clean(f"{title} {snippet}")
    round_name = hybrid._first_match(hybrid.ROUND_RE, text)
    score = hybrid._first_match(hybrid.SET_RESULT_RE, text) or hybrid._first_match(hybrid.RESULT_RE, text)
    competition = hybrid._extract_competition(text)
    player = _player(text)
    rank_info = _first(ATP_RANK_RE, text)

    if TENNIS_RE.search(text):
        facts = []
        if player:
            facts.append(f"zawodnik: {player}")
        if round_name:
            facts.append(f"etap: {round_name}")
        if score:
            facts.append(f"wynik: {score}")
        if rank_info:
            facts.append(f"ranking: {rank_info}")
        if competition:
            facts.append(f"turniej: {competition}")

        if rank_info:
            return _sentence(
                "Tenisowy konkret: " + ", ".join(facts) + ". "
                "Najważniejsza konsekwencja z dostępnego opisu dotyczy rankingu ATP/WTA lub wejścia do wskazanego przedziału rankingowego"
            )
        if round_name or score:
            return _sentence(
                "Tenisowy konkret: " + ", ".join(facts) + ". "
                "Z opisu wynika przede wszystkim rezultat lub etap turnieju; nie dopisuję wpływu na ranking ATP/WTA, jeśli RSS go nie podaje"
            )
        return ""

    facts = []
    if round_name:
        facts.append(f"etap: {round_name}")
    if score:
        facts.append(f"wynik: {score}")
    if competition:
        facts.append(f"rozgrywki: {competition}")

    if facts:
        return _sentence(
            "Sportowy konkret: " + ", ".join(facts) + ". "
            "To pomaga od razu zrozumieć, czy news dotyczy wyniku, awansu, odpadnięcia albo kolejnego etapu rywalizacji"
        )

    return ""


def deep_context_why_it_matters_pl(section_key: str, title: str, snippet: str) -> str:
    """Return why only if it adds concrete value. Empty string = omit the line in HTML."""
    text = f"{title} {snippet}"

    if is_weather_item(title, snippet):
        return ""

    if section_key == "sport" or hybrid.SPORT_RE.search(text) or TENNIS_RE.search(text):
        return deep_sport_context_why_pl(title, snippet)

    if hybrid.FUEL_ENERGY_RE.search(text):
        return (
            "Tu konkretem są koszty: paliwa i energia szybko przechodzą na rachunki, transport i marże firm. "
            "Najważniejsze jest, czy opis mówi o jednorazowej zmianie, czy o początku trwalszego trendu cenowego."
        )

    if section_key == "biznes" and hybrid.MACRO_ECON_RE.search(text):
        return (
            "To informacja makro: liczy się wpływ na inflację, popyt, koszty firm albo oczekiwania wobec banku centralnego. "
            "Największe znaczenie ma różnica między danymi a tym, czego spodziewał się rynek."
        )

    if hybrid.PUBLIC_POLICY_RE.search(text):
        return (
            "Sedno leży w praktycznych skutkach decyzji publicznej: kogo obejmuje, od kiedy działa i kto za nią płaci. "
            "Taki komentarz ma pokazać mechanizm zmiany, a nie tylko powtórzyć nagłówek."
        )

    if hybrid.HEALTH_RE.search(text):
        return (
            "W zdrowiu wartość newsa zależy od skali, grupy ryzyka i tego, czy zmienia realne zalecenia dla pacjentów lub lekarzy. "
            "Komentarz powinien oddzielać sygnał systemowy od pojedynczego przypadku."
        )

    if hybrid.SCIENCE_RE.search(text):
        return (
            "W newsie naukowym kluczowe są metoda, źródło danych i stopień potwierdzenia wyniku. "
            "Najważniejsze jest, co odkrycie realnie zmienia w rozumieniu tematu."
        )

    # Ograniczamy "Dlaczego to ważne" dla zwykłych newsów politycznych/osobowych/incydentalnych,
    # bo generowało to pustą publicystykę bez wartości dodanej.
    return ""


def deep_why_is_useful(why: str) -> bool:
    why = _clean(why)
    if not why or len(why) < 60:
        return False
    if GENERIC_SPORT_BAD_RE.search(why) or LOW_VALUE_WHY_RE.search(why):
        return False
    return hybrid.why_is_useful(why)


def fetch_section_deep(section_key: str):
    items = _original_fetch_section_deep(section_key)
    filtered = []
    tvn24_used = 0

    for it in items:
        title = it.get("title", "") or ""
        snippet = it.get("summary_raw", "") or it.get("ai_summary", "") or ""

        # Weather has low added value on BriefRooms because users already use weather apps.
        if is_weather_item(title, snippet):
            continue

        # TVN24 is treated as secondary/tertiary: keep at most one item per section.
        if is_tvn24_item(it):
            if tvn24_used >= 1:
                continue
            tvn24_used += 1

        why = deep_context_why_it_matters_pl(section_key, title, snippet)
        it["ai_why"] = why if deep_why_is_useful(why) else ""
        filtered.append(it)

    return filtered


def render_html_deep(sections: dict) -> str:
    html = _original_render_html_deep(sections)
    # Remove empty "Dlaczego to ważne" lines entirely instead of rendering an empty label.
    html = re.sub(r"\n\s*<div class=\"sec\"><strong>Dlaczego to ważne:</strong>\s*</div>", "", html)
    return html


# Patch the already-imported hybrid builder. Its monkey-patched base.ai_summarize_pl
# looks up these functions from the hybrid module at call time.
hybrid.sport_context_why_pl = deep_sport_context_why_pl
hybrid.context_why_it_matters_pl = deep_context_why_it_matters_pl
hybrid.why_is_useful = deep_why_is_useful
hybrid.base.fetch_section = fetch_section_deep
hybrid.base.render_html = render_html_deep

if __name__ == "__main__":
    hybrid.base.main()

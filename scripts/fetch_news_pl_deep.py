#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deep-context wrapper for the PL BriefRooms news builder.

Goal:
- keep the hybrid PL model from fetch_news_pl_hybrid.py;
- replace generic sports comments with useful article-level essence;
- never write vague ranking/prestige/advancement comments unless the RSS text actually contains that information.
"""

import re
import sys

import fetch_news_pl_hybrid as hybrid


GENERIC_SPORT_BAD_RE = re.compile(
    r"(może wpływać na prestiż, ranking, awans albo pozycję|prestiż, ranking, awans|warto sprawdzić|zależy od tabeli|potwierdzenia aktualnej formy|presji przed kolejnym spotkaniem|sedno sportowe trzeba czytać)",
    re.I,
)

TENNIS_RE = re.compile(r"\b(tenis|tenisista|tenisistka|ATP|WTA|set|gem|runda|turniej|finał|półfinał|ćwierćfinał|ranking|Top\s*100|top\s*100)\b", re.I)
ATP_RANK_RE = re.compile(r"\b(ATP|ranking(?:u)? ATP|Top\s*100|top\s*100|pierwszej setki|setki rankingu|awans(?:uje|ował|owała)?(?: do)?\s+\d{1,3}\.?\s*miejsca|\d{1,3}\.?\s*miejsce(?: w rankingu)?)\b", re.I)
PLAYER_RE = re.compile(
    r"\b([A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż-]+\s+[A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż-]+)\b"
)


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


def deep_sport_context_why_pl(title: str, snippet: str) -> str:
    """Build a concrete sports comment from visible RSS data only."""
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
                "Jeżeli opis mówi o rankingu ATP/WTA lub wejściu do Top 100, właśnie ta konsekwencja jest najważniejszą informacją dla czytelnika"
            )
        if round_name or score:
            return _sentence(
                "Tenisowy konkret: " + ", ".join(facts) + ". "
                "Z dostępnego opisu wynika przede wszystkim etap i/lub wynik meczu; nie ma podstaw, by dopisywać wpływ na ranking ATP/WTA, jeżeli nie podaje go RSS"
            )
        return _sentence(
            "To news tenisowy, ale krótki opis RSS nie podaje rundy, wyniku ani skutku rankingowego. "
            "W takim przypadku brief powinien streścić widoczny fakt z nagłówka, bez ogólnego hasła o prestiżu lub rankingu"
        )

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
            "Komentarz ma pokazać bezpośrednią konsekwencję z opisu: awans, odpadnięcie, zmianę sytuacji w tabeli albo kolejny etap rywalizacji — bez ogólników"
        )

    return _sentence(
        "To krótki news sportowy, ale RSS nie podaje wyniku ani etapu rozgrywek. "
        "Komentarz powinien wtedy zostać przy widocznym fakcie z nagłówka, zamiast dopisywać ogólny wpływ na ranking lub prestiż"
    )


def deep_context_why_it_matters_pl(section_key: str, title: str, snippet: str) -> str:
    text = f"{title} {snippet}"
    if section_key == "sport" or hybrid.SPORT_RE.search(text) or TENNIS_RE.search(text):
        return deep_sport_context_why_pl(title, snippet)
    return hybrid.context_why_it_matters_pl(section_key, title, snippet)


def deep_why_is_useful(why: str) -> bool:
    why = _clean(why)
    if GENERIC_SPORT_BAD_RE.search(why):
        return False
    return hybrid.why_is_useful(why)


# Patch the already-imported hybrid builder. Its monkey-patched base.ai_summarize_pl
# looks up these functions from the hybrid module at call time.
hybrid.sport_context_why_pl = deep_sport_context_why_pl
hybrid.context_why_it_matters_pl = deep_context_why_it_matters_pl
hybrid.why_is_useful = deep_why_is_useful

if __name__ == "__main__":
    hybrid.base.main()

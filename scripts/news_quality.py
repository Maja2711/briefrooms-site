#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

POLICY_VERSION = "news-value-filter-v3"

_DEATH_NOTICE = re.compile(
    r"\b(?:"
    r"nie\s+żyje|zmarł(?:a|o)?|odszedł\s+od\s+nas|odeszła\s+od\s+nas|"
    r"has\s+died|is\s+dead|died\s+aged|dies\s+aged|dead\s+at(?:\s+the\s+age\s+of)?|"
    r"obituary|in\s+memoriam"
    r")\b",
    re.IGNORECASE,
)

_INTERVIEW_META = re.compile(
    r"(?:"
    r"\bwywiad\s+z\b|"
    r"\brozmow(?:a|ę|y)\s+z\b|"
    r"\b(?:będzie|bedzie)\s+gościem\b|"
    r"\bgościem\s+(?:programu|poranka|radia|telewizji|tv|wydarzeń)\b|"
    r"\bgościem\s+[A-ZĄĆĘŁŃÓŚŹŻ][\wĄąĆćĘęŁłŃńÓóŚśŹźŻż-]+(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][\wĄąĆćĘęŁłŃńÓóŚśŹźŻż-]+)+\b|"
    r"\bgość\s+(?:wydarzeń|poranka|radia|telewizji|tv)\b|"
    r"\bgościu\s+(?:wydarzeń|poranka|radia|telewizji|tv)\b|"
    r"\b(?:dziś|dzisiaj|jutro|wkrótce)\b.{0,60}\b(?:na\s+antenie|w\s+tv|w\s+programie)\b|"
    r"\b(?:zobacz|obejrzyj|posłuchaj|oglądaj)\b.{0,45}\b(?:wywiad|rozmow\w*|program|podcast)\b|"
    r"\b(?:w|na)\s+(?:tvn24|polsat\s+news|tvp\s+info|radio\s+zet|rmf\s+fm|antenie|programie)\b|"
    r"\binterview\s+with\b|"
    r"\bin\s+conversation\s+with\b|"
    r"\bq\s*&\s*a\s+with\b|"
    r"\b(?:watch|listen\s+to)\b.{0,40}\b(?:interview|conversation|podcast)\b|"
    r"\b(?:joins\s+us|guest\s+on|will\s+appear\s+on)\b|"
    r"\[(?:wywiad|interview|podcast|oglądaj|zobacz|watch)\]"
    r")",
    re.IGNORECASE,
)

_GUEST_LISTING = re.compile(
    r"\bgościem\s+[A-ZĄĆĘŁŃÓŚŹŻ][\wĄąĆćĘęŁłŃńÓóŚśŹźŻż-]+"
    r"(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][\wĄąĆćĘęŁłŃńÓóŚśŹźŻż-]+)+\b",
    re.IGNORECASE,
)

_LOTTERY_TOPIC = re.compile(
    r"\b(?:"
    r"lotto|mini\s+lotto|lotto\s+plus|eurojackpot|eurojackpot|multi\s+multi|keno|"
    r"gry?\s+losow(?:e|ych)|loteri(?:a|i|ę)|"
    r"lottery|powerball|mega\s+millions|euromillions|lotto\s+6/49"
    r")\b",
    re.IGNORECASE,
)

_LOTTERY_RESULT_PROMO = re.compile(
    r"\b(?:"
    r"wynik(?:i|ów)?(?:\s+losowania)?|losowani(?:e|a)|wylosowan(?:o|e)|"
    r"szczęśliwe\s+liczby|zwycięskie\s+liczby|numery\s+(?:losowania|wygrywające)|"
    r"kumulacj(?:a|i|ę)|do\s+wygrania|główna\s+wygrana|padła\s+wygrana|"
    r"rekordowa\s+wygrana|jackpot|rollover|winning\s+numbers|lottery\s+results?|"
    r"draw\s+results?|numbers\s+drawn|prize\s+(?:rises|grows)|no\s+jackpot\s+winner"
    r")\b",
    re.IGNORECASE,
)

_GAMBLING_PUBLIC_INTEREST = re.compile(
    r"\b(?:"
    r"ustaw\w*|regulacj\w*|zakaz\w*|podatek|podatk\w*|licencj\w*|monopol\w*|"
    r"kontrol\w*|śledztw\w*|oszustw\w*|pranie\s+pieniędzy|uzależn\w*|reklam\w*|"
    r"law|regulat\w*|ban\w*|tax\w*|licen[cs]\w*|monopol\w*|investigat\w*|"
    r"fraud\w*|money\s+laundering|addiction|advertis\w*"
    r")\b",
    re.IGNORECASE,
)

_BETTING_PROMO = re.compile(
    r"\b(?:"
    r"bonus\s+\d+(?:[.,]\d+)?\s*(?:zł|pln|eur|usd)|"
    r"(?:odbierz|zgarnij|otrzymaj)\s+bonus|kod\s+promocyjny|"
    r"zakład\s+bez\s+ryzyka|darmowy\s+zakład|free\s+bet|"
    r"typy\s+bukmacherskie|specjalny\s+kurs"
    r")\b",
    re.IGNORECASE,
)

_SUBSTANTIVE_VERB = re.compile(
    r"\b(?:"
    r"zapowiada|ogłasza|ostrzega|twierdzi|mówi|ocenia|uważa|przyznaje|ujawnia|"
    r"potwierdza|wyjaśnia|apeluje|żąda|proponuje|chce|odrzuca|popiera|przewiduje|"
    r"wyklucza|podniesie|obniży|wprowadzi|zniesie|zablokuje|zagłosuje|przetrwa|"
    r"rozpadnie|upadnie|wzrośnie|spadnie|powinien|powinna|powinni|musi|muszą|"
    r"nie\s+poprze|poprze|"
    r"says|warns|announces|claims|reveals|confirms|explains|demands|proposes|wants|"
    r"rejects|backs|predicts|expects|rules\s+out|calls\s+for|plans\s+to|should|must|"
    r"will\s+(?:cut|raise|reduce|increase|introduce|block|support|reject)|"
    r"could\s+(?:fall|rise|drop|increase)"
    r")\b",
    re.IGNORECASE,
)

_MEANINGFUL_NUMBER = re.compile(
    r"(?:"
    r"\b(?:19|20)\d{2}\b|"
    r"\b\d+(?:[.,]\d+)?\s*(?:%|proc\.?|mld|mln|tys\.?|zł|pln|usd|eur|"
    r"dolar(?:ów|y)?|euro|pkt|punkt(?:ów|y)?|dni|lat|months?|years?|billion|million|percent)\b"
    r")",
    re.IGNORECASE,
)

_QUOTED_CONTENT = re.compile(r"[\"“”„«]([^\"“”„»«]{18,})[\"“”»]", re.IGNORECASE)
_PROMO_TAIL = re.compile(
    r"^(?:cał(?:y|a)\s+)?(?:wywiad|rozmowa|materiał|program|podcast)|"
    r"^(?:zobacz|obejrzyj|posłuchaj|oglądaj)|"
    r"^(?:o|na\s+temat|about)\s+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NewsQualityDecision:
    accepted: bool
    reason: str


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _word_count(value: str) -> int:
    return len(re.findall(r"\b[\wÀ-ž]+\b", value, flags=re.UNICODE))


def has_substantive_headline(title: Any) -> bool:
    """Return True only when the visible headline itself carries information."""
    value = _text(title)
    if not value:
        return False
    if _SUBSTANTIVE_VERB.search(value) or _MEANINGFUL_NUMBER.search(value):
        return True

    for match in _QUOTED_CONTENT.finditer(value):
        quote = match.group(1).strip()
        if _word_count(quote) >= 4 and not _PROMO_TAIL.search(quote):
            return True

    if ":" in value:
        tail = value.split(":", 1)[1].strip(" -–—")
        if _word_count(tail) >= 4 and not _PROMO_TAIL.search(tail):
            return True
    return False


def evaluate_story(title: Any, summary: Any = "") -> NewsQualityDecision:
    """Apply the BriefRooms editorial value policy to one candidate story."""
    headline = _text(title)
    description = _text(summary)
    combined = f"{headline} {description[:220]}".strip()

    if _DEATH_NOTICE.search(headline) or (
        _DEATH_NOTICE.search(combined)
        and re.search(r"\b(?:smutn\w*|tragiczn\w*|pożegn\w*|sad\s+news|tributes?)\b", headline, re.IGNORECASE)
    ):
        return NewsQualityDecision(False, "death_notice")

    if _GUEST_LISTING.search(headline) or (
        _INTERVIEW_META.search(headline) and not has_substantive_headline(headline)
    ):
        return NewsQualityDecision(False, "interview_promo_without_substance")

    if _LOTTERY_TOPIC.search(combined):
        headline_is_result = bool(_LOTTERY_RESULT_PROMO.search(headline))
        combined_is_result = bool(_LOTTERY_RESULT_PROMO.search(combined))
        headline_has_public_interest = bool(_GAMBLING_PUBLIC_INTEREST.search(headline))
        if headline_is_result or (combined_is_result and not headline_has_public_interest):
            return NewsQualityDecision(False, "lottery_result_or_jackpot_promo")

    if _BETTING_PROMO.search(combined) and not _GAMBLING_PUBLIC_INTEREST.search(headline):
        return NewsQualityDecision(False, "betting_promotion")

    return NewsQualityDecision(True, "publishable")


def is_publishable_story(title: Any, summary: Any = "") -> bool:
    return evaluate_story(title, summary).accepted


def public_policy() -> dict[str, Any]:
    return {
        "version": POLICY_VERSION,
        "purpose_pl": "Wybór wiadomości zawierających konkretną informację o znaczeniu publicznym, a nie zapowiedź materiału, komunikat personalny ani promocję hazardu.",
        "purpose_en": "Select stories containing concrete public-interest information rather than content promotion, personal death notices or gambling promotion.",
        "excluded": [
            {
                "id": "death_notice",
                "description_pl": "Samodzielne nekrologi i informacje, że konkretna osoba zmarła lub nie żyje.",
                "description_en": "Standalone obituaries and notices that a named person has died.",
            },
            {
                "id": "interview_promo_without_substance",
                "description_pl": "Zapowiedzi wywiadów, gości telewizyjnych i rozmów bez konkretnej tezy w widocznym nagłówku.",
                "description_en": "Interview, TV guest and podcast promotion without a concrete claim in the visible headline.",
            },
            {
                "id": "lottery_result_or_jackpot_promo",
                "description_pl": "Wyniki losowań, zwycięskie numery, kumulacje, jackpoty i promowanie kwot do wygrania.",
                "description_en": "Lottery draw results, winning numbers, rollovers, jackpots and prize-pool promotion.",
            },
            {
                "id": "betting_promotion",
                "description_pl": "Reklamy bonusów bukmacherskich, darmowych zakładów, kodów promocyjnych i specjalnych kursów.",
                "description_en": "Advertising for betting bonuses, free bets, promotional codes and special odds.",
            },
        ],
        "interview_exception_pl": "Materiał z wywiadu może zostać opublikowany, gdy nagłówek podaje konkretną wypowiedź, decyzję, prognozę, liczbę lub skutek.",
        "interview_exception_en": "Interview content may be published when the headline states a concrete claim, decision, forecast, number or consequence.",
        "lottery_exception_pl": "Dopuszczalne są wiadomości o regulacjach, podatkach, oszustwach i innych skutkach publicznych rynku gier losowych, o ile nagłówek nie jest wynikiem losowania ani promocją wygranej.",
        "lottery_exception_en": "Public-interest reporting on regulation, taxation, fraud and other societal effects of gambling may be published when the headline is not a draw result or prize promotion.",
    }

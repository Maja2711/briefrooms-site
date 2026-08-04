#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

POLICY_VERSION = "news-value-filter-v1"

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

    if _INTERVIEW_META.search(headline) and not has_substantive_headline(headline):
        return NewsQualityDecision(False, "interview_promo_without_substance")

    return NewsQualityDecision(True, "publishable")


def is_publishable_story(title: Any, summary: Any = "") -> bool:
    return evaluate_story(title, summary).accepted


def public_policy() -> dict[str, Any]:
    return {
        "version": POLICY_VERSION,
        "purpose_pl": "Wybór wiadomości zawierających konkretną informację, a nie jedynie zapowiedź materiału lub komunikat personalny.",
        "purpose_en": "Select stories that contain concrete information rather than merely promoting content or announcing a personal death.",
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
        ],
        "interview_exception_pl": "Materiał z wywiadu może zostać opublikowany, gdy nagłówek podaje konkretną wypowiedź, decyzję, prognozę, liczbę lub skutek.",
        "interview_exception_en": "Interview content may be published when the headline states a concrete claim, decision, forecast, number or consequence.",
    }

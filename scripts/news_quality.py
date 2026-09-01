#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Keep the external version stable because the live publication workflow validates it.
# The policy is strengthened below without changing the payload contract.
POLICY_VERSION = "news-value-filter-v4"

_DEATH_NOTICE = re.compile(
    r"\b(?:nie\s+żyje|zmarł(?:a|o)?|odszedł\s+od\s+nas|odeszła\s+od\s+nas|"
    r"has\s+died|is\s+dead|died\s+aged|dies\s+aged|dead\s+at(?:\s+the\s+age\s+of)?|"
    r"obituary|in\s+memoriam)\b",
    re.IGNORECASE,
)

_INTERVIEW_META = re.compile(
    r"(?:\bwywiad\s+z\b|\brozmow(?:a|ę|y)\s+z\b|\b(?:będzie|bedzie)\s+gościem\b|"
    r"\bgościem\s+(?:programu|poranka|radia|telewizji|tv|wydarzeń)\b|"
    r"\b(?:dziś|dzisiaj|jutro|wkrótce)\b.{0,60}\b(?:na\s+antenie|w\s+tv|w\s+programie)\b|"
    r"\b(?:zobacz|obejrzyj|posłuchaj|oglądaj)\b.{0,45}\b(?:wywiad|rozmow\w*|program|podcast)\b|"
    r"\binterview\s+with\b|\bin\s+conversation\s+with\b|\bq\s*&\s*a\s+with\b|"
    r"\b(?:watch|listen\s+to)\b.{0,40}\b(?:interview|conversation|podcast)\b|"
    r"\b(?:joins\s+us|guest\s+on|will\s+appear\s+on)\b|"
    r"\[(?:wywiad|interview|podcast|oglądaj|zobacz|watch)\])",
    re.IGNORECASE,
)

_GUEST_LISTING = re.compile(
    r"\bgościem\s+[A-ZĄĆĘŁŃÓŚŹŻ][\wĄąĆćĘęŁłŃńÓóŚśŹźŻż-]+"
    r"(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][\wĄąĆćĘęŁłŃńÓóŚśŹźŻż-]+)+\b",
    re.IGNORECASE,
)

_LOTTERY_TOPIC = re.compile(
    r"\b(?:lotto|mini\s+lotto|lotto\s+plus|eurojackpot|multi\s+multi|keno|"
    r"gry?\s+losow(?:e|ych)|loteri(?:a|i|ę)|lottery|powerball|mega\s+millions|"
    r"euromillions|lotto\s+6/49)\b",
    re.IGNORECASE,
)
_LOTTERY_RESULT_PROMO = re.compile(
    r"\b(?:wynik(?:i|ów)?(?:\s+losowania)?|losowani(?:e|a)|wylosowan(?:o|e)|"
    r"szczęśliwe\s+liczby|zwycięskie\s+liczby|numery\s+(?:losowania|wygrywające)|"
    r"kumulacj(?:a|i|ę)|do\s+wygrania|główna\s+wygrana|padła\s+wygrana|"
    r"rekordowa\s+wygrana|jackpot|rollover|winning\s+numbers|lottery\s+results?|"
    r"draw\s+results?|numbers\s+drawn|prize\s+(?:rises|grows)|no\s+jackpot\s+winner)\b",
    re.IGNORECASE,
)
_GAMBLING_PUBLIC_INTEREST = re.compile(
    r"\b(?:ustaw\w*|regulacj\w*|zakaz\w*|podatk\w*|licencj\w*|monopol\w*|"
    r"kontrol\w*|śledztw\w*|oszustw\w*|pranie\s+pieniędzy|uzależn\w*|reklam\w*|"
    r"law|regulat\w*|ban\w*|tax\w*|licen[cs]\w*|monopol\w*|investigat\w*|"
    r"fraud\w*|money\s+laundering|addiction|advertis\w*)\b",
    re.IGNORECASE,
)
_BETTING_PROMO = re.compile(
    r"\b(?:bonus\s+\d+(?:[.,]\d+)?\s*(?:zł|pln|eur|usd)|"
    r"(?:odbierz|zgarnij|otrzymaj)\s+bonus|kod\s+promocyjny|"
    r"zakład\s+bez\s+ryzyka|darmowy\s+zakład|free\s+bet|"
    r"typy\s+bukmacherskie|specjalny\s+kurs)\b",
    re.IGNORECASE,
)

# Class-level filter: reject media audience/ratings PR, not only one exact headline.
_MEDIA_AUDIENCE_METRIC = re.compile(
    r"\b(?:wynik\w*|oglądalno\w*|widowni\w*|zasięg\w*|udział\w*\s+w\s+rynku|"
    r"rekord\w*\s+widowni|liczb\w*\s+widz\w*|liczb\w*\s+czytelnik\w*|odsłon\w*|"
    r"pageviews?|audience|ratings?|reach|viewers?|readers?|circulation|market\s+share)\b",
    re.IGNORECASE,
)
_MEDIA_BRAND_OR_SELF_REFERENCE = re.compile(
    r"\b(?:tvn(?:24)?|fakty\s+tvn|tvn24\.pl|polsat(?:\s+news)?|tvp(?:\s+info)?|"
    r"rmf(?:\s*fm|24)?|radio\s+zet|onet|wp\.?pl|interia|gazeta\.pl|"
    r"nasz(?:a|ej|e)\s+(?:stacj\w*|portal\w*|program\w*|serwis\w*)|"
    r"our\s+(?:channel|show|site|network|newsroom)|bbc|cnn)\b",
    re.IGNORECASE,
)
_MEDIA_CELEBRATION = re.compile(
    r"\b(?:rewelacyjne|świetne|znakomite|rekordowe|najlepsze|lider\w*|wygrywa\w*|"
    r"dziękujemy|great|excellent|record|best|number\s+one|thank\s+you)\b",
    re.IGNORECASE,
)
_MEDIA_PUBLIC_INTEREST = re.compile(
    r"\b(?:krrit|regulator\w*|koncesj\w*|kara\w*|pozew\w*|śledztw\w*|"
    r"przejęci\w*|fuzj\w*|sprzedaż\w*|zwolnieni\w*|redukcj\w*|cenzur\w*|"
    r"dezinformacj\w*|prawo\w*|ustaw\w*|regulat\w*|licen[cs]\w*|fine\w*|"
    r"lawsuit\w*|investigat\w*|acqui\w*|merger\w*|layoffs?|censorship|"
    r"disinformation|legislation|antitrust|competition\s+authority)\b",
    re.IGNORECASE,
)

_ISOLATED_LOCAL_INCIDENT = re.compile(
    r"(?:\bkonflikt\b.{0,100}\b(?:taksówkarz\w*|taxi)\b|"
    r"\b(?:bójk\w*|bijatyk\w*|awantur\w*|sprzeczk\w*|szarpanin\w*)\b|"
    r"\b(?:kolizj\w*|stłuczk\w*)\b|"
    r"\bwydmuchał\b.{0,70}\bpromil\w*\b|"
    r"\b(?:podejrzan\w*|zatrzyman\w*)\b.{0,90}\b(?:gwałt\w*|kradzież\w*|właman\w*|pobic\w*)\b|"
    r"\b(?:gwałt\w*|kradzież\w*|właman\w*|pobic\w*)\b.{0,90}\b(?:podejrzan\w*|zatrzyman\w*)\b|"
    r"\b(?:taxi\s+(?:fight|brawl|dispute)|bar\s+brawl|street\s+fight|minor\s+crash|"
    r"drunk\s+driver|arrested\s+(?:after|for)\s+(?:an?\s+)?(?:assault|burglary|rape))\b)",
    re.IGNORECASE,
)
_LOCAL_HUMAN_INTEREST = re.compile(
    r"\b(?:nietypow\w*\s+interwencj\w*|policjanci\s+zatrzymali|mandat\s+za|"
    r"sąsiedz\w*\s+spór|kłótni\w*\s+o|viral\w*\s+(?:film|nagranie)|"
    r"internet\s+obiegło\s+nagranie|local\s+police|neighbour\s+dispute|viral\s+video)\b",
    re.IGNORECASE,
)
_INCIDENT_PUBLIC_INTEREST = re.compile(
    r"\b(?:zamach\w*|terror\w*|katastrof\w*|masow\w*|seryjn\w*|gang\w*|"
    r"grup\w*\s+przestępcz\w*|cyberatak\w*|infrastruktur\w*\s+krytycz\w*|"
    r"korupcj\w*|defraudacj\w*|systemow\w*|precedens\w*|ustaw\w*|regulacj\w*|"
    r"trybunał\w*|sąd\s+najwyższ\w*|prokuratur\w*\s+krajow\w*|"
    r"co\s+najmniej\s+\d+|dziesiątki|setki|tysiące|milion\w*|miliard\w*|"
    r"wiele\s+osób|ofiary\w*|dane\s+(?:osobowe|medyczne)|terror\w*|"
    r"mass\s+(?:casualty|shooting)|serial\s+(?:attacker|offender)|organized\s+crime|"
    r"criminal\s+network|critical\s+infrastructure|corrupt\w*|systemic|precedent|"
    r"supreme\s+court|constitutional\s+court|regulat\w*|at\s+least\s+\d+|"
    r"dozens|hundreds|thousands|millions?|billions?|victims?)\b",
    re.IGNORECASE,
)

_POLITICAL_THEATER = re.compile(
    r"(?:\b(?:zamieścił|opublikował)\s+wpis\b|\bzwrócił\s+się\s+do\b|"
    r"\b(?:odpowiedział|zareagował)\s+na\s+(?:słowa|wpis)\b|"
    r"\boburzeni\w*\s+(?:na|wśród|po)\b|\b(?:wywołał|wywołała)\s+burzę\b|"
    r"\b(?:pomoże|pomożecie)\s+pan\b|\bposted\s+on\s+(?:social\s+media|x)\b|"
    r"\b(?:hit\s+back|fired\s+back|reacted\s+to\s+(?:comments|post))\b|"
    r"\b(?:outrage|backlash)\s+(?:over|after)\b)",
    re.IGNORECASE,
)
_POLITICAL_SUBSTANCE = re.compile(
    r"\b(?:ustaw\w*|projekt\s+ustaw\w*|głosowani\w*|wybor\w*|referend\w*|"
    r"decyzj\w*|porozumieni\w*|umow\w*|sankcj\w*|budżet\w*|podatek\w*|"
    r"stopy\s+procentowe|bezpieczeństw\w*|wojn\w*|atak\w*|kryzys\w*|"
    r"wyrok\w*|orzeczeni\w*|śledztw\w*|korupcj\w*|dymisj\w*|powołan\w*|"
    r"bill|legislation|vote\w*|election\w*|referendum|decision\w*|agreement\w*|"
    r"treaty|sanctions?|budget|tax\w*|interest\s+rates?|security|war|attack\w*|"
    r"crisis|ruling|investigat\w*|corrupt\w*|resign\w*|appoint\w*)\b",
    re.IGNORECASE,
)

_SUBSTANTIVE_VERB = re.compile(
    r"\b(?:zapowiada|ogłasza|ostrzega|twierdzi|mówi|ocenia|uważa|przyznaje|ujawnia|"
    r"potwierdza|wyjaśnia|apeluje|żąda|proponuje|chce|odrzuca|popiera|przewiduje|"
    r"wyklucza|podniesie|obniży|wprowadzi|zniesie|zablokuje|zagłosuje|przetrwa|"
    r"rozpadnie|upadnie|wzrośnie|spadnie|powinien|powinna|powinni|musi|muszą|"
    r"nie\s+poprze|poprze|says|warns|announces|claims|reveals|confirms|explains|"
    r"demands|proposes|wants|rejects|backs|predicts|expects|rules\s+out|calls\s+for|"
    r"plans\s+to|should|must|will\s+(?:cut|raise|reduce|increase|introduce|block|support|reject)|"
    r"could\s+(?:fall|rise|drop|increase))\b",
    re.IGNORECASE,
)
_MEANINGFUL_NUMBER = re.compile(
    r"(?:\b(?:19|20)\d{2}\b|\b\d+(?:[.,]\d+)?\s*(?:%|proc\.?|mld|mln|tys\.?|zł|pln|usd|eur|"
    r"dolar(?:ów|y)?|euro|pkt|punkt(?:ów|y)?|dni|lat|months?|years?|billion|million|percent)\b)",
    re.IGNORECASE,
)
_QUOTED_CONTENT = re.compile(r"[\"“”„«]([^\"“”„»«]{18,})[\"“”»]", re.IGNORECASE)
_PROMO_TAIL = re.compile(
    r"^(?:cał(?:y|a)\s+)?(?:wywiad|rozmowa|materiał|program|podcast)|"
    r"^(?:zobacz|obejrzyj|posłuchaj|oglądaj)|^(?:o|na\s+temat|about)\s+",
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


def _is_media_self_promotion(combined: str) -> bool:
    """Reject audience/ratings PR unless the story has a real external consequence."""
    if _MEDIA_PUBLIC_INTEREST.search(combined):
        return False
    has_metric = bool(_MEDIA_AUDIENCE_METRIC.search(combined))
    has_brand = bool(_MEDIA_BRAND_OR_SELF_REFERENCE.search(combined))
    celebratory = bool(_MEDIA_CELEBRATION.search(combined))
    return has_metric and (has_brand or celebratory)


def evaluate_story(title: Any, summary: Any = "") -> NewsQualityDecision:
    """Apply the BriefRooms public-value gate to one candidate story."""
    headline = _text(title)
    description = _text(summary)
    combined = f"{headline} {description[:320]}".strip()

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

    if _is_media_self_promotion(combined):
        return NewsQualityDecision(False, "media_self_promotion")

    if (
        (_ISOLATED_LOCAL_INCIDENT.search(headline) or _LOCAL_HUMAN_INTEREST.search(headline))
        and not _INCIDENT_PUBLIC_INTEREST.search(combined)
    ):
        return NewsQualityDecision(False, "isolated_local_incident")

    if _POLITICAL_THEATER.search(headline) and not _POLITICAL_SUBSTANCE.search(headline):
        return NewsQualityDecision(False, "political_theater_without_public_consequence")

    return NewsQualityDecision(True, "publishable")


def is_publishable_story(title: Any, summary: Any = "") -> bool:
    return evaluate_story(title, summary).accepted


def public_policy() -> dict[str, Any]:
    return {
        "version": POLICY_VERSION,
        "purpose_pl": (
            "Wybór wiadomości o realnym znaczeniu publicznym, gospodarczym lub systemowym. "
            "Filtr odrzuca autopromocję wydawców, jednostkowe incydenty, komunikaty personalne "
            "i promocję hazardu, nawet gdy są świeże lub sensacyjne."
        ),
        "purpose_en": (
            "Select stories with real public, economic or systemic significance. "
            "Publisher self-promotion, isolated incidents, personal notices and gambling promotion "
            "are rejected even when fresh or sensational."
        ),
        "excluded": [
            {"id": "death_notice", "description_pl": "Samodzielne nekrologi i informacje o śmierci konkretnej osoby.", "description_en": "Standalone obituaries and personal death notices."},
            {"id": "interview_promo_without_substance", "description_pl": "Zapowiedzi wywiadów i gości bez konkretnej tezy lub skutku.", "description_en": "Interview and guest promotion without a concrete claim or consequence."},
            {"id": "lottery_result_or_jackpot_promo", "description_pl": "Wyniki losowań, jackpoty i promocja kwot do wygrania.", "description_en": "Lottery results, jackpots and prize-pool promotion."},
            {"id": "betting_promotion", "description_pl": "Bonusy bukmacherskie i reklamy zakładów.", "description_en": "Betting bonuses and promotional offers."},
            {"id": "media_self_promotion", "description_pl": "Autopromocja mediów: oglądalność, zasięg, widownia i podobne wewnętrzne KPI bez skutku rynkowego lub regulacyjnego.", "description_en": "Media self-promotion: ratings, reach, audience and similar internal KPIs without market or regulatory consequence."},
            {"id": "isolated_local_incident", "description_pl": "Jednostkowe bójki, konflikty, kolizje i lokalne ciekawostki bez szerszego znaczenia.", "description_en": "One-off fights, disputes, minor crashes and local human-interest incidents without wider significance."},
            {"id": "political_theater_without_public_consequence", "description_pl": "Wpisy i wymiana zdań polityków bez decyzji lub mierzalnego skutku.", "description_en": "Political posts and verbal sparring without a decision or measurable consequence."},
        ],
        "media_exception_pl": "Wiadomość o medium zostaje, gdy dotyczy regulacji, przejęcia, fuzji, zwolnień, cenzury, dezinformacji lub innego skutku rynkowego/publicznego.",
        "media_exception_en": "A media story remains eligible when it concerns regulation, acquisition, merger, layoffs, censorship, disinformation or another market/public consequence.",
        "incident_exception_pl": "Incydent zostaje, gdy jest masowy lub systemowy, dotyczy bezpieczeństwa publicznego, infrastruktury krytycznej, korupcji albo ważnego precedensu.",
        "incident_exception_en": "An incident remains eligible when it is mass-scale or systemic, concerns public safety, critical infrastructure, corruption or an important precedent.",
    }

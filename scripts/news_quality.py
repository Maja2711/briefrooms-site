#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

POLICY_VERSION = "news-value-filter-v4"

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

_MEDIA_SELF_PROMOTION = re.compile(
    r"(?:"
    r"\b(?:rewelacyjne|świetne|znakomite|rekordowe|najlepsze)\b.{0,90}"
    r"\b(?:wyniki|oglądalno\w*|zasięg\w*)\b|"
    r"\b(?:wyniki|oglądalno\w*|zasięg\w*)\b.{0,90}"
    r"\b(?:tvn24|fakt(?:y|ów)\s+tvn|tvn24\.pl|polsat\s+news|tvp\s+info|"
    r"nasz(?:a|ej)\s+(?:stacj\w*|portal\w*|program\w*))\b|"
    r"\b(?:great|excellent|record|best)\b.{0,90}\b(?:ratings?|audience|reach)\b|"
    r"\b(?:ratings?|audience|reach)\b.{0,90}\b(?:our\s+(?:channel|show|site)|bbc|cnn)\b|"
    r"\bdziękujemy(?:\s+widzom|\s+czytelnikom)?\b|\bthank\s+you,?\s+(?:viewers|readers)\b"
    r")",
    re.IGNORECASE,
)

_MEDIA_PUBLIC_INTEREST = re.compile(
    r"\b(?:"
    r"krrit|regulator\w*|koncesj\w*|kara\w*|pozew\w*|śledztw\w*|przejęci\w*|"
    r"fuzj\w*|sprzedaż\w*|zwolnieni\w*|redukcj\w*|cenzur\w*|dezinformacj\w*|"
    r"regulat\w*|licen[cs]\w*|fine\w*|lawsuit\w*|investigat\w*|acqui\w*|"
    r"merger\w*|layoffs?|censorship|disinformation"
    r")\b",
    re.IGNORECASE,
)

_ISOLATED_LOCAL_INCIDENT = re.compile(
    r"(?:"
    r"\bkonflikt\b.{0,80}\b(?:taksówkarz\w*|taxi)\b|"
    r"\b(?:bójk\w*|bijatyk\w*|awantur\w*|sprzeczk\w*)\b|"
    r"\b(?:kolizj\w*|stłuczk\w*)\b|"
    r"\bwydmuchał\b.{0,60}\bpromil\w*\b|"
    r"\b(?:podejrzan\w*|zatrzyman\w*)\b.{0,70}\b(?:gwałt\w*|kradzież\w*|właman\w*)\b|"
    r"\b(?:gwałt\w*|kradzież\w*|właman\w*)\b.{0,70}\b(?:podejrzan\w*|zatrzyman\w*)\b|"
    r"\b(?:taxi\s+(?:fight|brawl)|bar\s+brawl|street\s+fight|minor\s+crash|"
    r"drunk\s+driver|arrested\s+(?:after|for)\s+(?:an?\s+)?(?:assault|burglary|rape))\b"
    r")",
    re.IGNORECASE,
)

_INCIDENT_PUBLIC_INTEREST = re.compile(
    r"\b(?:"
    r"zamach\w*|terror\w*|katastrof\w*|masow\w*|seryjn\w*|gang\w*|"
    r"grup\w*\s+przestępcz\w*|cyberatak\w*|infrastruktur\w*\s+krytycz\w*|"
    r"korupcj\w*|defraudacj\w*|systemow\w*|precedens\w*|ustaw\w*|"
    r"trybunał\w*|sąd\s+najwyższ\w*|prokuratur\w*\s+krajow\w*|"
    r"co\s+najmniej\s+\d+|dziesiątki|setki|tysiące|milion\w*|miliard\w*|"
    r"wiele\s+osób|ofiary\w*|dane\s+(?:osobowe|medyczne)|"
    r"terror\w*|mass\s+(?:casualty|shooting)|serial\s+(?:attacker|offender)|"
    r"organized\s+crime|criminal\s+network|critical\s+infrastructure|"
    r"corrupt\w*|systemic|precedent|supreme\s+court|constitutional\s+court|"
    r"at\s+least\s+\d+|dozens|hundreds|thousands|millions?|billions?|victims?"
    r")\b",
    re.IGNORECASE,
)

_POLITICAL_THEATER = re.compile(
    r"(?:"
    r"\b(?:zamieścił|opublikował)\s+wpis\b|"
    r"\bzwrócił\s+się\s+do\b|"
    r"\b(?:odpowiedział|zareagował)\s+na\s+(?:słowa|wpis)\b|"
    r"\boburzeni\w*\s+(?:na|wśród|po)\b|"
    r"\b(?:wywołał|wywołała)\s+burzę\b|"
    r"\b(?:pomoże|pomożecie)\s+pan\b|"
    r"\bposted\s+on\s+(?:social\s+media|x)\b|"
    r"\b(?:hit\s+back|fired\s+back|reacted\s+to\s+(?:comments|post))\b|"
    r"\b(?:outrage|backlash)\s+(?:over|after)\b"
    r")",
    re.IGNORECASE,
)

_POLITICAL_SUBSTANCE = re.compile(
    r"\b(?:"
    r"ustaw\w*|projekt\s+ustaw\w*|głosowani\w*|wybor\w*|referend\w*|"
    r"decyzj\w*|porozumieni\w*|umow\w*|sankcj\w*|budżet\w*|podatek\w*|"
    r"stopy\s+procentowe|bezpieczeństw\w*|wojn\w*|atak\w*|kryzys\w*|"
    r"wyrok\w*|orzeczeni\w*|śledztw\w*|korupcj\w*|dymisj\w*|powołan\w*|"
    r"bill\b|legislation|vote\w*|election\w*|referendum|decision\w*|"
    r"agreement\w*|treaty|sanctions?|budget|tax\w*|interest\s+rates?|"
    r"security|war|attack\w*|crisis|ruling|investigat\w*|corrupt\w*|"
    r"resign\w*|appoint\w*"
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

    if _MEDIA_SELF_PROMOTION.search(headline) and not _MEDIA_PUBLIC_INTEREST.search(combined):
        return NewsQualityDecision(False, "media_self_promotion")

    if _ISOLATED_LOCAL_INCIDENT.search(headline) and not _INCIDENT_PUBLIC_INTEREST.search(combined):
        return NewsQualityDecision(False, "isolated_local_incident")

    if _POLITICAL_THEATER.search(headline) and not _POLITICAL_SUBSTANCE.search(headline):
        return NewsQualityDecision(False, "political_theater_without_public_consequence")

    return NewsQualityDecision(True, "publishable")


def is_publishable_story(title: Any, summary: Any = "") -> bool:
    return evaluate_story(title, summary).accepted


def public_policy() -> dict[str, Any]:
    return {
        "version": POLICY_VERSION,
        "purpose_pl": "Wybór wiadomości zawierających konkretną informację o znaczeniu publicznym lub systemowym, a nie autopromocję wydawcy, jednostkowy incydent, komunikat personalny ani promocję hazardu.",
        "purpose_en": "Select stories containing concrete public or systemic value rather than publisher self-promotion, isolated incidents, personal notices or gambling promotion.",
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
            {
                "id": "media_self_promotion",
                "description_pl": "Autopromocja wydawcy: wyniki oglądalności, zasięgi i podziękowania dla widzów lub czytelników.",
                "description_en": "Publisher self-promotion such as audience results, reach figures and thank-you announcements.",
            },
            {
                "id": "isolated_local_incident",
                "description_pl": "Jednostkowe bójki, konflikty, drobne kolizje i podobne incydenty bez szerszego znaczenia społecznego.",
                "description_en": "One-off fights, disputes, minor crashes and similar incidents without wider public significance.",
            },
            {
                "id": "political_theater_without_public_consequence",
                "description_pl": "Wpisy, zaczepki, oburzenie i wymiana zdań polityków bez decyzji lub mierzalnego skutku publicznego.",
                "description_en": "Political posts, outrage and verbal sparring without a decision or measurable public consequence.",
            },
        ],
        "interview_exception_pl": "Materiał z wywiadu może zostać opublikowany, gdy nagłówek podaje konkretną wypowiedź, decyzję, prognozę, liczbę lub skutek.",
        "interview_exception_en": "Interview content may be published when the headline states a concrete claim, decision, forecast, number or consequence.",
        "lottery_exception_pl": "Dopuszczalne są wiadomości o regulacjach, podatkach, oszustwach i innych skutkach publicznych rynku gier losowych, o ile nagłówek nie jest wynikiem losowania ani promocją wygranej.",
        "lottery_exception_en": "Public-interest reporting on regulation, taxation, fraud and other societal effects of gambling may be published when the headline is not a draw result or prize promotion.",
        "incident_exception_pl": "Incydent może zostać opublikowany, gdy ma charakter masowy lub systemowy, dotyczy bezpieczeństwa publicznego, infrastruktury krytycznej, korupcji albo ustanawia ważny precedens.",
        "incident_exception_en": "An incident may be published when it is mass-scale or systemic, concerns public safety, critical infrastructure, corruption or an important precedent.",
    }

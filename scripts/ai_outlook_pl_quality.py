#!/usr/bin/env python3
"""Fail-closed semantic validation for the public Polish AI Outlook edition."""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date
from typing import Any
from urllib.parse import unquote, urlparse

QUALITY_GATE_VERSION = "pl-semantic-quality-v2"
REQUIRED_METHODOLOGY_VERSION = "pl-outcome-forecast-v2"

ALLOWED_FORECAST_TYPES = {
    "official_decision",
    "official_indicator",
    "policy_implementation",
    "regulatory_milestone",
    "market_indicator",
    "clinical_endpoint",
    "scientific_result",
}
OFFICIAL_VERIFICATION_HOSTS = (
    "prezydent.pl",
    "gov.pl",
    "sejm.gov.pl",
    "senat.gov.pl",
    "dziennikustaw.gov.pl",
    "isap.sejm.gov.pl",
    "stat.gov.pl",
    "nbp.pl",
    "knf.gov.pl",
    "uokik.gov.pl",
    "rf.gov.pl",
    "zus.pl",
    "nfz.gov.pl",
    "pacjent.gov.pl",
    "pzh.gov.pl",
    "ema.europa.eu",
    "ec.europa.eu",
    "eurostat.ec.europa.eu",
    "ecb.europa.eu",
    "consilium.europa.eu",
    "eur-lex.europa.eu",
    "who.int",
    "clinicaltrials.gov",
    "nasa.gov",
    "esa.int",
)
META_FORECAST_RE = re.compile(
    r"\b("
    r"komunikat(?:y|ów|u|ach)?|aktualizacj\w*|artykuł\w*|publikacj\w*|"
    r"wzmiank\w*|doniesieni\w*|nagłówk\w*|zainteresowani\w*|"
    r"centrum uwagi|debat\w*|dyskusj\w*|reakcj\w* informacyjn\w*|"
    r"kolejne wyjaśnienia|dalszy ciąg tematu"
    r")\b",
    re.IGNORECASE,
)
VAGUE_PHRASES = (
    "w centrum uwagi",
    "pozostanie ważnym tematem",
    "utrzyma się presja",
    "może mieć znaczenie",
    "rynek będzie śledził",
    "temat będzie wracał",
)
AMBIGUOUS_METRIC = re.compile(r"\b(?:lub|albo|ewentualnie)\b|[/;]", re.IGNORECASE)
WORD = re.compile(r"[a-ząćęłńóśźż0-9]+", re.IGNORECASE)
STOPWORDS = {
    "oraz", "przez", "który", "która", "które", "tego", "temat", "dotyczących",
    "dotyczące", "polsce", "polska", "liczba", "wartość", "wskaźnik", "okresie",
    "publicznych", "publiczne", "oficjalnych", "oficjalne", "danych", "dane",
    "zostanie", "zostaną", "będzie", "wynik", "poziom",
}


class PolishOutlookQualityError(ValueError):
    """Raised when a Polish AI Outlook edition is unsafe to publish."""


def _plain(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().lower()


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _stems(value: Any) -> set[str]:
    words = []
    for token in WORD.findall(_plain(value)):
        if token in STOPWORDS or len(token) < 4 or token.isdigit():
            continue
        words.append(token[:7])
    return set(words)


def _official_verification_url(value: Any) -> bool:
    try:
        host = (urlparse(str(value or "")).hostname or "").lower().removeprefix("www.")
    except Exception:
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in OFFICIAL_VERIFICATION_HOSTS)


def _threshold_tokens(value: float) -> set[str]:
    compact = f"{float(value):g}"
    return {compact, compact.replace(".", ",")}


def _contains_threshold(text: Any, threshold: float) -> bool:
    normal = _plain(text)
    return any(token in normal for token in _threshold_tokens(threshold))


def _contains_deadline(text: Any, resolution_date: str) -> bool:
    year, month, day = resolution_date.split("-")
    alternatives = {
        resolution_date,
        f"{day}.{month}.{year}",
        f"{day}-{month}-{year}",
        year,
    }
    normal = _plain(text)
    return any(value in normal for value in alternatives)


def _validate_source_coherence(edition: dict[str, Any], metric: str) -> None:
    sources = edition.get("sources")
    if not isinstance(sources, list) or not sources:
        raise PolishOutlookQualityError("PL Outlook requires at least one source")

    provenance_ids: list[str] = []
    metric_stems = _stems(metric)
    public_stems = _stems(
        " ".join(str(edition.get(field) or "") for field in ("title", "thesis", "rationale"))
    )
    topic_stems = metric_stems | public_stems

    for source in sources:
        if not isinstance(source, dict):
            raise PolishOutlookQualityError("PL Outlook source is not an object")
        if source.get("source_language") != "pl":
            raise PolishOutlookQualityError("PL Outlook contains a non-Polish source")
        provenance_id = str(source.get("provenance_id") or "").strip()
        if not provenance_id:
            raise PolishOutlookQualityError("PL Outlook source has no provenance ID")
        provenance_ids.append(provenance_id)

        url = str(source.get("url") or "")
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise PolishOutlookQualityError("PL Outlook source URL must use HTTPS")
        path_text = unquote(parsed.path.replace("-", " ").replace("_", " "))
        path_stems = _stems(path_text)
        if len(path_stems) >= 5 and topic_stems and not (path_stems & topic_stems):
            raise PolishOutlookQualityError(
                "PL Outlook contains a source unrelated to the forecast outcome"
            )

    if len(set(provenance_ids)) != len(provenance_ids):
        raise PolishOutlookQualityError(
            "PL Outlook counts duplicated provenance as independent evidence"
        )


def validate_pl_edition(edition: dict[str, Any]) -> None:
    """Reject meta-forecasts, mixed topics and objectively unresolvable claims."""
    if not isinstance(edition, dict):
        raise PolishOutlookQualityError("missing Polish AI Outlook edition")

    forecast_type = str(edition.get("forecast_type") or "")
    if forecast_type not in ALLOWED_FORECAST_TYPES:
        raise PolishOutlookQualityError("PL Outlook has an unsupported forecast type")

    engine = edition.get("engine")
    if not isinstance(engine, dict):
        raise PolishOutlookQualityError("PL Outlook has no engine metadata")
    if engine.get("methodology_version") != REQUIRED_METHODOLOGY_VERSION:
        raise PolishOutlookQualityError("PL Outlook does not use the current outcome methodology")

    resolution = edition.get("resolution")
    if not isinstance(resolution, dict):
        raise PolishOutlookQualityError("PL Outlook has no structured resolution")

    metric = str(resolution.get("metric") or "").strip()
    if len(metric) < 12:
        raise PolishOutlookQualityError("PL Outlook metric is too vague")
    if META_FORECAST_RE.search(metric):
        raise PolishOutlookQualityError("PL Outlook metric measures media or publication activity")
    if AMBIGUOUS_METRIC.search(_plain(metric)):
        raise PolishOutlookQualityError(
            "PL Outlook metric combines alternative measures with 'lub/albo'"
        )

    threshold = resolution.get("threshold")
    baseline = resolution.get("baseline_value")
    if not _finite_number(threshold):
        raise PolishOutlookQualityError("PL Outlook threshold must be numeric")
    if not _finite_number(baseline):
        raise PolishOutlookQualityError(
            "PL Outlook baseline_value must be numeric before publication"
        )

    unit = str(resolution.get("unit") or "").strip()
    if not unit or META_FORECAST_RE.search(unit):
        raise PolishOutlookQualityError("PL Outlook resolution unit is invalid")
    if not str(resolution.get("data_source_for_verification") or "").strip():
        raise PolishOutlookQualityError("PL Outlook verification source is missing")
    if not _official_verification_url(resolution.get("verification_url")):
        raise PolishOutlookQualityError(
            "PL Outlook verification URL must identify an official institution or register"
        )

    try:
        baseline_date = date.fromisoformat(str(resolution.get("baseline_date") or ""))
        resolution_date = date.fromisoformat(str(resolution.get("resolution_date") or ""))
    except ValueError as exc:
        raise PolishOutlookQualityError("PL Outlook resolution dates are invalid") from exc
    if resolution_date <= baseline_date:
        raise PolishOutlookQualityError("PL Outlook resolution date must be in the future")
    if (resolution_date - baseline_date).days > 550:
        raise PolishOutlookQualityError("PL Outlook horizon exceeds 18 months")

    public_fields = (
        "title", "thesis", "rationale", "confirmation", "invalidation",
        "resolution_summary",
    )
    public_text = " ".join(str(edition.get(field) or "") for field in public_fields)
    if META_FORECAST_RE.search(public_text):
        raise PolishOutlookQualityError(
            "PL Outlook is a meta-forecast about communications or media activity"
        )
    plain_public_text = _plain(public_text)
    for phrase in VAGUE_PHRASES:
        if _plain(phrase) in plain_public_text:
            raise PolishOutlookQualityError(
                f"PL Outlook contains a non-testable phrase: {phrase}"
            )

    is_binary = "binar" in _plain(unit)
    if forecast_type in {"official_decision", "policy_implementation", "regulatory_milestone"}:
        if not is_binary or float(baseline) != 0.0 or float(threshold) != 1.0:
            raise PolishOutlookQualityError(
                "PL Outlook official event must use binary 0-to-1 resolution"
            )

    resolution_date_text = resolution_date.isoformat()
    for field in ("confirmation", "invalidation", "resolution_summary"):
        field_text = edition.get(field)
        if not _contains_deadline(field_text, resolution_date_text):
            raise PolishOutlookQualityError(
                f"PL Outlook {field} does not state the resolution deadline"
            )
        if not is_binary and not _contains_threshold(field_text, float(threshold)):
            raise PolishOutlookQualityError(
                f"PL Outlook {field} does not state the locked threshold"
            )

    metric_stems = _stems(metric)
    if len(metric_stems & _stems(edition.get("confirmation"))) < 1:
        raise PolishOutlookQualityError(
            "PL Outlook confirmation is not tied to the resolution metric"
        )
    if len(metric_stems & _stems(edition.get("invalidation"))) < 1:
        raise PolishOutlookQualityError(
            "PL Outlook invalidation is not tied to the resolution metric"
        )
    if len(metric_stems & _stems(edition.get("resolution_summary"))) < 1:
        raise PolishOutlookQualityError(
            "PL Outlook resolution summary is not tied to the resolution metric"
        )

    probability = edition.get("probability")
    if not isinstance(probability, int) or not 55 <= probability <= 75:
        raise PolishOutlookQualityError("PL Outlook probability is outside the allowed range")

    _validate_source_coherence(edition, metric)

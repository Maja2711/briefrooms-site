#!/usr/bin/env python3
"""Fail-closed semantic validation for the public Polish AI Outlook edition."""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date
from typing import Any
from urllib.parse import unquote, urlparse

QUALITY_GATE_VERSION = "pl-semantic-quality-v1"

_VAGUE_PHRASES = (
    "w centrum uwagi",
    "pozostanie w centrum uwagi",
    "utrzyma się w centrum uwagi",
    "utrzymają się w centrum uwagi",
    "istotne zmiany zachowań",
    "dynamika regulacyjna",
    "może mieć znaczenie",
    "będzie ważnym tematem",
)
_AMBIGUOUS_METRIC = re.compile(r"\b(?:lub|albo|ewentualnie)\b|[/;]", re.IGNORECASE)
_WORD = re.compile(r"[a-ząćęłńóśźż0-9]+", re.IGNORECASE)
_STOPWORDS = {
    "oraz", "przez", "który", "która", "które", "tego", "temat", "dotyczących",
    "dotyczące", "polsce", "polska", "liczba", "wartość", "wskaźnik", "okresie",
    "publicznych", "publiczne", "oficjalnych", "oficjalne", "danych", "dane",
}


class PolishOutlookQualityError(ValueError):
    """Raised when a Polish AI Outlook edition is unsafe to publish."""


def _plain(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().lower()


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _stems(value: Any) -> set[str]:
    words = []
    for token in _WORD.findall(_plain(value)):
        if token in _STOPWORDS or len(token) < 4 or token.isdigit():
            continue
        words.append(token[:7])
    return set(words)


def _threshold_tokens(value: float) -> set[str]:
    compact = f"{float(value):g}"
    return {compact, compact.replace(".", ",")}


def _contains_threshold(text: Any, threshold: float) -> bool:
    normal = _plain(text)
    return any(token in normal for token in _threshold_tokens(threshold))


def _validate_source_coherence(edition: dict[str, Any], metric: str) -> None:
    sources = edition.get("sources")
    if not isinstance(sources, list) or not sources:
        raise PolishOutlookQualityError("PL Outlook requires at least one source")

    provenance_ids: list[str] = []
    metric_stems = _stems(metric)
    for source in sources:
        if not isinstance(source, dict):
            raise PolishOutlookQualityError("PL Outlook source is not an object")
        provenance_id = str(source.get("provenance_id") or "").strip()
        if not provenance_id:
            raise PolishOutlookQualityError("PL Outlook source has no provenance ID")
        provenance_ids.append(provenance_id)

        url = str(source.get("url") or "")
        parsed = urlparse(url)
        path_text = unquote(parsed.path.replace("-", " ").replace("_", " "))
        path_stems = _stems(path_text)
        # Descriptive Polish URLs must visibly concern the forecast metric. Opaque
        # article IDs are allowed and are checked by the remaining evidence gates.
        if len(path_stems) >= 6 and metric_stems and not (path_stems & metric_stems):
            raise PolishOutlookQualityError(
                "PL Outlook contains a source unrelated to the resolution metric"
            )

    if len(set(provenance_ids)) != len(provenance_ids):
        raise PolishOutlookQualityError(
            "PL Outlook counts duplicated provenance as independent evidence"
        )


def validate_pl_edition(edition: dict[str, Any]) -> None:
    """Reject vague, mixed-topic or objectively unresolvable Polish forecasts."""
    if not isinstance(edition, dict):
        raise PolishOutlookQualityError("missing Polish AI Outlook edition")

    resolution = edition.get("resolution")
    if not isinstance(resolution, dict):
        raise PolishOutlookQualityError("PL Outlook has no structured resolution")

    metric = str(resolution.get("metric") or "").strip()
    if len(metric) < 12:
        raise PolishOutlookQualityError("PL Outlook metric is too vague")
    if _AMBIGUOUS_METRIC.search(_plain(metric)):
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
    if not str(resolution.get("unit") or "").strip():
        raise PolishOutlookQualityError("PL Outlook resolution unit is missing")
    if not str(resolution.get("data_source_for_verification") or "").strip():
        raise PolishOutlookQualityError("PL Outlook verification source is missing")
    verification_url = str(resolution.get("verification_url") or "")
    if not verification_url.startswith("https://"):
        raise PolishOutlookQualityError("PL Outlook verification URL must use HTTPS")

    try:
        baseline_date = date.fromisoformat(str(resolution.get("baseline_date") or ""))
        resolution_date = date.fromisoformat(str(resolution.get("resolution_date") or ""))
    except ValueError as exc:
        raise PolishOutlookQualityError("PL Outlook resolution dates are invalid") from exc
    if resolution_date <= baseline_date:
        raise PolishOutlookQualityError("PL Outlook resolution date must be in the future")

    public_text = " ".join(
        str(edition.get(field) or "")
        for field in (
            "title", "thesis", "rationale", "confirmation", "invalidation",
            "resolution_summary",
        )
    )
    plain_public_text = _plain(public_text)
    for phrase in _VAGUE_PHRASES:
        if _plain(phrase) in plain_public_text:
            raise PolishOutlookQualityError(
                f"PL Outlook contains a non-testable phrase: {phrase}"
            )

    threshold_number = float(threshold)
    for field in ("confirmation", "invalidation", "resolution_summary"):
        if not _contains_threshold(edition.get(field), threshold_number):
            raise PolishOutlookQualityError(
                f"PL Outlook {field} does not state the locked threshold"
            )

    metric_stems = _stems(metric)
    if len(metric_stems & _stems(edition.get("confirmation"))) < 1:
        raise PolishOutlookQualityError(
            "PL Outlook confirmation is not tied to the resolution metric"
        )
    if len(metric_stems & _stems(edition.get("resolution_summary"))) < 1:
        raise PolishOutlookQualityError(
            "PL Outlook resolution summary is not tied to the resolution metric"
        )

    _validate_source_coherence(edition, metric)

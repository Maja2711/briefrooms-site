#!/usr/bin/env python3
"""Public analysis contract for BriefRooms AI Outlook editions.

The headline probability must name one exact event.  A separate direction
object prevents procedural forecasts (for example, that a court will rule)
from being mistaken for a forecast of the ruling's merits.
"""

from __future__ import annotations

import re
from typing import Any

ANALYSIS_CONTRACT_VERSION = "ai-outlook-analysis-v1"
ANALYSIS_TEXT_FIELDS = (
    "probability_event",
    "analysis_summary",
    "impact",
    "watch_items",
)
DIRECTION_STATUSES = {
    "estimated",
    "embedded_in_event",
    "insufficient_evidence",
    "not_applicable",
}
WORD = re.compile(r"[a-ząćęłńóśźż0-9]+", re.IGNORECASE)
STOPWORDS = {
    "oraz", "który", "która", "które", "przez", "tego", "jest", "będzie",
    "zostanie", "do", "the", "and", "that", "this", "will", "with", "from",
    "for", "into", "its", "within", "before", "after",
}


class OutlookAnalysisError(ValueError):
    """Raised when public AI Outlook analysis is ambiguous or too thin."""


def _compact(value: Any, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit].rstrip()


def _tokens(value: Any) -> set[str]:
    return {
        token.lower()[:9]
        for token in WORD.findall(_compact(value).lower())
        if len(token) >= 4 and token.lower() not in STOPWORDS and not token.isdigit()
    }


def _similar(left: Any, right: Any) -> float:
    a = _tokens(left)
    b = _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def validate_public_analysis(edition: dict[str, Any], language: str) -> None:
    """Validate explicit probability semantics and value-added analysis."""
    if language not in {"pl", "en"}:
        raise OutlookAnalysisError(f"unsupported analysis language: {language}")
    if edition.get("analysis_contract_version") != ANALYSIS_CONTRACT_VERSION:
        raise OutlookAnalysisError(f"missing {language} analysis contract version")

    minimum = {
        "probability_event": 34,
        "analysis_summary": 70,
        "impact": 65,
        "watch_items": 50,
    }
    for field in ANALYSIS_TEXT_FIELDS:
        value = _compact(edition.get(field))
        if len(value) < minimum[field]:
            raise OutlookAnalysisError(f"{language}.{field} is too vague")

    resolution = edition.get("resolution")
    if not isinstance(resolution, dict):
        raise OutlookAnalysisError(f"missing {language} resolution for probability event")
    deadline = str(resolution.get("resolution_date") or "").strip()
    event = _compact(edition.get("probability_event"))
    if len(deadline) != 10 or deadline[:4] not in event:
        raise OutlookAnalysisError(f"{language}.probability_event omits the deadline")
    metric = _compact(resolution.get("metric"))
    if metric and not (_tokens(metric) & _tokens(event)):
        raise OutlookAnalysisError(f"{language}.probability_event does not identify the metric")

    # The analysis must add a new inference, not repeat the thesis or rationale.
    for source_field in ("thesis", "rationale"):
        if _similar(edition.get("analysis_summary"), edition.get(source_field)) >= 0.72:
            raise OutlookAnalysisError(
                f"{language}.analysis_summary merely repeats {source_field}"
            )

    direction = edition.get("direction")
    if not isinstance(direction, dict):
        raise OutlookAnalysisError(f"missing {language} direction assessment")
    status = str(direction.get("status") or "")
    if status not in DIRECTION_STATUSES:
        raise OutlookAnalysisError(f"invalid {language} direction status")
    perspective = _compact(direction.get("perspective"), 160)
    explanation = _compact(direction.get("explanation"), 520)
    scenarios = direction.get("scenarios")
    if not isinstance(scenarios, list):
        raise OutlookAnalysisError(f"invalid {language} direction scenarios")

    if status == "estimated":
        if len(perspective) < 4 or len(explanation) < 45 or not 2 <= len(scenarios) <= 3:
            raise OutlookAnalysisError(f"incomplete {language} directional estimate")
        total = 0
        labels: set[str] = set()
        for scenario in scenarios:
            if not isinstance(scenario, dict):
                raise OutlookAnalysisError(f"invalid {language} direction scenario")
            label = _compact(scenario.get("label"), 90)
            meaning = _compact(scenario.get("meaning"), 300)
            probability = scenario.get("probability")
            if not label or len(meaning) < 24:
                raise OutlookAnalysisError(f"vague {language} direction scenario")
            if not isinstance(probability, int) or isinstance(probability, bool) or not 1 <= probability <= 99:
                raise OutlookAnalysisError(f"invalid {language} direction probability")
            labels.add(label.lower())
            total += probability
        if len(labels) != len(scenarios) or total != 100:
            raise OutlookAnalysisError(
                f"{language} direction scenarios must be unique and sum to 100"
            )
    else:
        if scenarios:
            raise OutlookAnalysisError(
                f"{language} direction scenarios require status=estimated"
            )
        if len(explanation) < 45:
            raise OutlookAnalysisError(f"missing {language} direction explanation")
        if status in {"embedded_in_event", "insufficient_evidence"} and len(perspective) < 4:
            raise OutlookAnalysisError(f"missing {language} direction perspective")

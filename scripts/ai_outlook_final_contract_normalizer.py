#!/usr/bin/env python3
"""Deterministically normalize locked AI Outlook final-contract fields.

The normalizer never changes the selected forecast, probability, threshold,
metric or deadline. Mechanically derivable probability semantics are always
rendered from the immutable resolution contract so model wording cannot drift
away from the event that will later be resolved.
"""
from __future__ import annotations

import copy
import json
from typing import Any

import comment_quality


def _payload(message: dict[str, str]) -> dict[str, Any] | None:
    if not isinstance(message, dict) or message.get("role") != "user":
        return None
    try:
        value = json.loads(str(message.get("content") or ""))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _locked_context(messages) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return locked resolution and requested final JSON shape, if present."""
    for message in messages or []:
        payload = _payload(message)
        if not payload:
            continue
        locked = payload.get("locked_candidate")
        shape = payload.get("required_json_shape")
        if not isinstance(locked, dict) or not isinstance(shape, dict):
            continue
        resolution = locked.get("resolution")
        if isinstance(resolution, dict):
            return resolution, shape
    return None, None


def _ensure_deadline(text: Any, deadline: str, language: str = "pl") -> str:
    value = " ".join(str(text or "").split()).strip()
    if not value:
        return value
    year = deadline[:4]
    ddmmyyyy = f"{deadline[8:10]}.{deadline[5:7]}.{deadline[:4]}"
    if deadline in value or ddmmyyyy in value or year in value:
        return value
    if value[-1:] not in ".!?":
        value += "."
    suffix = (
        f" Termin rozstrzygnięcia: {deadline}."
        if language == "pl"
        else f" Resolution date: {deadline}."
    )
    return value + suffix


def _format_threshold(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def probability_event_from_resolution(resolution: dict[str, Any], language: str) -> str:
    """Render the exact event measured by probability from the locked rubric."""
    metric = " ".join(str(resolution.get("metric") or "").split()).strip()
    deadline = str(resolution.get("resolution_date") or "").strip()
    operator = str(resolution.get("comparison_operator") or "").strip()
    threshold = resolution.get("threshold")
    unit = " ".join(str(resolution.get("unit") or "").split()).strip()

    if not metric or len(deadline) != 10:
        return ""

    binary_units = {"boolean", "binary", "zdarzenie binarne", "event"}
    is_binary = unit.lower() in binary_units or (
        threshold in (1, 1.0, True) and operator in {"==", ">=", ""}
    )
    if is_binary:
        if language == "pl":
            return f"Do {deadline} nastąpi zdarzenie: {metric}."
        return f"By {deadline}, the following event occurs: {metric}."

    if threshold is None:
        if language == "pl":
            return f"Do {deadline} zostanie rozstrzygnięty wskaźnik: {metric}."
        return f"By {deadline}, the following metric is resolved: {metric}."

    threshold_text = _format_threshold(threshold)
    value_text = f"{threshold_text} {unit}".strip()
    pl_ops = {
        ">=": "wyniesie co najmniej",
        "<=": "wyniesie co najwyżej",
        "==": "wyniesie dokładnie",
        ">": "przekroczy",
        "<": "spadnie poniżej",
    }
    en_ops = {
        ">=": "is at least",
        "<=": "is at most",
        "==": "equals",
        ">": "is above",
        "<": "is below",
    }
    if language == "pl":
        phrase = pl_ops.get(operator, "osiągnie wartość")
        return f"Do {deadline} {metric} {phrase} {value_text}."
    phrase = en_ops.get(operator, "reaches")
    return f"By {deadline}, {metric} {phrase} {value_text}."


def _normalize_bilingual(result: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(result)
    deadline = str(resolution.get("resolution_date") or "").strip()
    for language in ("pl", "en"):
        section = normalized.get(language)
        if not isinstance(section, dict):
            continue
        # This field is contract metadata, not editorial prose. Always replace
        # model wording with the deterministic representation of the locked
        # metric/threshold/deadline so the displayed percentage and resolver
        # can never describe different events.
        event = probability_event_from_resolution(resolution, language)
        if event:
            section["probability_event"] = event
        if len(deadline) == 10:
            for field in ("confirmation", "invalidation", "resolution_summary"):
                section[field] = _ensure_deadline(
                    section.get(field), deadline, language
                )
    return normalized


def install() -> None:
    if getattr(comment_quality, "_briefrooms_pl_final_deadline_normalizer", False):
        return
    original = comment_quality.request_json_completion

    def wrapped(**kwargs):
        messages = kwargs.get("messages") or []
        resolution, shape = _locked_context(messages)
        result = original(**kwargs)
        if not resolution or not isinstance(result, dict) or not isinstance(shape, dict):
            return result

        # Canonical v2 final editor asks for bilingual {pl, en}. Probability
        # semantics come exclusively from the locked resolution contract.
        if "pl" in shape and "en" in shape:
            return _normalize_bilingual(result, resolution)

        # Preserve the older single-language PL normalization path while also
        # locking probability_event to the same resolution rubric.
        if "title" in shape and "thesis" in shape and "category" in shape:
            deadline = str(resolution.get("resolution_date") or "").strip()
            if len(deadline) != 10:
                return result
            normalized = copy.deepcopy(result)
            for field in ("confirmation", "invalidation", "resolution_summary"):
                normalized[field] = _ensure_deadline(
                    normalized.get(field), deadline, "pl"
                )
            event = probability_event_from_resolution(resolution, "pl")
            if event:
                normalized["probability_event"] = event
            return normalized
        return result

    comment_quality.request_json_completion = wrapped
    comment_quality._briefrooms_pl_final_deadline_normalizer = True

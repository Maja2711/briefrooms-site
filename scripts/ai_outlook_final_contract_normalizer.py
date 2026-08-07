#!/usr/bin/env python3
"""Deterministically expose locked PL resolution metadata in final copy.

The normalizer never changes the selected forecast, probability, threshold,
metric or deadline. It only makes the already locked resolution deadline
explicit in the three public fields required by the PL quality contract.
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


def _locked_resolution(messages) -> dict[str, Any] | None:
    for message in messages or []:
        payload = _payload(message)
        if not payload:
            continue
        locked = payload.get("locked_candidate")
        shape = payload.get("required_json_shape")
        if not (
            isinstance(locked, dict)
            and isinstance(shape, dict)
            and "title" in shape
            and "thesis" in shape
            and "category" in shape
            and "pl" not in shape
            and "en" not in shape
        ):
            continue
        resolution = locked.get("resolution")
        if isinstance(resolution, dict):
            return resolution
    return None


def _ensure_deadline(text: Any, deadline: str) -> str:
    value = " ".join(str(text or "").split()).strip()
    if not value:
        return value
    year = deadline[:4]
    ddmmyyyy = f"{deadline[8:10]}.{deadline[5:7]}.{deadline[:4]}"
    if deadline in value or ddmmyyyy in value or year in value:
        return value
    if value[-1:] not in ".!?":
        value += "."
    return f"{value} Termin rozstrzygnięcia: {deadline}."


def install() -> None:
    if getattr(comment_quality, "_briefrooms_pl_final_deadline_normalizer", False):
        return
    original = comment_quality.request_json_completion

    def wrapped(**kwargs):
        messages = kwargs.get("messages") or []
        resolution = _locked_resolution(messages)
        result = original(**kwargs)
        if not resolution or not isinstance(result, dict):
            return result
        deadline = str(resolution.get("resolution_date") or "").strip()
        if len(deadline) != 10:
            return result
        normalized = copy.deepcopy(result)
        for field in ("confirmation", "invalidation", "resolution_summary"):
            normalized[field] = _ensure_deadline(normalized.get(field), deadline)
        return normalized

    comment_quality.request_json_completion = wrapped
    comment_quality._briefrooms_pl_final_deadline_normalizer = True

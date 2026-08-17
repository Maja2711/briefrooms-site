#!/usr/bin/env python3
"""Expand only placeholders whose value is already locked in the forecast.

This is not a prose fallback. It merely prevents literal template tokens such
as {deadline} from reaching the public quality gate when the locked resolution
date is already known.
"""
from __future__ import annotations

import copy
import json
from typing import Any

import comment_quality


def _locked_values(messages: list[dict[str, str]]) -> dict[str, str]:
    for message in messages or []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        try:
            payload = json.loads(str(message.get("content") or ""))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        locked = payload.get("locked_candidate")
        if not isinstance(locked, dict):
            continue
        resolution = locked.get("resolution")
        if not isinstance(resolution, dict):
            continue
        deadline = str(resolution.get("resolution_date") or "").strip()
        if not deadline:
            continue
        return {
            "{deadline}": deadline,
            "{resolution_date}": deadline,
        }
    return {}


def _replace(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, str):
        for token, replacement in mapping.items():
            value = value.replace(token, replacement)
        return value
    if isinstance(value, list):
        return [_replace(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: _replace(item, mapping) for key, item in value.items()}
    return value


def install() -> None:
    if getattr(comment_quality, "_briefrooms_outlook_placeholder_normalizer", False):
        return
    original = comment_quality.request_json_completion

    def wrapped(**kwargs):
        result = original(**kwargs)
        mapping = _locked_values(kwargs.get("messages") or [])
        if not mapping:
            return result
        return _replace(copy.deepcopy(result), mapping)

    comment_quality.request_json_completion = wrapped
    comment_quality._briefrooms_outlook_placeholder_normalizer = True

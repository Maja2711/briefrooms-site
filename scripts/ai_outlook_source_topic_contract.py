#!/usr/bin/env python3
"""Reject PL AI Outlook candidates that are unrelated to their source URLs.

This mirrors the final public source-coherence guard before a candidate reaches
selection, so Gemini can correct the topic instead of failing only at the end.
"""
from __future__ import annotations

import copy
import json
from typing import Any
from urllib.parse import unquote, urlparse

import comment_quality
from ai_outlook_pl_quality import _stems


def _payload(message: dict[str, str]) -> dict[str, Any] | None:
    if not isinstance(message, dict) or message.get("role") != "user":
        return None
    try:
        value = json.loads(str(message.get("content") or ""))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _context(messages):
    for message in messages or []:
        payload = _payload(message)
        if not payload:
            continue
        shape = payload.get("required_json_shape")
        if payload.get("language") == "pl" and isinstance(shape, dict) and "candidates" in shape:
            sources = {
                source.get("id"): source
                for source in payload.get("sources", [])
                if isinstance(source, dict) and source.get("id") is not None
            }
            return payload, sources
    return None, {}


def _candidate_is_coherent(candidate: dict[str, Any], sources: dict[Any, dict[str, Any]]) -> bool:
    resolution = candidate.get("resolution") if isinstance(candidate.get("resolution"), dict) else {}
    topic_stems = _stems(
        " ".join(
            str(value or "")
            for value in (
                candidate.get("title"),
                candidate.get("forecast_statement"),
                resolution.get("metric"),
            )
        )
    )
    if not topic_stems:
        return False

    matched_source = False
    for source_id in candidate.get("source_ids") or []:
        source = sources.get(source_id)
        if not isinstance(source, dict):
            continue
        matched_source = True
        url = str(source.get("url") or "")
        parsed = urlparse(url)
        path_text = unquote(parsed.path.replace("-", " ").replace("_", " "))
        path_stems = _stems(path_text)
        if len(path_stems) >= 5 and not (path_stems & topic_stems):
            return False
        title_stems = _stems(str(source.get("title") or ""))
        if len(title_stems) >= 4 and not (title_stems & topic_stems):
            return False
    return matched_source


def _augment(messages, retry=False):
    result = copy.deepcopy(messages)
    for message in result:
        payload = _payload(message)
        if not payload:
            continue
        shape = payload.get("required_json_shape")
        if not (payload.get("language") == "pl" and isinstance(shape, dict) and "candidates" in shape):
            continue
        rules = list(payload.get("hard_rules") or [])
        rules.extend([
            "temat title, forecast_statement i resolution.metric musi wynikać bezpośrednio z tytułu/opisu/URL każdego wskazanego źródła",
            "nie wolno przenosić prognozy na inny program, fundusz, regulację lub projekt tylko dlatego, że dotyczy tej samej instytucji",
            "kluczowe rzeczowniki prognozowanego rezultatu muszą być rozpoznawalne w źródle; sama zgodność nazwy instytucji nie wystarcza",
        ])
        if retry:
            payload["source_topic_retry"] = (
                "Poprzedni kandydat nie był zgodny tematycznie z URL/tytułem źródła. "
                "Wybierz inny kandydat albo opisz dokładnie zdarzenie z wybranego źródła; "
                "nie używaj pokrewnego programu tej samej instytucji."
            )
        payload["hard_rules"] = rules
        message["content"] = json.dumps(payload, ensure_ascii=False)
    return result


def install() -> None:
    if getattr(comment_quality, "_briefrooms_pl_source_topic_contract", False):
        return
    original = comment_quality.request_json_completion

    def wrapped(**kwargs):
        messages = kwargs.get("messages") or []
        _, sources = _context(messages)
        if not sources:
            return original(**kwargs)

        for attempt in range(2):
            call_kwargs = dict(kwargs)
            call_kwargs["messages"] = _augment(messages, retry=attempt > 0)
            result = original(**call_kwargs)
            if not isinstance(result, dict) or not isinstance(result.get("candidates"), list):
                return result
            valid = [
                candidate for candidate in result["candidates"]
                if isinstance(candidate, dict) and _candidate_is_coherent(candidate, sources)
            ]
            if valid:
                output = dict(result)
                output["candidates"] = valid
                return output

        output = dict(result) if isinstance(result, dict) else {}
        output["candidates"] = []
        return output

    comment_quality.request_json_completion = wrapped
    comment_quality._briefrooms_pl_source_topic_contract = True

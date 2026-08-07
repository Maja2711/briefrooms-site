#!/usr/bin/env python3
"""Reject PL AI Outlook candidates unrelated to their cited source evidence.

Title and summary are the primary evidence. URL-path terms are a secondary
fallback only, because one article may contain a forecastable secondary fact
that is not reflected in its slug.
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

        evidence_stems = _stems(
            " ".join(
                str(source.get(field) or "")
                for field in ("title", "summary")
            )
        )
        evidence_overlap = evidence_stems & topic_stems
        if len(evidence_stems) >= 4:
            # The actual article evidence is authoritative for topic matching.
            if not evidence_overlap:
                return False
            continue

        # Only when title/summary are too sparse do we fall back to the URL path.
        url = str(source.get("url") or "")
        parsed = urlparse(url)
        path_text = unquote(parsed.path.replace("-", " ").replace("_", " "))
        path_stems = _stems(path_text)
        if len(path_stems) >= 5 and not (path_stems & topic_stems):
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
            "temat title, forecast_statement i resolution.metric musi wynikać bezpośrednio z tytułu lub streszczenia wskazanego źródła",
            "URL jest sygnałem pomocniczym; jeżeli streszczenie artykułu wprost opisuje prognozowane zdarzenie, nie odrzucaj go tylko dlatego, że slug URL opisuje główny wątek artykułu",
            "nie wolno przenosić prognozy na inny program, fundusz, regulację lub projekt, którego nie ma ani w tytule, ani w streszczeniu",
            "sama zgodność nazwy instytucji nie wystarcza; prognozowany rezultat musi mieć wspólne pojęcia z rzeczywistą treścią źródła",
        ])
        if retry:
            payload["source_topic_retry"] = (
                "Poprzedni kandydat nie był zgodny tematycznie z tytułem/streszczeniem źródła. "
                "Wybierz inne źródło albo opisz dokładnie zdarzenie, które jest w nim wprost wymienione."
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

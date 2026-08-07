#!/usr/bin/env python3
"""Strict PL candidate contract used before the canonical AI Outlook publisher.

This module does not lower any publication threshold and does not invent a
forecast. It only makes two existing requirements explicit to the model and
removes invalid candidates before the governed methodology sees them.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

import comment_quality

_META = re.compile(
    r"\b(?:artyku(?:ł|l)|publikac\w*|komunikat\w*|aktualizac\w*|wzmiank\w*|"
    r"zainteresowani\w*|nagłów\w*|headline\w*|mention\w*|update\w*)\b",
    re.IGNORECASE,
)


def _is_pl_candidate_request(messages: list[dict[str, str]]) -> bool:
    for message in messages or []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        try:
            payload = json.loads(str(message.get("content") or ""))
        except Exception:
            continue
        shape = payload.get("required_json_shape") if isinstance(payload, dict) else None
        if (
            isinstance(payload, dict)
            and payload.get("language") == "pl"
            and isinstance(shape, dict)
            and "candidates" in shape
        ):
            return True
    return False


def _augment(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    result = copy.deepcopy(messages)
    for message in result:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        try:
            payload = json.loads(str(message.get("content") or ""))
        except Exception:
            continue
        shape = payload.get("required_json_shape") if isinstance(payload, dict) else None
        if not (
            isinstance(payload, dict)
            and payload.get("language") == "pl"
            and isinstance(shape, dict)
            and "candidates" in shape
        ):
            continue

        payload["candidate_contract_correction"] = {
            "meta_forecast_scope": (
                "Zakaz prognozowania mediów dotyczy title, forecast_statement, "
                "selection_reason i resolution.metric. selection_reason ma opisywać "
                "znaczenie przyszłego rezultatu, a nie artykuł, publikację, komunikat, "
                "aktualizację, nagłówek, wzmiankę ani zainteresowanie tematem."
            ),
            "continuous_threshold": (
                "Jeżeli unit nie jest zdarzeniem binarnym, threshold MUSI różnić się "
                "od baseline_value i oznaczać przyszły poziom. Nie wolno kopiować "
                "baseline do threshold. Baseline musi pochodzić ze źródła; threshold "
                "jest prognozowanym poziomem, który sam oceniasz."
            ),
            "verification_competence": (
                "Wybierz oficjalne źródło mające kompetencję do potwierdzenia metryki. "
                "Dla detalicznych obligacji Skarbu Państwa użyj Gov.pl, nie NBP."
            ),
            "preference": (
                "Jeżeli masz kilka równie mocnych okazji, preferuj zapowiedziane "
                "zdarzenie/decyzję z jednoznacznym terminem albo wskaźnik z wyraźnym "
                "baseline i naturalnym oficjalnym źródłem weryfikacji."
            ),
        }
        rules = list(payload.get("hard_rules") or [])
        rules.extend(
            [
                "selection_reason nie może zawierać słów artykuł, publikacja, komunikat, aktualizacja, nagłówek, wzmianka ani zainteresowanie",
                "dla metryki ciągłej threshold != baseline_value",
                "nie przedstawiaj osiągniętego już baseline jako przyszłego progu",
                "verification_url musi odpowiadać kompetencji instytucji dla danej metryki",
            ]
        )
        payload["hard_rules"] = rules
        message["content"] = json.dumps(payload, ensure_ascii=False)
    return result


def _invalid(candidate: dict[str, Any]) -> bool:
    text = " ".join(
        str(candidate.get(field) or "")
        for field in ("title", "forecast_statement", "selection_reason")
    )
    resolution = candidate.get("resolution") if isinstance(candidate.get("resolution"), dict) else {}
    metric = str(resolution.get("metric") or "")
    if _META.search(text) or _META.search(metric):
        return True

    unit = str(resolution.get("unit") or "").lower()
    if "binar" not in unit:
        try:
            baseline = float(resolution.get("baseline_value"))
            threshold = float(resolution.get("threshold"))
        except (TypeError, ValueError):
            return False
        if abs(baseline - threshold) <= max(1e-9, abs(baseline) * 1e-9):
            return True
    return False


def install() -> None:
    if getattr(comment_quality, "_briefrooms_pl_candidate_contract_installed", False):
        return
    original = comment_quality.request_json_completion

    def wrapped(**kwargs):
        messages = kwargs.get("messages") or []
        if not _is_pl_candidate_request(messages):
            return original(**kwargs)

        first_kwargs = dict(kwargs)
        first_kwargs["messages"] = _augment(messages)
        payload = original(**first_kwargs)
        if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
            return payload
        valid = [row for row in payload["candidates"] if isinstance(row, dict) and not _invalid(row)]
        if valid:
            payload = dict(payload)
            payload["candidates"] = valid
            return payload

        # One bounded second attempt. No values are fabricated by code.
        second_messages = _augment(messages)
        for message in second_messages:
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            try:
                data = json.loads(str(message.get("content") or ""))
            except Exception:
                continue
            if isinstance(data, dict) and data.get("language") == "pl":
                data["retry_instruction"] = (
                    "Poprzednia odpowiedź nie miała żadnego poprawnego kandydata: "
                    "nie używaj języka medialnego w polach prognozy i nie ustawiaj "
                    "threshold równego baseline. Wygeneruj nowego kandydata z "
                    "rzeczywistym przyszłym rezultatem albo zwróć pustą listę tylko, "
                    "jeśli żadne candidate_opportunities nie istnieją."
                )
                message["content"] = json.dumps(data, ensure_ascii=False)
        second_kwargs = dict(kwargs)
        second_kwargs["messages"] = second_messages
        payload = original(**second_kwargs)
        if isinstance(payload, dict) and isinstance(payload.get("candidates"), list):
            payload = dict(payload)
            payload["candidates"] = [
                row for row in payload["candidates"]
                if isinstance(row, dict) and not _invalid(row)
            ]
        return payload

    comment_quality.request_json_completion = wrapped
    comment_quality._briefrooms_pl_candidate_contract_installed = True

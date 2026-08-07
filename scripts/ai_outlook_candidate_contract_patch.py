#!/usr/bin/env python3
"""Strict PL contracts used before the canonical AI Outlook publisher.

This module does not lower publication thresholds and does not invent a
forecast. It makes existing candidate/final-copy requirements explicit and
uses one bounded retry when Gemini violates them.
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


def _user_payload(message: dict[str, str]) -> dict[str, Any] | None:
    if not isinstance(message, dict) or message.get("role") != "user":
        return None
    try:
        value = json.loads(str(message.get("content") or ""))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _is_pl_candidate_request(messages: list[dict[str, str]]) -> bool:
    for message in messages or []:
        payload = _user_payload(message)
        if not payload:
            continue
        shape = payload.get("required_json_shape")
        if (
            payload.get("language") == "pl"
            and isinstance(shape, dict)
            and "candidates" in shape
        ):
            return True
    return False


def _is_pl_final_request(messages: list[dict[str, str]]) -> bool:
    for message in messages or []:
        payload = _user_payload(message)
        if not payload:
            continue
        shape = payload.get("required_json_shape")
        if (
            isinstance(payload.get("locked_candidate"), dict)
            and isinstance(shape, dict)
            and "category" in shape
            and "title" in shape
            and "thesis" in shape
            and "pl" not in shape
            and "en" not in shape
        ):
            return True
    return False


def _augment_candidate(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    result = copy.deepcopy(messages)
    for message in result:
        payload = _user_payload(message)
        if not payload:
            continue
        shape = payload.get("required_json_shape")
        if not (
            payload.get("language") == "pl"
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


def _augment_final(messages: list[dict[str, str]], retry: bool = False) -> list[dict[str, str]]:
    result = copy.deepcopy(messages)
    for message in result:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "system":
            message["content"] = str(message.get("content") or "") + (
                " W finalnym tekście opisuj wyłącznie prognozowany rezultat, jego mechanizm, "
                "warunek potwierdzenia i warunek zanegowania. W żadnym polu nie używaj słów "
                "artykuł, publikacja, komunikat, aktualizacja, nagłówek, wzmianka ani "
                "zainteresowanie. Jeżeli trzeba wskazać weryfikację, napisz że wynik zostanie "
                "sprawdzony w oficjalnych danych wskazanej instytucji."
            )
            continue
        payload = _user_payload(message)
        if not payload:
            continue
        shape = payload.get("required_json_shape")
        if not (
            isinstance(payload.get("locked_candidate"), dict)
            and isinstance(shape, dict)
            and "category" in shape
            and "title" in shape
            and "thesis" in shape
            and "pl" not in shape
            and "en" not in shape
        ):
            continue
        requirements = list(payload.get("requirements") or [])
        requirements.extend(
            [
                "żadne pole finalnego tekstu nie może opisywać artykułu, publikacji, komunikatu, aktualizacji, nagłówka, wzmianki ani zainteresowania; opisuj wyłącznie wynik w świecie",
                "resolution_summary opisuje metrykę i warunek rozstrzygnięcia bez fraz typu po publikacji/po komunikacie; użyj sformułowania w oficjalnych danych instytucji",
                "nie kopiuj medialnego słownictwa z selection_reason zablokowanego kandydata",
            ]
        )
        if retry:
            requirements.append(
                "To jest poprawka po odrzuceniu redakcyjnym: zachowaj wszystkie zablokowane fakty i przepisz każde pole zawierające język medialny na opis rzeczywistego rezultatu."
            )
        payload["requirements"] = requirements
        message["content"] = json.dumps(payload, ensure_ascii=False)
    return result


def _invalid_candidate(candidate: dict[str, Any]) -> bool:
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


def _invalid_final(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return True
    fields = (
        "category", "title", "thesis", "horizon", "rationale",
        "confirmation", "invalidation", "resolution_summary",
    )
    if any(not str(payload.get(field) or "").strip() for field in fields):
        return True
    return bool(_META.search(" ".join(str(payload.get(field) or "") for field in fields)))


def install() -> None:
    if getattr(comment_quality, "_briefrooms_pl_candidate_contract_installed", False):
        return
    original = comment_quality.request_json_completion

    def wrapped(**kwargs):
        messages = kwargs.get("messages") or []

        if _is_pl_final_request(messages):
            first_kwargs = dict(kwargs)
            first_kwargs["messages"] = _augment_final(messages, retry=False)
            payload = original(**first_kwargs)
            if not _invalid_final(payload):
                return payload
            second_kwargs = dict(kwargs)
            second_kwargs["messages"] = _augment_final(messages, retry=True)
            return original(**second_kwargs)

        if not _is_pl_candidate_request(messages):
            return original(**kwargs)

        first_kwargs = dict(kwargs)
        first_kwargs["messages"] = _augment_candidate(messages)
        payload = original(**first_kwargs)
        if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
            return payload
        valid = [
            row for row in payload["candidates"]
            if isinstance(row, dict) and not _invalid_candidate(row)
        ]
        if valid:
            payload = dict(payload)
            payload["candidates"] = valid
            return payload

        second_messages = _augment_candidate(messages)
        for message in second_messages:
            payload_message = _user_payload(message)
            if isinstance(payload_message, dict) and payload_message.get("language") == "pl":
                payload_message["retry_instruction"] = (
                    "Poprzednia odpowiedź nie miała żadnego poprawnego kandydata: "
                    "nie używaj języka medialnego w polach prognozy i nie ustawiaj "
                    "threshold równego baseline. Wygeneruj nowego kandydata z "
                    "rzeczywistym przyszłym rezultatem albo zwróć pustą listę tylko, "
                    "jeśli żadne candidate_opportunities nie istnieją."
                )
                message["content"] = json.dumps(payload_message, ensure_ascii=False)
        second_kwargs = dict(kwargs)
        second_kwargs["messages"] = second_messages
        payload = original(**second_kwargs)
        if isinstance(payload, dict) and isinstance(payload.get("candidates"), list):
            payload = dict(payload)
            payload["candidates"] = [
                row for row in payload["candidates"]
                if isinstance(row, dict) and not _invalid_candidate(row)
            ]
        return payload

    comment_quality.request_json_completion = wrapped
    comment_quality._briefrooms_pl_candidate_contract_installed = True

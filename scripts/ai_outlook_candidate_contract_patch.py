#!/usr/bin/env python3
"""Strict PL contracts used before the canonical AI Outlook publisher.

This module does not lower publication thresholds and does not invent a
forecast. It makes existing candidate/final-copy requirements explicit and
uses bounded retries when Gemini violates the same methodology gate used by
the publisher.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import date
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


def _candidate_context(messages: list[dict[str, str]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str, list[str]]:
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
            return (
                payload,
                [row for row in payload.get("sources", []) if isinstance(row, dict)],
                str(payload.get("publication_date_europe_warsaw") or ""),
                [str(value) for value in payload.get("recent_titles_to_avoid", [])],
            )
    return None, [], "", []


def _is_pl_candidate_request(messages: list[dict[str, str]]) -> bool:
    return _candidate_context(messages)[0] is not None


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


def _augment_candidate(messages: list[dict[str, str]], retry_reasons: list[str] | None = None) -> list[dict[str, str]]:
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
                "od baseline_value i oznaczać przyszły poziom. Baseline musi pochodzić "
                "ze źródła; threshold jest prognozowanym poziomem."
            ),
            "horizon_contract": (
                "AI Outlook jest średnioterminowy. resolution_date musi przypadać co "
                "najmniej 90 dni po publication_date. horizon_pl/horizon_en są etykietami "
                "prezentacyjnymi i zostaną wyliczone z resolution_date przez kod."
            ),
            "verification_competence": (
                "Wybierz oficjalne źródło mające kompetencję do potwierdzenia metryki. "
                "Dla detalicznych obligacji Skarbu Państwa użyj Gov.pl, nie NBP."
            ),
        }
        rules = list(payload.get("hard_rules") or [])
        rules.extend(
            [
                "selection_reason nie może zawierać słów artykuł, publikacja, komunikat, aktualizacja, nagłówek, wzmianka ani zainteresowanie",
                "dla metryki ciągłej threshold != baseline_value",
                "nie przedstawiaj osiągniętego już baseline jako przyszłego progu",
                "resolution_date musi być co najmniej 90 dni po publication_date",
                "verification_url musi odpowiadać kompetencji instytucji dla danej metryki",
            ]
        )
        payload["hard_rules"] = rules
        if retry_reasons:
            payload["retry_instruction"] = (
                "Poprzednia próba została odrzucona przez obowiązującą metodologię. "
                "Wygeneruj nowy zestaw, usuwając dokładnie te błędy: "
                + ", ".join(sorted(set(retry_reasons)))
                + ". Nie obniżaj jakości ani nie obchodź reguł."
            )
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


def _local_invalid_reason(candidate: dict[str, Any], publication_date: str) -> str | None:
    text = " ".join(
        str(candidate.get(field) or "")
        for field in ("title", "forecast_statement", "selection_reason")
    )
    resolution = candidate.get("resolution") if isinstance(candidate.get("resolution"), dict) else {}
    metric = str(resolution.get("metric") or "")
    if _META.search(text) or _META.search(metric):
        return "meta_forecast_contract"

    unit = str(resolution.get("unit") or "").lower()
    if "binar" not in unit:
        try:
            baseline = float(resolution.get("baseline_value"))
            threshold = float(resolution.get("threshold"))
        except (TypeError, ValueError):
            baseline = threshold = None
        if baseline is not None and abs(baseline - threshold) <= max(1e-9, abs(baseline) * 1e-9):
            return "threshold_equals_baseline"

    try:
        published = date.fromisoformat(publication_date)
        resolved = date.fromisoformat(str(resolution.get("resolution_date") or ""))
        if (resolved - published).days < 90:
            return "resolution_horizon_under_90_days"
    except ValueError:
        pass
    return None


def _methodology_valid_candidates(payload: Any, messages: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        return [], ["missing_candidates_array"]
    _, items, publication_date, _ = _candidate_context(messages)
    try:
        import ai_outlook_pl_methodology as plm
    except Exception:
        plm = None

    valid: list[dict[str, Any]] = []
    reasons: list[str] = []
    for row in payload["candidates"]:
        if not isinstance(row, dict):
            reasons.append("candidate_not_object")
            continue
        local_reason = _local_invalid_reason(row, publication_date)
        if local_reason:
            reasons.append(local_reason)
            continue
        if plm is not None:
            reason = plm.candidate_rejection_reason(row, items, publication_date)
            if reason:
                reasons.append(reason)
                continue
        valid.append(row)
    return valid, reasons


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

        last_payload: Any = {"candidates": []}
        retry_reasons: list[str] = []
        for attempt in range(3):
            attempt_messages = _augment_candidate(
                messages,
                retry_reasons=retry_reasons if attempt else None,
            )
            attempt_kwargs = dict(kwargs)
            attempt_kwargs["messages"] = attempt_messages
            last_payload = original(**attempt_kwargs)
            valid, retry_reasons = _methodology_valid_candidates(last_payload, messages)
            if valid:
                result = dict(last_payload)
                result["candidates"] = valid
                return result

        # Fail closed: the canonical publisher will stop instead of publishing filler.
        if isinstance(last_payload, dict):
            result = dict(last_payload)
            result["candidates"] = []
            return result
        return {"candidates": []}

    comment_quality.request_json_completion = wrapped
    comment_quality._briefrooms_pl_candidate_contract_installed = True

#!/usr/bin/env python3
"""Strict PL contracts used before the canonical AI Outlook publisher.

This module does not lower publication thresholds and does not invent a
forecast. It makes existing candidate/final-copy requirements explicit and
uses bounded retries when Gemini violates the same methodology gate used by
the publisher. If two copy-edit attempts violate only the editorial contract,
a deterministic final copy is rendered from the already locked candidate.
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
_MONTHS_PL = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4,
    "maja": 5, "czerwca": 6, "lipca": 7, "sierpnia": 8,
    "września": 9, "wrzesnia": 9, "października": 10,
    "pazdziernika": 10, "listopada": 11, "grudnia": 12,
}


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
        if payload.get("language") == "pl" and isinstance(shape, dict) and "candidates" in shape:
            return (
                payload,
                [row for row in payload.get("sources", []) if isinstance(row, dict)],
                str(payload.get("publication_date_europe_warsaw") or ""),
                [str(value) for value in payload.get("recent_titles_to_avoid", [])],
            )
    return None, [], "", []


def _final_context(messages: list[dict[str, str]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for message in messages or []:
        payload = _user_payload(message)
        if not payload:
            continue
        shape = payload.get("required_json_shape")
        locked = payload.get("locked_candidate")
        if (
            isinstance(locked, dict)
            and isinstance(shape, dict)
            and "category" in shape and "title" in shape and "thesis" in shape
            and "pl" not in shape and "en" not in shape
        ):
            return payload, locked
    return None, None


def _is_pl_candidate_request(messages: list[dict[str, str]]) -> bool:
    return _candidate_context(messages)[0] is not None


def _is_pl_final_request(messages: list[dict[str, str]]) -> bool:
    return _final_context(messages)[0] is not None


def _augment_candidate(messages: list[dict[str, str]], retry_reasons: list[str] | None = None) -> list[dict[str, str]]:
    result = copy.deepcopy(messages)
    for message in result:
        payload = _user_payload(message)
        if not payload:
            continue
        shape = payload.get("required_json_shape")
        if not (payload.get("language") == "pl" and isinstance(shape, dict) and "candidates" in shape):
            continue

        payload["candidate_contract_correction"] = {
            "meta_forecast_scope": (
                "Zakaz prognozowania mediów dotyczy title, forecast_statement, "
                "selection_reason i resolution.metric. selection_reason ma opisywać "
                "znaczenie przyszłego rezultatu, nie aktywność medialną."
            ),
            "continuous_threshold": (
                "Jeżeli unit nie jest zdarzeniem binarnym, threshold MUSI różnić się "
                "od baseline_value i oznaczać przyszły poziom."
            ),
            "deadline_contract": (
                "Jeżeli title lub forecast_statement zawiera konkretną datę lub termin "
                "zdarzenia, resolution_date MUSI być dokładnie tą samą datą. Nie wolno "
                "prognozować zdarzenia do jednej daty i rozstrzygać go później."
            ),
            "horizon_contract": (
                "Dopuszczalne są także krótkie realne horyzonty. horizon_pl/horizon_en "
                "są etykietami prezentacyjnymi i zostaną wyliczone przez kod z "
                "resolution_date; nie wydłużaj resolution_date tylko po to, by dopasować etykietę."
            ),
            "verification_competence": (
                "Wybierz oficjalne źródło mające kompetencję do potwierdzenia metryki."
            ),
        }
        rules = list(payload.get("hard_rules") or [])
        rules.extend([
            "selection_reason nie może opisywać artykułu, publikacji, komunikatu, aktualizacji, nagłówka, wzmianki ani zainteresowania",
            "dla metryki ciągłej threshold != baseline_value",
            "nie przedstawiaj osiągniętego już baseline jako przyszłego progu",
            "jeżeli prognoza podaje konkretną datę zdarzenia, resolution_date musi być identyczna z tą datą",
            "nie wydłużaj resolution_date sztucznie; krótki horyzont jest dozwolony, jeśli wynika ze źródła",
            "verification_url musi odpowiadać kompetencji instytucji dla danej metryki",
        ])
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
                "warunek potwierdzenia i warunek zanegowania. Daty w thesis, confirmation, "
                "invalidation i resolution_summary muszą być zgodne z zablokowaną "
                "resolution.resolution_date. Nie wprowadzaj alternatywnego terminu. "
                "Nie używaj języka medialnego."
            )
            continue
        payload = _user_payload(message)
        if not payload:
            continue
        shape = payload.get("required_json_shape")
        if not (
            isinstance(payload.get("locked_candidate"), dict)
            and isinstance(shape, dict)
            and "category" in shape and "title" in shape and "thesis" in shape
            and "pl" not in shape and "en" not in shape
        ):
            continue
        locked = payload.get("locked_candidate") or {}
        resolution = locked.get("resolution") if isinstance(locked.get("resolution"), dict) else {}
        deadline = str(resolution.get("resolution_date") or "")
        requirements = list(payload.get("requirements") or [])
        requirements.extend([
            "żadne pole finalnego tekstu nie może opisywać aktywności medialnej; opisuj wyłącznie wynik w świecie",
            f"jedynym terminem rozstrzygnięcia prognozy jest {deadline}; nie używaj innej daty granicznej w thesis, confirmation ani invalidation",
            f"confirmation, invalidation i resolution_summary muszą jawnie zawierać termin {deadline}",
            "resolution_summary opisuje metrykę i warunek rozstrzygnięcia w oficjalnych danych instytucji",
        ])
        if retry:
            requirements.append(
                "To jest poprawka po odrzuceniu: zachowaj zablokowane fakty, usuń sprzeczne daty i użyj wyłącznie resolution_date."
            )
        payload["requirements"] = requirements
        message["content"] = json.dumps(payload, ensure_ascii=False)
    return result


def _explicit_dates(text: str) -> set[str]:
    found: set[str] = set(re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", text))
    for day, month, year in re.findall(r"\b(\d{1,2})[.-](\d{1,2})[.-](20\d{2})\b", text):
        try:
            found.add(date(int(year), int(month), int(day)).isoformat())
        except ValueError:
            pass
    month_pattern = "|".join(map(re.escape, _MONTHS_PL))
    for day, month_name, year in re.findall(
        rf"\b(\d{{1,2}})\s+({month_pattern})\s+(20\d{{2}})\b",
        text.lower(),
    ):
        try:
            found.add(date(int(year), _MONTHS_PL[month_name], int(day)).isoformat())
        except ValueError:
            pass
    return found


def _local_invalid_reason(candidate: dict[str, Any], publication_date: str) -> str | None:
    statement = " ".join(str(candidate.get(field) or "") for field in ("title", "forecast_statement"))
    all_text = statement + " " + str(candidate.get("selection_reason") or "")
    resolution = candidate.get("resolution") if isinstance(candidate.get("resolution"), dict) else {}
    metric = str(resolution.get("metric") or "")
    if _META.search(all_text) or _META.search(metric):
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

    resolution_date = str(resolution.get("resolution_date") or "")
    dates_in_claim = _explicit_dates(statement)
    if dates_in_claim and resolution_date not in dates_in_claim:
        return "forecast_deadline_mismatch"
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
    fields = ("category", "title", "thesis", "horizon", "rationale", "confirmation", "invalidation", "resolution_summary")
    if any(not str(payload.get(field) or "").strip() for field in fields):
        return True
    return bool(_META.search(" ".join(str(payload.get(field) or "") for field in fields)))


def _operator_text(operator: str) -> tuple[str, str]:
    mapping = {
        ">=": ("co najmniej", "poniżej"),
        ">": ("powyżej", "nie przekroczy"),
        "<=": ("nie więcej niż", "powyżej"),
        "<": ("poniżej", "nie spadnie poniżej"),
        "==": ("osiągnie", "nie osiągnie"),
        "=": ("osiągnie", "nie osiągnie"),
    }
    return mapping.get(str(operator or "").strip(), ("osiągnie", "nie osiągnie"))


def _deterministic_final(messages: list[dict[str, str]]) -> dict[str, str] | None:
    payload, locked = _final_context(messages)
    if not payload or not locked:
        return None
    shape = payload.get("required_json_shape") or {}
    resolution = locked.get("resolution") if isinstance(locked.get("resolution"), dict) else {}
    deadline = str(resolution.get("resolution_date") or "").strip()
    metric = " ".join(str(resolution.get("metric") or "").split()).strip()
    threshold = resolution.get("threshold")
    unit = " ".join(str(resolution.get("unit") or "").split()).strip()
    source_name = " ".join(str(resolution.get("data_source_for_verification") or "oficjalnego źródła").split()).strip()
    operator = str(resolution.get("comparison_operator") or "").strip()
    confirm_phrase, reject_phrase = _operator_text(operator)
    threshold_text = f"{threshold} {unit}".strip()

    title = " ".join(str(locked.get("title") or "").split()).strip()
    thesis = " ".join(str(locked.get("forecast_statement") or "").split()).strip()
    reason = " ".join(str(locked.get("selection_reason") or "").split()).strip()
    causal = " ".join(str(locked.get("causal_chain") or "").split()).strip()
    rationale = " ".join(part for part in (reason, causal) if part).strip()
    if _META.search(rationale):
        rationale = reason if reason and not _META.search(reason) else (
            "Punkt wyjścia i mechanizm wynikają bezpośrednio z cytowanego źródła oraz zablokowanej metryki."
        )

    confirmation = (
        f"Do {deadline} w oficjalnych danych {source_name} metryka „{metric}” "
        f"{confirm_phrase} {threshold_text}."
    )
    invalidation = (
        f"Prognoza będzie nietrafiona, jeżeli do {deadline} w oficjalnych danych "
        f"{source_name} metryka „{metric}” {reject_phrase} {threshold_text}."
    )
    resolution_summary = (
        f"Termin rozstrzygnięcia: {deadline}. Sprawdzimy w oficjalnych danych "
        f"{source_name}, czy „{metric}” {confirm_phrase} {threshold_text}."
    )

    result = {
        "category": " ".join(str(shape.get("category") or "").split()).strip(),
        "title": title,
        "thesis": thesis,
        "horizon": " ".join(str(shape.get("horizon") or locked.get("horizon_pl") or "").split()).strip(),
        "rationale": rationale,
        "confirmation": confirmation,
        "invalidation": invalidation,
        "resolution_summary": resolution_summary,
    }
    if any(not value for value in result.values()) or _invalid_final(result):
        return None
    return result


def install() -> None:
    if getattr(comment_quality, "_briefrooms_pl_candidate_contract_installed", False):
        return
    original = comment_quality.request_json_completion

    def wrapped(**kwargs):
        messages = kwargs.get("messages") or []
        if _is_pl_final_request(messages):
            first_kwargs = dict(kwargs)
            first_kwargs["messages"] = _augment_final(messages, retry=False)
            first = original(**first_kwargs)
            if not _invalid_final(first):
                return first
            second_kwargs = dict(kwargs)
            second_kwargs["messages"] = _augment_final(messages, retry=True)
            second = original(**second_kwargs)
            if not _invalid_final(second):
                return second
            fallback = _deterministic_final(messages)
            if fallback is not None:
                return fallback
            return second

        if not _is_pl_candidate_request(messages):
            return original(**kwargs)

        last_payload: Any = {"candidates": []}
        retry_reasons: list[str] = []
        for attempt in range(3):
            attempt_messages = _augment_candidate(messages, retry_reasons if attempt else None)
            attempt_kwargs = dict(kwargs)
            attempt_kwargs["messages"] = attempt_messages
            last_payload = original(**attempt_kwargs)
            valid, retry_reasons = _methodology_valid_candidates(last_payload, messages)
            if valid:
                result = dict(last_payload)
                result["candidates"] = valid
                return result

        if isinstance(last_payload, dict):
            result = dict(last_payload)
            result["candidates"] = []
            return result
        return {"candidates": []}

    comment_quality.request_json_completion = wrapped
    comment_quality._briefrooms_pl_candidate_contract_installed = True

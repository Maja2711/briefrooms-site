#!/usr/bin/env python3
"""Require analytical value in AI Outlook without destabilising generation.

Candidate generation keeps the established methodology unchanged. Once a valid
candidate pool exists, source-like forecasts receive a ranking penalty. Final
public prose is separately reviewed for genuine inference, counterforce,
consequences and concrete monitoring signals. Generic deterministic prose is
never accepted as a quality fallback.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

import comment_quality

VALUE_ADD_CONTRACT_VERSION = "ai-outlook-value-add-v2.2"

WORD = re.compile(r"[a-ząćęłńóśźż0-9]+", re.IGNORECASE)
PLACEHOLDER = re.compile(r"\{[a-z_][a-z0-9_ -]*\}", re.IGNORECASE)
STOPWORDS = {
    "oraz", "który", "która", "które", "przez", "tego", "jest", "będzie",
    "zostanie", "zostaną", "roku", "dnia", "the", "and", "that", "this",
    "will", "with", "from", "for", "into", "its", "within", "before", "after",
    "would", "could", "should", "about", "their", "there", "were", "have",
}

BOILERPLATE = re.compile(
    r"(?:"
    r"model ocenia dokładnie zablokowane zdarzenie|"
    r"zablokowane zdarzenie|"
    r"realizacja prognozy oznacza osiągnięcie warunku|"
    r"brak realizacji pozostawi stan bazowy|"
    r"metryka zbliża się do progu|"
    r"dla rezultatu opisanego w prognozie|"
    r"wskazany mechanizm doprowadzi do wyniku przed terminem|"
    r"the model evaluates the locked event|"
    r"locked event|"
    r"forecast occurring means the condition|"
    r"failure leaves the baseline unchanged|"
    r"metric approaches the threshold|"
    r"for the result described in the forecast"
    r")",
    re.IGNORECASE,
)
MODEL_SPEAK = re.compile(
    r"\b(?:model(?:u|em)?|zablokowan\w*|rubryk\w*|locked|rubric|scoring|threshold)\b",
    re.IGNORECASE,
)
COUNTERFORCE = {
    "pl": re.compile(
        r"\b(?:ryzyk\w*|niepewn\w*|jednak|ale|barier\w*|wąsk\w* gard\w*|"
        r"opóźn\w*|zależ\w*|warunk\w*|może nie|może osłab\w*)\b",
        re.IGNORECASE,
    ),
    "en": re.compile(
        r"\b(?:risk\w*|uncertain\w*|however|but|constraint\w*|bottleneck\w*|"
        r"delay\w*|depend\w*|condition\w*|may not|could weaken)\b",
        re.IGNORECASE,
    ),
}


class OutlookValueAddError(ValueError):
    """Raised when technically valid copy is analytically empty."""


def _compact(value: Any, limit: int = 1600) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit].rstrip()


def _tokens(value: Any) -> set[str]:
    return {
        token.lower()[:9]
        for token in WORD.findall(_compact(value).lower())
        if len(token) >= 4 and token.lower() not in STOPWORDS and not token.isdigit()
    }


def _coverage(claim: Any, evidence: Any) -> tuple[float, int, int]:
    claim_tokens = _tokens(claim)
    evidence_tokens = _tokens(evidence)
    if not claim_tokens or not evidence_tokens:
        return 0.0, 0, len(claim_tokens)
    overlap = len(claim_tokens & evidence_tokens)
    return overlap / len(claim_tokens), overlap, len(claim_tokens)


def _jaccard(left: Any, right: Any) -> float:
    a = _tokens(left)
    b = _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _payload(message: dict[str, str]) -> dict[str, Any] | None:
    if not isinstance(message, dict) or message.get("role") != "user":
        return None
    try:
        value = json.loads(str(message.get("content") or ""))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _request_context(messages: list[dict[str, str]]) -> tuple[str, dict[str, Any] | None]:
    for message in messages or []:
        payload = _payload(message)
        if not payload:
            continue
        shape = payload.get("required_json_shape")
        if not isinstance(shape, dict):
            continue
        if "candidates" in shape:
            return "candidate", payload
        if not isinstance(payload.get("locked_candidate"), dict):
            continue
        if "pl" in shape and "en" in shape:
            return "final_bilingual", payload
        if "title" in shape and "thesis" in shape:
            language = str(payload.get("language") or "pl").lower()
            return ("final_en" if language == "en" else "final_pl"), payload
    return "other", None


def _final_requirements(language: str) -> list[str]:
    if language == "en":
        return [
            "Public copy must discuss the real-world event, not the model, rubric, locked metric or scoring mechanics.",
            "analysis_summary must add one concrete inference not stated directly in the source and the strongest counterforce or uncertainty; do not summarize the article.",
            "rationale must be a short causal chain A → B → C rather than a recap of source facts.",
            "impact must say who is affected, what changes if the forecast occurs, and what the opposite scenario means; avoid tautologies.",
            "watch_items must name 2-3 observable signals such as a specific decision, data release, document, date, level or event; avoid generic 'new data' wording.",
            "Keep analysis_summary, impact and watch_items concise (roughly 420 characters each) and non-repetitive.",
            "If the evidence cannot support added analytical value without inventing facts, do not hide that with generic filler.",
        ]
    return [
        "tekst publiczny opisuje świat, a nie pracę modelu, rubryki, zablokowanej metryki ani scoringu",
        "analysis_summary zawiera konkretny wniosek niewypowiedziany wprost w źródle oraz najsilniejszą kontrsiłę lub niepewność; nie streszczaj artykułu",
        "rationale to krótki łańcuch przyczynowy A → B → C, nie powtórzenie faktów ze źródła",
        "impact mówi komu i co realnie zmienia realizacja prognozy oraz co oznacza scenariusz przeciwny; nie używaj tautologii",
        "watch_items wskazuje 2-3 nazwane, obserwowalne sygnały: konkretną decyzję, dane, termin, dokument, poziom albo zdarzenie; nie pisz ogólnie 'nowe dane'",
        "analysis_summary, impact i watch_items są krótkie, każde maksymalnie około 420 znaków, i nie powtarzają się nawzajem",
        "jeżeli nie da się stworzyć wartościowego wniosku ponad źródło bez wymyślania faktów, nie maskuj tego generycznym tekstem",
    ]


def _augment_final(
    messages: list[dict[str, str]],
    language: str,
    retry_reason: str = "",
) -> list[dict[str, str]]:
    result = copy.deepcopy(messages)
    for message in result:
        payload = _payload(message)
        if not payload or not isinstance(payload.get("locked_candidate"), dict):
            continue
        shape = payload.get("required_json_shape")
        if not isinstance(shape, dict):
            continue
        requirements = list(payload.get("requirements") or [])
        requirements.extend(_final_requirements(language))
        payload["requirements"] = requirements
        payload["value_add_contract_version"] = VALUE_ADD_CONTRACT_VERSION
        if retry_reason:
            if language == "en":
                payload["value_add_retry"] = (
                    "The previous version was rejected as paraphrase or generic filler. "
                    "Rewrite the analysis while preserving every locked fact. Reason: "
                    + retry_reason
                )
            else:
                payload["value_add_retry"] = (
                    "Poprzednia wersja została odrzucona jako parafraza albo generyczny filler. "
                    "Napisz analizę od nowa, zachowując wszystkie zablokowane fakty. Powód: "
                    + retry_reason
                )
        message["content"] = json.dumps(payload, ensure_ascii=False)
    return result


def _candidate_sources(payload: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [row for row in payload.get("sources", []) if isinstance(row, dict)]
    by_id = {str(row.get("id")): row for row in rows}
    return [by_id[str(value)] for value in candidate.get("source_ids", []) if str(value) in by_id]


def _candidate_restatement_score(
    payload: dict[str, Any], candidate: dict[str, Any]
) -> tuple[float, int, int]:
    claim = " ".join(
        _compact(candidate.get(field), 700)
        for field in ("title", "forecast_statement", "thesis")
        if candidate.get(field)
    )
    best = (0.0, 0, len(_tokens(claim)))
    for source in _candidate_sources(payload, candidate):
        evidence = f"{source.get('title', '')} {source.get('summary', '')}"
        score = _coverage(claim, evidence)
        if score[0] > best[0]:
            best = score
    return best


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _penalize_candidates(result: Any, payload: dict[str, Any]) -> Any:
    """Down-rank source restatements but never delete methodology-valid rows."""
    if not isinstance(result, dict) or not isinstance(result.get("candidates"), list):
        return result
    output = dict(result)
    rows: list[dict[str, Any]] = []
    for candidate in result["candidates"]:
        if not isinstance(candidate, dict):
            continue
        row = dict(candidate)
        ratio, overlap, count = _candidate_restatement_score(payload, row)
        is_restatement = count >= 8 and overlap >= 8 and ratio >= 0.66
        row["value_add_source_restatement_risk"] = is_restatement
        row["value_add_source_coverage"] = round(ratio, 3)
        if is_restatement:
            row["novelty"] = min(_number(row.get("novelty"), 50.0), 20.0)
            row["causal_strength"] = min(_number(row.get("causal_strength"), 70.0), 55.0)
            row["speculation_risk"] = max(_number(row.get("speculation_risk"), 40.0), 65.0)
        rows.append(row)
    output["candidates"] = rows
    return output


def _source_text(payload: dict[str, Any]) -> str:
    rows = payload.get("locked_sources")
    if not isinstance(rows, list):
        rows = payload.get("sources")
    if not isinstance(rows, list):
        return ""
    return " ".join(
        f"{row.get('title', '')} {row.get('summary', '')}"
        for row in rows
        if isinstance(row, dict)
    )


def _final_sections(result: Any, kind: str) -> list[tuple[str, dict[str, Any]]]:
    if not isinstance(result, dict):
        return []
    if kind == "final_bilingual":
        return [
            (language, result[language])
            for language in ("pl", "en")
            if isinstance(result.get(language), dict)
        ]
    language = "en" if kind == "final_en" else "pl"
    return [(language, result)]


def _final_problem(result: Any, payload: dict[str, Any], kind: str) -> str | None:
    sections = _final_sections(result, kind)
    if not sections:
        return "missing final analytical section"
    evidence = _source_text(payload)

    for language, section in sections:
        analysis = _compact(section.get("analysis_summary"), 1200)
        impact = _compact(section.get("impact"), 1200)
        watch = _compact(section.get("watch_items"), 1200)
        rationale = _compact(section.get("rationale"), 1200)
        public_value = " ".join((analysis, impact, watch))

        if not analysis or not impact or not watch or not rationale:
            return f"{language}: missing analytical field"
        if PLACEHOLDER.search(" ".join(str(value) for value in section.values())):
            return f"{language}: unexpanded placeholder"
        if BOILERPLATE.search(" ".join((analysis, impact, watch, rationale))):
            return f"{language}: generic filler detected"
        if MODEL_SPEAK.search(public_value):
            return f"{language}: public copy describes model mechanics"
        if len(analysis) > 470 or len(impact) > 470 or len(watch) > 470:
            return f"{language}: analytical section too long"

        coverage, overlap, count = _coverage(analysis, evidence)
        if count >= 8 and overlap >= 8 and coverage >= 0.76:
            return f"{language}: AI conclusion is a source paraphrase"
        if _jaccard(analysis, impact) >= 0.58 or _jaccard(analysis, watch) >= 0.58:
            return f"{language}: analytical sections repeat each other"

        counterforce = COUNTERFORCE[language]
        if not counterforce.search(analysis):
            return f"{language}: analysis lacks a counterforce or uncertainty"

        concrete = bool(re.search(r"\b20\d{2}\b|\d+(?:[.,]\d+)?\s*%", watch))
        separators = len(re.findall(r"(?:^|\s)\d+[.)]\s|;", watch))
        named_signal = bool(re.search(
            r"\b(?:NBP|GUS|KNF|TSUE|Curia|Sejm|Senat|Komisj\w* Europejsk\w*|"
            r"Fed|ECB|EIA|OPEC|FDA|EMA|WHO|NASA|ESA|court|ministry|government|"
            r"quarterly|monthly|earnings|filing|register|rejestr\w*|raport\w*|decyzj\w*)\b",
            watch,
            re.IGNORECASE,
        ))
        if not concrete and separators < 1 and not named_signal:
            return f"{language}: monitoring signals are too generic"
    return None


def install() -> None:
    if getattr(comment_quality, "_briefrooms_value_add_guard_v2", False):
        return
    original = comment_quality.request_json_completion

    def wrapped(**kwargs):
        messages = kwargs.get("messages") or []
        kind, payload = _request_context(messages)
        if kind == "other" or payload is None:
            return original(**kwargs)

        # Candidate generation is intentionally untouched. The existing strict
        # methodology gets first chance to produce a valid pool; only then do we
        # alter ranking signals. This avoids empty-pool failures.
        if kind == "candidate":
            generated = original(**kwargs)
            return _penalize_candidates(generated, payload)

        language = "en" if kind == "final_en" else "pl"
        first_kwargs = dict(kwargs)
        first_kwargs["messages"] = _augment_final(messages, language)
        first = original(**first_kwargs)
        problem = _final_problem(first, payload, kind)
        if problem is None:
            return first

        retry_kwargs = dict(kwargs)
        retry_kwargs["messages"] = _augment_final(messages, language, problem)
        second = original(**retry_kwargs)
        second_problem = _final_problem(second, payload, kind)
        if second_problem is None:
            return second

        raise OutlookValueAddError(
            "AI Outlook value-add gate rejected final copy after retry: " + second_problem
        )

    comment_quality.request_json_completion = wrapped
    comment_quality._briefrooms_value_add_guard_v2 = True

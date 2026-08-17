#!/usr/bin/env python3
"""Require real analytical value in public AI Outlook copy.

The source article is evidence, not the product. Candidate forecasts that are
mostly restatements are penalized in ranking rather than blindly removed, while
final public copy still fails closed if it becomes a source paraphrase,
generic filler or model-speak.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

import comment_quality

VALUE_ADD_CONTRACT_VERSION = "ai-outlook-value-add-v2.1"

WORD = re.compile(r"[a-ząćęłńóśźż0-9]+", re.IGNORECASE)
PLACEHOLDER = re.compile(r"\{[a-z_][a-z0-9_ -]*\}", re.IGNORECASE)
STOPWORDS = {
    "oraz", "który", "która", "które", "przez", "tego", "jest", "będzie",
    "zostanie", "zostaną", "roku", "dnia", "the", "and", "that", "this",
    "will", "with", "from", "for", "into", "its", "within", "before", "after",
    "would", "could", "should", "about", "their", "there", "were", "have",
}

# Text that can be emitted without understanding the event is not analysis.
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
COUNTERFORCE_PL = re.compile(
    r"\b(?:ryzyk\w*|niepewn\w*|jednak|ale|barier\w*|wąsk\w* gard\w*|"
    r"opóźn\w*|zależ\w*|warunk\w*|może nie|może osłab\w*)\b",
    re.IGNORECASE,
)
COUNTERFORCE_EN = re.compile(
    r"\b(?:risk\w*|uncertain\w*|however|but|constraint\w*|bottleneck\w*|"
    r"delay\w*|depend\w*|condition\w*|may not|could weaken)\b",
    re.IGNORECASE,
)


class OutlookValueAddError(ValueError):
    """Raised when copy is technically valid but analytically empty."""


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
        if isinstance(payload.get("locked_candidate"), dict):
            if "pl" in shape and "en" in shape:
                return "final_bilingual", payload
            if "title" in shape and "thesis" in shape:
                return "final_single", payload
    return "other", None


def _augment(messages: list[dict[str, str]], kind: str, retry_reason: str = "") -> list[dict[str, str]]:
    result = copy.deepcopy(messages)
    for message in result:
        payload = _payload(message)
        if not payload:
            continue
        shape = payload.get("required_json_shape")
        if not isinstance(shape, dict):
            continue

        if kind == "candidate" and "candidates" in shape:
            rules = list(payload.get("hard_rules") or [])
            rules.extend([
                "AI Outlook nie jest streszczeniem: kandydat będący tylko przyszłoczasową parafrazą nagłówka, zapowiedzianego harmonogramu, transakcji albo planu ma bardzo niską novelty",
                "źródło jest punktem wyjścia; preferuj testowalną inferencję: skutek drugiego rzędu, mierzalną zmianę albo niepewny rezultat, którego źródło nie stwierdza wprost",
                "nie wybieraj jako najlepszego kandydata samej finalizacji już ogłoszonej transakcji na ogłoszonych warunkach, jeśli źródła nie pokazują niezależnego ryzyka wykonania",
                "najwyżej oceniaj prognozę, po której czytelnik znający artykuł źródłowy nadal dowiaduje się czegoś nowego i falsyfikowalnego",
            ])
            payload["hard_rules"] = rules
            payload["value_add_contract_version"] = VALUE_ADD_CONTRACT_VERSION
            if retry_reason:
                payload["value_add_retry"] = (
                    "Pierwszy zestaw był zbyt bliski materiałom źródłowym. Zaproponuj bardziej "
                    "analityczne kandydatury, ale nadal wyłącznie na podstawie dostarczonych faktów. "
                    "Powód: " + retry_reason
                )
            message["content"] = json.dumps(payload, ensure_ascii=False)
            continue

        if kind.startswith("final") and isinstance(payload.get("locked_candidate"), dict):
            requirements = list(payload.get("requirements") or [])
            requirements.extend([
                "tekst publiczny nie opisuje pracy modelu, rubryki ani zablokowanej metryki; w analysis_summary, impact i watch_items nie używaj słów 'model', 'zablokowane', 'locked', 'rubric', 'threshold'",
                "analysis_summary zawiera konkretny wniosek niewypowiedziany wprost w źródle oraz najsilniejszą kontrsiłę lub niepewność; nie streszczaj artykułu",
                "rationale to krótki łańcuch przyczynowy A → B → C, nie powtórzenie faktów ze źródła",
                "impact mówi komu i co realnie zmienia realizacja prognozy oraz co oznacza scenariusz przeciwny; nie używaj tautologii typu 'realizacja oznacza spełnienie warunku'",
                "watch_items wskazuje 2-3 nazwane, obserwowalne sygnały: konkretną decyzję, dane, termin, dokument, poziom albo zdarzenie; nie pisz ogólnie 'nowe dane' lub 'zmiana harmonogramu'",
                "analysis_summary, impact i watch_items są krótkie, każde maksymalnie około 420 znaków, i nie powtarzają się nawzajem",
                "jeżeli nie da się stworzyć wartościowego wniosku ponad źródło bez wymyślania faktów, nie maskuj tego generycznym tekstem",
            ])
            payload["requirements"] = requirements
            payload["value_add_contract_version"] = VALUE_ADD_CONTRACT_VERSION
            if retry_reason:
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


def _candidate_restatement_score(payload: dict[str, Any], candidate: dict[str, Any]) -> tuple[float, int, int]:
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


def _penalize_candidates(result: Any, payload: dict[str, Any]) -> tuple[Any, int]:
    """Prefer novel inference without ever emptying an otherwise valid pool."""
    if not isinstance(result, dict) or not isinstance(result.get("candidates"), list):
        return result, 0
    output = dict(result)
    rows: list[dict[str, Any]] = []
    high_risk = 0
    for candidate in result["candidates"]:
        if not isinstance(candidate, dict):
            continue
        row = dict(candidate)
        ratio, overlap, count = _candidate_restatement_score(payload, row)
        is_restatement = count >= 8 and overlap >= 8 and ratio >= 0.66
        if is_restatement:
            high_risk += 1
            # This is a ranking penalty, not a hard deletion. If today's source
            # pool is thin, the pipeline remains operational; the stricter final
            # value-add gate still prevents empty public prose.
            row["novelty"] = min(_number(row.get("novelty"), 50.0), 30.0)
            row["causal_strength"] = min(_number(row.get("causal_strength"), 70.0), 65.0)
            row["speculation_risk"] = max(_number(row.get("speculation_risk"), 40.0), 55.0)
            row["value_add_source_restatement_risk"] = True
            row["value_add_source_coverage"] = round(ratio, 3)
        else:
            row["value_add_source_restatement_risk"] = False
        rows.append(row)
    output["candidates"] = rows
    return output, high_risk


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
    return [("pl", result)]


def _final_problem(result: Any, payload: dict[str, Any], kind: str) -> str | None:
    sections = _final_sections(result, kind)
    if not sections:
        return "brak poprawnej sekcji finalnej"
    evidence = _source_text(payload)

    for language, section in sections:
        analysis = _compact(section.get("analysis_summary"), 1200)
        impact = _compact(section.get("impact"), 1200)
        watch = _compact(section.get("watch_items"), 1200)
        rationale = _compact(section.get("rationale"), 1200)
        public_value = " ".join((analysis, impact, watch))

        if not analysis or not impact or not watch or not rationale:
            return f"{language}: brak jednego z pól analitycznych"
        if PLACEHOLDER.search(" ".join(str(value) for value in section.values())):
            return f"{language}: nierozwinięty placeholder w tekście publicznym"
        if BOILERPLATE.search(" ".join((analysis, impact, watch, rationale))):
            return f"{language}: wykryto generyczny filler"
        if MODEL_SPEAK.search(public_value):
            return f"{language}: tekst opisuje mechanikę modelu zamiast świata"
        if len(analysis) > 470 or len(impact) > 470 or len(watch) > 470:
            return f"{language}: sekcja analityczna jest zbyt rozwlekła"

        coverage, overlap, count = _coverage(analysis, evidence)
        if count >= 8 and overlap >= 8 and coverage >= 0.76:
            return f"{language}: wniosek AI jest parafrazą źródła"
        if _jaccard(analysis, impact) >= 0.58 or _jaccard(analysis, watch) >= 0.58:
            return f"{language}: sekcje powtarzają tę samą treść"

        counterforce = COUNTERFORCE_PL if language == "pl" else COUNTERFORCE_EN
        if not counterforce.search(analysis):
            return f"{language}: wniosek AI nie pokazuje głównej niepewności lub kontrsiły"

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
            return f"{language}: sygnały monitorujące są zbyt ogólne"
    return None


def install() -> None:
    """Install the value-add layer around the active AI transport."""
    if getattr(comment_quality, "_briefrooms_value_add_guard_v2", False):
        return
    original = comment_quality.request_json_completion

    def wrapped(**kwargs):
        messages = kwargs.get("messages") or []
        kind, payload = _request_context(messages)
        if kind == "other" or payload is None:
            return original(**kwargs)

        first_kwargs = dict(kwargs)
        first_kwargs["messages"] = _augment(messages, kind)
        first = original(**first_kwargs)

        if kind == "candidate":
            penalized, high_risk = _penalize_candidates(first, payload)
            candidates = penalized.get("candidates", []) if isinstance(penalized, dict) else []
            if candidates and high_risk < len(candidates):
                return penalized
            if candidates:
                retry_kwargs = dict(kwargs)
                retry_kwargs["messages"] = _augment(
                    messages,
                    kind,
                    f"wszystkie {len(candidates)} kandydatury są zbyt bliskie źródłom",
                )
                second = original(**retry_kwargs)
                second_penalized, second_high_risk = _penalize_candidates(second, payload)
                second_candidates = (
                    second_penalized.get("candidates", [])
                    if isinstance(second_penalized, dict) else []
                )
                if second_candidates and second_high_risk < len(second_candidates):
                    return second_penalized
                # Never destroy a methodology-valid pool just because the daily
                # evidence is thin. Ranking penalties plus the final prose gate
                # are safer than returning zero candidates.
                if second_candidates:
                    return second_penalized
                return penalized
            return penalized

        problem = _final_problem(first, payload, kind)
        if problem is None:
            return first

        retry_kwargs = dict(kwargs)
        retry_kwargs["messages"] = _augment(messages, kind, problem)
        second = original(**retry_kwargs)
        second_problem = _final_problem(second, payload, kind)
        if second_problem is None:
            return second

        # No deterministic prose fallback: a failed publication is preferable to
        # putting a mechanical pseudo-analysis in front of readers.
        raise OutlookValueAddError(
            "AI Outlook value-add gate rejected final copy after retry: " + second_problem
        )

    comment_quality.request_json_completion = wrapped
    comment_quality._briefrooms_value_add_guard_v2 = True

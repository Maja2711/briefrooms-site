#!/usr/bin/env python3
"""Fail closed when AI Outlook becomes a source paraphrase or generic filler.

This guard sits around the model transport. It does not change locked forecast
facts, probability, metric, threshold or deadline. It only raises the editorial
bar: a publishable Outlook must contain a non-obvious inference, a counterforce,
specific consequences and concrete monitoring signals.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

import comment_quality

VALUE_ADD_CONTRACT_VERSION = "ai-outlook-value-add-v2"

WORD = re.compile(r"[a-ząćęłńóśźż0-9]+", re.IGNORECASE)
PLACEHOLDER = re.compile(r"\{[a-z_][a-z0-9_ -]*\}", re.IGNORECASE)
STOPWORDS = {
    "oraz", "który", "która", "które", "przez", "tego", "jest", "będzie",
    "zostanie", "zostaną", "roku", "dnia", "the", "and", "that", "this",
    "will", "with", "from", "for", "into", "its", "within", "before", "after",
    "would", "could", "should", "about", "their", "there", "were", "have",
}

# Phrases below are not merely stylistic dislikes. They identify deterministic
# filler that can be generated without understanding the article and therefore
# carries no analytical value for the reader.
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
    r"opóźn\w*|zależ\w*|warunk\w*)\b",
    re.IGNORECASE,
)
COUNTERFORCE_EN = re.compile(
    r"\b(?:risk\w*|uncertain\w*|however|but|constraint\w*|bottleneck\w*|"
    r"delay\w*|depend\w*|condition\w*)\b",
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
                "AI Outlook nie jest streszczeniem: odrzuć kandydata, jeżeli prognoza jest tylko przyszłoczasową parafrazą nagłówka, zapowiedzianego harmonogramu, transakcji albo deklarowanego planu ze źródła",
                "źródło może być punktem wyjścia, ale prognoza musi wnosić testowalną inferencję: skutek drugiego rzędu, mierzalną zmianę albo niepewny rezultat, którego źródło nie stwierdza wprost",
                "nie prognozuj po prostu, że ogłoszona transakcja zostanie sfinalizowana na ogłoszonych warunkach, chyba że dostarczone źródła pokazują konkretny niezależny czynnik ryzyka wykonania",
                "preferuj kandydatów, przy których czytelnik po przeczytaniu źródła nadal dowiaduje się z AI Outlook czegoś nowego i falsyfikowalnego",
            ])
            payload["hard_rules"] = rules
            payload["value_add_contract_version"] = VALUE_ADD_CONTRACT_VERSION
            if retry_reason:
                payload["value_add_retry"] = (
                    "Poprzedni zestaw nie wnosił wystarczającej wartości ponad źródła. "
                    "Zbuduj inne prognozy, a nie nowe parafrazy. Powód: " + retry_reason
                )
            message["content"] = json.dumps(payload, ensure_ascii=False)
            continue

        if kind.startswith("final") and isinstance(payload.get("locked_candidate"), dict):
            requirements = list(payload.get("requirements") or [])
            requirements.extend([
                "tekst publiczny nie może brzmieć jak opis pracy modelu, rubryki lub zablokowanej metryki; nie używaj w analysis_summary, impact ani watch_items słów 'model', 'zablokowane', 'locked', 'rubric', 'threshold'",
                "analysis_summary ma zawierać konkretny wniosek niewypowiedziany wprost w źródle oraz najsilniejszy czynnik, który może ten wniosek osłabić; nie streszczaj artykułu",
                "rationale ma być krótkim łańcuchem przyczynowym A → B → C, a nie powtórzeniem faktów ze źródła",
                "impact ma powiedzieć komu i co realnie zmienia realizacja prognozy oraz co oznacza scenariusz przeciwny; zakazane są tautologie typu 'realizacja oznacza spełnienie warunku'",
                "watch_items ma wskazać 2-3 nazwane, obserwowalne sygnały: konkretną decyzję, publikację danych, termin, dokument, poziom albo zdarzenie; zakazane są ogólniki typu 'nowe dane' lub 'zmiana harmonogramu' bez wskazania czego dokładnie",
                "analysis_summary, impact i watch_items mają być krótkie: każde maksymalnie około 420 znaków i bez powtarzania tych samych zdań innymi słowami",
                "jeżeli nie da się stworzyć wartościowego wniosku ponad materiał źródłowy bez wymyślania faktów, nie maskuj tego generycznym tekstem",
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


def _candidate_is_source_restatement(payload: dict[str, Any], candidate: dict[str, Any]) -> bool:
    claim = " ".join(
        _compact(candidate.get(field), 700)
        for field in ("title", "forecast_statement", "thesis")
        if candidate.get(field)
    )
    claim_tokens = _tokens(claim)
    if len(claim_tokens) < 8:
        return False
    for source in _candidate_sources(payload, candidate):
        evidence = f"{source.get('title', '')} {source.get('summary', '')}"
        ratio, overlap, count = _coverage(claim, evidence)
        # Asymmetric coverage matters more than ordinary similarity here: a short
        # candidate can copy the source almost completely while the source itself
        # contains many extra words. This is exactly the low-value pattern we reject.
        if count >= 8 and overlap >= 8 and ratio >= 0.66:
            return True
    return False


def _filter_candidates(result: Any, payload: dict[str, Any]) -> tuple[Any, int]:
    if not isinstance(result, dict) or not isinstance(result.get("candidates"), list):
        return result, 0
    kept = []
    rejected = 0
    for candidate in result["candidates"]:
        if not isinstance(candidate, dict):
            rejected += 1
            continue
        if _candidate_is_source_restatement(payload, candidate):
            rejected += 1
            continue
        kept.append(candidate)
    output = dict(result)
    output["candidates"] = kept
    return output, rejected


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

        # A monitoring field made only of generic nouns is not useful. Require
        # either an explicit date/number, or at least two item separators.
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
    """Install one additional quality layer around the active AI transport."""
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
            filtered, rejected = _filter_candidates(first, payload)
            if isinstance(filtered, dict) and filtered.get("candidates"):
                return filtered
            retry_kwargs = dict(kwargs)
            retry_kwargs["messages"] = _augment(
                messages,
                kind,
                f"{rejected or 'wszystkie'} kandydatów odrzucono jako zbyt bliskie materiałowi źródłowemu",
            )
            second = original(**retry_kwargs)
            filtered, _ = _filter_candidates(second, payload)
            return filtered

        problem = _final_problem(first, payload, kind)
        if problem is None:
            return first

        retry_kwargs = dict(kwargs)
        retry_kwargs["messages"] = _augment(messages, kind, problem)
        second = original(**retry_kwargs)
        second_problem = _final_problem(second, payload, kind)
        if second_problem is None:
            return second

        # Deliberately no deterministic prose fallback. Keeping yesterday's
        # stronger Outlook is preferable to publishing today's empty paraphrase.
        raise OutlookValueAddError(
            "AI Outlook value-add gate rejected final copy after retry: " + second_problem
        )

    comment_quality.request_json_completion = wrapped
    comment_quality._briefrooms_value_add_guard_v2 = True

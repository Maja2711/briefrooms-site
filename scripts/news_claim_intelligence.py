#!/usr/bin/env python3
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

try:
    from .news_source_architecture import source_profile
except ImportError:
    from news_source_architecture import source_profile

CLAIM_INTELLIGENCE_VERSION = "claim-intelligence-v1"
CONTRADICTION_DETECTION_VERSION = "contradiction-detection-v1"

_SPECIAL_FOLD = str.maketrans({"ł": "l", "đ": "d", "ð": "d", "þ": "th", "æ": "ae", "œ": "oe"})
_NUMBER_RE = re.compile(r"(?<![\w.])(?P<value>\d+(?:[.,]\d+)?)(?![\w.])")
_PERCENT_RE = re.compile(r"(?:%|\bpercent(?:age)?\b|\bproc\.?\b)", re.I)
_BPS_RE = re.compile(r"\b(?:bps?|basis points?|pb|punkt(?:y|ow)? bazow(?:y|ych)?)\b", re.I)

_METRIC_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("casualties_deaths", ("killed", "dead", "deaths", "died", "fatalities", "zgin", "zabit", "ofiar smiert", "smierc")),
    ("casualties_injured", ("injured", "wounded", "rann")),
    ("inflation_rate", ("inflation", "inflacja", "cpi")),
    ("policy_rate_level", ("interest rate", "interest rates", "policy rate", "benchmark rate", "stopa procent", "stopy procent", "stopa referencyjna", "oprocent")),
    ("unemployment_rate", ("unemployment", "bezroboc")),
    ("gdp_growth", ("gdp", "pkb", "gross domestic product")),
    ("revenue", ("revenue", "sales", "przychod", "sprzedaz")),
    ("profit", ("profit", "earnings", "zysk")),
)

_MONOTONIC_UPDATE_METRICS = {"casualties_deaths", "casualties_injured"}
_RATE_ACTORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Federal Reserve", ("federal reserve", "fed ", " fed", "fomc")),
    ("ECB", ("european central bank", "ecb")),
    ("NBP", ("narodowy bank polski", "nbp", "rada polityki pienieznej", "rpp")),
    ("Bank of England", ("bank of england", "boe")),
    ("central_bank", ("central bank", "bank centralny")),
)
_DECISION_ACTORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("regulator", ("regulator", "regulatory authority", "organ nadzoru")),
    ("commission", ("commission", "komisja")),
    ("court", ("court", "sad ", " sad", "trybunal")),
    ("government", ("government", "rzad")),
    ("parliament", ("parliament", "sejm", "senat", "senate")),
)

_TIER_WEIGHT = {
    "primary": 1.00,
    "wire": 0.92,
    "premium": 0.78,
    "quality": 0.64,
    "broad": 0.45,
    "unknown": 0.35,
}


def _fold(value: Any) -> str:
    text = str(value or "").lower().translate(_SPECIAL_FOLD)
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _story_text(story: Mapping[str, Any]) -> str:
    return " ".join(str(story.get(key) or "") for key in ("title", "summary"))


def _published_at(story: Mapping[str, Any]) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(story.get("published_at") or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def claim_evidence_root(story: Mapping[str, Any]) -> str:
    origin = str(story.get("origin_source") or "").strip()
    confidence = float(story.get("origin_confidence") or 0.0)
    if origin and confidence >= 0.80:
        return source_profile(origin).canonical_name
    publisher = str(story.get("publisher_source") or story.get("source") or "unknown").strip()
    return source_profile(publisher).canonical_name


def _metric_for_window(window: str, number_offset: int) -> str | None:
    """Bind a number to the nearest known metric phrase, not any phrase in the window."""
    folded = _fold(window)
    best_metric: str | None = None
    best_distance = 10_000
    for metric, keywords in _METRIC_KEYWORDS:
        for keyword in keywords:
            start = 0
            while True:
                position = folded.find(keyword, start)
                if position < 0:
                    break
                keyword_center = position + len(keyword) // 2
                distance = abs(keyword_center - number_offset)
                if distance < best_distance:
                    best_distance = distance
                    best_metric = metric
                start = position + 1
    return best_metric if best_distance <= 75 else None


def _parse_number(raw: str) -> float:
    return float(raw.replace(",", "."))


def _currency_unit(window: str) -> tuple[str | None, float]:
    folded = _fold(window)
    multiplier = 1.0
    if re.search(r"\b(?:billion|bn|mld)\b", folded):
        multiplier = 1_000_000_000.0
    elif re.search(r"\b(?:million|mln)\b", folded):
        multiplier = 1_000_000.0
    currency = None
    if re.search(r"(?:\busd\b|\bdollar|\$)", folded):
        currency = "USD"
    elif re.search(r"(?:\beur\b|\beuro\b|€)", folded):
        currency = "EUR"
    elif re.search(r"(?:\bpln\b|\bzlot|\bzl\b)", folded):
        currency = "PLN"
    return currency, multiplier


def _detect_actor(text: str, candidates: tuple[tuple[str, tuple[str, ...]], ...]) -> str | None:
    folded = f" {_fold(text)} "
    for canonical, aliases in candidates:
        if any(alias in folded for alias in aliases):
            return canonical
    return None


def _numeric_claims(story: Mapping[str, Any]) -> list[dict[str, Any]]:
    text = _story_text(story)
    root = claim_evidence_root(story)
    publisher = str(story.get("publisher_source") or story.get("source") or "unknown")
    published = _published_at(story)
    rate_actor = _detect_actor(text, _RATE_ACTORS)
    claims: list[dict[str, Any]] = []

    for match in _NUMBER_RE.finditer(text):
        value = _parse_number(match.group("value"))
        left = max(0, match.start() - 90)
        right = min(len(text), match.end() + 90)
        window = text[left:right]
        number_offset = match.start() - left + len(match.group("value")) // 2
        metric = _metric_for_window(window, number_offset)

        # Units must be physically close to the number. A percentage elsewhere in
        # the sentence must not turn a year/date into a comparable percentage.
        unit_left = max(0, match.start() - 14)
        unit_right = min(len(text), match.end() + 28)
        unit_window = text[unit_left:unit_right]
        has_percent = bool(_PERCENT_RE.search(unit_window))
        has_bps = bool(_BPS_RE.search(unit_window))

        if has_bps and rate_actor:
            metric = "policy_rate_change"
        if metric is None:
            continue

        unit: str | None = None
        normalized_value = value

        if metric in {"casualties_deaths", "casualties_injured"}:
            if 1900 <= value <= 2100:
                continue
            unit = "count"
        elif has_bps:
            unit = "basis_points"
        elif has_percent:
            unit = "percent"
        elif metric in {"revenue", "profit"}:
            currency_window = text[max(0, match.start() - 30):min(len(text), match.end() + 45)]
            currency, multiplier = _currency_unit(currency_window)
            if currency is None:
                continue
            unit = currency
            normalized_value = value * multiplier
        else:
            continue

        key = f"metric:{metric}:{unit}"
        claims.append(
            {
                "claim_key": key,
                "claim_type": "numeric_metric",
                "metric": metric,
                "value": value,
                "normalized_value": normalized_value,
                "unit": unit,
                "confidence": 0.90 if metric.startswith("casualties_") else 0.86,
                "evidence_root": root,
                "publisher": publisher,
                "published_at": published.isoformat() if published else None,
                "context": " ".join(window.split())[:220],
            }
        )

    unique: dict[tuple[str, float], dict[str, Any]] = {}
    for claim in claims:
        unique[(claim["claim_key"], float(claim["normalized_value"]))] = claim
    return list(unique.values())


def _categorical_claims(story: Mapping[str, Any]) -> list[dict[str, Any]]:
    title = str(story.get("title") or "")
    title_folded = _fold(title)
    full_text = _story_text(story)
    root = claim_evidence_root(story)
    publisher = str(story.get("publisher_source") or story.get("source") or "unknown")
    published = _published_at(story)
    claims: list[dict[str, Any]] = []

    rate_actor = _detect_actor(full_text, _RATE_ACTORS)
    if rate_actor:
        direction = None
        if re.search(r"\b(?:raise[sd]?|hike[sd]?|increase[sd]?|podniosl|podniosla|podwyzszyl|podwyzszyla)\b", title_folded):
            direction = "raise"
        elif re.search(r"\b(?:cut[st]?|lower(?:s|ed)?|reduce[sd]?|obnizyl|obnizyla|obcina|scial|sciela)\b", title_folded):
            direction = "cut"
        elif re.search(r"\b(?:hold[sd]?|held|keep[st]?|leave[sd]? unchanged|unchanged|utrzymal|utrzymala|pozostawil|pozostawila|bez zmian)\b", title_folded):
            direction = "hold"
        if direction:
            claims.append(
                {
                    "claim_key": f"decision:policy_rate_direction:{rate_actor}",
                    "claim_type": "categorical_decision",
                    "metric": "policy_rate_direction",
                    "value": direction,
                    "normalized_value": direction,
                    "unit": None,
                    "confidence": 0.93,
                    "evidence_root": root,
                    "publisher": publisher,
                    "published_at": published.isoformat() if published else None,
                    "context": title[:220],
                }
            )

    decision_actor = _detect_actor(title, _DECISION_ACTORS)
    if decision_actor:
        outcome = None
        if re.search(r"\b(?:approve[sd]?|authori[sz]e[sd]?|zatwierdzil|zatwierdzila|zaakceptowal|zaakceptowala)\b", title_folded):
            outcome = "approve"
        elif re.search(r"\b(?:reject[sd]?|block[sd]?|odrzucil|odrzucila|zablokowal|zablokowala)\b", title_folded):
            outcome = "reject"
        if outcome:
            claims.append(
                {
                    "claim_key": f"decision:outcome:{decision_actor}",
                    "claim_type": "categorical_decision",
                    "metric": "decision_outcome",
                    "value": outcome,
                    "normalized_value": outcome,
                    "unit": None,
                    "confidence": 0.82,
                    "evidence_root": root,
                    "publisher": publisher,
                    "published_at": published.isoformat() if published else None,
                    "context": title[:220],
                }
            )
    return claims


def extract_story_claims(story: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _categorical_claims(story) + _numeric_claims(story)


def _claim_time(claim: Mapping[str, Any]) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(claim.get("published_at") or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _latest_claim_per_root(claims: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_root: dict[str, dict[str, Any]] = {}
    for raw in claims:
        claim = dict(raw)
        root = str(claim.get("evidence_root") or "unknown")
        current = by_root.get(root)
        if current is None:
            by_root[root] = claim
            continue
        current_time = _claim_time(current)
        new_time = _claim_time(claim)
        if new_time and (current_time is None or new_time >= current_time):
            by_root[root] = claim
        elif new_time == current_time and float(claim.get("confidence") or 0.0) > float(current.get("confidence") or 0.0):
            by_root[root] = claim
    return list(by_root.values())


def _numeric_tolerance(metric: str, unit: str | None, values: list[float]) -> float:
    if unit == "percent":
        return 0.05
    if unit == "basis_points":
        return 1.0
    if unit == "count":
        return 0.0
    if unit in {"USD", "EUR", "PLN"}:
        scale = max([abs(v) for v in values] or [1.0])
        return max(1.0, scale * 0.005)
    return 0.0


def _is_evolving_update(metric: str, claims: list[dict[str, Any]], tolerance: float) -> bool:
    if metric not in _MONOTONIC_UPDATE_METRICS or len(claims) < 2:
        return False
    dated = [(time, float(claim["normalized_value"])) for claim in claims if (time := _claim_time(claim))]
    if len(dated) < 2:
        return False
    dated.sort(key=lambda item: item[0])
    span = (dated[-1][0] - dated[0][0]).total_seconds()
    if span < 90 * 60:
        return False
    values = [value for _, value in dated]
    if max(values) - min(values) <= tolerance:
        return False
    return all(later + tolerance >= earlier for earlier, later in zip(values, values[1:]))


def _authority_factor(claims: Iterable[Mapping[str, Any]]) -> float:
    weights = []
    for claim in claims:
        tier = source_profile(claim.get("evidence_root")).tier
        weights.append(_TIER_WEIGHT.get(tier, 0.35))
    return sum(weights) / len(weights) if weights else 0.35


def _group_analysis(claim_key: str, claims: list[dict[str, Any]]) -> dict[str, Any]:
    independent = _latest_claim_per_root(claims)
    roots = sorted({str(claim.get("evidence_root") or "unknown") for claim in independent})
    sample = independent[0] if independent else claims[0]
    claim_type = str(sample.get("claim_type") or "unknown")
    metric = str(sample.get("metric") or "unknown")
    unit = sample.get("unit")
    values_by_root = [
        {
            "root": str(claim.get("evidence_root") or "unknown"),
            "publisher": str(claim.get("publisher") or "unknown"),
            "value": claim.get("value"),
            "normalized_value": claim.get("normalized_value"),
            "published_at": claim.get("published_at"),
        }
        for claim in sorted(independent, key=lambda item: str(item.get("evidence_root") or ""))
    ]

    status = "insufficient_evidence"
    contradiction_score = 0.0
    variants = {str(claim.get("normalized_value")) for claim in independent}

    if len(roots) >= 2:
        if claim_type == "numeric_metric":
            numeric_values = [float(claim["normalized_value"]) for claim in independent]
            tolerance = _numeric_tolerance(metric, str(unit) if unit else None, numeric_values)
            spread = max(numeric_values) - min(numeric_values)
            if spread <= tolerance:
                status = "consistent"
            elif _is_evolving_update(metric, independent, tolerance):
                status = "evolving_update"
            else:
                status = "disputed"
                scale = max(max(abs(v) for v in numeric_values), 1.0)
                relative = min(1.0, spread / scale)
                contradiction_score = min(100.0, 42.0 + 38.0 * relative + 18.0 * _authority_factor(independent))
        elif len(variants) == 1:
            status = "consistent"
        else:
            status = "disputed"
            contradiction_score = min(100.0, 68.0 + 20.0 * _authority_factor(independent))

    return {
        "claim_key": claim_key,
        "claim_type": claim_type,
        "metric": metric,
        "unit": unit,
        "status": status,
        "independent_roots": len(roots),
        "roots": roots,
        "variants": values_by_root,
        "contradiction_score": round(contradiction_score, 1),
    }


def analyze_event_claims(stories: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    all_claims: list[dict[str, Any]] = []
    for story in stories:
        all_claims.extend(extract_story_claims(story))

    grouped: dict[str, list[dict[str, Any]]] = {}
    for claim in all_claims:
        grouped.setdefault(str(claim["claim_key"]), []).append(claim)
    analyses = [_group_analysis(key, claims) for key, claims in sorted(grouped.items())]

    disputed = [item for item in analyses if item["status"] == "disputed"]
    evolving = [item for item in analyses if item["status"] == "evolving_update"]
    consistent = [item for item in analyses if item["status"] == "consistent"]
    comparable = [item for item in analyses if item["independent_roots"] >= 2]

    if disputed:
        status = "disputed"
    elif evolving:
        status = "evolving_update"
    elif consistent:
        status = "consistent"
    else:
        status = "insufficient_evidence"

    contradiction_score = max((float(item["contradiction_score"]) for item in disputed), default=0.0)
    return {
        "claim_intelligence_version": CLAIM_INTELLIGENCE_VERSION,
        "contradiction_detection_version": CONTRADICTION_DETECTION_VERSION,
        "claim_consistency_status": status,
        "contradiction_score": round(contradiction_score, 1),
        "extracted_claim_count": len(all_claims),
        "comparable_claim_group_count": len(comparable),
        "disputed_claim_count": len(disputed),
        "consistent_claim_count": len(consistent),
        "evolving_claim_count": len(evolving),
        "claim_groups": analyses[:12],
    }


def adjusted_corroboration_score(raw_score: float, analysis: Mapping[str, Any]) -> float:
    raw = max(0.0, min(100.0, float(raw_score)))
    status = str(analysis.get("claim_consistency_status") or "insufficient_evidence")
    contradiction = max(0.0, min(100.0, float(analysis.get("contradiction_score") or 0.0)))
    if status == "disputed":
        factor = 1.0 - 0.55 * (contradiction / 100.0)
        return round(raw * max(0.40, factor), 1)
    if status == "consistent":
        comparable = int(analysis.get("consistent_claim_count") or 0)
        return round(min(100.0, raw + min(8.0, comparable * 3.0)), 1)
    if status == "evolving_update":
        return round(raw * 0.95, 1)
    return round(raw, 1)


def claim_ranking_adjustment(story: Mapping[str, Any]) -> float:
    status = str(story.get("claim_consistency_status") or "")
    if status == "disputed":
        return -min(14.0, float(story.get("contradiction_score") or 0.0) * 0.14)
    if status == "consistent":
        return min(7.0, 2.5 * int(story.get("consistent_claim_count") or 0))
    if status == "evolving_update":
        return 1.0
    return 0.0


def public_claim_policy() -> dict[str, Any]:
    return {
        "version": CLAIM_INTELLIGENCE_VERSION,
        "contradiction_detection_version": CONTRADICTION_DETECTION_VERSION,
        "mode": "deterministic_same_event_claim_comparison_across_independent_evidence_roots",
        "principles": [
            "Contradictions are assessed only inside one CanonicalEvent.",
            "Republications sharing one origin count as one claim lineage, not independent confirmation.",
            "Numeric contradictions require the same metric and explicit comparable units.",
            "A number is bound to its nearest semantic metric and to a locally adjacent unit.",
            "Monotonic casualty updates separated in time are treated as evolving reports rather than automatic contradictions.",
            "Disputed claims reduce effective corroboration but do not hide a materially important event.",
        ],
    }

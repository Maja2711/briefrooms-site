#!/usr/bin/env python3
"""PR36 — Champion vs Challenger + Statistical Promotion Gate v1.

This is the final authorization layer between PR35 promotion and production
materialization.  It compares champion and challenger on the same prospective
marginal cases, applies a conservative transaction-cost stress, and requires a
deterministic bootstrap confidence interval above zero before production write.

PR35 may temporarily mark a candidate PROMOTED after its descriptive gate.  If
PR36 is not yet satisfied, this module restores the parent policy and returns the
candidate to SHADOW_VALIDATION.  Therefore an unproven challenger cannot become
the effective production policy or cascade into a second candidate.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean, median
from typing import Any, Mapping, Sequence

try:
    import autonomous_policy_promotion as ap
except ModuleNotFoundError:  # pragma: no cover
    from scripts import autonomous_policy_promotion as ap

CONFIG_PATH = "data/investments/statistical_promotion_gate_config.json"
AUTH_FILENAME = "statistical_policy_authorizations.json"
REPORT_FILENAME = "statistical_promotion_gate_report.json"
AUTH_SCHEMA = "briefrooms-statistical-policy-authorizations-v1"
REPORT_SCHEMA = "briefrooms-statistical-promotion-gate-report-v1"
CONFIG_SCHEMA = "briefrooms-statistical-promotion-gate-config-v1"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _atomic(path: Path, payload: Mapping[str, Any], *, hash_field: str | None = None) -> None:
    body = copy.deepcopy(dict(payload))
    if hash_field:
        body.pop(hash_field, None)
        body[hash_field] = _sha(body)
    text = json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        tmp = Path(handle.name)
    tmp.replace(path)


def load_config(repo_root: Path) -> dict[str, Any]:
    payload = json.loads((repo_root / CONFIG_PATH).read_text(encoding="utf-8"))
    if payload.get("schema_version") != CONFIG_SCHEMA:
        raise ValueError("PR36 config schema mismatch")
    if int(payload.get("minimum_paired_n") or 0) < ap.VALIDATION_MIN_N:
        raise ValueError("PR36 paired minimum cannot be below PR35 validation minimum")
    if int(payload.get("maximum_paired_n_before_reject") or 0) < int(payload["minimum_paired_n"]):
        raise ValueError("PR36 maximum sample is below minimum")
    confidence = float(payload.get("confidence_level") or 0.0)
    if not 0.8 <= confidence < 1.0:
        raise ValueError("PR36 confidence level out of bounds")
    if int(payload.get("bootstrap_samples") or 0) < 1000:
        raise ValueError("PR36 bootstrap sample count too small")
    if set(payload.get("engines") or {}) != {"gpw_daily", "us_daily"}:
        raise ValueError("PR36 engine config mismatch")
    for engine_id, spec in payload["engines"].items():
        cost = float(spec.get("round_trip_cost_stress_percent") or 0.0)
        if not 0.0 <= cost <= 1.0:
            raise ValueError(f"PR36 cost stress invalid for {engine_id}")
    return payload


def _load_authorizations(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": AUTH_SCHEMA,
            "updated_at": None,
            "authorizations": {},
            "controls": {
                "paired_same_cases_required": True,
                "cost_stress_required": True,
                "bootstrap_confidence_required": True,
                "human_approval_required": False,
            },
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != AUTH_SCHEMA:
        raise ValueError("PR36 authorization schema mismatch")
    stored = str(payload.get("authorizations_sha256") or "")
    body = dict(payload)
    body.pop("authorizations_sha256", None)
    if not stored or stored != _sha(body):
        raise ValueError("PR36 authorization hash mismatch")
    if not isinstance(payload.get("authorizations"), dict):
        raise ValueError("PR36 authorizations must be an object")
    return payload


def save_authorizations(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic(path, payload, hash_field="authorizations_sha256")


def verify_authorizations(path: Path) -> dict[str, Any]:
    payload = _load_authorizations(path)
    return {"authorizations": len(payload.get("authorizations") or {})}


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("quantile requires values")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lo = int(math.floor(position))
    hi = int(math.ceil(position))
    if lo == hi:
        return ordered[lo]
    weight = position - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def deterministic_bootstrap_mean_ci(
    values: Sequence[float], *, samples: int, confidence: float, seed_text: str
) -> tuple[float, float]:
    if not values:
        raise ValueError("bootstrap requires paired values")
    seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    n = len(values)
    means: list[float] = []
    for _ in range(samples):
        means.append(fmean(values[rng.randrange(n)] for _ in range(n)))
    alpha = (1.0 - confidence) / 2.0
    return _quantile(means, alpha), _quantile(means, 1.0 - alpha)


def _max_drawdown(values: Sequence[float]) -> float:
    cumulative = peak = 0.0
    drawdown = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        drawdown = min(drawdown, cumulative - peak)
    return drawdown


def _paired_rows(state_dir: Path, candidate: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = ap._read_jsonl(state_dir / ap.SHADOW_FILENAME)
    return list(
        ap._marginal_rows(
            rows,
            engine_id=str(candidate["engine_id"]),
            gate=str(candidate["gate"]),
            current_value=float(candidate["from_value"]),
            candidate_value=float(candidate["to_value"]),
            after=ap._parse_time(str(candidate["validation_start_at"])),
        )
    )


def paired_metrics(
    rows: Sequence[Mapping[str, Any]], *, candidate_id: str, cost_stress_percent: float, bootstrap_samples: int,
    confidence_level: float
) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: str(row.get("decision_at") or ""))
    gross = [float(row["return_percent"]) for row in ordered]
    # On the exact marginal set the champion rejects the LONG solely on the
    # current score gate; its incremental return for this comparison is zero.
    champion = [0.0 for _ in ordered]
    net = [value - cost_stress_percent for value in gross]
    delta = [challenger - base for challenger, base in zip(net, champion)]
    if not ordered:
        return {
            "n": 0,
            "champion_mean_return_percent": None,
            "challenger_gross_mean_return_percent": None,
            "challenger_net_mean_return_percent": None,
            "paired_net_incremental_mean_percent": None,
            "paired_net_incremental_median_percent": None,
            "paired_net_positive_rate": None,
            "bootstrap_ci_low_percent": None,
            "bootstrap_ci_high_percent": None,
            "unique_symbols": 0,
            "span_days": 0,
            "first_half_net_mean_percent": None,
            "second_half_net_mean_percent": None,
            "max_drawdown_net_percent": None,
            "largest_positive_contribution_share": None,
            "cost_stress_percent": cost_stress_percent,
        }
    low, high = deterministic_bootstrap_mean_ci(
        delta,
        samples=bootstrap_samples,
        confidence=confidence_level,
        seed_text=f"{candidate_id}:{len(delta)}:{cost_stress_percent}",
    )
    dates = [ap._parse_time(str(row["decision_at"])).date() for row in ordered]
    symbols = {str(row.get("symbol") or "") for row in ordered if str(row.get("symbol") or "")}
    half = max(1, len(delta) // 2)
    positive = [value for value in delta if value > 0]
    positive_sum = sum(positive)
    concentration = None if positive_sum <= 0 else max(positive) / positive_sum
    return {
        "n": len(delta),
        "champion_action": "FLAT",
        "challenger_action": "LONG",
        "champion_mean_return_percent": 0.0,
        "challenger_gross_mean_return_percent": round(fmean(gross), 8),
        "challenger_net_mean_return_percent": round(fmean(net), 8),
        "paired_net_incremental_mean_percent": round(fmean(delta), 8),
        "paired_net_incremental_median_percent": round(median(delta), 8),
        "paired_net_positive_rate": round(sum(value > 0 for value in delta) / len(delta), 6),
        "bootstrap_ci_low_percent": round(low, 8),
        "bootstrap_ci_high_percent": round(high, 8),
        "unique_symbols": len(symbols),
        "span_days": (max(dates) - min(dates)).days if len(dates) > 1 else 0,
        "first_half_net_mean_percent": round(fmean(delta[:half]), 8),
        "second_half_net_mean_percent": round(fmean(delta[half:]), 8) if delta[half:] else round(fmean(delta[:half]), 8),
        "max_drawdown_net_percent": round(_max_drawdown(delta), 8),
        "largest_positive_contribution_share": None if concentration is None else round(concentration, 8),
        "cost_stress_percent": cost_stress_percent,
        "confidence_level": confidence_level,
        "bootstrap_samples": bootstrap_samples,
    }


def statistical_gate(metrics: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[str, list[str]]:
    n = int(metrics.get("n") or 0)
    minimum_n = int(config["minimum_paired_n"])
    maximum_n = int(config["maximum_paired_n_before_reject"])
    if n < minimum_n:
        return "COLLECTING", [f"paired_n_below_{minimum_n}"]

    reasons: list[str] = []
    if float(metrics.get("paired_net_incremental_mean_percent") or 0.0) < float(config["minimum_net_incremental_return_percent"]):
        reasons.append("net_incremental_mean_below_minimum")
    if float(metrics.get("paired_net_positive_rate") or 0.0) < float(config["minimum_net_positive_rate"]):
        reasons.append("net_positive_rate_below_minimum")
    if float(metrics.get("bootstrap_ci_low_percent") or 0.0) <= 0.0:
        reasons.append("bootstrap_lower_bound_not_positive")
    if int(metrics.get("unique_symbols") or 0) < int(config["minimum_unique_symbols"]):
        reasons.append("insufficient_symbol_diversity")
    if int(metrics.get("span_days") or 0) < int(config["minimum_span_days"]):
        reasons.append("validation_span_too_short")
    if float(metrics.get("first_half_net_mean_percent") or 0.0) <= 0.0:
        reasons.append("first_half_net_not_positive")
    if float(metrics.get("second_half_net_mean_percent") or 0.0) <= 0.0:
        reasons.append("second_half_net_not_positive")
    concentration = metrics.get("largest_positive_contribution_share")
    if concentration is not None and float(concentration) > float(config["maximum_single_positive_contribution_share"]):
        reasons.append("single_observation_concentration_too_high")

    if not reasons:
        return "PASS", []
    if n >= maximum_n:
        return "FAIL", reasons
    return "HOLD", reasons


def _restore_parent(registry: dict[str, Any], candidate: dict[str, Any], now: datetime, *, permanent: bool) -> None:
    engine_id = str(candidate["engine_id"])
    state = registry["engines"][engine_id]
    if str(state.get("source_candidate_id") or "") != str(candidate["candidate_id"]):
        raise RuntimeError("PR36 candidate is not the active provisional policy")
    parent = state.get("parent")
    if not isinstance(parent, Mapping):
        raise RuntimeError("PR36 cannot restore missing parent policy")
    restored = copy.deepcopy(dict(parent))
    restored["blocked_until"] = None
    registry["engines"][engine_id] = restored
    if permanent:
        candidate["status"] = "STATISTICAL_REJECTED"
        candidate["statistical_rejected_at"] = ap._iso(now)
        blocked_until = now + timedelta(days=30)
        registry.setdefault("rejected_transitions", []).append({
            "engine_id": engine_id,
            "parameter": candidate["parameter"],
            "from_value": candidate["from_value"],
            "to_value": candidate["to_value"],
            "blocked_until": ap._iso(blocked_until),
            "reason": "PR36_STATISTICAL_GATE_FAIL",
            "candidate_id": candidate["candidate_id"],
        })
    else:
        candidate["status"] = "SHADOW_VALIDATION"


def _authorization_key(state: Mapping[str, Any]) -> str:
    return str(state.get("policy_id") or "")


def _authorize(
    authorizations: dict[str, Any], state: Mapping[str, Any], candidate: Mapping[str, Any], report: Mapping[str, Any], now: datetime
) -> None:
    key = _authorization_key(state)
    if not key:
        raise RuntimeError("PR36 cannot authorize empty policy id")
    authorizations.setdefault("authorizations", {})[key] = {
        "status": "PASS",
        "engine_id": state["engine_id"],
        "policy_id": key,
        "effective_policy_version": state["effective_policy_version"],
        "candidate_id": candidate["candidate_id"],
        "authorized_at": ap._iso(now),
        "statistical_gate": copy.deepcopy(dict(report)),
    }
    authorizations["updated_at"] = ap._iso(now)


def run(state_dir: Path, repo_root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    config = load_config(repo_root)
    registry_path = state_dir / ap.REGISTRY_FILENAME
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    ap.validate_registry(registry)
    ap.verify_shadow(state_dir / ap.SHADOW_FILENAME)
    auth_path = state_dir / AUTH_FILENAME
    authorizations = _load_authorizations(auth_path)

    evaluated: list[dict[str, Any]] = []
    for candidate in list((registry.get("candidates") or {}).values()):
        if candidate.get("status") != "PROMOTED":
            continue
        engine_id = str(candidate.get("engine_id") or "")
        if engine_id not in config["engines"]:
            continue
        state = registry["engines"].get(engine_id) or {}
        if str(state.get("source_candidate_id") or "") != str(candidate.get("candidate_id") or ""):
            continue
        policy_id = _authorization_key(state)
        existing = (authorizations.get("authorizations") or {}).get(policy_id)
        if isinstance(existing, Mapping) and existing.get("status") == "PASS" and existing.get("effective_policy_version") == state.get("effective_policy_version"):
            candidate["statistical_gate"] = copy.deepcopy(existing.get("statistical_gate") or {})
            continue

        rows = _paired_rows(state_dir, candidate)
        engine_cost = float(config["engines"][engine_id]["round_trip_cost_stress_percent"])
        metrics = paired_metrics(
            rows,
            candidate_id=str(candidate["candidate_id"]),
            cost_stress_percent=engine_cost,
            bootstrap_samples=int(config["bootstrap_samples"]),
            confidence_level=float(config["confidence_level"]),
        )
        gate_status, reasons = statistical_gate(metrics, config)
        report = {
            "schema_version": REPORT_SCHEMA,
            "candidate_id": candidate["candidate_id"],
            "engine_id": engine_id,
            "from_value": candidate["from_value"],
            "to_value": candidate["to_value"],
            "evaluated_at": ap._iso(now),
            "status": gate_status,
            "blocking_reasons": reasons,
            "metrics": metrics,
            "comparison_contract": {
                "same_cases": True,
                "champion_on_marginal_cases": "FLAT",
                "challenger_on_marginal_cases": "LONG",
                "future_only": True,
                "cost_stressed": True,
                "bootstrap_confidence": True,
            },
        }
        previous = candidate.get("statistical_gate") if isinstance(candidate.get("statistical_gate"), Mapping) else {}
        candidate["statistical_gate"] = report
        evaluated.append(report)

        if gate_status == "PASS":
            _authorize(authorizations, state, candidate, report, now)
            if previous.get("status") != "PASS":
                ap.append_audit(state_dir / ap.AUDIT_FILENAME, "statistical_promotion_pass", report, ap._iso(now))
        elif gate_status == "FAIL":
            _restore_parent(registry, candidate, now, permanent=True)
            ap.append_audit(state_dir / ap.AUDIT_FILENAME, "statistical_promotion_rejected", report, ap._iso(now))
        else:
            _restore_parent(registry, candidate, now, permanent=False)
            if previous.get("status") != gate_status or ((previous.get("metrics") or {}).get("n") != metrics.get("n")):
                ap.append_audit(state_dir / ap.AUDIT_FILENAME, "statistical_promotion_hold", report, ap._iso(now))

    registry["updated_at"] = ap._iso(now)
    ap._atomic_json(registry_path, registry)
    save_authorizations(auth_path, authorizations)
    overall = {
        "schema_version": REPORT_SCHEMA,
        "generated_at": ap._iso(now),
        "evaluated": evaluated,
        "active_policies": {
            engine_id: {
                "policy_id": state.get("policy_id"),
                "effective_policy_version": state.get("effective_policy_version"),
                "revision": state.get("revision"),
                "source_candidate_id": state.get("source_candidate_id"),
                "statistically_authorized": (
                    int(state.get("revision") or 0) == 0
                    or _authorization_key(state) in (authorizations.get("authorizations") or {})
                ),
            }
            for engine_id, state in registry["engines"].items()
        },
    }
    _atomic(state_dir / REPORT_FILENAME, overall)
    verify_state(state_dir)
    return overall


def verify_state(state_dir: Path) -> dict[str, Any]:
    registry = json.loads((state_dir / ap.REGISTRY_FILENAME).read_text(encoding="utf-8"))
    ap.validate_registry(registry)
    ap.verify_shadow(state_dir / ap.SHADOW_FILENAME)
    auth = _load_authorizations(state_dir / AUTH_FILENAME)
    authorizations = auth.get("authorizations") or {}
    for engine_id, state in registry["engines"].items():
        revision = int(state.get("revision") or 0)
        if revision <= 0:
            continue
        key = _authorization_key(state)
        row = authorizations.get(key)
        if not isinstance(row, Mapping) or row.get("status") != "PASS":
            raise RuntimeError(f"PR36 active non-baseline policy lacks authorization: {engine_id}")
        if row.get("effective_policy_version") != state.get("effective_policy_version"):
            raise RuntimeError(f"PR36 authorization version mismatch: {engine_id}")
        if row.get("candidate_id") != state.get("source_candidate_id"):
            raise RuntimeError(f"PR36 authorization candidate mismatch: {engine_id}")
    return {"engines": list(registry["engines"]), "authorizations": len(authorizations)}


def main() -> int:
    parser = argparse.ArgumentParser(description="PR36 Champion vs Challenger statistical promotion gate")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    state_dir = Path(args.state_dir)
    if args.verify:
        print(json.dumps(verify_state(state_dir), sort_keys=True))
        return 0
    report = run(state_dir, Path(args.repo_root))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Evidence-backed reflection for TR-03's existing local policy component.

Persistent memory lives inside eurusd_production_policy_state.json. Discovery
may inspect old actual trades; only the separate, frozen prospective gate can
authorize a policy. Findings describe associations, never an invented cause.
"""
from __future__ import annotations

import copy
import math
from collections import defaultdict
from datetime import datetime, timezone
from statistics import fmean
from typing import Any, Mapping

try:
    from briefrooms_evolution_contracts import sha256, stable_id
except ImportError:
    from scripts.briefrooms_evolution_contracts import sha256, stable_id

SCHEMA = "eurusd-recursive-reflection-v1"
AUTHORITY = {
    "owner_module": "TR-03", "mode": "AGGRESSIVE_BOUNDED",
    "execution_authority": False, "direction_mutation_allowed": False,
    "risk_mutation_allowed": False, "hard_safety_override_allowed": False,
    "discovery_is_promotion_evidence": False,
}


def _time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("reflection timestamp requires timezone")
    return parsed.astimezone(timezone.utc)


def _require(value: bool, message: str) -> None:
    if not value:
        raise ValueError("reflection: " + message)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None


def _manifest(trade: Mapping[str, Any]) -> Mapping[str, Any] | None:
    manifest = trade.get("decision_manifest") or (trade.get("entry_thesis") or {}).get("decision_manifest")
    return manifest if isinstance(manifest, Mapping) else None



REGIME_FAMILIES = (
    "UPTREND_LONG", "DOWNTREND_SHORT",
    "HORIZONTAL_RANGE", "TRANSITION_ABSTAIN",
)
UNKNOWN_FAMILY = "UNVERIFIED"


def regime_family(strategy: Mapping[str, Any] | None, direction: str) -> str:
    """One frozen as-of H1 regime; no hindsight classification from outcomes."""
    data = strategy or {}
    tag = str(data.get("strategy") or "")
    if tag in {"STRONG_TREND", "TREND_CONTINUATION"}:
        return "UPTREND_LONG" if direction == "LONG" else "DOWNTREND_SHORT" if direction == "SHORT" else UNKNOWN_FAMILY
    if tag == "RANGE_EDGE":
        return "HORIZONTAL_RANGE"
    if tag in {"WAIT_TRANSITION", "WAIT_D1_CONFLICT", "WAIT_RANGE_EDGE"}:
        return "TRANSITION_ABSTAIN"
    return UNKNOWN_FAMILY


def family_from_manifest(manifest: Mapping[str, Any]) -> str:
    details = (manifest.get("execution_admission") or {}).get("multi_timeframe") or {}
    return regime_family(details, str((manifest.get("final_decision") or {}).get("direction") or ""))


def _family_summary(observations: Mapping[str, Any]) -> dict[str, Any]:
    result = {}
    for family in (*REGIME_FAMILIES, UNKNOWN_FAMILY):
        records = [v for v in observations.values() if v["family"] == family]
        returns = [float(row["net_r"]) for row in records]
        findings = sorted({label for row in records for label in row.get("findings") or []})
        result[family] = {
            "actual_closed_n": len(records),
            "wins": sum(x > 0 for x in returns),
            "losses": sum(x < 0 for x in returns),
            "mean_net_r": fmean(returns) if returns else None,
            "findings": {label: {
                "n": sum(label in row.get("findings", ()) for row in records),
                "mean_net_r": fmean(row["net_r"] for row in records if label in row.get("findings", ())),
            } for label in findings},
            "readiness": "DISCOVERY_ONLY" if records else "NO_ACTUAL_EVIDENCE",
            "direction_or_trade_authority": False,
            "future_promotion_requires_separate_prospective_gate": True,
        }
    return result


def _family_actuals(records: Mapping[str, Any], history: Mapping[str, Any]) -> dict[str, Any]:
    trades = {str(row.get("trade_id")): row for row in history.get("trades") or [] if isinstance(row, Mapping)}
    observations = {}
    for tid, record in records.items():
        trade = trades.get(tid)
        _require(trade is not None and sha256(trade) == record["source_trade_sha256"], "family has no canonical actual")
        manifest = _manifest(trade)
        _require(manifest is not None, "family manifest missing")
        observations[tid] = {
            "trade_id": tid, "family": family_from_manifest(manifest),
            "source_trade_sha256": record["source_trade_sha256"],
            "manifest_sha256": record["manifest_sha256"],
            "closed_at": record["closed_at"], "net_r": record["net_r"],
            "findings": list(record["findings"]),
            "source": "CANONICAL_ACTUAL_DISCOVERY_NOT_OOS",
        }
    return observations


def _source_hash(final: Mapping[str, Any]) -> str | None:
    facts = []
    for belief in final.get("used_beliefs") or []:
        if not isinstance(belief, Mapping):
            continue
        sources = [{"evidence_id": row.get("evidence_id"), "observed_at": row.get("observed_at")}
                   for row in belief.get("source_evidence") or [] if isinstance(row, Mapping)]
        if sources:
            facts.append({"belief_id": belief.get("belief_id"),
                          "sources": sorted(sources, key=lambda row: str(row["evidence_id"]))})
    return sha256(sorted(facts, key=lambda row: str(row["belief_id"]))) if facts else None


def _record(trade: Mapping[str, Any], prior: Mapping[str, Any], at: str) -> dict[str, Any]:
    manifest = _manifest(trade)
    _require(manifest is not None and manifest.get("instrument") == "EUR/USD", "native EURUSD manifest required")
    body = dict(manifest)
    recorded = body.pop("manifest_sha256", None)
    _require(recorded == sha256(body), "entry manifest hash mismatch")
    final = manifest.get("final_decision") or {}
    direction = str(trade.get("direction"))
    _require(direction in {"LONG", "SHORT"} and final.get("direction") == direction, "direction mismatch")
    opened, closed = _time(trade["opened_at"]), _time(trade["closed_at"])
    _require(_time(manifest["decision_at"]) <= opened <= closed <= _time(at), "noncausal actual outcome")
    geometry = manifest.get("execution_geometry") or {}
    entry, stop, exit_price = (_finite(geometry.get("entry")), _finite(geometry.get("stop")),
                               _finite(trade.get("exit_price")))
    _require(entry is not None and stop is not None and exit_price is not None and abs(entry - stop) > 0,
             "frozen entry/risk or actual exit missing")
    _require(_finite(trade.get("entry")) == entry, "actual entry differs from frozen manifest")
    score, confidence = _finite(final.get("score")), _finite(final.get("confidence"))
    coverage = _finite(final.get("coverage_weight"))
    source_hash = _source_hash(final)
    previous = [row for row in prior.values() if row["trade_id"] != str(trade["trade_id"])
                and row["direction"] == direction and row["net_r"] < 0
                and _time(row["closed_at"]) <= opened
                and 0 <= (opened - _time(row["closed_at"])).total_seconds() <= 3600]
    previous_loss = max(previous, key=lambda row: _time(row["closed_at"]), default=None)
    findings = []
    if score is None or confidence is None or coverage is None or source_hash is None:
        findings.append("ENTRY_CONTEXT_PARTIALLY_UNKNOWN")
    if score is not None and abs(score - 50) < 6:
        findings.append("NARROW_DIRECTIONAL_MARGIN")
    if confidence is not None and confidence < .50:
        findings.append("LOW_BELIEF_CONFIDENCE")
    if coverage is not None and coverage < .65:
        findings.append("LIMITED_EVIDENCE_COVERAGE")
    if previous_loss:
        findings.append("SAME_DIRECTION_REENTRY_WITHIN_1H_OF_LOSS")
        if source_hash is not None and previous_loss.get("source_evidence_sha256") == source_hash:
            findings.append("UNCHANGED_SOURCE_EVIDENCE_AFTER_LOSS")
    payload = {
        "trade_id": str(trade["trade_id"]), "learned_at": at,
        "opened_at": trade["opened_at"], "closed_at": trade["closed_at"], "direction": direction,
        "source_trade_sha256": sha256(trade), "manifest_sha256": recorded,
        "runtime_code_sha256": manifest.get("runtime_code_sha256"),
        "policy_version_id": manifest.get("policy_version_id"),
        "score": score, "confidence": confidence, "coverage_weight": coverage,
        "source_evidence_sha256": source_hash,
        "net_r": (1 if direction == "LONG" else -1) * (exit_price - entry) / abs(entry - stop),
        "exit_reason": trade.get("exit_reason"),
        "prior_loss_trade_id": previous_loss["trade_id"] if previous_loss else None,
        "findings": findings, "causal_status": "OBSERVED_ASSOCIATION_ONLY",
        "evidence_use": "DISCOVERY_ONLY_NOT_OOS", "execution_authority": False,
    }
    return {**payload, "reflection_sha256": sha256(payload)}


def _lessons(records: Mapping[str, Any]) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records.values():
        for finding in record["findings"]:
            groups[finding].append(record)
    lessons = []
    for finding, rows in sorted(groups.items()):
        values = [row["net_r"] for row in rows]
        lessons.append({
            "lesson_id": stable_id("eurusd-lesson", finding), "finding": finding,
            "n": len(rows), "losses": sum(value < 0 for value in values),
            "wins": sum(value > 0 for value in values), "mean_net_r": fmean(values),
            "status": "REPEATED_ASSOCIATION" if len(rows) >= 5 else "HYPOTHESIS_ONLY",
            "claim": "Test whether avoiding this context improves future net results.",
            "production_authority": False,
        })
    return lessons


def _hypotheses(records: Mapping[str, Any], runtime_code_sha256: str | None) -> list[dict[str, Any]]:
    rows = [row for row in records.values()
            if row["score"] is not None and row.get("runtime_code_sha256") == runtime_code_sha256]
    hypotheses = []
    for floor in (4.0, 5.0, 6.0):
        skipped = [row for row in rows if abs(row["score"] - 50) < floor]
        hypotheses.append({
            "hypothesis_id": stable_id("eurusd-margin-hypothesis", {"floor": floor}),
            "minimum_score_distance": floor, "discovery_n": len(rows),
            "changed_admissions": len(skipped),
            "discovery_mean_delta_r": -sum(row["net_r"] for row in skipped) / len(rows) if rows else None,
            "lost_winners": sum(row["net_r"] > 0 for row in skipped),
            "avoided_losses": sum(row["net_r"] < 0 for row in skipped),
            "runtime_code_sha256": runtime_code_sha256,
            "status": "REQUIRES_FROZEN_PROSPECTIVE_TEST", "production_authority": False,
        })
    return hypotheses


def sync(
    previous: Mapping[str, Any] | None, history: Mapping[str, Any],
    policy: Mapping[str, Any], *, now: str, runtime_code_sha256: str,
) -> dict[str, Any]:
    result = copy.deepcopy(previous) if previous is not None else {
        "schema_version": SCHEMA, "authority": dict(AUTHORITY), "activated_at": now,
        "records": {}, "iterations": [], "lessons": [], "hypotheses": [],
    }
    records = result["records"]
    eligible = sorted((row for row in history.get("trades") or []
                       if isinstance(row, Mapping) and _manifest(row) is not None
                       and _manifest(row).get("instrument") == "EUR/USD"
                       and _time(row["closed_at"]) <= _time(now)),
                      key=lambda row: (_time(row["opened_at"]), str(row["trade_id"])))
    for trade in eligible:
        tid = str(trade["trade_id"])
        if tid in records:
            _require(records[tid]["source_trade_sha256"] == sha256(trade), "canonical history changed after reflection")
        else:
            records[tid] = _record(trade, records, now)
    feedback = {
        "active_version_id": policy["active_version_id"],
        "candidates": [{"candidate_id": row["candidate_id"], "status": row["status"],
                        "selected_n": len(row["evaluation_case_ids"]),
                        "resolved_n": sum(policy["cases"][cid]["outcome"] is not None for cid in row["evaluation_case_ids"]),
                        "gate_id": (row.get("gate") or {}).get("gate_id"),
                        "blockers": (row.get("gate") or {}).get("blockers") or []}
                       for row in sorted(policy["candidates"].values(), key=lambda row: row["candidate_id"])],
        "rollback_ids": [row["rollback_id"] for row in policy["rollback_events"]],
        "record_hashes": sorted(row["reflection_sha256"] for row in records.values()),
        "runtime_code_sha256": runtime_code_sha256,
        "adaptive_reentry": {
            "active_version_id": (policy.get("reentry_learning") or {}).get("active_version_id"),
            "candidates": [{"candidate_id": row["candidate_id"], "status": row["status"],
                            "selected_n": len(row["evaluation_case_ids"]),
                            "gate_id": (row.get("gate") or {}).get("gate_id")}
                           for row in sorted((policy.get("reentry_learning") or {}).get("candidates", {}).values(),
                                             key=lambda row: row["candidate_id"])],
            "rollbacks": [row["rollback_id"] for row in (policy.get("reentry_learning") or {}).get("rollback_events", [])],
        },
    }
    feedback_hash = sha256(feedback)
    iterations = result["iterations"]
    if not iterations or iterations[-1]["feedback_sha256"] != feedback_hash:
        iteration = {
            "number": len(iterations) + 1, "at": now, "feedback": feedback,
            "feedback_sha256": feedback_hash,
            "parent_sha256": iterations[-1]["iteration_sha256"] if iterations else None,
            "next_action": "TEST_PARALLEL_BOUNDED_CHALLENGERS_AND_REVIEW_FAILED_GATES",
            "execution_authority": False,
        }
        iterations.append({**iteration, "iteration_sha256": sha256(iteration)})
    result["lessons"] = _lessons(records)
    result["hypotheses"] = _hypotheses(records, runtime_code_sha256)
    family_actuals = _family_actuals(records, history)
    old_families = (previous or {}).get("family_learning") or {}
    for tid, old in (old_families.get("actuals") or {}).items():
        _require(family_actuals.get(tid) == old, "frozen family learning rewritten")
    result["family_learning"] = {
        "schema_version": "eurusd-regime-family-learning-v1",
        "actuals": family_actuals,
        "summary": _family_summary(family_actuals),
        "families": list(REGIME_FAMILIES),
        "transition_policy": "ABSTAIN_AND_COLLECT_PROSPECTIVE_REJECTIONS_ONLY",
        "source": "FROZEN_NATIVE_ENTRY_MANIFEST_AND_ACTUAL_CLOSE",
        "execution_authority": False, "automatic_family_policy_promotion": False,
    }
    result["runtime_code_sha256"] = runtime_code_sha256
    result["legacy_trades_without_native_manifest"] = sum(
        isinstance(row, Mapping) and _manifest(row) is None for row in history.get("trades") or [])
    validate(result)
    return result


def review(memory: Mapping[str, Any] | None, final: Mapping[str, Any], *, observed_at: str,
           regime_family_id: str | None = None) -> dict[str, Any]:
    records = (memory or {}).get("records") or {}
    score, confidence = _finite(final.get("score")), _finite(final.get("confidence"))
    known_iterations = [row for row in (memory or {}).get("iterations") or []
                        if _time(row["at"]) <= _time(observed_at)]
    family_actuals = ((memory or {}).get("family_learning") or {}).get("actuals") or {}
    known = [row for row in records.values() if row["direction"] == final.get("direction")
             and (regime_family_id is None or (family_actuals.get(row["trade_id"]) or {}).get("family") == regime_family_id)
             and _time(row["learned_at"]) <= _time(observed_at)
             and row["score"] is not None and score is not None and abs(row["score"] - score) <= 3
             and row["confidence"] is not None and confidence is not None and abs(row["confidence"] - confidence) <= .10]
    losses = sorted((row for row in known if row["net_r"] < 0),
                    key=lambda row: _time(row["closed_at"]))
    return {
        "mode": "EVIDENCE_BACKED_PRE_ENTRY_REFLECTION", "regime_family": regime_family_id,
        "cross_family_outcomes_used": False if regime_family_id else None, "available": memory is not None and _time(memory["activated_at"]) <= _time(observed_at),
        "reflection_iteration": len(known_iterations),
        "memory_sha256": known_iterations[-1]["iteration_sha256"] if known_iterations else None,
        "similar_actual_trades": len(known), "similar_losses": len(losses),
        "similar_mean_net_r": fmean(row["net_r"] for row in known) if known else None,
        "recent_similar_loss_ids": [row["trade_id"] for row in losses[-3:]],
        "relevant_lesson_ids": sorted({stable_id("eurusd-lesson", finding)
                                      for row in losses for finding in row["findings"]}),
        "source_evidence_changed_since_latest_similar_loss": (
            _source_hash(final) != losses[-1]["source_evidence_sha256"]
            if losses and _source_hash(final) is not None and losses[-1]["source_evidence_sha256"] is not None else None),
        "assessment": "REVIEW_FAILED_CONTEXT_AND_REQUIRE_ALL_HARD_SAFETY_GATES" if losses else
                      "NO_MEASURED_SIMILAR_LOSS" if known else "INSUFFICIENT_COMPARABLE_ACTUAL_EVIDENCE",
        "historical_association_is_causal_proof": False,
        "may_override_direction_risk_or_safety": False,
        "policy_change_requires_prospective_gate": True,
    }


def validate(memory: Mapping[str, Any]) -> None:
    _require(memory.get("schema_version") == SCHEMA and memory.get("authority") == AUTHORITY, "contract/authority drift")
    for tid, row in memory["records"].items():
        body = dict(row)
        digest = body.pop("reflection_sha256", None)
        _require(tid == row["trade_id"] and sha256(body) == digest, "frozen trade reflection mutated")
        _require(_time(row["closed_at"]) <= _time(row["learned_at"]), "reflection before actual outcome")
        _require(row["evidence_use"] == "DISCOVERY_ONLY_NOT_OOS" and row["execution_authority"] is False, "discovery authority drift")
    _require(memory["lessons"] == _lessons(memory["records"]), "lesson statistics mismatch")
    _require(memory["hypotheses"] == _hypotheses(memory["records"], memory["runtime_code_sha256"]), "research hypothesis mismatch")
    family_data = memory.get("family_learning")
    if family_data is not None:
        _require(family_data["schema_version"] == "eurusd-regime-family-learning-v1", "family schema changed")
        _require(family_data["families"] == list(REGIME_FAMILIES), "family set changed")
        _require(family_data["summary"] == _family_summary(family_data["actuals"]), "family observations mismatch")
        _require(family_data["execution_authority"] is False and
                 family_data["automatic_family_policy_promotion"] is False, "unapproved family authority")
        for tid, item in family_data["actuals"].items():
            _require(tid in memory["records"] and item["source_trade_sha256"] == memory["records"][tid]["source_trade_sha256"],
                     "family record not bound to canonical actual")

    parent = None
    for index, row in enumerate(memory["iterations"]):
        body = dict(row)
        digest = body.pop("iteration_sha256", None)
        _require(row["number"] == index + 1 and row["parent_sha256"] == parent and digest == sha256(body), "recursive iteration chain mutated")
        _require(row["feedback_sha256"] == sha256(row["feedback"]), "iteration feedback mismatch")
        parent = digest


def validate_history(memory: Mapping[str, Any], history: Mapping[str, Any]) -> None:
    trades = {str(row["trade_id"]): row for row in history.get("trades") or [] if isinstance(row, Mapping)}
    if memory.get("family_learning") is not None:
        _require(memory["family_learning"]["actuals"] == _family_actuals(memory["records"], history),
                 "family history differs from frozen source")

    for tid, row in memory["records"].items():
        _require(tid in trades and sha256(trades[tid]) == row["source_trade_sha256"], "reflection has no identical canonical trade")
        _require(_record(trades[tid], memory["records"], row["learned_at"]) == row, "reflection facts differ from canonical entry/outcome")


def validate_transition(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> None:
    if before is None:
        return
    _require(after is not None and before["activated_at"] == after["activated_at"], "memory epoch removed or reset")
    for tid, row in before["records"].items():
        _require((after["records"].get(tid)) == row, "reflection history rewritten")
    _require(after["iterations"][:len(before["iterations"])] == before["iterations"], "recursive feedback history rewritten")
    before_families = (before.get("family_learning") or {}).get("actuals") or {}
    after_families = (after.get("family_learning") or {}).get("actuals") or {}
    for tid, record in before_families.items():
        _require(after_families.get(tid) == record, "regime family history rewritten")


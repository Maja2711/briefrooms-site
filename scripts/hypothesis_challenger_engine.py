#!/usr/bin/env python3
"""L3 P2: automatically propose, preregister, freeze and settle Shadow challengers.

This is a governed *probability methodology* challenger. It cannot rewrite a
hypothesis' semantic outcome rule, frozen Evidence, baseline forecast, engine
policy, or production overlay. Passing the prospective gate emits only a
PROMOTION_ELIGIBLE handoff to the BriefRooms Evolution Controller.

Strictly out-of-time: candidate fitted on already verified independent events;
each prospective shadow prediction MUST be committed before target/outcome, and
comes from a canonical forecast frozen AFTER the candidate was created.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Mapping

from belief_closed_loop import metrics as probability_metrics, transform_probability
from forecast_event_identity import canonical_event_rows, identity

SCHEMA = "briefrooms-hypothesis-challenger-v1"
MIN_DISCOVERY = 50
MIN_DISCOVERY_DATES = 20
MIN_VALIDATION = 15
MIN_OOS = 50
MIN_OOS_DATES = 20
MIN_BRIER_GAIN_REL = .05
MAX_ECE_DEGRADATION = .01
MAX_ACCURACY_DEGRADATION = .03
MAX_WORST_BLOCK_DEGRADATION = .10
BLOCKS = 4
MAX_ACTIVE = 3
MAX_NEW_PER_RUN = 2
MIN_NEW_AFTER_REJECTION = 30
POLICY = "first_eligible_frozen_forecast_per_independent_event"
AUTHORITY = {
    "automatic_candidate_creation": True,
    "automatic_shadow_prediction_freeze": True,
    "automatic_oos_settlement": True,
    "automatic_promotion_gate_evaluation": True,
    "automatic_production_promotion": False,
    "automatic_hypothesis_definition_writeback": False,
    "belief_probability_override": False,
    "source_evidence_mutation": False,
    "frozen_forecast_mutation": False,
    "trade_execution": False,
    "production_writeback": False,
}


def canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=str)


def sha(value: Any) -> str:
    return hashlib.sha256(canon(value).encode()).hexdigest()


def sid(prefix: str, value: Any) -> str:
    return prefix + "-" + sha(value)[:24]


def utc(value: Any) -> datetime:
    if not value:
        raise ValueError("missing time")
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("naive time")
    return dt.astimezone(timezone.utc)


def ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _float(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("bool not numeric")
    v = float(value)
    if not math.isfinite(v):
        raise ValueError("non-finite number")
    return v


def _score(pairs: list[tuple[float, bool]]) -> dict[str, Any]:
    return probability_metrics([(float(p), int(y)) for p, y in pairs])


def _pairs(rows: list[dict[str, Any]], transform: Mapping[str, Any] | None = None):
    return [(transform_probability(r["p"], transform) if transform else r["p"], bool(r["y"]))
            for r in rows]


def _improvement(ch: Mapping[str, Any], base: Mapping[str, Any]) -> float | None:
    x, y = ch.get("brier"), base.get("brier")
    if x is None or y is None or float(y) <= 0:
        return None
    return (float(y) - float(x)) / float(y)


def valid_resolved(state: Mapping[str, Any], now: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    forecasts = {str(f.get("forecast_id")): f for f in state.get("forecasts", [])
                 if isinstance(f, Mapping) and f.get("forecast_id")}
    verified: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for v in state.get("verifications", []):
        if not isinstance(v, Mapping) or not v.get("forecast_id"):
            continue
        verified[str(v["forecast_id"])].append(dict(v))
    matched = []
    invalid = defaultdict(int)
    for fid, f in forecasts.items():
        vs = verified.get(fid, [])
        if len(vs) > 1:
            invalid["multiple_verifications"] += 1
            continue
        if not vs:
            continue
        v = vs[0]
        if not bool(v.get("calibration_eligible", False)) or not isinstance(v.get("outcome"), bool):
            invalid["invalid_verification"] += 1
            continue
        try:
            at, target, settle_time = utc(f["forecast_at"]), utc(f["target_at"]), utc(v["verified_at"])
            p = _float(f["predicted_probability"])
            if not at < target <= settle_time <= now or not 0 <= p <= 1:
                raise ValueError("nonprospective or future or bad probability")
            if str(v.get("belief_id")) != str(f.get("belief_id")):
                raise ValueError("belief ID mismatch")
        except (ValueError, TypeError, KeyError, OverflowError):
            invalid["invalid_time_or_identity"] += 1
            continue
        matched.append({
            "f": f, "y": bool(v["outcome"]), "p": p,
            "event_id": identity(f)["event_id"], "v": v,
            "at": at, "target": target, "verified": settle_time,
        })
    chosen, audit = canonical_event_rows(matched)
    return chosen, {"independent_event_check": audit, "rejected": dict(sorted(invalid.items()))}


def fit_candidate(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Train only on resolved events; hold out LATER target dates completely."""
    rows = sorted(rows, key=lambda r: (r["target"], r["at"], r["event_id"]))
    dates = sorted({r["target"].date().isoformat() for r in rows})
    if len(rows) < MIN_DISCOVERY or len(dates) < MIN_DISCOVERY_DATES:
        return None
    split_index = max(1, int(len(dates) * .70))
    split_date = dates[split_index] if split_index < len(dates) else dates[-1]
    train = [r for r in rows if r["target"].date().isoformat() < split_date]
    holdout = [r for r in rows if r["target"].date().isoformat() >= split_date]
    if len(train) < 30 or len(holdout) < MIN_VALIDATION:
        return None

    # Bounded, deterministic model family. Holdout is NEVER used for tuning.
    grid = []
    for intercept in (-1.5, -1.0, -.7, -.4, -.2, 0.0, .2, .4, .7, 1.0, 1.5):
        for slope in (.4, .55, .7, .85, 1.0, 1.15):
            transform = {"type": "logit_affine_v1", "intercept": intercept, "slope": slope}
            value = _score(_pairs(train, transform))["brier"]
            penalty = .002 * ((intercept / 1.5)**2 + ((slope - 1)**2))
            grid.append((float(value) + penalty, abs(intercept), abs(slope - 1), intercept, slope))
    _, _, _, ai, bi = min(grid)
    transform = {"type": "logit_affine_v1", "intercept": float(ai), "slope": float(bi)}
    base = _score(_pairs(holdout))
    challenger = _score(_pairs(holdout, transform))
    gain = _improvement(challenger, base)
    if (gain is None or gain < .02 or
        challenger["log_loss"] >= base["log_loss"] or
        challenger["ece"] > base["ece"] + .02):
        return None
    return {
        "transform": transform,
        "discovery_n": len(train),
        "validation_n": len(holdout),
        "discovery_distinct_target_dates": len({r["target"].date() for r in train}),
        "validation_distinct_target_dates": len({r["target"].date() for r in holdout}),
        "validation_brier_relative_improvement": round(gain, 6),
        "validation_control": base,
        "validation_challenger": challenger,
        "source_event_ids_hash": sha([r["event_id"] for r in rows]),
        "source_verified_cutoff_at": max(ts(r["verified"]) for r in rows),
    }


def _candidate_key(bid: str, version: str, horizon: str) -> str:
    return bid + "@" + version + "#" + horizon


def _new_candidate(bid: str, version: str, horizon: str, fit: Mapping[str, Any],
                   now: datetime, round_number: int) -> dict[str, Any]:
    key = _candidate_key(bid, version, horizon)
    cid = sid("hch", {"key": key, "round": round_number, "transform": fit["transform"],
                      "evidence_hash": fit["source_event_ids_hash"]})
    return {
        "candidate_id": cid,
        "candidate_type": "hypothesis_probability_methodology",
        "scope": key,
        "hypothesis_id": bid,
        "hypothesis_version": version,
        "horizon_bucket": horizon,
        "round": round_number,
        "created_at": ts(now),
        "activation_boundary": ts(now),
        "status": "OOS_RUNNING",
        "shadow_only": True,
        "proposed_change": {"type": "probability_calibration_only",
                            "transform": dict(fit["transform"])},
        "proposed_change_sha256": sha(fit["transform"]),
        "discovery": {k: v for k, v in fit.items() if k != "transform"},
        "shadow_forecasts": {},
        "settlements": {},
        "gate": {"status": "COLLECTING", "observed_sample": 0,
                 "minimum_sample": MIN_OOS, "blockers": ["minimum_independent_oos_sample"]},
        "production_write_authority": False,
        "auto_promotion": False,
        "promotion_route": "briefrooms_evolution_controller_review",
    }


def _freeze(candidate: dict[str, Any], state: Mapping[str, Any], now: datetime) -> int:
    created = utc(candidate["activation_boundary"])
    frozen = candidate["shadow_forecasts"]
    verifications = {str(v.get("forecast_id")) for v in state.get("verifications", [])
                     if isinstance(v, Mapping) and v.get("forecast_id")}
    # A candidate can freeze only canonical forecasts from the SAME hypothesis
    # and horizon that were created AFTER this candidate's activation.
    to_freeze = []
    for f in state.get("forecasts", []):
        if not isinstance(f, Mapping) or not f.get("forecast_id"):
            continue
        ids = identity(f)
        if ids["hypothesis_id"] != candidate["hypothesis_id"] or ids["hypothesis_version"] != candidate["hypothesis_version"]:
            continue
        if (candidate["horizon_bucket"] != "__ALL_HORIZONS__" and
            str((f.get("metadata") or {}).get("calibration_horizon_bucket") or "unknown") != candidate["horizon_bucket"]):
            # For older records, only use the explicit default bucket when it is
            # the same as the one registered for the candidate.
            continue
        try:
            at, target = utc(f["forecast_at"]), utc(f["target_at"])
            p = _float(f["predicted_probability"])
            if not (created < at <= now < target and 0 < p < 1):
                continue
        except (TypeError, ValueError, KeyError, OverflowError):
            continue
        if str(f["forecast_id"]) in verifications:
            continue
        eid = ids["event_id"]
        if eid in frozen:
            continue
        to_freeze.append((at, str(f["forecast_id"]), ids, f, target, p))
    to_freeze.sort(key=lambda x: (x[0], x[1]))
    frozen_count = 0
    for at, fid, ids, f, target, p in to_freeze:
        eid = ids["event_id"]
        if eid in frozen:
            continue
        score_p = transform_probability(p, candidate["proposed_change"]["transform"])
        commitment = {
            "shadow_forecast_id": sid("hcf", {"candidate": candidate["candidate_id"], "event_id": eid}),
            "candidate_id": candidate["candidate_id"],
            "event_id": eid,
            "forecast_id": fid,
            "hypothesis_id": ids["hypothesis_id"],
            "hypothesis_version": ids["hypothesis_version"],
            "forecast_revision_id": ids["forecast_revision_id"],
            "forecast_at": ts(at),
            "target_at": ts(target),
            "frozen_at": ts(now),
            "raw_probability": round(p, 12),
            "challenger_probability": round(score_p, 12),
            "source_snapshot_sha256": sha({
                "forecast_id": fid,
                "event_id": eid,
                "forecast_at": ts(at),
                "target_at": ts(target),
                "raw_probability": round(p, 12),
            }),
            "transform_sha256": candidate["proposed_change_sha256"],
            "horizon_bucket": str((f.get("metadata") or {}).get("calibration_horizon_bucket") or "unknown"),
        }
        frozen[eid] = commitment  # immutable; do not update after first write
        frozen_count += 1
    return frozen_count


def _settle(candidate: dict[str, Any], state: Mapping[str, Any], now: datetime) -> tuple[int, list[str]]:
    forecast_by_id = {str(f.get("forecast_id")): f for f in state.get("forecasts", [])
                      if isinstance(f, Mapping) and f.get("forecast_id")}
    verified_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for v in state.get("verifications", []):
        if not isinstance(v, Mapping) or not v.get("forecast_id"):
            continue
        f = forecast_by_id.get(str(v["forecast_id"]))
        if f is not None:
            verified_by_event[identity(f)["event_id"]].append(dict(v))
    new = 0
    conflicts = []
    for eid, commitment in candidate["shadow_forecasts"].items():
        # A later contradicting real Verification must invalidate even an
        # already-passed gate. Never treat an earlier PASS as irreversible.
        if eid in candidate["settlements"]:
            old = candidate["settlements"][eid]
            source_now = forecast_by_id.get(commitment["forecast_id"])
            if source_now is None or identity(source_now)["event_id"] != eid:
                conflicts.append(eid)
                continue
            try:
                current_hash = sha({
                    "forecast_id": commitment["forecast_id"], "event_id": eid,
                    "forecast_at": commitment["forecast_at"],
                    "target_at": commitment["target_at"],
                    "raw_probability": round(_float(source_now["predicted_probability"]), 12),
                })
                if current_hash != commitment["source_snapshot_sha256"]:
                    conflicts.append(eid)
                    continue
            except (TypeError, ValueError, KeyError):
                conflicts.append(eid)
                continue
            trusted = []
            for v in verified_by_event.get(eid, []):
                try:
                    verified_at = utc(v["verified_at"])
                    if (bool(v.get("calibration_eligible", False)) and
                        isinstance(v.get("outcome"), bool) and
                        utc(commitment["target_at"]) <= verified_at <= now and
                        utc(commitment["frozen_at"]) < verified_at and
                        str(v.get("belief_id")) == candidate["hypothesis_id"]):
                        trusted.append(v)
                except (TypeError, ValueError, KeyError, OverflowError):
                    continue
            if (not trusted or
                any(v["outcome"] != old["outcome"] for v in trusted) or
                old.get("source_verification_id") not in {v.get("verification_id") for v in trusted} or
                abs(_float(old.get("control_brier")) -
                    (commitment["raw_probability"] - int(old["outcome"]))**2) > 1e-8 or
                abs(_float(old.get("challenger_brier")) -
                    (commitment["challenger_probability"] - int(old["outcome"]))**2) > 1e-8):
                conflicts.append(eid)
            continue
        source = forecast_by_id.get(commitment["forecast_id"])
        if source is None:
            conflicts.append(eid)
            continue
        try:
            source_p = _float(source["predicted_probability"])
            expected_hash = sha({
                "forecast_id": commitment["forecast_id"], "event_id": eid,
                "forecast_at": commitment["forecast_at"], "target_at": commitment["target_at"],
                "raw_probability": round(source_p, 12),
            })
            if expected_hash != commitment["source_snapshot_sha256"] or identity(source)["event_id"] != eid:
                conflicts.append(eid)
                continue
            if sha(candidate["proposed_change"]["transform"]) != commitment["transform_sha256"]:
                conflicts.append(eid)
                continue
            target = utc(commitment["target_at"])
            shadow_at = utc(commitment["frozen_at"])
            if not shadow_at < target <= now:
                continue
        except (TypeError, ValueError, KeyError, OverflowError):
            conflicts.append(eid)
            continue
        verifications = verified_by_event.get(eid, [])
        if not verifications:
            continue
        eligible = []
        for v in verifications:
            try:
                verified = utc(v["verified_at"])
                if (not bool(v.get("calibration_eligible", False)) or
                    not isinstance(v.get("outcome"), bool) or
                    not target <= verified <= now or verified <= shadow_at):
                    continue
                if str(v.get("belief_id")) != candidate["hypothesis_id"]:
                    continue
                eligible.append(v)
            except (TypeError, ValueError, KeyError, OverflowError):
                continue
        if not eligible:
            continue
        if len({v["outcome"] for v in eligible}) > 1:
            conflicts.append(eid)
            continue
        first = min(eligible, key=lambda v: (str(v["verified_at"]), str(v.get("verification_id"))))
        y = int(first["outcome"])
        p = commitment["raw_probability"]
        q = commitment["challenger_probability"]
        settlement = {
            "event_id": eid,
            "shadow_forecast_id": commitment["shadow_forecast_id"],
            "source_verification_id": first.get("verification_id"),
            "outcome": bool(y),
            "verified_at": first["verified_at"],
            "target_at": commitment["target_at"],
            "control_brier": round((p - y)**2, 12),
            "challenger_brier": round((q - y)**2, 12),
        }
        candidate["settlements"][eid] = settlement
        new += 1
    return new, sorted(set(conflicts))


def _gate(candidate: dict[str, Any], conflict_events: list[str], now: datetime) -> dict[str, Any]:
    frozen = candidate["shadow_forecasts"]
    settled = candidate["settlements"]
    rows = sorted([{"commit": frozen[eid], **v} for eid, v in settled.items() if eid in frozen],
                  key=lambda r: (r["target_at"], r["event_id"]))
    n = len(rows)
    unique_dates = len({r["target_at"][:10] for r in rows})
    pairs_control = [(r["commit"]["raw_probability"], bool(r["outcome"])) for r in rows]
    pairs_challenger = [(r["commit"]["challenger_probability"], bool(r["outcome"])) for r in rows]
    base = _score(pairs_control)
    ch = _score(pairs_challenger)
    gain = _improvement(ch, base)
    per_day = defaultdict(list)
    for r in rows:
        per_day[r["target_at"][:10]].append(r)
    dates = sorted(per_day)
    blocks = []
    if n >= MIN_OOS and len(dates) >= BLOCKS:
        for i in range(BLOCKS):
            part_dates = dates[i * len(dates) // BLOCKS : (i + 1) * len(dates) // BLOCKS]
            part = [r for d in part_dates for r in per_day[d]]
            cm = _score([(r["commit"]["raw_probability"], bool(r["outcome"])) for r in part])
            nm = _score([(r["commit"]["challenger_probability"], bool(r["outcome"])) for r in part])
            blocks.append(_improvement(nm, cm))
    # A pooled probability adjustment MUST not conceal material degradation
    # of a forecast horizon behind stronger aggregate performance.
    slices: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        slices[str(r["commit"].get("horizon_bucket") or "unknown")].append(r)
    horizon_checks = []
    for horizon, segment in sorted(slices.items()):
        c = _score([(x["commit"]["raw_probability"], bool(x["outcome"])) for x in segment])
        q = _score([(x["commit"]["challenger_probability"], bool(x["outcome"])) for x in segment])
        improvement = _improvement(q, c)
        horizon_checks.append({
            "horizon_bucket": horizon, "n": len(segment),
            "brier_relative_improvement": None if improvement is None else round(improvement, 6),
            "protected": len(segment) >= 10,
        })
    blockers = []
    if candidate["horizon_bucket"] == "__ALL_HORIZONS__":
        if any(x["protected"] and (x["brier_relative_improvement"] is None or
               x["brier_relative_improvement"] < -.05) for x in horizon_checks):
            blockers.append("protected_horizon_brier_degradation")
    if conflict_events:
        blockers.append("identity_or_outcome_conflict")
    if n < MIN_OOS:
        blockers.append("minimum_independent_oos_sample")
    if unique_dates < MIN_OOS_DATES:
        blockers.append("minimum_distinct_target_dates")
    if n >= MIN_OOS and unique_dates >= MIN_OOS_DATES:
        if gain is None or gain < MIN_BRIER_GAIN_REL:
            blockers.append("brier_relative_improvement_below_5pct")
        if base["log_loss"] is None or ch["log_loss"] is None or ch["log_loss"] >= base["log_loss"]:
            blockers.append("log_loss_not_improved")
        if base["ece"] is None or ch["ece"] is None or ch["ece"] > base["ece"] + MAX_ECE_DEGRADATION:
            blockers.append("ece_degradation")
        if base["accuracy"] is None or ch["accuracy"] is None or ch["accuracy"] < base["accuracy"] - MAX_ACCURACY_DEGRADATION:
            blockers.append("accuracy_degradation")
        if (len(blocks) < BLOCKS or sum(x is not None and x > 0 for x in blocks) < 3):
            blockers.append("chronological_block_stability_below_3_of_4")
        if any(x is None or x < -MAX_WORST_BLOCK_DEGRADATION for x in blocks):
            blockers.append("chronological_block_loss_above_10pct")
    if conflict_events:
        status = "HOLD"
    elif n < MIN_OOS or unique_dates < MIN_OOS_DATES:
        status = "COLLECTING"
    else:
        status = "PASS" if not blockers else "FAIL"
    return {
        "gate_id": sid("hgate", {"candidate_id": candidate["candidate_id"],
                                 "sample_hash": sha([(r["event_id"], r["outcome"]) for r in rows])}),
        "candidate_id": candidate["candidate_id"],
        "evaluated_at": ts(now),
        "status": status, "prospective_only": True,
        "minimum_sample": MIN_OOS,
        "observed_sample": n,
        "distinct_target_dates": unique_dates,
        "frozen_shadow_predictions": len(frozen),
        "blockers": blockers,
        "conflict_event_ids": conflict_events,
        "criteria": {
            "min_independent_oos": MIN_OOS, "min_target_dates": MIN_OOS_DATES,
            "brier_relative_gain": MIN_BRIER_GAIN_REL,
            "log_loss_improve": True, "max_ece_degradation": MAX_ECE_DEGRADATION,
            "max_accuracy_degradation": MAX_ACCURACY_DEGRADATION,
            "min_positive_chronological_blocks": 3, "max_worst_block_degradation": .10,
        },
        "metrics": {
            "control": base, "challenger": ch,
            "brier_relative_improvement": None if gain is None else round(gain, 6),
            "chronological_block_relative_improvements": [None if x is None else round(x, 6) for x in blocks],
            "horizon_checks": horizon_checks,
        },
        "production_write_authority": False,
    }


def run(state: Mapping[str, Any], utility: Mapping[str, Any],
        previous: Mapping[str, Any] | None = None, *,
        now: str | None = None, discover: bool = True) -> dict[str, Any]:
    at = utc(now) if now else datetime.now(timezone.utc)
    if utility.get("schema_version") != "briefrooms-hypothesis-utility-v1":
        raise ValueError("P1 HUE required")
    if previous and previous.get("schema_version") != SCHEMA:
        raise ValueError("unsupported challenger state schema")
    # Deep copy prevents accidentally mutating previously persisted state objects.
    prior = json.loads(canon(previous or {}))
    candidates: dict[str, dict[str, Any]] = dict(prior.get("candidates") or {})
    attempts: dict[str, Any] = dict(prior.get("discovery_attempts") or {})
    events: list[dict[str, Any]] = []

    # 1. Settle only shadow predictions genuinely committed before outcome.
    for cid, candidate in sorted(candidates.items()):
        if candidate.get("status") not in {"OOS_RUNNING", "PROMOTION_ELIGIBLE", "GATE_FAILED", "HOLD"}:
            continue
        count, conflicts = _settle(candidate, state, at)
        gate = _gate(candidate, conflicts, at)
        old_status = candidate.get("status")
        candidate["gate"] = gate
        if gate["status"] == "PASS":
            candidate["status"] = "PROMOTION_ELIGIBLE"
        elif gate["status"] == "HOLD":
            candidate["status"] = "HOLD"
        elif gate["status"] == "FAIL":
            candidate["status"] = "GATE_FAILED"
        elif old_status not in {"GATE_FAILED", "PROMOTION_ELIGIBLE"}:
            candidate["status"] = "OOS_RUNNING"
        if count:
            events.append({"event": "SHADOW_VERIFICATION_SETTLED", "candidate_id": cid,
                           "newly_settled": count, "at": ts(at)})
        if old_status != candidate["status"]:
            events.append({"event": "GATE_STATUS_CHANGED", "candidate_id": cid,
                           "from": old_status, "to": candidate["status"], "at": ts(at)})

    # 2. Freeze eligible pending canonical forecasts for existing candidates.
    # Settlements are processed next run, NEVER in the same run as freezing.
    for cid, candidate in sorted(candidates.items()):
        if candidate.get("status") != "OOS_RUNNING":
            continue
        count = _freeze(candidate, state, at)
        if count:
            events.append({"event": "SHADOW_FORECASTS_FROZEN", "candidate_id": cid,
                           "newly_frozen": count, "at": ts(at)})

    # 3. Discovery is allowed only from prior REAL verified independent events.
    resolved, source = valid_resolved(state, at)
    by_scope: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in resolved:
        ids = identity(row["f"])
        horizon = str((row["f"].get("metadata") or {}).get("calibration_horizon_bucket") or "unknown")
        by_scope[_candidate_key(ids["hypothesis_id"], ids["hypothesis_version"], horizon)].append(row)
        by_scope[_candidate_key(ids["hypothesis_id"], ids["hypothesis_version"], "__ALL_HORIZONS__")].append(row)
    slots = max(0, MAX_ACTIVE - sum(c.get("status") in {"OOS_RUNNING", "HOLD", "PROMOTION_ELIGIBLE"}
                                     for c in candidates.values()))
    created = 0
    for bid, h in sorted((utility.get("hypotheses") or {}).items()) if discover else []:
        if created >= MAX_NEW_PER_RUN or slots <= 0:
            break
        if not isinstance(h, Mapping) or h.get("lifecycle_status") != "CHALLENGER":
            continue
        version = str(h.get("hypothesis_version") or "1")
        scopes = [(scope, rows) for scope, rows in by_scope.items()
                  if scope.startswith(str(bid) + "@" + version + "#")]
        for scope, rows in sorted(scopes, key=lambda x: (-len(x[1]), x[0])):
            if len(rows) < MIN_DISCOVERY:
                continue
            existing = [x for x in candidates.values() if x.get("scope") == scope]
            if any(x.get("status") in {"OOS_RUNNING", "HOLD", "PROMOTION_ELIGIBLE"}
                   for x in existing):
                continue
            attempt = attempts.get(scope) or {}
            if len(rows) < int(attempt.get("retry_after_n") or 0):
                continue
            if existing and len(rows) < max(int(x.get("discovery", {}).get("source_n") or 0)
                                       for x in existing) + MIN_NEW_AFTER_REJECTION:
                continue
            fit = fit_candidate(rows)
            attempts[scope] = {
                "last_at": ts(at), "source_n": len(rows),
                "retry_after_n": len(rows) + MIN_NEW_AFTER_REJECTION,
                "result": "VALID_SHADOW_CHALLENGER" if fit else "NO_DISCOVERY_HOLDOUT_LIFT",
            }
            if not fit:
                events.append({"event": "DISCOVERY_NOT_VALIDATED", "scope": scope, "at": ts(at)})
                continue
            round_number = 1 + sum(x.get("scope") == scope for x in existing)
            horizon = scope.rsplit("#", 1)[-1]
            candidate = _new_candidate(str(bid), version, horizon, fit, at, round_number)
            candidate["discovery"]["source_n"] = len(rows)
            candidates[candidate["candidate_id"]] = candidate
            events.append({"event": "CHALLENGER_PREREGISTERED", "scope": scope,
                           "candidate_id": candidate["candidate_id"], "at": ts(at)})
            created += 1
            slots -= 1
            break

    # Do not erase prior events; bound the audit window in the cumulative artifact.
    audit = (list(prior.get("audit_events") or []) + events)[-1000:]
    summary = {
        "candidates_total": len(candidates),
        "oos_running": sum(x.get("status") == "OOS_RUNNING" for x in candidates.values()),
        "gate_pass": sum(x.get("gate", {}).get("status") == "PASS" for x in candidates.values()),
        "promotion_eligible": sum(x.get("status") == "PROMOTION_ELIGIBLE" for x in candidates.values()),
        "gate_failed": sum(x.get("status") == "GATE_FAILED" for x in candidates.values()),
        "held": sum(x.get("status") == "HOLD" for x in candidates.values()),
        "frozen_shadow_forecasts": sum(len(x.get("shadow_forecasts") or {}) for x in candidates.values()),
        "settled_oos_events": sum(len(x.get("settlements") or {}) for x in candidates.values()),
        "new_candidate_count": created,
        "discovery_scopes_attempted": len(attempts),
        "discovery_scopes_no_validated_candidate": sum(
            x.get("result") == "NO_DISCOVERY_HOLDOUT_LIFT" for x in attempts.values()
        ),
        "production_promotions": 0,
    }
    return {
        "schema_version": SCHEMA, "mode": "shadow_oos_governed",
        "generated_at": ts(at), "authority": dict(AUTHORITY),
        "policy": {
            "min_discovery_events": MIN_DISCOVERY,
            "min_discovery_dates": MIN_DISCOVERY_DATES,
            "min_oos_events": MIN_OOS,
            "min_oos_dates": MIN_OOS_DATES,
            "one_revision_per_event": POLICY,
            "candidate_type": "probability_methodology_only_not_semantic_hypothesis_mutation",
            "promotion_route": "briefrooms_evolution_controller_review_no_automatic_materialization",
        },
        "source": {"eligible_resolved": len(resolved), **source},
        "summary": summary, "candidates": candidates,
        "discovery_attempts": attempts, "audit_events": audit, "events_this_run": events,
    }


def public_view(report: Mapping[str, Any]) -> dict[str, Any]:
    if report.get("schema_version") != SCHEMA:
        return {"schema_version": SCHEMA, "status": "NOT_AVAILABLE",
                "summary": {"candidates_total": 0}, "candidates": [],
                "authority": {"production_writeback": False}}
    candidates = []
    for c in sorted((report.get("candidates") or {}).values(),
                    key=lambda x: (str(x.get("scope")), str(x.get("candidate_id")))):
        g = c.get("gate") or {}
        candidates.append({
            "candidate_id": c.get("candidate_id"),
            "candidate_type": c.get("candidate_type"),
            "hypothesis_id": c.get("hypothesis_id"),
            "hypothesis_version": c.get("hypothesis_version"),
            "horizon_bucket": c.get("horizon_bucket"),
            "status": c.get("status"),
            "created_at": c.get("created_at"),
            "activation_boundary": c.get("activation_boundary"),
            "discovery": {
                k: c.get("discovery", {}).get(k)
                for k in ("source_n", "discovery_n", "validation_n",
                          "validation_brier_relative_improvement", "validation_distinct_target_dates")
            },
            "gate": {
                "status": g.get("status"), "observed_sample": g.get("observed_sample"),
                "minimum_sample": g.get("minimum_sample"),
                "distinct_target_dates": g.get("distinct_target_dates"),
                "frozen_shadow_predictions": g.get("frozen_shadow_predictions"),
                "blockers": list(g.get("blockers") or []),
                "brier_relative_improvement": (g.get("metrics") or {}).get("brier_relative_improvement"),
                "positive_chronological_blocks": sum((x is not None and x > 0)
                    for x in (g.get("metrics") or {}).get("chronological_block_relative_improvements", [])),
                "protected_horizon_checks": list((g.get("metrics") or {}).get("horizon_checks") or []),
            },
            "automatic_promotion_allowed": False,
        })
    return {
        "schema_version": SCHEMA, "generated_at": report.get("generated_at"),
        "status": "SHADOW_OOS_GOVERNED",
        "summary": dict(report.get("summary") or {}),
        "policy": dict(report.get("policy") or {}),
        "authority": {"production_writeback": False,
                      "automatic_production_promotion": False,
                      "trade_execution": False},
        "candidates": candidates,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Run automated P2 governed challenger loop")
    p.add_argument("--state-dir", type=Path, required=True)
    p.add_argument("--output", type=Path)
    p.add_argument("--now")
    p.add_argument("--advance-only", action="store_true",
                   help="Belief collector bridge: freeze/settle existing candidates only, no discovery")
    args = p.parse_args()
    base = args.state_dir
    state = json.loads((base / "state.json").read_text(encoding="utf-8"))
    output = args.output or base / "HYPOTHESIS_CHALLENGERS_STATE.json"
    if args.advance_only and not output.exists():
        print("P2_BRIDGE_NO_ACTIVE_STATE: calibration creates candidates; no bootstrap in collector")
        return 0
    utility_path = base / "HYPOTHESIS_UTILITY_REPORT.json"
    if args.advance_only:
        utility = {"schema_version": "briefrooms-hypothesis-utility-v1", "hypotheses": {}}
    else:
        utility = json.loads(utility_path.read_text(encoding="utf-8"))
    previous = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
    result = run(state, utility, previous, now=args.now, discover=not args.advance_only)
    if args.advance_only:
        result["bridge_telemetry"] = {
            "last_collector_bridge_at": result["generated_at"],
            "last_collector_bridge_events": len(result["events_this_run"]),
            "last_collector_bridge_freezes": sum(
                int(e.get("newly_frozen") or 0) for e in result["events_this_run"]
                if e.get("event") == "SHADOW_FORECASTS_FROZEN"
            ),
            "last_collector_bridge_settlements": sum(
                int(e.get("newly_settled") or 0) for e in result["events_this_run"]
                if e.get("event") == "SHADOW_VERIFICATION_SETTLED"
            ),
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, output)
    print(json.dumps(result["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

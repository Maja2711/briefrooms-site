#!/usr/bin/env python3
"""Preregistered, timestamp-checked EURUSD event-risk SHADOW comparison.

This evaluator does not fetch prices, reconstruct missed entries, alter trades,
certify a broker fill, or activate a policy. Missing prospective samples remain
missing. Outcomes never enter the decision-time assessor.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from eurusd_central_bank_event_risk import assess_pre_event, validate_policy
from eurusd_central_bank_wire import dt, iso, digest, validate_snapshot

SCHEMA = "eurusd-cb-shadow-replay-v1"
ROOT = Path(__file__).resolve().parents[1]
MIN_EVENTS = 40


def evaluator_hash() -> str:
    files = ("eurusd_cb_shadow_replay.py", "eurusd_central_bank_event_risk.py",
             "eurusd_central_bank_wire.py", "eurusd_central_bank_news_interpreter.py")
    return digest({name: hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest() for name in files})


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _seal(payload: Mapping, field: str) -> dict:
    result = copy.deepcopy(dict(payload))
    result.pop(field, None)
    result[field] = digest(result)
    return result


def freeze_manifest(descriptors: Sequence[Mapping], policy: Mapping, *, frozen_at: datetime,
                    slippage_pips: Sequence[float] = (0., 1., 3.)) -> dict:
    """Freeze sample membership and analysis choices before any decision/outcome.

    Train/test membership is supplied before collection, never selected from
    winning outcomes. Independent support counts events, not two engines or
    repeated observations of the same event.
    """
    frozen = dt(frozen_at)
    if not frozen:
        raise ValueError("aware freeze time required")
    policy = validate_policy(policy)
    if not descriptors or len(descriptors) > 1000:
        raise ValueError("predeclare 1..1000 episodes")
    allowed = {"episode_id", "event_id", "bank", "scope", "decision_at", "horizon_at", "split"}
    ids, rows = set(), []
    for item in descriptors:
        if set(item) != allowed or not all(isinstance(item.get(key), str) and item[key] for key in allowed):
            raise ValueError("descriptor must contain only predeclared identities/times/split")
        row = dict(item)
        start, end = dt(row["decision_at"]), dt(row["horizon_at"])
        if not start or not end or not frozen <= start < end or end - start > timedelta(hours=24):
            raise ValueError("freeze must precede decision and bounded horizon")
        if row["episode_id"] in ids or row["bank"] not in {"FED", "ECB"} or row["scope"] not in {"EURUSD_WEEKLY", "EURUSD_DAILY"} or row["split"] not in {"TRAIN", "TEST"}:
            raise ValueError("invalid/duplicate episode identity")
        ids.add(row["episode_id"])
        row.update(decision_at=iso(start), horizon_at=iso(end))
        rows.append(row)
    train = [row for row in rows if row["split"] == "TRAIN"]
    test = [row for row in rows if row["split"] == "TEST"]
    if train and test and max(dt(row["horizon_at"]) for row in train) >= min(dt(row["decision_at"]) for row in test):
        raise ValueError("training and test windows must be chronological and disjoint")
    # One event can have both independent engine adapters, but cannot occur in
    # both train/test or manufacture independent support from overlapping events.
    by_event: dict[str, list[dict]] = {}
    for row in rows:
        by_event.setdefault(row["event_id"], []).append(row)
    for event_rows in by_event.values():
        if len({row["split"] for row in event_rows}) != 1 or len({row["bank"] for row in event_rows}) != 1:
            raise ValueError("event cluster crosses bank or train/test split")
    event_windows = sorted((min(dt(row["decision_at"]) for row in group), max(dt(row["horizon_at"]) for row in group), event_id)
                           for event_id, group in by_event.items())
    if any(right[0] <= left[1] for left, right in zip(event_windows, event_windows[1:])):
        raise ValueError("independent event outcome windows overlap")
    stress = tuple(_finite(value) for value in slippage_pips)
    if not stress or len(stress) > 8 or any(value is None or not 0 <= value <= 20 for value in stress) or 0. not in stress:
        raise ValueError("bounded slippage sensitivity including zero required")
    payload = {"schema_version": SCHEMA, "kind": "PREREGISTRATION", "frozen_at": iso(frozen),
               "episodes": sorted(rows, key=lambda row: (row["decision_at"], row["episode_id"])),
               "policy": policy, "policy_sha256": digest(policy), "evaluator_sha256": evaluator_hash(),
               "minimum_independent_test_events": MIN_EVENTS, "minimum_events_per_bank": 10,
               "minimum_test_regimes": 2, "slippage_stress_pips": list(stress),
               "maximum_execution_delay_seconds": 60, "maximum_end_quote_age_seconds": 60,
               "alpha": 0.05, "minimum_net_delta_R": 0., "maximum_incremental_drawdown_R": 0.,
               "authority": "SHADOW_ONLY_NO_PROMOTION"}
    return _seal(payload, "manifest_sha256")


def validate_manifest(manifest: Mapping) -> None:
    if manifest.get("schema_version") != SCHEMA or manifest.get("manifest_sha256") != _seal(manifest, "manifest_sha256")["manifest_sha256"]:
        raise ValueError("invalid frozen manifest")
    rebuilt = freeze_manifest(manifest["episodes"], manifest["policy"], frozen_at=dt(manifest["frozen_at"]), slippage_pips=manifest["slippage_stress_pips"])
    if rebuilt != manifest:
        raise ValueError("manifest analysis rules or evaluator changed after freeze")


def _quote(row: Mapping, *, visible_by: datetime) -> dict | None:
    at, received = dt(row.get("observed_at")), dt(row.get("ingested_at"))
    bid, ask, cost = (_finite(row.get(key)) for key in ("bid", "ask", "cost_pips"))
    if not at or not received or not at <= received <= visible_by or not row.get("source") or not row.get("quote_id") or row.get("verified") is not True:
        return None
    if received - at > timedelta(seconds=60):
        return None
    if bid is None or ask is None or cost is None or not .1 <= bid <= ask <= 5 or cost < 0:
        return None
    return {**row, "bid": bid, "ask": ask, "cost_pips": cost}


def _metrics(curve: Sequence[float]) -> dict:
    peak = 0.
    drawdown = 0.
    for value in curve:
        peak = max(peak, value)
        drawdown = max(drawdown, peak - value)
    increments = [curve[0]] + [right - left for left, right in zip(curve, curve[1:])] if curve else []
    ordered = sorted(increments)
    tail_count = max(1, math.ceil(len(ordered) * .05))
    return {"net_R": curve[-1] if curve else None, "maximum_drawdown_R": drawdown,
            "observed_episode_tail_mean_5pct_R": sum(ordered[:tail_count]) / tail_count if ordered else None,
            "tail_statistic_status": "DESCRIPTIVE_NOT_CALIBRATED_ES95", "equity_curve_R": list(curve)}


def _publication_windows(descriptor: Mapping, episode: Mapping, path: Sequence[Mapping], *,
                         visible_by: datetime, risk_pips: float, sign: int) -> dict:
    """Observed +1/+5/+15/+30/+60/+120m paths, never calibrated forecasts."""
    snapshot = episode.get("outcome_wire") or episode["decision_input"]["wire"]
    if not validate_snapshot(snapshot, visible_by):
        return {"status": "OFFICIAL_PUBLICATION_RECEIPT_MISSING_OR_INVALID", "windows": []}
    documents = [row for row in snapshot["events"] if row.get("bank") == descriptor["bank"]
                 and row.get("related_calendar_event_id") == descriptor["event_id"] and row.get("statement_verified")]
    if not documents:
        return {"status": "OFFICIAL_PUBLICATION_RECEIPT_MISSING_OR_INVALID", "windows": []}
    # An amended publication does not move the original release-time reference.
    published = min(dt(row["published_at"]) for row in documents)
    eligible = [row for row in path if dt(row["observed_at"]) >= published]
    if not eligible:
        return {"status": "NO_POST_PUBLICATION_EXECUTABLE_QUOTES", "windows": []}
    def mark(row):
        return row["bid"] if sign == 1 else row["ask"]
    reference = eligible[0]
    result = []
    for minutes in (1, 5, 15, 30, 60, 120):
        cutoff = published + timedelta(minutes=minutes)
        sample = [row for row in eligible if dt(row["observed_at"]) <= cutoff and dt(row["ingested_at"]) <= cutoff]
        if not sample or (cutoff - dt(sample[-1]["observed_at"])).total_seconds() > 60 or (dt(reference["observed_at"]) - published).total_seconds() > 60:
            result.append({"minutes": minutes, "status": "INSUFFICIENT_QUOTE_COVERAGE"})
            continue
        changes = [sign * (mark(row) - mark(reference)) * 10000 / risk_pips for row in sample]
        result.append({"minutes": minutes, "status": "OBSERVED_QUOTE_PATH_ONLY", "mae_R": min([0., *changes]),
                       "mfe_R": max([0., *changes]), "absolute_move_pips": abs(mark(sample[-1]) - mark(reference)) * 10000,
                       "net_exit_R_change": changes[-1] - (sample[-1]["cost_pips"] - reference["cost_pips"]) / risk_pips,
                       "maximum_quote_gap_seconds": max((dt(right["observed_at"]) - dt(left["observed_at"])).total_seconds() for left, right in zip(sample, sample[1:])) if len(sample) > 1 else 0.})
    return {"status": "DESCRIPTIVE_NOT_CALIBRATED", "published_at": iso(published),
            "reference_quote_id": reference["quote_id"], "windows": result}


def _episode(descriptor: Mapping, episode: Mapping, manifest: Mapping, *, evaluated_at: datetime) -> dict:
    decision_at, horizon = dt(descriptor["decision_at"]), dt(descriptor["horizon_at"])
    if evaluated_at < horizon:
        raise ValueError("episode outcome horizon not yet observable")
    inputs = episode.get("decision_input")
    if not isinstance(inputs, Mapping) or not {"position", "quote", "wire", "calendar_events"} <= set(inputs):
        raise ValueError("missing captured decision inputs")
    receipt = episode.get("capture_receipt", {})
    if receipt.get("input_sha256") != digest(inputs) or dt(receipt.get("captured_at")) != decision_at or not receipt.get("immutable_id"):
        raise ValueError("missing immutable decision-time capture receipt")
    position, quote, snapshot = inputs["position"], inputs["quote"], inputs["wire"]
    if position.get("instrument_id") != "eurusd" or position.get("position_id") != episode.get("position_id"):
        raise ValueError("position identity must be captured EURUSD")
    if not validate_snapshot(snapshot, decision_at):
        raise ValueError("future or invalid as-of wire")
    decision_quote = _quote(quote, visible_by=decision_at)
    if decision_quote is None:
        raise ValueError("decision quote has no executable as-of proof")
    if (decision_at - dt(quote["observed_at"])).total_seconds() > manifest["policy"]["quote_max_age_seconds"]:
        raise ValueError("decision quote is stale")
    # NO future path is passed into this call.
    assessment = assess_pre_event(position, decision_quote, snapshot, inputs["calendar_events"], manifest["policy"], now=decision_at, scope=descriptor["scope"])
    if assessment.position_id != position["position_id"]:
        raise ValueError("assessor position mismatch")
    if assessment.event_id != descriptor["event_id"] or assessment.diagnostics.get("bank") != descriptor["bank"]:
        raise ValueError("decision is for a different central-bank event")
    sign = 1 if position.get("direction") == "LONG" else -1
    entry, stop, target, initial = (_finite(position.get(key)) for key in ("entry_price", "stop_loss", "take_profit", "initial_stop_loss"))
    if initial is None:
        initial = stop
    if not entry or not stop or not target or not initial or not sign * (entry - initial) > 0 or not sign * (target - entry) > 0:
        raise ValueError("invalid frozen position geometry")
    risk_pips = abs(entry - initial) * 10000
    path = []
    for raw in episode.get("outcome_quotes", []):
        normalized = _quote(raw, visible_by=evaluated_at)
        if normalized is None:
            raise ValueError("unverified future outcome quote")
        if decision_at <= dt(normalized["observed_at"]) <= dt(normalized["ingested_at"]) <= horizon:
            path.append(normalized)
    path.sort(key=lambda row: (row["ingested_at"], row["observed_at"], row["quote_id"]))
    if len({row["quote_id"] for row in path}) != len(path) or not path:
        raise ValueError("missing/duplicate outcome price evidence")
    if any(left["observed_at"] == right["observed_at"] and (left["bid"], left["ask"]) != (right["bid"], right["ask"]) for left, right in zip(path, path[1:])):
        raise ValueError("conflicting executable prices at one timestamp")
    def price(row):
        return row["bid"] if sign == 1 else row["ask"]
    baseline_exit, baseline_reason = None, None
    canonical = episode.get("canonical_exit")
    canonical_quote = None
    if canonical:
        canonical_quote = next((row for row in path if row["quote_id"] == canonical.get("quote_id")), None)
        executed = dt(canonical.get("executed_at"))
        if not canonical_quote or canonical.get("position_id") != position["position_id"] or canonical.get("verified") is not True or not canonical.get("immutable_id") or executed != dt(canonical_quote["observed_at"]) or _finite(canonical.get("price")) != price(canonical_quote):
            raise ValueError("canonical exit receipt does not match executable evidence")
    for row in path:
        mark = price(row)
        # A gap closes at its observed executable side, never at an assumed SL.
        if sign * (mark - stop) <= 0:
            baseline_exit, baseline_reason = row, "FROZEN_SL_OBSERVED_QUOTE"
            break
        if sign * (mark - target) >= 0:
            baseline_exit, baseline_reason = row, "FROZEN_TP_OBSERVED_QUOTE"
            break
        if canonical_quote is row:
            baseline_exit, baseline_reason = row, "CANONICAL_LIFECYCLE_EXIT_RECEIPT"
            break
    if baseline_exit is None:
        baseline_exit, baseline_reason = path[-1], "HORIZON_MARK_NOT_A_TRADE_FILL"
        if (horizon - dt(baseline_exit["observed_at"])).total_seconds() > manifest["maximum_end_quote_age_seconds"]:
            raise ValueError("end-of-window executable quote is stale")
    event_exit = baseline_exit
    policy_status = "BASELINE_HELD"
    if assessment.action == "CLOSE_BEFORE_EVENT":
        earliest = next((row for row in path if dt(row["observed_at"]) >= decision_at and
                         (dt(row["ingested_at"]) - decision_at).total_seconds() <= manifest["maximum_execution_delay_seconds"]), None)
        if earliest is None:
            raise ValueError("no timely executable quote for shadow close")
        if dt(earliest["ingested_at"]) <= dt(baseline_exit["ingested_at"]):
            event_exit, policy_status = earliest, "COUNTERFACTUAL_CLOSE_NOT_EXECUTED"
    elif assessment.action == "PROTECT_PROFIT":
        policy_status = "UNSUPPORTED_STOP_UPGRADE_BASELINE_RETAINED"
    regime = episode.get("regime", {})
    if not isinstance(regime, Mapping) or not regime.get("label") or not regime.get("source") or not dt(regime.get("observed_at")) or dt(regime["observed_at"]) > decision_at:
        raise ValueError("regime must be sourced and known at decision")
    def curve(until, slippage):
        values = []
        for row in path:
            if dt(row["ingested_at"]) > dt(until["ingested_at"]):
                break
            values.append((sign * (price(row) - entry) * 10000 - row["cost_pips"] - slippage) / risk_pips)
        return values
    stress = []
    for slippage in manifest["slippage_stress_pips"]:
        base_curve, event_curve = curve(baseline_exit, slippage), curve(event_exit, slippage)
        # Once closed, the counterfactual equity remains constant over the same
        # quote-time grid as baseline; no profitable future mark is substituted.
        event_curve += [event_curve[-1]] * (len(base_curve) - len(event_curve))
        base_metrics, event_metrics = _metrics(base_curve), _metrics(event_curve)
        stress.append({"slippage_pips": slippage, "baseline_original": base_metrics,
                       "event_risk_policy": event_metrics, "delta_net_R": event_metrics["net_R"] - base_metrics["net_R"],
                       "opportunity_cost_R": max(0., base_metrics["net_R"] - event_metrics["net_R"]),
                       "unnecessary_close": policy_status == "COUNTERFACTUAL_CLOSE_NOT_EXECUTED" and event_metrics["net_R"] < base_metrics["net_R"]})
    return {"episode_id": descriptor["episode_id"], "event_id": descriptor["event_id"], "bank": descriptor["bank"],
            "scope": descriptor["scope"], "split": descriptor["split"], "regime": regime["label"],
            "decision_at": descriptor["decision_at"], "horizon_at": descriptor["horizon_at"],
            "decision_id": assessment.audit["immutable_id"], "recommended_action": assessment.action,
            "shadow_status": policy_status, "baseline_exit_reason": baseline_reason,
            "baseline_exit_quote_id": baseline_exit["quote_id"], "policy_exit_quote_id": event_exit["quote_id"],
            "close_latency_seconds": (dt(event_exit["ingested_at"]) - decision_at).total_seconds() if policy_status == "COUNTERFACTUAL_CLOSE_NOT_EXECUTED" else None,
            "input_sha256": digest(inputs), "episode_sha256": digest(episode), "stress_results": stress,
            "baseline_parity": episode.get("baseline_lifecycle_parity", "NOT_CERTIFIED"),
            "publication_windows": _publication_windows(descriptor, episode, path, visible_by=evaluated_at, risk_pips=risk_pips, sign=sign),
            "price_coverage": "OBSERVED_EXECUTABLE_QUOTES_ONLY_INTRABAR_UNKNOWN", "execution_authorized": False}


def evaluate(manifest: Mapping, episodes: Sequence[Mapping], *, evaluated_at: datetime) -> dict:
    validate_manifest(manifest)
    evaluated = dt(evaluated_at)
    if not evaluated or evaluated < dt(manifest["frozen_at"]):
        raise ValueError("invalid evaluation time")
    selected = {row["episode_id"]: row for row in manifest["episodes"]}
    supplied = {}
    for row in episodes:
        identity = row.get("episode_id")
        if identity not in selected or identity in supplied:
            raise ValueError("unselected or duplicate episode cannot change frozen sample")
        supplied[identity] = row
    results, pending, rejected = [], [], []
    for identity, descriptor in selected.items():
        if identity not in supplied or evaluated < dt(descriptor["horizon_at"]):
            pending.append(identity)
            continue
        try:
            results.append(_episode(descriptor, supplied[identity], manifest, evaluated_at=evaluated))
        except (ValueError, KeyError, TypeError) as error:
            rejected.append({"episode_id": identity, "reason": str(error)})
    test = [row for row in results if row["split"] == "TEST"]
    events = {row["event_id"] for row in test}
    banks = {bank: len({row["event_id"] for row in test if row["bank"] == bank}) for bank in ("FED", "ECB")}
    regimes = sorted({row["regime"] for row in test})
    evidence_ready = (not pending and not rejected and len(events) >= manifest["minimum_independent_test_events"]
                      and all(count >= manifest["minimum_events_per_bank"] for count in banks.values())
                      and len(regimes) >= manifest["minimum_test_regimes"]
                      and all(row["baseline_parity"] == "CERTIFIED_BY_SEPARATE_REVIEW" for row in test))
    summaries = []
    for index, slippage in enumerate(manifest["slippage_stress_pips"]):
        # Equal weight per independent event cluster; running both adapters does
        # not double effective N. Statistics are descriptive pending calibration.
        cluster_rows = []
        for event_id in sorted(events, key=lambda event_id: min(row["decision_at"] for row in test if row["event_id"] == event_id)):
            members = [row["stress_results"][index] for row in test if row["event_id"] == event_id]
            cluster_rows.append((sum(row["baseline_original"]["net_R"] for row in members) / len(members),
                                 sum(row["event_risk_policy"]["net_R"] for row in members) / len(members)))
        base_curve, event_curve = [], []
        for baseline, event in cluster_rows:
            base_curve.append((base_curve[-1] if base_curve else 0.) + baseline)
            event_curve.append((event_curve[-1] if event_curve else 0.) + event)
        summaries.append({"slippage_pips": slippage, "independent_test_events": len(events),
                          "baseline_original": _metrics(base_curve), "event_risk_policy": _metrics(event_curve),
                          "mean_paired_delta_R": sum(event - baseline for baseline, event in cluster_rows) / len(cluster_rows) if cluster_rows else None,
                          "unnecessary_closes": sum(row["stress_results"][index]["unnecessary_close"] for row in test),
                          "statistical_status": "DESCRIPTIVE_NO_AUTOMATIC_PROMOTION"})
    status = "PENDING_INCOMPLETE_FROZEN_SAMPLE" if pending else "INVALID_EVIDENCE" if rejected else "READY_FOR_INDEPENDENT_REVIEW" if evidence_ready else "INSUFFICIENT_EVIDENCE"
    return _seal({"schema_version": SCHEMA, "kind": "SHADOW_EVALUATION", "evaluated_at": iso(evaluated),
                  "manifest_sha256": manifest["manifest_sha256"], "evaluator_sha256": manifest["evaluator_sha256"],
                  "status": status, "sample_closed": not pending and not rejected, "selected_count": len(selected),
                  "pending_episode_ids": pending, "rejected_episodes": rejected, "results": results,
                  "coverage": {"independent_test_events": len(events), "by_bank": banks, "regimes": regimes},
                  "sensitivity": summaries, "activation_allowed": False, "operator_approval_required": True,
                  "limitations": ["NO_REAL_DATA_IS_CREATED_BY_THIS_EVALUATOR", "COUNTERFACTUAL_EXITS_ARE_NOT_BROKER_FILLS",
                                  "CANONICAL_DYNAMIC_EXIT_PARITY_REQUIRES_SEPARATE_REVIEW", "QUOTE_GAPS_DO_NOT_PROVE_INTRABAR_SL_TP_PATH",
                                  "DESCRIPTIVE_TAILS_ARE_NOT_CALIBRATED_PROBABILITIES", "MULTIPLE_POLICY_TRIALS_REQUIRE_PREDECLARED_FAMILY_CORRECTION"]}, "report_sha256")


def persist_immutable(path: str | Path, payload: Mapping) -> bool:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    try:
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
    except FileExistsError:
        if target.read_text(encoding="utf-8") != body:
            raise ValueError("frozen replay artifact already exists with different content")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("freeze", "evaluate"))
    parser.add_argument("--input", type=Path, required=True, help="Descriptors for freeze; captured episodes for evaluate")
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.input.stat().st_size > 30_000_000:
        parser.error("input byte cap exceeded; split capture storage before evaluation")
    incoming = json.loads(args.input.read_text(encoding="utf-8"))
    if args.mode == "freeze":
        if not args.policy:
            parser.error("freeze requires --policy")
        output = freeze_manifest(incoming, json.loads(args.policy.read_text(encoding="utf-8")), frozen_at=now)
    else:
        if not args.manifest:
            parser.error("evaluate requires --manifest")
        output = evaluate(json.loads(args.manifest.read_text(encoding="utf-8")), incoming, evaluated_at=now)
    persist_immutable(args.output, output)
    print(json.dumps({"kind": output["kind"], "status": output.get("status", "FROZEN"), "activation_allowed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

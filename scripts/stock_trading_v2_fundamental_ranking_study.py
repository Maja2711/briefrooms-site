#!/usr/bin/env python3
"""Research-only, prospective paired net-return study of fundamental ranking.

Never changes the V1 US fundamental quality gate, opportunity utility, trading
orders, portfolio, or main-owned promotion controller. Each session is frozen
BEFORE outcomes and all ranking alternatives use the exact same eligible set.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import stock_trading_fundamental_policy as quality
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_fundamental_trial as v1
    from scripts import stock_trading_v2_opportunity_experience as events
    from scripts import stock_trading_v2_experience_store as experience
    from scripts import stock_trading_v2_outcome_replay as outcomes
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_fundamental_policy as quality
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_fundamental_trial as v1
    import stock_trading_v2_opportunity_experience as events
    import stock_trading_v2_experience_store as experience
    import stock_trading_v2_outcome_replay as outcomes

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data/investments/stock_trading_v2_fundamental_ranking_config.json"
V1_PROTOCOL_PATH = ROOT / "data/investments/stock_trading_v2_fundamental_trial_config.json"
REPLAY_CONFIG_PATH = ROOT / "data/investments/stock_trading_v2_learning_config.json"
STUDY_ROOT = ROOT / "data/investments/stock_trading_v2_fundamental_trials/ranking_v1"
V1_ROOT = v1.DEFAULT_ROOT
EVENT_ROOT = v1.EXPERIENCE_ROOT
OUTCOME_ROOT = outcomes.DEFAULT_ROOT
REPORT_PATH = ROOT / "data/investments/stock_trading_v2_learning/fundamental_ranking_study.json"
SCHEMA = "stock-trading-v2-fundamental-ranking-study-v1"
ARMS = (10, 20, 30)
REPLAY_VERSION = "frozen-geometry-next-session-open-v1"


def read(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("research study requires JSON object: " + str(path))
    return result


def validate_protocol(config: Mapping[str, Any]) -> None:
    if (config.get("schema_version") != "stock-trading-v2-fundamental-ranking-protocol-v1"
            or config.get("baseline") != "US_QUALITY_GATE_55_V1"
            or config.get("weights_percent") != list(ARMS)
            or config.get("fixed_n") != 60
            or config.get("horizons_sessions") != [1, 2]
            or config.get("one_formal_look") is not True
            or config.get("promotion_authority") is not False):
        raise ValueError("ranking study protocol must remain fixed and non-production")
    if (config.get("bootstrap_resamples") != 4000
            or config.get("block_length_sessions") != 5
            or config.get("familywise_alpha") != 0.05):
        raise ValueError("ranking study statistical contract drift")
    quality.utc(config["experiment_not_before"])


def eligible_quality(opportunity: Mapping[str, Any]) -> list[dict[str, Any]]:
    """V1-quality-approved *and* existing-risk/edge-approved candidates only."""
    at = str(opportunity.get("generated_at") or "")
    approved = []
    for row in quality.original_candidates(opportunity):
        ok, _ = quality.verify_fundamental(row, decision_at=at)
        if ok and float(row["fundamental_analysis"]["fundamental_score"]) >= quality.MIN_FUNDAMENTAL_SCORE:
            approved.append(row)
    return approved


def choose(opportunity: Mapping[str, Any], weight_percent: int) -> dict[str, Any] | None:
    if weight_percent not in (0, *ARMS):
        raise ValueError("unregistered ranking arm")
    approved = eligible_quality(opportunity)
    if not approved:
        return None
    if weight_percent == 0:
        return approved[0]
    # Scores are 0..100. This is a RE-RANK only, never an eligibility gate
    # relaxation, utility modification, or instruction to buy.
    return min(
        approved,
        key=lambda row: (
            -((100 - weight_percent) * float(row["utility"])
              + weight_percent * float(row["fundamental_analysis"]["fundamental_score"])) / 100,
            -float(row["utility"]),
            int(row.get("deep_rank") or 999999),
            str(row["symbol"]),
        ),
    )


def choices(snapshot: Mapping[str, Any]) -> dict[str, str | None]:
    return {
        "v1": (choose(snapshot, 0) or {}).get("symbol"),
        **{
            "w" + str(w): (choose(snapshot, w) or {}).get("symbol")
            for w in ARMS
        },
    }


def validate_receipt(
    receipt: Mapping[str, Any], config: Mapping[str, Any], v1_receipt: Mapping[str, Any],
) -> None:
    validate_protocol(config)
    if (receipt.get("schema_version") != SCHEMA or receipt.get("market") != "US"
            or receipt.get("protocol_sha256") != contracts.payload_sha256(config)
            or receipt.get("v1_trial_sha256") != v1_receipt.get("trial_sha256")
            or receipt.get("quality_policy_sha256") != v1.digest_file(Path(quality.__file__))
            or receipt.get("ranking_code_sha256") != v1.digest_file(Path(__file__))):
        raise ValueError("ranking study schema/protocol/code provenance mismatch")
    if receipt.get("governance") != {
        "immutable": True, "no_lookahead": True,
        "production_decision_influence": False, "automatic_promotion": False,
    }:
        raise ValueError("ranking study gained unauthorized authority")
    decision = quality.utc(receipt.get("decision_at"))
    frozen = quality.utc(receipt.get("frozen_at"))
    if (decision < quality.utc(config["experiment_not_before"])
            or decision > frozen or (frozen - decision).total_seconds() > 3600
            or receipt.get("session_date") != decision.date().isoformat()
            or receipt.get("decision_at") != v1_receipt.get("decision_at")):
        raise ValueError("ranking trial not frozen prospectively with V1")
    snap = receipt.get("decision_input")
    if (not isinstance(snap, Mapping) or snap != v1_receipt.get("decision_input")
            or receipt.get("source_opportunity_sha256") != v1_receipt.get("source_opportunity_sha256")
            or receipt.get("source_evidence_sha256") != v1_receipt.get("source_evidence_sha256")
            or receipt.get("source_fundamentals_sha256") != v1_receipt.get("source_fundamentals_sha256")):
        raise ValueError("ranking trial does not use identical immutable V1 inputs")
    selected = choices(snap)
    if (selected.get("v1") != (v1_receipt.get("challenger") or {}).get("candidate_symbol")):
        raise ValueError("ranking baseline differs from existing V1 quality gate")
    refs = receipt.get("arms")
    if not isinstance(refs, Mapping) or set(refs) != set(selected):
        raise ValueError("ranking study arm set mismatch")
    for arm, symbol in selected.items():
        row = refs[arm]
        if symbol is None:
            if row is not None:
                raise ValueError("research study manufactured a CASH event")
            continue
        if (not isinstance(row, Mapping) or row.get("candidate_symbol") != symbol
                or not isinstance(row.get("event"), Mapping)
                or not row["event"].get("event_id") or not row["event"].get("event_sha256")):
            raise ValueError("ranking candidate missing frozen experience proof")
    body = dict(receipt)
    claimed = body.pop("study_sha256", None)
    if claimed != contracts.payload_sha256(body):
        raise ValueError("ranking study receipt hash mismatch")


def freeze(
    opportunity: Mapping[str, Any], evidence: Mapping[str, Any], *,
    config: Mapping[str, Any], v1_root: Path = V1_ROOT,
    root: Path = STUDY_ROOT, event_root: Path = EVENT_ROOT,
    frozen_at: datetime | None = None,
) -> dict[str, Any]:
    validate_protocol(config)
    decision_at = str(opportunity.get("generated_at") or "")
    decision = quality.utc(decision_at)
    if decision < quality.utc(config["experiment_not_before"]):
        return {"status": "NOT_STARTED"}
    now = frozen_at or datetime.now(timezone.utc)
    if decision > now or (now - decision).total_seconds() > 3600:
        return {"status": "LATE_OR_FUTURE_DECISION"}
    day = decision.date().isoformat()
    v1_path = v1_root / (day + ".json")
    if not v1_path.exists():
        return {"status": "WAITING_V1_PROSPECTIVE_FREEZE"}
    baseline = read(v1_path)
    v1.validate_receipt(baseline, read(V1_PROTOCOL_PATH))
    if (baseline.get("decision_at") != decision_at
            or baseline.get("source_opportunity_sha256") != opportunity.get("opportunity_sha256")
            or baseline.get("source_evidence_sha256") != evidence.get("evidence_sha256")):
        return {"status": "DIFFERENT_V1_SOURCE_NO_BACKFILL"}
    path = root / (day + ".json")
    if path.exists():
        recorded = read(path)
        validate_receipt(recorded, config, baseline)
        return {"status": "ALREADY_FROZEN", "study_sha256": recorded["study_sha256"]}
    snap = baseline["decision_input"]
    proposed = choices(snap)
    available = events.events_from_snapshots(opportunity, evidence, candidates_per_cycle=10)
    by_symbol = {str(e["symbol"]).upper(): e for e in available}
    refs: dict[str, Any] = {}
    for arm, symbol in proposed.items():
        if symbol is None:
            refs[arm] = None
            continue
        original = next((r for r in snap["evaluated_candidates"] if r.get("symbol") == symbol), None)
        if original is None:
            raise ValueError("ranking arm selected unobserved source candidate")
        key = str(original.get("market_data_symbol") or symbol).upper()
        event = by_symbol.get(key)
        if event is None:
            return {"status": "WAITING_FROZEN_EXPERIENCE", "symbol": symbol}
        ep = experience.event_path(event_root, event)
        if not ep.exists():
            return {"status": "WAITING_FROZEN_EXPERIENCE", "symbol": symbol}
        actual = read(ep)
        contracts.validate_experience_event(actual)
        if (contracts.event_without_runtime_fields(event)
                != contracts.event_without_runtime_fields(actual)
                or actual["source"]["payload_sha256"] != opportunity["opportunity_sha256"]
                or actual["recorded_at"] > contracts.iso_utc(now)):
            raise ValueError("ranking study event differs from immutable original")
        refs[arm] = {"candidate_symbol": symbol, "event": v1._event_receipt(actual)}
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA, "market": "US",
        "protocol_sha256": contracts.payload_sha256(config),
        "v1_trial_sha256": baseline["trial_sha256"],
        "quality_policy_sha256": v1.digest_file(Path(quality.__file__)),
        "ranking_code_sha256": v1.digest_file(Path(__file__)),
        "decision_at": decision_at, "session_date": day,
        "frozen_at": contracts.iso_utc(now),
        "source_opportunity_sha256": baseline["source_opportunity_sha256"],
        "source_evidence_sha256": baseline["source_evidence_sha256"],
        "source_fundamentals_sha256": baseline["source_fundamentals_sha256"],
        "decision_input": snap, "arms": refs,
        "governance": {
            "immutable": True, "no_lookahead": True,
            "production_decision_influence": False, "automatic_promotion": False,
        },
    }
    receipt["study_sha256"] = contracts.payload_sha256(receipt)
    validate_receipt(receipt, config, baseline)
    root.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        json.dump(receipt, file, ensure_ascii=False, sort_keys=True, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    return {
        "status": "FROZEN", "study_sha256": receipt["study_sha256"],
        "changed_arms": [arm for arm, row in refs.items()
                         if arm != "v1" and (row or {}).get("candidate_symbol")
                         != (refs["v1"] or {}).get("candidate_symbol")],
    }


def verify_root(root: Path, config: Mapping[str, Any], *, v1_root: Path = V1_ROOT) -> dict[str, Any]:
    count = 0
    for path in sorted(root.glob("*.json")):
        v1_record = read(v1_root / path.name)
        v1.validate_receipt(v1_record, read(V1_PROTOCOL_PATH))
        receipt = read(path)
        validate_receipt(receipt, config, v1_record)
        if receipt["session_date"] != path.stem:
            raise ValueError("ranking trial incorrectly named")
        count += 1
    return {"status": "OK", "frozen_sessions": count, "production_decision_influence": False}


def _validated_event(row: Mapping[str, Any] | None, receipt: Mapping[str, Any], event_root: Path) -> dict[str, Any] | None:
    if row is None:
        return None
    ref = row["event"]
    path = event_root / "us" / receipt["session_date"] / (ref["event_id"] + ".json")
    event = read(path)
    contracts.validate_experience_event(event)
    if (event.get("event_sha256") != ref["event_sha256"]
            or event.get("symbol") != ref["symbol"]
            or event.get("source", {}).get("payload_sha256") != receipt["source_opportunity_sha256"]
            or event.get("decision_at") != receipt["decision_at"]
            or event.get("recorded_at") > receipt["frozen_at"]):
        raise ValueError("ranking trial event source or freeze mismatch")
    context = ((event.get("candidate_state") or {}).get("decision_context") or {})
    original = context.get("opportunity_snapshot")
    if not isinstance(original, Mapping):
        raise ValueError("ranking trial original opportunity source missing")
    full = dict(original)
    fingerprint = full.pop("opportunity_sha256", None)
    if fingerprint != receipt["source_opportunity_sha256"] or contracts.payload_sha256(full) != fingerprint:
        raise ValueError("ranking trial source opportunity hash invalid")
    for key in ("market", "generated_at", "cash", "evaluated_candidates"):
        if original.get(key) != receipt["decision_input"].get(key):
            raise ValueError("ranking trial source opportunity snapshot mismatch")
    evidence = (original.get("decision_context") or {}).get("evidence_snapshot") or {}
    if (original.get("source_evidence_sha256") != receipt["source_evidence_sha256"]
            or evidence.get("source_fundamentals_sha256") != receipt["source_fundamentals_sha256"]):
        raise ValueError("ranking trial original evidence chain broken")
    return event


def net_return(
    row: Mapping[str, Any] | None, receipt: Mapping[str, Any], horizon: int, *,
    event_root: Path, outcome_root: Path,
) -> tuple[str, float | None]:
    event = _validated_event(row, receipt, event_root)
    if event is None:
        return "SETTLED", 0.0
    event_id = event["event_id"]
    outcome_id = "stoutv2-" + contracts.payload_sha256({
        "event_id": event_id, "horizon": horizon, "replay_version": REPLAY_VERSION,
    })[:24]
    path = outcome_root / "us" / receipt["session_date"] / (outcome_id + ".json")
    if not path.exists():
        return "PENDING", None
    payload = read(path)
    outcomes.validate_outcome(payload)
    outcomes.validate_source_binding(payload, event)
    if (int(payload["horizon_sessions"]) != horizon
            or payload["settled_at"] <= receipt["frozen_at"]):
        raise ValueError("ranking trial outcome horizon or time invalid")
    replay = payload["replay"]
    if replay.get("status") == "UNPLAYABLE":
        return "UNPLAYABLE", None
    if replay.get("status") == "NOT_ACTIVATED":
        return "SETTLED", 0.0
    value = quality.finite(replay.get("net_return_percent"))
    if value is None or not -200 <= value <= 200:
        raise ValueError("ranking trial net return invalid")
    return "SETTLED", value


def _block_bootstrap_lower(values: list[float], *, alpha: float, n: int,
                           block_length: int, seed: int) -> float:
    if not values:
        raise ValueError("no prospective returns")
    rng = random.Random(seed)
    count = len(values)
    results = []
    for _ in range(n):
        sampled: list[float] = []
        while len(sampled) < count:
            start = rng.randrange(count)
            sampled.extend(values[(start + k) % count] for k in range(block_length))
        results.append(sum(sampled[:count]) / count)
    results.sort()
    return results[max(0, math.ceil(alpha * n) - 1)]


def evaluate(*, config: Mapping[str, Any], root: Path = STUDY_ROOT,
             v1_root: Path = V1_ROOT, event_root: Path = EVENT_ROOT,
             outcome_root: Path = OUTCOME_ROOT,
             replay_config_path: Path = REPLAY_CONFIG_PATH) -> dict[str, Any]:
    """Only the FIRST fixed 60 sessions; no rolling re-tests or winner selection."""
    validate_protocol(config)
    replay_cfg = read(replay_config_path)
    if (replay_cfg.get("replay") or {}).get("version") != REPLAY_VERSION:
        raise ValueError("ranking trial cannot silently change replay-cost methodology")
    files = sorted(root.glob("*.json")) if root.exists() else []
    n = int(config["fixed_n"])
    initial = {
        "schema_version": "stock-trading-v2-fundamental-ranking-result-v1",
        "production_decision_influence": False,
        "automatic_promotion": False,
        "eligible_for_production": False,
        "evidence_type": "PROSPECTIVE_VIRTUAL_OPEN_SLOT_STRESSED_NET_NOT_LIVE_FILLS",
        "weights_percent": list(ARMS),
        "frozen_sessions": len(files),
        "fixed_n": n,
    }
    if len(files) < n:
        return {**initial, "status": "COLLECTING"}
    series: dict[int, dict[int, list[float]]] = {arm: {1: [], 2: []} for arm in ARMS}
    changed: dict[int, int] = {arm: 0 for arm in ARMS}
    positives: dict[int, dict[int, int]] = {arm: {1: 0, 2: 0} for arm in ARMS}
    symbols: dict[int, set[str]] = {arm: set() for arm in ARMS}
    sessions: list[str] = []
    samples: list[str] = []
    for path in files[:n]:
        receipt = read(path)
        v1_receipt = read(v1_root / path.name)
        v1.validate_receipt(v1_receipt, read(V1_PROTOCOL_PATH))
        validate_receipt(receipt, config, v1_receipt)
        if path.stem != receipt["session_date"]:
            raise ValueError("ranking trial session file mismatch")
        sessions.append(receipt["session_date"])
        samples.append(receipt["study_sha256"])
        returns: dict[str, dict[int, float]] = {}
        for arm, row in receipt["arms"].items():
            returns[arm] = {}
            for horizon in (1, 2):
                status, net = net_return(row, receipt, horizon, event_root=event_root, outcome_root=outcome_root)
                if status == "PENDING":
                    return {**initial, "status": "AWAITING_OUTCOMES",
                            "session_date": receipt["session_date"], "horizon": horizon}
                if status == "UNPLAYABLE":
                    return {**initial, "status": "FAILED_DATA",
                            "session_date": receipt["session_date"], "horizon": horizon}
                returns[arm][horizon] = float(net)
        baseline_symbol = (receipt["arms"]["v1"] or {}).get("candidate_symbol")
        for arm in ARMS:
            name = "w" + str(arm)
            arm_symbol = (receipt["arms"][name] or {}).get("candidate_symbol")
            different = arm_symbol != baseline_symbol
            if different:
                changed[arm] += 1
                symbols[arm].update(s for s in (arm_symbol, baseline_symbol) if s)
            for horizon in (1, 2):
                delta = returns[name][horizon] - returns["v1"][horizon]
                series[arm][horizon].append(delta)
                if different and delta > 0:
                    positives[arm][horizon] += 1
    span = (date.fromisoformat(sessions[-1]) - date.fromisoformat(sessions[0])).days
    results = []
    corrected_alpha = float(config["familywise_alpha"]) / (len(ARMS) * 2)
    for arm in ARMS:
        arm_rows = []
        count = changed[arm]
        for horizon in (1, 2):
            diffs = series[arm][horizon]
            mean = sum(diffs) / n
            positive = [v for v in diffs if v > 0]
            lower = _block_bootstrap_lower(
                diffs, alpha=corrected_alpha, n=int(config["bootstrap_resamples"]),
                block_length=int(config["block_length_sessions"]),
                seed=int(config["bootstrap_seed"]) + 10 * arm + horizon,
            )
            positive_rate = positives[arm][horizon] / count if count else 0.0
            concentration = max(positive) / sum(positive) if positive else 1.0
            accepted = (
                count >= int(config["min_changed_sessions"])
                and len(symbols[arm]) >= int(config["min_unique_symbols"])
                and span >= int(config["min_span_days"])
                and mean >= float(config["min_mean_uplift_pct_points"])
                and lower > 0
                and positive_rate >= float(config["min_positive_changed_fraction"])
                and concentration <= float(config["max_single_positive_share"])
            )
            arm_rows.append({
                "horizon_sessions": horizon, "mean_incremental_net_pct_points": round(mean, 6),
                "block_bootstrap_lower_familywise_corrected": round(lower, 6),
                "positive_changed_fraction": round(positive_rate, 6),
                "largest_positive_contribution_share": round(concentration, 6),
                "research_pass": accepted,
            })
        results.append({
            "ranking_fundamentals_weight_percent": arm,
            "changed_sessions": count, "unique_changed_symbols": len(symbols[arm]),
            "research_candidate_pass": all(h["research_pass"] for h in arm_rows),
            "horizons": arm_rows,
        })
    result = {
        **initial, "status": "RESEARCH_CANDIDATE" if any(r["research_candidate_pass"] for r in results)
        else "NO_PROVEN_RANKING_ADVANTAGE",
        "start_session": sessions[0], "end_session": sessions[-1],
        "span_days": span, "familywise_alpha": config["familywise_alpha"],
        "corrected_per_test_alpha": corrected_alpha,
        "session_receipt_hashes": samples, "arms": results,
        "next_step": "separately_governed_independent_holdout_and_manual_policy_review_not_auto_promotion",
    }
    result["result_sha256"] = contracts.payload_sha256(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--opportunity", type=Path, default=v1.OPPORTUNITY)
    parser.add_argument("--evidence", type=Path, default=v1.EVIDENCE)
    parser.add_argument("--root", type=Path, default=STUDY_ROOT)
    parser.add_argument("--v1-root", type=Path, default=V1_ROOT)
    parser.add_argument("--event-root", type=Path, default=EVENT_ROOT)
    parser.add_argument("--outcome-root", type=Path, default=OUTCOME_ROOT)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if sum((args.freeze, args.verify, args.evaluate)) != 1:
        parser.error("select exactly one of --freeze, --verify, --evaluate")
    config = read(args.config)
    if args.freeze:
        result = freeze(read(args.opportunity), read(args.evidence), config=config,
                        v1_root=args.v1_root, root=args.root, event_root=args.event_root)
    elif args.verify:
        result = verify_root(args.root, config, v1_root=args.v1_root)
    else:
        result = evaluate(config=config, root=args.root, v1_root=args.v1_root,
                          event_root=args.event_root, outcome_root=args.outcome_root)
    if args.output:
        if not args.evaluate:
            parser.error("--output is only for research evaluation")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

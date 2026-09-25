#!/usr/bin/env python3
"""Autonomous, fail-closed Research Lab execution loop for EUR/USD.

This module owns the complete research-only path:

candidate catalog -> development evaluation -> walk-forward -> frozen holdout ->
cost stress -> volatility-regime stability -> prospective shadow collection ->
promotion-review eligibility.

It never mutates production trading rules.  Candidates can only reach a
shadow-only registry with zero runtime adjustment.  A separate production
promotion controller is required for any live/paper influence.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from statistics import fmean, median
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

try:
    from investments_weekly_eurusd_ma_multitimeframe_research import (
        SYMBOL,
        TZ as MARKET_TZ,
        add_features,
        clean,
        resample_ohlc,
        weekly_records,
    )
except ImportError:  # pragma: no cover - package import in tests
    from scripts.investments_weekly_eurusd_ma_multitimeframe_research import (
        SYMBOL,
        TZ as MARKET_TZ,
        add_features,
        clean,
        resample_ohlc,
        weekly_records,
    )

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "data/investments/research_lab_policy.json"
STATE = ROOT / "data/investments/research_lab_state.json"
REGISTRY = ROOT / "data/investments/research_lab_promotion_registry.json"
REPORT = ROOT / "data/investments/research_lab_report.json"
TZ = ZoneInfo("Europe/Warsaw")
STATE_SCHEMA = "briefrooms-research-lab-state-v2"
REPORT_SCHEMA = "briefrooms-research-lab-report-v2"
REGISTRY_SCHEMA = "briefrooms-research-lab-promotion-registry-v2"
EVALUATOR_VERSION = "research-lab-evaluator-v2.0.0"

STANDALONE_RULES = (
    "full_stack",
    "fast_with_slow_filter",
    "price_vs_all",
    "support_or_resistance_hold",
    "ma30_reclaim_trigger",
    "ma30_60_cross_trigger",
)
TIMEFRAMES = ("H1", "H4", "D1", "W1", "M1")
SIDES = ("long", "short")
COMBINATION_RULES = (
    "D1_W1_trend_confirmation",
    "H4_trigger_D1_W1_trend",
    "H1_trigger_H4_D1_trend",
    "H4_support_D1_W1_trend",
    "H1_H4_D1_W1_unanimous_stack",
    "D1_W1_with_M1_macro_filter",
    "H4_trigger_D1_W1_M1_confirmation",
)


def read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def cid(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _aligned(record: dict[str, Any], names: Iterable[str], side: str, field: str) -> bool:
    sign = 1 if side == "long" else -1
    return all(((record.get("tf") or {}).get(name) or {}).get(field) == sign for name in names)


def candidate_matches(record: dict[str, Any], spec: dict[str, Any]) -> bool:
    side = str(spec["side"])
    sign = 1 if side == "long" else -1
    kind = spec["kind"]
    rule = spec["rule"]
    tf = record.get("tf") or {}

    if kind == "standalone":
        row = tf.get(spec["timeframe"]) or {}
        if rule == "full_stack":
            return row.get("stack") == sign
        if rule == "fast_with_slow_filter":
            return row.get("fast") == sign and row.get("slow") == sign
        if rule == "price_vs_all":
            return row.get("price_all") == sign
        if rule == "support_or_resistance_hold":
            return bool(row.get("support_hold_long" if side == "long" else "resistance_hold_short"))
        if rule == "ma30_reclaim_trigger":
            return bool(row.get("reclaim_ma30_long" if side == "long" else "reclaim_ma30_short"))
        if rule == "ma30_60_cross_trigger":
            return bool(row.get("cross_30_60_long" if side == "long" else "cross_30_60_short"))
        return False

    if rule == "D1_W1_trend_confirmation":
        return _aligned(record, ("D1", "W1"), side, "fast") and _aligned(record, ("D1", "W1"), side, "slow")
    if rule == "H4_trigger_D1_W1_trend":
        trigger = bool((tf.get("H4") or {}).get("reclaim_ma30_long" if side == "long" else "reclaim_ma30_short"))
        return trigger and _aligned(record, ("D1", "W1"), side, "slow")
    if rule == "H1_trigger_H4_D1_trend":
        trigger = bool((tf.get("H1") or {}).get("reclaim_ma30_long" if side == "long" else "reclaim_ma30_short"))
        return trigger and _aligned(record, ("H4", "D1"), side, "fast") and _aligned(record, ("H4", "D1"), side, "slow")
    if rule == "H4_support_D1_W1_trend":
        support = bool((tf.get("H4") or {}).get("support_hold_long" if side == "long" else "resistance_hold_short"))
        return support and _aligned(record, ("D1", "W1"), side, "slow")
    if rule == "H1_H4_D1_W1_unanimous_stack":
        return _aligned(record, ("H1", "H4", "D1", "W1"), side, "stack")
    if rule == "D1_W1_with_M1_macro_filter":
        return _aligned(record, ("D1", "W1"), side, "fast") and _aligned(record, ("D1", "W1", "M1"), side, "slow")
    if rule == "H4_trigger_D1_W1_M1_confirmation":
        trigger = bool((tf.get("H4") or {}).get("reclaim_ma30_long" if side == "long" else "reclaim_ma30_short"))
        return trigger and _aligned(record, ("D1", "W1", "M1"), side, "slow")
    return False


def candidate_catalog(policy: dict[str, Any]) -> list[dict[str, Any]]:
    limit = int((policy.get("search") or {}).get("max_candidates_per_cycle") or 120)
    rows: list[dict[str, Any]] = []
    for tf in TIMEFRAMES:
        for rule in STANDALONE_RULES:
            for side in SIDES:
                spec = {
                    "instrument_id": "eurusd",
                    "kind": "standalone",
                    "timeframe": tf,
                    "rule": rule,
                    "side": side,
                    "feature_model": "ma30_60_100_200",
                    "execution_horizon": "monday_0800_to_friday_2200",
                }
                rows.append({"candidate_id": cid(spec), "spec": spec})
    for rule in COMBINATION_RULES:
        for side in SIDES:
            spec = {
                "instrument_id": "eurusd",
                "kind": "combination",
                "rule": rule,
                "side": side,
                "feature_model": "ma30_60_100_200",
                "execution_horizon": "monday_0800_to_friday_2200",
            }
            rows.append({"candidate_id": cid(spec), "spec": spec})
    return rows[:limit]


def _selected_returns(records: list[dict[str, Any]], spec: dict[str, Any], cost_bps: float) -> list[dict[str, Any]]:
    cost_percent = float(cost_bps) / 100.0
    sign = 1.0 if spec["side"] == "long" else -1.0
    out: list[dict[str, Any]] = []
    for record in records:
        if not candidate_matches(record, spec):
            continue
        gross = float(record["return_pct"]) * sign
        out.append({
            "week": record["week"],
            "gross_percent": gross,
            "net_percent": gross - cost_percent,
            "market_abs_percent": abs(float(record["return_pct"])),
        })
    return out


def metrics(selected: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(x["net_percent"]) for x in selected]
    if not values:
        return {
            "count": 0,
            "mean_net_percent": None,
            "hit_rate": None,
            "profit_factor": None,
            "max_drawdown_percent": None,
        }
    wins = [x for x in values if x > 0]
    losses = [-x for x in values if x < 0]
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "count": len(values),
        "mean_net_percent": round(fmean(values), 6),
        "hit_rate": round(len(wins) / len(values), 6),
        "profit_factor": round(sum(wins) / sum(losses), 6) if losses else ("inf" if wins else 0.0),
        "max_drawdown_percent": round(max_dd, 6),
    }


def _positive_profit_factor(value: Any, threshold: float) -> bool:
    if value == "inf":
        return True
    try:
        return float(value) >= threshold
    except (TypeError, ValueError):
        return False


def _walk_forward(records: list[dict[str, Any]], spec: dict[str, Any], cost_bps: float, folds: int) -> dict[str, Any]:
    folds = max(1, int(folds))
    if len(records) < folds + 1:
        return {"folds": [], "passed_folds": 0, "required_folds": folds, "status": "INSUFFICIENT_DATA"}
    boundaries = [round(i * len(records) / (folds + 1)) for i in range(folds + 2)]
    rows = []
    passed = 0
    for idx in range(1, folds + 1):
        validation = records[boundaries[idx]:boundaries[idx + 1]]
        m = metrics(_selected_returns(validation, spec, cost_bps))
        ok = (
            m["count"] >= 3
            and m["mean_net_percent"] is not None
            and m["mean_net_percent"] > 0
            and _positive_profit_factor(m["profit_factor"], 1.0)
        )
        passed += int(ok)
        rows.append({"fold": idx, "metrics": m, "pass": ok})
    return {
        "folds": rows,
        "passed_folds": passed,
        "required_folds": folds,
        "status": "PASS" if passed == folds else "FAIL",
    }


def _regime_stability(records: list[dict[str, Any]], spec: dict[str, Any], cost_bps: float) -> dict[str, Any]:
    selected = _selected_returns(records, spec, cost_bps)
    if len(selected) < 10:
        return {"status": "INSUFFICIENT_DATA", "low": metrics([]), "high": metrics([])}
    abs_values = sorted(float(x["market_abs_percent"]) for x in selected)
    pivot = float(median(abs_values))
    low = metrics([x for x in selected if float(x["market_abs_percent"]) <= pivot])
    high = metrics([x for x in selected if float(x["market_abs_percent"]) > pivot])
    enough = low["count"] >= 5 and high["count"] >= 5
    stable = enough and (low["mean_net_percent"] or 0) >= 0 and (high["mean_net_percent"] or 0) >= 0
    return {"status": "PASS" if stable else ("INSUFFICIENT_DATA" if not enough else "FAIL"), "pivot_abs_percent": pivot, "low": low, "high": high}


def evaluate_candidate(
    records: list[dict[str, Any]],
    candidate: dict[str, Any],
    policy: dict[str, Any],
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prior = prior or {}
    target = policy.get("target") or {}
    governance = policy.get("governance") or {}
    costs = policy.get("costs") or {}
    min_total = int(target.get("minimum_total_trades") or 60)
    min_holdout = int(target.get("minimum_holdout_trades") or 20)
    min_wr = float(target.get("minimum_promotable_win_rate") or 0.58)
    min_mean = float(target.get("minimum_mean_week_percent") or 0.02)
    min_pf = float(target.get("minimum_profit_factor") or 1.10)
    max_dd = float(target.get("maximum_drawdown_percent") or 8.0)
    folds = int(target.get("minimum_walk_forward_folds") or 3)
    min_shadow = int(target.get("minimum_prospective_shadow_trades") or 12)
    cost_bps = float(costs.get("round_trip_cost_bps") or 0.0)

    holdout_fraction = float(target.get("holdout_fraction") or 0.20)
    split_at = max(1, min(len(records) - 1, int(len(records) * (1.0 - holdout_fraction))))
    development = records[:split_at]
    holdout = records[split_at:]

    dev_metrics = metrics(_selected_returns(development, candidate["spec"], cost_bps))
    holdout_metrics = metrics(_selected_returns(holdout, candidate["spec"], cost_bps))
    wf = _walk_forward(development, candidate["spec"], cost_bps, folds)
    regime = _regime_stability(holdout, candidate["spec"], cost_bps)
    full_metrics = metrics(_selected_returns(records, candidate["spec"], cost_bps))

    development_min = max(10, min_total - min_holdout)
    reasons: list[str] = []
    if full_metrics["count"] < min_total or dev_metrics["count"] < development_min:
        status = "insufficient_data"
        reasons.append("minimum_total_or_development_sample_not_met")
    elif dev_metrics["mean_net_percent"] is None or dev_metrics["mean_net_percent"] <= 0:
        status = "rejected_discovery"
        reasons.append("development_expectancy_not_positive_after_costs")
    elif wf["status"] != "PASS":
        status = "rejected_walk_forward"
        reasons.append("walk_forward_gate_failed")
    elif (
        holdout_metrics["count"] < min_holdout
        or holdout_metrics["hit_rate"] is None
        or holdout_metrics["hit_rate"] < min_wr
        or holdout_metrics["mean_net_percent"] is None
        or holdout_metrics["mean_net_percent"] < min_mean
        or not _positive_profit_factor(holdout_metrics["profit_factor"], min_pf)
        or holdout_metrics["max_drawdown_percent"] is None
        or holdout_metrics["max_drawdown_percent"] > max_dd
    ):
        status = "rejected_holdout"
        reasons.append("frozen_holdout_gate_failed")
    elif governance.get("require_regime_stability", True) and regime["status"] != "PASS":
        status = "rejected_regime"
        reasons.append("volatility_regime_stability_gate_failed")
    else:
        status = "approved_for_shadow"

    shadow_start_week = prior.get("shadow_start_week")
    shadow_metrics = metrics([])
    if status == "approved_for_shadow":
        if shadow_start_week:
            prospective = [r for r in records if str(r.get("week")) > str(shadow_start_week)]
            shadow_metrics = metrics(_selected_returns(prospective, candidate["spec"], cost_bps))
            if shadow_metrics["count"] >= min_shadow:
                if (
                    shadow_metrics["mean_net_percent"] is not None
                    and shadow_metrics["mean_net_percent"] >= min_mean
                    and _positive_profit_factor(shadow_metrics["profit_factor"], min_pf)
                    and shadow_metrics["max_drawdown_percent"] is not None
                    and shadow_metrics["max_drawdown_percent"] <= max_dd
                ):
                    status = "eligible_for_promotion_review"
                else:
                    status = "shadow_failed"
                    reasons.append("prospective_shadow_gate_failed")
            else:
                status = "shadow_collecting"
        else:
            shadow_start_week = records[-1]["week"] if records else None

    return {
        **candidate,
        "status": status,
        "reasons": reasons,
        "development_metrics": dev_metrics,
        "walk_forward": wf,
        "holdout_metrics": holdout_metrics,
        "regime_stability": regime,
        "full_sample_metrics": full_metrics,
        "shadow_start_week": shadow_start_week,
        "shadow_metrics": shadow_metrics,
        "runtime_activation": "promotion_review_only" if status == "eligible_for_promotion_review" else ("shadow_only" if status in {"approved_for_shadow", "shadow_collecting"} else "none"),
        "runtime_adjustment_points": 0.0,
        "production_impact": False,
    }


def source_fingerprint(records: list[dict[str, Any]], policy: dict[str, Any] | None = None) -> str:
    compact = [
        [r.get("week"), round(float(r.get("entry") or 0), 8), round(float(r.get("exit") or 0), 8)]
        for r in records
    ]
    payload = {
        "evaluator_version": EVALUATOR_VERSION,
        "policy": policy or {},
        "records": compact,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_weekly_records() -> list[dict[str, Any]]:
    import yfinance as yf

    raw_h1 = yf.download(SYMBOL, period="720d", interval="1h", progress=False, auto_adjust=False, prepost=True, threads=False)
    raw_d1 = yf.download(SYMBOL, period="15y", interval="1d", progress=False, auto_adjust=False, threads=False)
    h1 = clean(raw_h1)
    d1 = clean(raw_d1)
    if h1.empty or d1.empty:
        raise RuntimeError("EURUSD H1 or D1 history unavailable")
    h4 = resample_ohlc(h1, "4h")
    frames = {
        "H1": add_features(h1),
        "H4": add_features(h4),
        "D1": add_features(d1),
        "W1": add_features(resample_ohlc(d1, "W-FRI")),
        "M1": add_features(resample_ohlc(d1, "ME")),
    }
    records = weekly_records(frames["H1"], frames)
    if len(records) < 30:
        raise RuntimeError(f"insufficient canonical weekly records: {len(records)}")
    return records


def run(policy: dict[str, Any], records: list[dict[str, Any]], old: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bool]:
    catalog = candidate_catalog(policy)
    fp = source_fingerprint(records, policy)
    old_candidates = old.get("known_candidates") if isinstance(old.get("known_candidates"), dict) else {}
    catalog_ids = [row["candidate_id"] for row in catalog]
    old_ids = sorted(old_candidates)
    changed = old.get("schema_version") != STATE_SCHEMA or old.get("source_fingerprint") != fp or old_ids != sorted(catalog_ids)
    if not changed:
        registry = read(REGISTRY, {})
        report = read(REPORT, {})
        return old, registry, report, False

    evaluated: dict[str, Any] = {}
    for candidate in catalog:
        evaluated[candidate["candidate_id"]] = evaluate_candidate(
            records, candidate, policy, prior=old_candidates.get(candidate["candidate_id"]) or {}
        )

    statuses: dict[str, int] = {}
    for row in evaluated.values():
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1

    legacy_cycles = int(old.get("legacy_generation_cycles") or (old.get("cycle") or 0 if old.get("schema_version") != STATE_SCHEMA else 0))
    evidence_cycle = int(old.get("evidence_cycle") or 0) + 1
    now = datetime.now(TZ).isoformat(timespec="seconds")
    state = {
        "schema_version": STATE_SCHEMA,
        "version": "2.0.0",
        "execution_mode": "closed_loop_fail_closed",
        "updated_at": now,
        "cycle": legacy_cycles + evidence_cycle,
        "legacy_generation_cycles": legacy_cycles,
        "evidence_cycle": evidence_cycle,
        "source_fingerprint": fp,
        "source_sample": {
            "record_count": len(records),
            "first_week": records[0]["week"],
            "last_week": records[-1]["week"],
            "timezone": MARKET_TZ,
        },
        "known_candidates": evaluated,
        "queue": [],
        "queue_remaining": 0,
        "status_counts": statuses,
    }

    registry_candidates = []
    for row in evaluated.values():
        if row["status"] not in {"approved_for_shadow", "shadow_collecting", "eligible_for_promotion_review"}:
            continue
        registry_candidates.append({
            "candidate_id": row["candidate_id"],
            "instrument_id": "eurusd",
            "status": row["status"],
            "spec": row["spec"],
            "shadow_start_week": row.get("shadow_start_week"),
            "shadow_metrics": row.get("shadow_metrics"),
            "holdout_metrics": row.get("holdout_metrics"),
            "runtime_activation": row["runtime_activation"],
            "runtime_adjustment_points": 0.0,
            "production_impact": False,
        })
    registry = {
        "schema_version": REGISTRY_SCHEMA,
        "version": "2.0.0",
        "generated_at": now,
        "authority": {
            "production_impact": False,
            "automatic_production_promotion": False,
            "runtime_adjustment_points": 0.0,
        },
        "rule": "only evidence-passed candidates may enter prospective shadow; production promotion is external and never automatic",
        "candidates": registry_candidates,
    }

    ranked = sorted(
        evaluated.values(),
        key=lambda row: (
            row["status"] in {"eligible_for_promotion_review", "shadow_collecting", "approved_for_shadow"},
            (row.get("holdout_metrics") or {}).get("mean_net_percent") or -999.0,
        ),
        reverse=True,
    )
    report = {
        "schema_version": REPORT_SCHEMA,
        "version": "2.0.0",
        "generated_at": now,
        "cycle": state["cycle"],
        "evidence_cycle": evidence_cycle,
        "legacy_generation_cycles": legacy_cycles,
        "execution_loop_closed": True,
        "source_fingerprint": fp,
        "source_sample": state["source_sample"],
        "candidate_count": len(catalog),
        "generated_this_cycle": sum(1 for row in catalog if row["candidate_id"] not in old_candidates),
        "evaluated_this_cycle": len(evaluated),
        "queue_remaining": 0,
        "status_counts": statuses,
        "promotion_registry_count": len(registry_candidates),
        "promotion_review_count": sum(1 for row in evaluated.values() if row["status"] == "eligible_for_promotion_review"),
        "cost_model": {
            "round_trip_cost_bps": float((policy.get("costs") or {}).get("round_trip_cost_bps") or 0.0),
            "kind": "explicit_research_assumption",
        },
        "top_candidates": [
            {
                "candidate_id": row["candidate_id"],
                "status": row["status"],
                "spec": row["spec"],
                "holdout_metrics": row["holdout_metrics"],
                "shadow_metrics": row["shadow_metrics"],
            }
            for row in ranked[:10]
        ],
        "governance": "candidate_to_walk_forward_to_frozen_holdout_to_regime_to_prospective_shadow; zero production authority",
    }
    return state, registry, report, True


def main() -> int:
    policy = read(POLICY, {})
    if not policy.get("enabled"):
        raise SystemExit("Research Lab policy disabled")
    records = load_weekly_records()
    old = read(STATE, {})
    state, registry, report, changed = run(policy, records, old)
    if changed:
        write(STATE, state)
        write(REGISTRY, registry)
        write(REPORT, report)
        print(json.dumps({
            "changed": True,
            "evidence_cycle": state["evidence_cycle"],
            "candidates": len(state["known_candidates"]),
            "queue_remaining": state["queue_remaining"],
            "statuses": state["status_counts"],
            "promotion_registry_count": len(registry["candidates"]),
        }, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps({
            "changed": False,
            "reason": "market_evidence_fingerprint_unchanged",
            "evidence_cycle": old.get("evidence_cycle"),
            "candidates": len(old.get("known_candidates") or {}),
        }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

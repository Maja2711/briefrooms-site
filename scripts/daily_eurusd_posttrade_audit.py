#!/usr/bin/env python3
"""Read-only, event-driven + scheduled EURUSD Daily post-trade auditing.

Canonical closed positions are the only trigger universe. The result of a
trade NEVER trains or modifies a trading rule here. Every recorded close gets
a deterministic audit, even when market evidence / challenger studies are
missing; those gaps are labelled, not silently treated as zeros or wins.

Live observations require first_seen_at <= canonical close, candle closure
<= first_seen_at, and 180s maximum availability latency. Reconstructed,
late, post-close, duplicate or unknown-timestamp observations are NOT timely
evidence. Earlier first-seen records beat later copies. Archived UTC
two-hour chunks complement the bounded current journal.

Only output: data/investments/eurusd_posttrade_audits.json
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "eurusd-daily-posttrade-audit-v1"
OUTPUT = "data/investments/eurusd_posttrade_audits.json"
AUDIT_LIMIT = 250
PIP = 0.0001
LATENCY_LIMIT_SECONDS = 180
EXPECTED_COVERAGE = 0.95
MAX_HEALTH_GAP_MINUTES = 2
CHALLENGERS = ("MOMENTUM_5_15", "GIVEBACK_MOMENTUM", "CLOSE_TRAIL_035R")
VALID_EXIT_REASONS = (
    "STOP_LOSS", "TAKE_PROFIT", "TIME_EXIT", "TIME_EXIT_27H",
    "SOFT_HORIZON_EXIT", "DYNAMIC_PROFIT_EXIT", "DYNAMIC_RISK_EXIT",
    "DYNAMIC_EDGE_GIVEBACK_EXIT",
)


def parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        d = value
    elif isinstance(value, str) and value.strip():
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load(path: Path, fallback: dict | None = None) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else (fallback or {})
    except (OSError, ValueError):
        return fallback or {}


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def same_trade(trade: Mapping[str, Any]) -> bool:
    return (trade.get("instrument") == "EUR/USD"
            and bool(trade.get("trade_id"))
            and str(trade.get("direction")) in {"SHORT", "LONG"}
            and parse_time(trade.get("closed_at")) is not None)


def _first_of_two(old: Mapping[str, Any] | None,
                  current: Mapping[str, Any]) -> Mapping[str, Any]:
    """Keep the oldest PROVEN first-seen time, not the most optimistic flags."""
    if old is None:
        return current
    a = parse_time(old.get("first_seen_at"))
    b = parse_time(current.get("first_seen_at"))
    if a is None and b is not None:
        return current
    if b is None:
        return old
    if a is None or b < a:
        return current
    return old


def gather_evidence(journal: Mapping[str, Any], archive_dir: Path) -> tuple[list[dict], dict]:
    """The audit never invents missing minute captures from OHLC."""
    records: dict[tuple[str, str], dict] = {}
    archive_files = 0
    invalid_files = 0

    def register(row: Any) -> None:
        if not isinstance(row, Mapping):
            return
        key = str(row.get("trade_id") or ""), str(row.get("market_bar_at") or "")
        if not all(key):
            return
        records[key] = dict(_first_of_two(records.get(key), row))

    if archive_dir.exists():
        for file in sorted(archive_dir.glob("*/*.json")):
            data = load(file)
            if data.get("authority") != "SHADOW_OBSERVATION_ONLY":
                invalid_files += 1
                continue
            archive_files += 1
            for sample in data.get("snapshots") or []:
                register(sample)
    for sample in journal.get("snapshots") or []:
        register(sample)
    return list(records.values()), {
        "validated_archive_shards": archive_files,
        "invalid_archive_shards": invalid_files,
        "deduplicated_source_samples": len(records),
        "in_memory_rehydration_only": True,
    }


def _ceiled_next_minute(t: datetime) -> datetime:
    at_minute = t.replace(second=0, microsecond=0)
    return at_minute if at_minute == t else at_minute + timedelta(minutes=1)


def timeline_audit(trade: Mapping[str, Any], samples: Sequence[Mapping[str, Any]]) -> dict:
    opened = parse_time(trade.get("opened_at"))
    closed = parse_time(trade.get("closed_at"))
    if opened is None or closed is None or closed < opened:
        return {
            "status": "INVALID_POSITION_TIMESTAMPS", "expected_full_minutes": None,
            "timely_minutes": 0, "missing_or_late_minutes": None,
            "timely_coverage_fraction": None, "gap_alert": True,
        }
    first_full = _ceiled_next_minute(opened)
    # Count only minutes whose close is no later than actual position close.
    end_bar = closed - timedelta(minutes=1)
    expected = max(0, int((end_bar-first_full).total_seconds() // 60) + 1)
    by_bar: dict[datetime, dict] = {}
    late = post_close = no_first_seen = invalid_clock = 0
    for row in samples:
        if row.get("trade_id") != trade.get("trade_id"):
            continue
        bar = parse_time(row.get("market_bar_at"))
        if bar is None or not first_full <= bar or bar+timedelta(minutes=1) > closed:
            continue
        seen = parse_time(row.get("first_seen_at"))
        if seen is None:
            no_first_seen += 1
            continue
        finish = bar + timedelta(minutes=1)
        if seen < finish:
            invalid_clock += 1
            continue
        if seen > closed:
            post_close += 1
            continue
        lag = (seen - finish).total_seconds()
        if lag > LATENCY_LIMIT_SECONDS:
            late += 1
            continue
        if row.get("timely_observation") is not True:
            # We refuse to promote earlier unverified/retrospective records.
            late += 1
            continue
        previous = by_bar.get(bar)
        if not previous or lag < previous["latency_seconds"]:
            by_bar[bar] = {
                "first_seen_at": iso(seen), "market_bar_at": iso(bar),
                "latency_seconds": round(lag, 1),
                "signals": sorted(k for k, v in (row.get("signals") or {}).items() if v is True),
                "current_indicative_pips": row.get("current_indicative_pips"),
            }
    times = sorted(by_bar)
    gaps = [
        int((times[i] - times[i-1]).total_seconds() // 60) - 1
        for i in range(1, len(times))
    ]
    missing = max(0, expected-len(times))
    coverage = round(len(times)/expected, 4) if expected > 0 else None
    first_gap = round((times[0]-first_full).total_seconds()/60, 1) if times else None
    last_full = end_bar.replace(second=0,microsecond=0)
    end_gap = round((last_full-times[-1]).total_seconds()/60, 1) if times else None
    if expected == 0:
        status = "INSUFFICIENT_POSITION_DURATION_FOR_1M_AUDIT"
    elif not times:
        status = "MISSING_TIMELY_LIVE_EVIDENCE"
    elif coverage is not None and coverage >= EXPECTED_COVERAGE and max(gaps or [0]) <= MAX_HEALTH_GAP_MINUTES and (end_gap is not None and end_gap <= MAX_HEALTH_GAP_MINUTES):
        status = "NEAR_COMPLETE_TIMELY_LIVE_EVIDENCE"
    else:
        status = "PARTIAL_TIMELY_LIVE_EVIDENCE"
    return {
        "status": status,
        "expected_full_minutes": expected,
        "timely_minutes": len(times),
        "missing_or_late_minutes": missing,
        "timely_coverage_fraction": coverage,
        "first_timely_bar_at": iso(times[0]) if times else None,
        "last_timely_bar_at": iso(times[-1]) if times else None,
        "gap_before_first_timely_minutes": first_gap,
        "gap_after_last_timely_minutes": end_gap,
        "max_internal_missing_minutes": max(gaps or [0]) if times else None,
        "number_of_internal_gaps": sum(g > 0 for g in gaps),
        "late_sample_count": late, "postclose_backfill_count": post_close,
        "samples_missing_first_seen": no_first_seen,
        "clock_invalid_count": invalid_clock,
        "signal_bearing_timely_minutes": sum(bool(x["signals"]) for x in by_bar.values()),
        "first_timely_signal_at": next((x["first_seen_at"] for x in (by_bar[t] for t in times) if x["signals"]), None),
        "representative_first_alerts": [
            dict(by_bar[t]) for t in times if by_bar[t]["signals"]
        ][:5],
        "gap_alert": status not in ("NEAR_COMPLETE_TIMELY_LIVE_EVIDENCE",
                                     "INSUFFICIENT_POSITION_DURATION_FOR_1M_AUDIT"),
        "denominator_warning": "WALL_CLOCK_1M_MINUTES; holiday/weekend gaps may require session-aware denominator",
        "does_not_reconstruct_live_signals_from_historical_prices": True,
        "timely_rule": "first_seen_at >= bar_close; <=180s; first_seen_at <= actual_closed_at",
    }


def pnl_and_exit(trade: Mapping[str, Any]) -> dict:
    direction = str(trade.get("direction") or "")
    entry, exit_price, stop, target = (
        float(trade.get(k) or 0) for k in ("entry","exit_price","stop","target")
    )
    recorded = trade.get("r_multiple")
    reason = str(trade.get("exit_reason") or "")
    risk_pips = abs(entry-stop)/PIP if entry > 0 and stop > 0 else None
    if direction not in {"LONG","SHORT"} or not exit_price or not entry or not risk_pips:
        return {"status":"INVALID_PRICE_OR_RISK_GEOMETRY","actual_r":recorded,
                "exit_reason":reason,"exit_reason_valid":reason in VALID_EXIT_REASONS,
                "reconciles_to_entry_stop_exit":False}
    signed = 1 if direction == "LONG" else -1
    result_pips = signed*(exit_price-entry)/PIP
    derived_r = result_pips/risk_pips
    diff = abs(derived_r-float(recorded)) if recorded is not None else None
    matches = diff is not None and diff <= 0.003
    dynamic = (trade.get("monitor") or {}).get("dynamic_exit") or {}
    metadata_status = (
        "OBSERVED_R_PACE_V1" if str(dynamic.get("policy")) == "R_PACE_V1" else
        "NO_RECORDED_R_PACE_DIAGNOSTICS"
    )
    exit_time = parse_time(trade.get("closed_at"))
    opened = parse_time(trade.get("opened_at"))
    age = (exit_time-opened).total_seconds()/60 if exit_time and opened else None
    return {
        "status":"CONSISTENT_WITH_RECORDED_R" if matches else "R_MULTIPLE_MISMATCH_OR_MISSING",
        "direction":direction, "entry_price":entry, "exit_price":exit_price,
        "stop_price":stop,"take_profit_price":target,
        "exit_reason":reason, "exit_reason_valid":reason in VALID_EXIT_REASONS,
        "exit_category": (
            "R_PACE_DYNAMIC" if reason.startswith("DYNAMIC_") or reason=="SOFT_HORIZON_EXIT"
            else "HARD_SL_TP" if reason in ("STOP_LOSS","TAKE_PROFIT")
            else "TIME_HORIZON" if reason.startswith("TIME_EXIT")
            else "OTHER_OR_UNKNOWN"
        ),
        "actual_pips":round(result_pips,3),
        "actual_r":float(recorded) if recorded is not None else None,
        "derived_r":round(derived_r,5),
        "risk_pips":round(risk_pips,3),
        "r_reconciliation_difference":round(diff,6) if diff is not None else None,
        "reconciles_to_entry_stop_exit":matches,
        "position_age_minutes":round(age,2) if age is not None else None,
        "maximum_favorable_pips_retrospective":trade.get("mfe_pips"),
        "maximum_adverse_pips_retrospective":trade.get("mae_pips"),
        "recorded_dynamic_exit": {
            "status":metadata_status,
            "policy":dynamic.get("policy"),
            "current_r_at_close":dynamic.get("current_r"),
            "mfe_r_at_close":dynamic.get("mfe_r"),
            "giveback_r_at_close":dynamic.get("giveback_r"),
            "velocity_1h_rph":dynamic.get("velocity_1h_rph"),
            "velocity_3h_rph":dynamic.get("velocity_3h_rph"),
            "hold_score":dynamic.get("hold_score"),
            "tp_feasibility_ratio":dynamic.get("tp_feasibility_ratio"),
        },
        "pricing_provenance":{
            "paper_trading_only":(trade.get("execution_price_engine") or {}).get("paper_trading_only"),
            "entry_epe_verification":(trade.get("execution_price_engine") or {}).get("status"),
            "cross_feed_warning":(trade.get("execution_price_engine") or {}).get("cross_feed_warning"),
            "exit_price_basis":(trade.get("monitor") or {}).get("execution_price_basis"),
            "exit_is_executable_quote":False,
        },
    }


def challenger_audit(trade: Mapping[str, Any], compared: Mapping[str, Any] | None) -> dict:
    if not compared:
        return {
            "status":"PENDING_SHADOW_COMPARISON",
            "canonical_trade_matched":False, "results":{},
            "claimed_winner":None,
        }
    baseline = compared.get("baseline") or {}
    recorded_r = trade.get("r_multiple")
    matched = (
        compared.get("trade_id") == trade.get("trade_id")
        and baseline.get("actual_r") is not None and recorded_r is not None
        and abs(float(baseline["actual_r"])-float(recorded_r)) <= 0.003
        and str(baseline.get("exit_reason")) == str(trade.get("exit_reason"))
        and baseline.get("not_a_replay") is True
    )
    if not matched:
        return {
            "status":"BASELINE_MISMATCH",
            "canonical_trade_matched":False, "results":{},
            "claimed_winner":None,
        }
    rows = {}
    for strategy in CHALLENGERS:
        x = (compared.get("challengers") or {}).get(strategy) or {}
        state = str(x.get("status") or "MISSING_CHALLENGER_RESULT")
        earlier = state == "EARLIER_RESEARCH_EXIT"
        neutral = state == "NOT_TRIGGERED_BEFORE_BASELINE"
        proven_cost = x.get("epe_half_spread_recorded") is True
        delta = (
            round(float(x["vs_actual_r"]),6)
            if earlier and proven_cost and x.get("vs_actual_r") is not None
            and x.get("execution_proven") is False
            and x.get("signal_available_before_fill") is True else
            0.0 if neutral and proven_cost else None
        )
        label = (
            "RESEARCH_ONLY_EARLIER_EXIT" if earlier and delta is not None else
            "RESEARCH_NO_EARLIER_EXIT" if neutral and delta is not None else
            "NOT_COMPARABLE"
        )
        rows[strategy] = {
            "status":label, "simulation_status":state,
            "earlier_simulated_exit_at":x.get("simulated_exit_at") if earlier else None,
            "simulated_r":x.get("simulated_r") if earlier else None,
            "incremental_r_vs_canonical":delta,
            "spread_from_recorded_epe":proven_cost,
            "signal_from_historical_replay_not_live_proof":earlier,
            "execution_proven":False,
        }
    count = sum(x["incremental_r_vs_canonical"] is not None for x in rows.values())
    return {
        "status":"RESEARCH_COMPARISON_AVAILABLE" if count else "NO_VALID_COMPARABLE_CHALLENGERS",
        "canonical_trade_matched":True,
        "comparable_count":count,
        "results":rows,
        "claimed_winner":None,
        "no_auto_strategy_promotion":True,
        "selection_note":"One observed trade cannot establish superior protection policy",
    }


def audit_trade(trade: Mapping[str, Any], samples: Sequence[Mapping[str, Any]],
                compared: Mapping[str, Any] | None, review: Mapping[str, Any] | None,
                now: datetime) -> dict:
    result=pnl_and_exit(trade)
    telemetry=timeline_audit(trade,samples)
    challengers=challenger_audit(trade,compared)
    review_matches = bool(review and review.get("trade_id")==trade["trade_id"] and
                          review.get("closed_at")==trade.get("closed_at"))
    checks = []
    if not result.get("reconciles_to_entry_stop_exit"):
        checks.append("CANONICAL_PNL_R_GEOMETRY_MISMATCH")
    if not result.get("exit_reason_valid"):
        checks.append("UNKNOWN_EXIT_REASON")
    if result.get("exit_category")=="R_PACE_DYNAMIC" and result.get("recorded_dynamic_exit",{}).get("status")!="OBSERVED_R_PACE_V1":
        checks.append("MISSING_R_PACE_EXIT_DIAGNOSTICS")
    if telemetry.get("gap_alert"):
        checks.append("MISSING_OR_LATE_PRE_EXIT_LIVE_SIGNALS")
    if challengers["status"]=="PENDING_SHADOW_COMPARISON":
        checks.append("CHALLENGER_REPORT_PENDING")
    elif challengers["status"]=="BASELINE_MISMATCH":
        checks.append("CHALLENGER_BASELINE_MISMATCH")
    if not review_matches:
        checks.append("EXIT_INTELLIGENCE_REVIEW_PENDING_OR_STALE")
    recorded_at=parse_time(trade.get("closed_at"))
    if recorded_at is None:
        raise ValueError("closed trade without a valid closure timestamp")
    created = {
        "schema_version": SCHEMA,
        "trade_id": str(trade["trade_id"]),
        "instrument":"EUR/USD",
        "direction":trade["direction"],
        "opened_at":trade.get("opened_at"),
        "closed_at":trade["closed_at"],
        "canonical_exit":result,
        "live_telemetry":telemetry,
        "shadow_challengers":challengers,
        "existing_exit_review":{
            "status":"MATCHED_CANONICAL_CLOSE" if review_matches else "PENDING_OR_MISMATCHED",
            "reviewed_exit_reason":review.get("actual_exit_reason") if review_matches else None,
            "reviewed_actual_r":review.get("actual_r") if review_matches else None,
            "profit_snapshot_count":(review.get("data_audit") or {}).get("profit_snapshots_at_least_8p") if review_matches else None,
            "review_uses_hindsight_for_later_price_path":True,
        },
        "findings":checks,
        "overall_status":(
            "NEEDS_DATA_OR_REVIEW" if any(item in checks for item in
                  ("CHALLENGER_REPORT_PENDING","EXIT_INTELLIGENCE_REVIEW_PENDING_OR_STALE"))
            else "AUDITED_WITH_WARNINGS" if checks else "AUDITED"
        ),
        "automation":{
            "read_only":True,
            "source":"CANONICAL_EURUSD_DAILY_HISTORY",
            "trade_execution":False,
            "alter_r_pace_v1":False,
            "belief_core_mutation":False,
            "auto_promotion":False,
            "after_close_only":True,
            "audited_not_executed":True,
        },
    }
    return created


def build_report(previous: Mapping[str,Any], history: Mapping[str,Any],
                 journal: Mapping[str,Any], lab: Mapping[str,Any],
                 reviews: Mapping[str,Any], archived: Sequence[Mapping[str,Any]],
                 now: datetime) -> dict:
    source=list(history.get("trades") or [])
    trades=sorted((t for t in source if same_trade(t)),key=lambda t:t["closed_at"])
    recent=trades[-AUDIT_LIMIT:]
    # Prefer earliest true first-seen observation when hot journal/archive overlap.
    by: dict[tuple[str,str],dict] = {}
    for row in list(archived)+list(journal.get("snapshots") or []):
        if not isinstance(row,Mapping):continue
        key=(str(row.get("trade_id") or ""),str(row.get("market_bar_at") or ""))
        if not all(key):continue
        by[key]=dict(_first_of_two(by.get(key),row))
    samples=list(by.values())
    labs={str(x.get("trade_id")):x for x in lab.get("comparisons") or [] if isinstance(x,Mapping)}
    prior_reviews={str(x.get("trade_id")):x for x in reviews.get("reviews") or [] if isinstance(x,Mapping)}
    existing={str(x.get("trade_id")):x for x in previous.get("audits") or [] if isinstance(x,Mapping)}
    audits=[]
    changed=[]
    for trade in recent:
        tid=str(trade["trade_id"])
        item=audit_trade(trade,samples,labs.get(tid),prior_reviews.get(tid),now)
        prior=existing.get(tid)
        # Idempotence: newly scheduled runs MUST NOT repaint audit time unless
        # substantive canonical/research evidence actually changed.
        old_without={k:v for k,v in prior.items() if k!="audited_at"} if prior else {}
        if prior and old_without==item:
            item["audited_at"]=prior.get("audited_at",iso(now))
        else:
            item["audited_at"]=iso(now)
            changed.append(tid)
        audits.append(item)
    newest=audits[-1] if audits else None
    counters={
        "canonical_closed_trades_in_scope":len(recent),
        "audited_trades":len(audits),
        "audits_with_findings":sum(bool(x["findings"]) for x in audits),
        "missing_or_partial_live_evidence":sum(
            x["live_telemetry"]["status"] not in
            ("NEAR_COMPLETE_TIMELY_LIVE_EVIDENCE","INSUFFICIENT_POSITION_DURATION_FOR_1M_AUDIT")
            for x in audits),
        "waiting_for_challengers":sum(
            x["shadow_challengers"]["status"]=="PENDING_SHADOW_COMPARISON" for x in audits),
        "changed_audits_this_run":len(changed),
    }
    report={
        "schema_version":SCHEMA,
        "instrument":"EUR/USD",
        "engine":"EURUSD_DAILY",
        "mode":"READ_ONLY_POST_CLOSE_AUDIT",
        "authority":{
            "trade_execution":False,
            "canonical_history_write":False,
            "r_pace_v1_change":False,
            "belief_core_mutation":False,
            "automatic_promotion":False,
        },
        "controls":{
            "trigger_contract":"canonical_history_persisted_CLOSE_push_and_15min_scheduled_backstop",
            "snapshot_sources":"first_seen_1m_archive_plus_hot_journal",
            "no_backfill_claimed_as_live":True,
            "comparison":"three_shadow_challengers_vs_actual_canonical_R_PACE_or_lifecycle",
            "unknown_is_not_zero_or_pass":True,
            "outputs_affect_trading":False,
        },
        "summary":counters,
        "latest_trade_id":newest["trade_id"] if newest else None,
        "latest_trade_summary":{
            "closed_at":newest["closed_at"],
            "actual_r":newest["canonical_exit"].get("actual_r"),
            "actual_pips":newest["canonical_exit"].get("actual_pips"),
            "exit_reason":newest["canonical_exit"].get("exit_reason"),
            "telemetry_status":newest["live_telemetry"]["status"],
            "shadow_status":newest["shadow_challengers"]["status"],
            "audit_status":newest["overall_status"],
            "findings":newest["findings"],
        } if newest else None,
        "audits":audits,
    }
    report_comparable=dict(report)
    old_comparable=dict(previous)
    report_comparable.pop("updated_at",None)
    old_comparable.pop("updated_at",None)
    report["updated_at"]=(
        previous.get("updated_at",iso(now)) if report_comparable==old_comparable
        else iso(now)
    )
    return report


def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--history",default="data/investments/eurusd_daily_history.json")
    p.add_argument("--journal",default="data/investments/eurusd_exit_signal_journal.json")
    p.add_argument("--lab",default="data/investments/eurusd_profit_protection_lab.json")
    p.add_argument("--reviews",default="data/investments/eurusd_exit_intelligence_reviews.json")
    p.add_argument("--archive-dir",default="data/investments/eurusd_live_signal_archive")
    p.add_argument("--output",default=OUTPUT)
    p.add_argument("--now",default=None)
    args=p.parse_args()
    now=parse_time(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        p.error("invalid --now")
    history=load(Path(args.history))
    if not isinstance(history.get("trades"),list):
        print("EURUSD_POSTTRADE_AUDIT_FAIL_CLOSED_NO_HISTORY",file=sys.stderr)
        return 2
    journal=load(Path(args.journal))
    lab=load(Path(args.lab))
    reviews=load(Path(args.reviews))
    archived,_=gather_evidence({},Path(args.archive_dir))
    target=Path(args.output)
    result=build_report(load(target),history,journal,lab,reviews,archived,now)
    previous=load(target)
    if result != previous:
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    latest=result.get("latest_trade_summary") or {}
    print("EURUSD_POSTTRADE_AUDIT",json.dumps({
        "audited":result["summary"]["audited_trades"],
        "changed":result["summary"]["changed_audits_this_run"],
        "latest":result["latest_trade_id"],
        "latest_status":latest.get("audit_status"),
        "telemetry":latest.get("telemetry_status"),
        "challengers":latest.get("shadow_status"),
        "zero_trade_authority":not result["authority"]["trade_execution"],
    },sort_keys=True))
    if latest.get("telemetry_status") in ("MISSING_TIMELY_LIVE_EVIDENCE","PARTIAL_TIMELY_LIVE_EVIDENCE"):
        print("::warning::Latest EURUSD closed trade has incomplete live signal telemetry.")
    return 0


if __name__=="__main__":
    raise SystemExit(main())

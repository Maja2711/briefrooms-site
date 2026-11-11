#!/usr/bin/env python3
"""WES recursive blocker diagnosis (RSI): observation and prospective hypotheses only.

This learning stage has NO authority to set direction, release a risk gate,
alter orders, write weekly position state, or backfill unexecuted profits.
Time/event expiry lives in the separately governed WES/CB policy.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Warsaw")
INSTRUMENTS = ("eurusd", "sp500_futures", "btcusd")
WEEKLY = ROOT / "data/investments/weekly"
OUTPUT = ROOT / "data/investments/wes_blocker_learning.json"


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def timestamp(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return result.astimezone(timezone.utc) if result.tzinfo else None


def has_trade(item: Mapping[str, Any]) -> bool:
    if item.get("entry_price") is not None and item.get("entry_captured_at"):
        return True
    return any(isinstance(leg, dict) and leg.get("entry_price") is not None
               for leg in (item.get("position_legs") or []))


def historical_block_evidence(history: list[dict], iid: str) -> dict:
    """Blocked-candidate P/L is unknowable without separately validated replay."""
    blockers: dict[str, int] = {}
    executed = 0
    observed = 0
    for week in history:
        row = next((x for x in week.get("instruments", [])
                    if isinstance(x, dict) and x.get("instrument_id") == iid), None)
        if row is None:
            continue
        observed += 1
        if has_trade(row):
            executed += 1
            continue
        reason = str(row.get("no_trade_reason") or row.get("next_entry_status")
                     or "NO_TRADE_WITHOUT_RECORDED_CAUSE")
        blockers[reason] = blockers.get(reason, 0) + 1
    return {
        "observed_weeks": observed,
        "executed_weeks": executed,
        "no_trade_weeks": observed - executed,
        "historical_reason_counts": blockers,
        "verified_blocker_benefit_samples": 0,
        "blocker_benefit_status": "UNPROVEN_WITHOUT_POINT_IN_TIME_REJECTED_CANDIDATE_OUTCOMES",
    }


def _market_active(iid: str, now: datetime) -> bool:
    local = now.astimezone(TZ)
    if iid == "btcusd":
        return True
    return local.weekday() <= 4 and 8 <= local.hour < 23


def evaluate(
    week: Mapping[str, Any], prices: Mapping[str, Any], exposure: Mapping[str, Any],
    history: list[dict], *, now: datetime,
) -> dict:
    if now.tzinfo is None:
        raise ValueError("aware now required")
    now_utc = now.astimezone(timezone.utc)
    items = {str(x.get("instrument_id")): x for x in (week.get("instruments") or [])
             if isinstance(x, dict)}
    quotes = prices.get("prices") if isinstance(prices.get("prices"), dict) else {}
    states = exposure.get("instruments") if isinstance(exposure.get("instruments"), dict) else {}
    cb = exposure.get("eurusd_central_bank_event_risk") or {}
    results: dict[str, dict] = {}
    recovery = []
    no_trade_count = 0
    for iid in INSTRUMENTS:
        item = items.get(iid, {})
        evidence = historical_block_evidence(history, iid)
        quote = quotes.get(iid) if isinstance(quotes.get(iid), dict) else {}
        observed = timestamp(quote.get("current_price_updated_at") or quote.get("timestamp"))
        age = (now_utc - observed).total_seconds() if observed else None
        quote_fresh = bool(age is not None and -30 <= age <= 360
                           and quote.get("price") is not None)
        state = states.get(iid) if isinstance(states.get(iid), dict) else {}
        candidate = item.get("wes_trigger_candidate") if isinstance(item.get("wes_trigger_candidate"), dict) else {}
        required = item.get("wes_required_trigger") if isinstance(item.get("wes_required_trigger"), dict) else {}
        lock = item.get("reentry_lock") if isinstance(item.get("reentry_lock"), dict) else {}
        auth = item.get("wes_entry_authorization") if isinstance(item.get("wes_entry_authorization"), dict) else {}
        pending = item.get("pending_entry_decision") if isinstance(item.get("pending_entry_decision"), dict) else {}
        funnel = state.get("decision") if isinstance(state.get("decision"), dict) else {}
        prior_carried = state.get("next_entry_status") == "position_carried_from_prior_week"
        executed = has_trade(item) or prior_carried
        reasons: list[str] = []
        warnings: list[str] = []
        if not item:
            reasons.append("WES_WEEK_INSTRUMENT_MISSING")
            recovery.append(iid + ":WES_WEEK_INSTRUMENT_MISSING")
        if executed:
            reasons.append("POSITION_ALREADY_EXECUTED_OR_CARRIED")
        else:
            no_trade_count += 1
            if not candidate:
                reasons.append("WES_TRIGGER_EVIDENCE_MISSING")
            if candidate.get("direction") not in ("long", "short"):
                reasons.append("NO_QUALIFIED_DIRECTION")
            if candidate.get("directional_admission", {}).get("passed") is not True:
                reasons.append("DIRECTIONAL_ADMISSION_NOT_PASSED")
            if int(candidate.get("confirmations") or 0) < 2:
                reasons.append("FEWER_THAN_TWO_INDEPENDENT_CONFIRMATIONS")
            if required.get("allowed") is False:
                reasons.append("LEGACY_TIME_OF_WEEK_VETO_DETECTED")
                recovery.append(iid + ":LEGACY_TIME_OF_WEEK_VETO_DETECTED")
            if lock.get("active") is True:
                if lock.get("scope") == "wes_no_trade_monitoring":
                    reasons.append("MONITORING_LOCK_NOT_A_HARD_VETO")
                else:
                    reasons.append("GOVERNED_REENTRY_LOCK_REQUIRES_PROOF")
            if funnel.get("reason_codes"):
                reasons.append("V5_DECISION_REASONS_PRESENT")
            epe = item.get("execution_price_engine") if isinstance(item.get("execution_price_engine"), dict) else {}
            if epe.get("reason") in ("missing_canonical_live_target_contract", "historical_target_touch_not_live_executable_quote"):
                reasons.append("EXECUTION_EVIDENCE_MISSING_EPE_MUST_REMAIN_ENFORCED")
            if iid == "eurusd" and (cb.get("entry_guard_enforced") is True
                                    or state.get("next_entry_status") == "blocked_by_central_bank_event_risk"):
                reasons.append("EURUSD_CENTRAL_BANK_GUARD_PRESENT")
                diagnostics = cb.get("diagnostics") if isinstance(cb.get("diagnostics"), dict) else {}
                event_minutes = diagnostics.get("minutes_until")
                if isinstance(event_minutes, (int, float)) and event_minutes < -1440:
                    warnings.append("OBSOLETE_CB_EVENT_STILL_VETOING_CHECK_POLICY_AND_RECOVER")
                    recovery.append(iid + ":OBSOLETE_CB_EVENT_STILL_VETOING")
            if not quote_fresh and _market_active(iid, now):
                warnings.append("CANONICAL_LIVE_PRICE_STALE_OR_UNVERIFIED")
                if auth or pending:
                    recovery.append(iid + ":PENDING_ENTRY_LIVE_QUOTE_STALE")
        # Stale quotes cannot be used to justify any release or hypothetical fill.
        results[iid] = {
            "status": ("TRADE_OR_CARRY" if executed else "NO_TRADE_UNDER_REVIEW"),
            "executed_this_week_or_carried": executed,
            "quote_fresh": quote_fresh,
            "quote_age_seconds": round(age, 2) if age is not None else None,
            "candidate_direction": candidate.get("direction"),
            "candidate_raw_score": candidate.get("raw_score"),
            "confirmations": candidate.get("confirmations"),
            "entry_authorization_active": bool(auth),
            "pending_entry": bool(pending),
            "blocker_reasons": list(dict.fromkeys(reasons)),
            "warnings": list(dict.fromkeys(warnings)),
            "past": evidence,
            "learning": {
                "hypothesis": ("REVIEW_GOVERNED_ADMISSION_THRESHOLDS_IN_SHADOW"
                               if not executed else "SETTLE_ACTUAL_TRADE_OUTCOME"),
                "hypothesis_status": "RESEARCH_ONLY_NOT_PROMOTED",
                "hypothesis_reason": "NO_TRADE_IS_NOT_EVIDENCE_OF_FOREGONE_PROFIT",
                "automatic_unlock_allowed": False,
                "hard_safety_guards_immutable": ["DIRECTIONAL_ADMISSION", "EPE",
                                                 "NO_RETROACTIVE", "FROZEN_SL_TP",
                                                 "MATERIAL_EVENT_EXIT"],
                "release_rule": "ONLY_EXPIRED_PROVENANCE_BOUND_TEMPORAL_GUARDS_VIA_GOVERNED_POLICY",
            },
        }
    local = now.astimezone(TZ)
    return {
        "schema_version": "wes-recursive-blocker-review-v1",
        "module_ids": ["TR-05", "LE-08", "LE-09"],
        "mode": "OBSERVE_ONLY",
        "as_of": local.isoformat(timespec="seconds"),
        "week_id": week.get("week_id"),
        "weekly_no_trade_review_required": local.weekday() == 4 and no_trade_count > 0,
        "no_trade_instruments": no_trade_count,
        "status": "RECOVERY_REQUIRED" if recovery else ("NO_TRADE_REVIEWED" if no_trade_count else "ACTIVE_POSITIONS"),
        "recovery_reasons": list(dict.fromkeys(recovery)),
        "instruments": results,
        "no_trade_not_a_system_failure": True,
        "auto_trade_forbidden": True,
        "historical_counterfactual_benefit_verified": False,
    }


def run(*, now: datetime | None = None, root: Path = ROOT) -> dict:
    now = now or datetime.now(TZ)
    iso = now.astimezone(TZ).isocalendar()
    week_id = f"{iso.year}-W{iso.week:02d}"
    weekly_dir = root / "data/investments/weekly"
    week = load(weekly_dir / f"{week_id}.json")
    if not week:
        local = now.astimezone(TZ)
        inactive = local.weekday() >= 5 or local.hour < 8
        return {"schema_version": "wes-recursive-blocker-review-v1",
                "mode": "OBSERVE_ONLY", "as_of": local.isoformat(timespec="seconds"),
                "week_id": week_id,
                "status": "INACTIVE_NO_CURRENT_WEEK" if inactive else "RECOVERY_REQUIRED",
                "recovery_reasons": [] if inactive else ["current_week_state_missing"],
                "auto_trade_forbidden": True, "instruments": {}}
    previous = [load(p) for p in sorted(weekly_dir.glob("????-W??.json"))[-9:]
                if p.stem != week_id]
    return evaluate(
        week,
        load(root / "data/investments/live_prices.json"),
        load(root / "data/investments/multi_instrument_exposure_state_v5.json"),
        previous,
        now=now,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("diagnose", "observe"), default="diagnose")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = run()
    if args.mode == "observe":
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 2 if args.check and report["status"] == "RECOVERY_REQUIRED" else 0


if __name__ == "__main__":
    raise SystemExit(main())

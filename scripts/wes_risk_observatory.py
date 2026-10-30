#!/usr/bin/env python3
"""Prospective WES risk/entry observability. Read-only: never changes orders or positions."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEEKLY = ROOT / "data/investments/weekly"
PRICES = ROOT / "data/investments/live_prices.json"
OUTPUT = ROOT / "data/investments/wes_risk_observatory.json"
INSTRUMENTS = ("eurusd", "btcusd", "sp500_futures")
MAX_AGE_SECONDS = 360
GIVEBACK_ALERT_FRACTION = 0.5
MIN_PROFIT_R = 0.5


def read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def stamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (ValueError, TypeError):
        return None


def latest_week_path(directory):
    paths = sorted(Path(directory).glob("????-W??.json"))
    return paths[-1] if paths else None


def evaluate(week, prices, previous, now):
    """No backfilled peaks: new surveillance starts at first fresh post-entry quote."""
    rows = {str(r.get("instrument_id")): r for r in week.get("instruments", []) if isinstance(r, dict)}
    market = prices.get("prices", {}) if isinstance(prices.get("prices"), dict) else {}
    old = previous.get("instruments", {}) if isinstance(previous.get("instruments"), dict) else {}
    outcome = {"schema_version": "wes-risk-observatory-v1", "mode": "OBSERVE_ONLY",
               "checked_at": now.isoformat(), "week_id": week.get("week_id"),
               "instruments": {}, "alerts": []}
    for iid in INSTRUMENTS:
        row, quote = rows.get(iid, {}), market.get(iid, {})
        quote = quote if isinstance(quote, dict) else {}
        point = number(quote.get("price"))
        observed = stamp(quote.get("current_price_updated_at") or quote.get("timestamp"))
        age = (now - observed).total_seconds() if observed else None
        fresh = (point is not None and point > 0 and observed is not None
                 and age is not None and -30 <= age <= MAX_AGE_SECONDS)
        trade_open = (row.get("trade_status") == "open"
                      and row.get("direction") in ("long", "short")
                      and number(row.get("entry_price")) is not None
                      and row.get("exit_price") is None)
        result = {"trade_status": row.get("trade_status"), "quote_fresh": bool(fresh),
                  "quote_age_seconds": round(age, 1) if age is not None else None,
                  "quote_source": quote.get("source"), "state": "NO_OPEN_POSITION",
                  "warnings": []}
        candidate = row.get("wes_trigger_candidate") or {}
        epe = row.get("execution_price_engine") or {}
        if not trade_open:
            result["entry_funnel"] = {
                "candidate_direction": candidate.get("direction"),
                "candidate_raw": candidate.get("raw_score"),
                "confirmations": candidate.get("confirmations"),
                "admission_passed": (candidate.get("directional_admission") or {}).get("passed"),
                "delta_gate_passed": (candidate.get("delta_gate") or {}).get("passed"),
                "epe_status": epe.get("status"),
                "epe_reason": epe.get("reason"),
                "pending_authorization": bool(row.get("wes_entry_authorization")),
                "pending_decision": bool(row.get("pending_entry_decision")),
            }
            if epe.get("reason") == "missing_canonical_live_target_contract":
                result["warnings"].append("EPE_LIVE_TARGET_CONTRACT_MISSING")
        elif not fresh:
            result["state"] = "PRICE_UNVERIFIED"
            result["warnings"].append("OPEN_POSITION_QUOTE_STALE_OR_MISSING")
            # Keep verified peaks through outages without using stale prices as evidence.
            entry = number(row.get("entry_price"))
            identity = "|".join([str(week.get("week_id")), iid,
                                 str(row.get("entry_captured_at")), str(entry)])
            old_item = old.get(iid, {})
            if old_item.get("position_identity") == identity:
                result["position_identity"] = identity
                result["peak_favorable_R"] = old_item.get("peak_favorable_R")
        else:
            entry = number(row.get("entry_price"))
            opened = stamp(row.get("entry_captured_at"))
            stop = number((row.get("risk_plan") or {}).get("stop_loss_price"))
            risk = abs(entry - stop) if stop is not None else None
            if opened is None or observed <= opened or not risk or risk <= 0:
                result["state"] = "RISK_EVIDENCE_INCOMPLETE"
                result["warnings"].append("CANNOT_VALIDATE_PROFIT_PROTECTION")
            else:
                signed = (entry - point) if row["direction"] == "short" else (point - entry)
                current_r = signed / risk
                identity = "|".join([str(week.get("week_id")), iid,
                                     str(row.get("entry_captured_at")), str(entry)])
                old_item = old.get(iid, {})
                old_peak = number(old_item.get("peak_favorable_R")) if old_item.get("position_identity") == identity else None
                peak = max(current_r, old_peak) if old_peak is not None else current_r
                result.update(state="MONITORING", position_identity=identity,
                              price_observed_at=observed.isoformat(),
                              current_R=round(current_r, 4),
                              peak_favorable_R=round(peak, 4),
                              giveback_R=round(max(0, peak-current_r), 4))
                # Proposed protection is advisory, not an executable stop.
                # For short: lock half the observed peak with a BUY stop below entry.
                # For long: lock half the observed peak with a SELL stop above entry.
                if peak >= MIN_PROFIT_R:
                    protected_r = 0.5 * peak
                    suggested = entry + (protected_r * risk if row["direction"] == "long"
                                         else -protected_r * risk)
                    result["profit_protection_shadow"] = {
                        "mode": "PROPOSAL_ONLY",
                        "protected_R": round(protected_r, 4),
                        "suggested_stop_price": round(suggested, 8),
                        "basis": "prospective_fresh_snapshot_MFE",
                        "requires_execution_authority": True,
                        "broker_executable_quote_verified": False,
                    }
                if peak >= MIN_PROFIT_R and peak-current_r >= max(0.25, peak*GIVEBACK_ALERT_FRACTION):
                    result["warnings"].append("PROFIT_GIVEBACK_REVIEW_REQUIRED")
        outcome["instruments"][iid] = result
        for warning in result["warnings"]:
            outcome["alerts"].append({"instrument_id": iid, "code": warning})
    return outcome


def main():
    week_path = latest_week_path(WEEKLY)
    if not week_path:
        raise SystemExit("WES_OBSERVER_NO_WEEK")
    now = datetime.now(timezone.utc)
    result = evaluate(read(week_path), read(PRICES), read(OUTPUT), now)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"week": result["week_id"], "mode": result["mode"],
                      "alerts": result["alerts"]}, ensure_ascii=False))
    for alert in result["alerts"]:
        print(f"::warning title=WES {alert['instrument_id']}::{alert['code']}")


if __name__ == "__main__":
    main()

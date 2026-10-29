#!/usr/bin/env python3
"""Private EURUSD Weekly Saxo-SIM BID/ASK risk observation in isolated D1.

Only an existing canonical WES EURUSD position is observed. This worker
never opens/closes a WES trade, sends Saxo orders, or writes Saxo quotations
to public Git, logs or notifications. Its quote is indicative, not proof of
an executable broker fill. It stores private evidence in a separate D1 table;
the Daily D1 state table and all other instruments remain untouched.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from saxo_private_daily_d1_runner import D1, PrivateStateFailure
from saxo_private_feed import fetch_private_saxo_quote
from eurusd_weekly_cb_event_adapter import normalize_weekly_position
from eurusd_central_bank_wire import load_wire
from eurusd_central_bank_event_risk import assess_pre_event, load_policy, validate_policy

ROOT = Path(__file__).resolve().parents[1]
SAXO_MAX_AGE_SECONDS = 15.0
SCHEMA = "private-saxo-eurusd-weekly-cb-observation-v1"
TABLE = "private_saxo_eurusd_weekly_cb_observations"
MAX_AUDIT_BYTES = 100_000
CREATE_SQL = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
  event_id TEXT PRIMARY KEY,
  evaluated_at TEXT NOT NULL,
  position_id TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  payload TEXT NOT NULL
)"""
INSERT_SQL = f"""INSERT OR IGNORE INTO {TABLE}
  (event_id, evaluated_at, position_id, sha256, payload) VALUES (?, ?, ?, ?, ?)"""
SELECT_SQL = f"SELECT payload, sha256 FROM {TABLE} WHERE event_id = ?"


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def current_weekly_eurusd(root: Path, now: datetime) -> tuple[str, dict[str, Any] | None]:
    """Read ONLY the latest public WES EURUSD entry; no publication or mutation."""
    iso = now.astimezone(ZoneInfo("Europe/Warsaw")).isocalendar()
    name = f"{iso.year}-W{iso.week:02d}"
    target = root / "data" / "investments" / "weekly" / (name + ".json")
    try:
        state = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PrivateStateFailure("private_weekly_canonical_state_unavailable") from None
    if state.get("week_id") != name:
        raise PrivateStateFailure("private_weekly_week_identity_mismatch")
    rows = [row for row in state.get("instruments", ())
            if isinstance(row, Mapping) and row.get("instrument_id") == "eurusd"]
    if len(rows) != 1:
        raise PrivateStateFailure("private_weekly_eurusd_not_unique")
    item = rows[0]
    if item.get("trade_status") != "open":
        return name, None
    position = normalize_weekly_position(item, name)
    if not position or not position.get("position_id"):
        raise PrivateStateFailure("private_weekly_position_not_verifiable")
    return name, position


def private_assessment(
    *, root: Path, now: datetime, quote: Mapping[str, Any],
    week_id: str, position: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate current private BID/ASK; no execution/admission authority."""
    if not (os.environ.get("BRIEFROOMS_PRIVATE_RUNTIME") == "1"
            and os.environ.get("SAXO_PRIVATE_PRIMARY") == "1"):
        raise PrivateStateFailure("private_weekly_runtime_not_authorized")
    stamp = quote.get("observed_at")
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        raise PrivateStateFailure("private_weekly_quote_clock_unverified")
    age = (now - stamp.astimezone(timezone.utc)).total_seconds()
    try:
        bid = float(quote["bid"])
        ask = float(quote["ask"])
    except (KeyError, ValueError, TypeError):
        raise PrivateStateFailure("private_weekly_quote_unavailable") from None
    if (not 0 <= age <= SAXO_MAX_AGE_SECONDS or not all(math.isfinite(x) for x in (bid, ask))
            or not .8 < bid < ask < 1.5 or (ask - bid) > 0.0015):
        raise PrivateStateFailure("private_weekly_saxo_bid_ask_not_fresh")
    wire = load_wire(root / "data/investments/eurusd_central_bank_wire.json", now=now)
    policy = validate_policy(load_policy(root / "data/investments/eurusd_cb_risk_policy.json"))
    # For an indicative SIM quote, verified=False is intentional. The common
    # event assessor must NEVER label it an executable brokerage price.
    # Side-correct indicative economics are independently recorded below.
    risk_quote = {
        "bid": bid, "ask": ask, "mid": None,
        "observed_at": stamp.isoformat(), "source": "PRIVATE_SAXO_SIM_INDICATIVE",
        "verified": False, "liquidity_ok": False, "cost_pips": None,
    }
    assessment = assess_pre_event(
        position, risk_quote, wire, policy=policy, now=now, scope="EURUSD_WEEKLY"
    ).to_dict()
    direction = str(position.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        raise PrivateStateFailure("private_weekly_direction_invalid")
    entry = float(position["entry_price"])
    # A SHORT is bought back at ASK; a LONG is sold at BID. Cost after the
    # quoted spread remains unknown; gross P/L must not be labelled net P/L.
    close_side = "ASK" if direction == "SHORT" else "BID"
    mark = ask if direction == "SHORT" else bid
    gross_pips = (entry - mark if direction == "SHORT" else mark - entry) * 10000
    if not math.isfinite(gross_pips):
        raise PrivateStateFailure("private_weekly_economics_invalid")
    # Keep the full confidential quote, position/economics and risk report
    # *inside* private D1, never the public WES JSON or workflow logs.
    return {
        "schema": SCHEMA,
        "evaluated_at": now.isoformat(),
        "week_id": week_id, "position_id": str(position["position_id"]),
        "source": "SAXO_SIM_PRIVATE_INDICATIVE", "redistributable": False,
        "paper_only": True, "broker_order_sent": False,
        "canonical_weekly_state_mutated": False,
        "executable_broker_fill_confirmed": False,
        "execution_authority": False,
        "exit_intent_created": False,
        "quote": {
            "bid": bid, "ask": ask, "observed_at": stamp.isoformat(),
            "age_seconds": round(age, 3), "close_side": close_side,
            "spread_pips": round((ask - bid) * 10000, 3),
            "private_only": True,
        },
        "economics": {
            "gross_pips_at_indicative_close_side": round(gross_pips, 3),
            "estimated_net_pips": None,
            "broker_commissions_and_slippage_verified": False,
            "entry_price_from_frozen_public_paper_week": entry,
            "simulated_fill_executed": False,
        },
        "risk": assessment,
    }


def persist_private(d1: D1, report: Mapping[str, Any]) -> str:
    """Content-addressed immutable private D1 journal, idempotent on retry."""
    data = _canonical(report)
    if len(data.encode("utf-8")) > MAX_AUDIT_BYTES:
        raise PrivateStateFailure("private_weekly_evidence_too_large")
    digest = _digest(data)
    d1.query(CREATE_SQL)
    d1.query(INSERT_SQL, [
        digest, str(report["evaluated_at"]), str(report["position_id"]),
        digest, data,
    ])
    rows = d1.query(SELECT_SQL, [digest])["results"]
    if len(rows) != 1 or rows[0].get("payload") != data or rows[0].get("sha256") != digest:
        raise PrivateStateFailure("private_weekly_d1_readback_mismatch")
    return digest


def main() -> int:
    """Log states only; exception texts can contain secrets or licensed quotes."""
    if (os.environ.get("SAXO_PRIVATE_AUTO_ENABLED") != "true"
            or os.environ.get("BRIEFROOMS_PRIVATE_RUNTIME") != "1"
            or os.environ.get("SAXO_PRIVATE_PRIMARY") != "1"):
        print("PRIVATE_WEEKLY_SAXO=DISABLED")
        return 2
    if not os.environ.get("SAXO_PRIVATE_BASIC_PASSWORD"):
        print("PRIVATE_WEEKLY_SAXO=AUTH_UNAVAILABLE")
        return 2
    from daily_eurusd_cycle_watchdog import fx_market_active
    now = datetime.now(timezone.utc)
    if not fx_market_active(now):
        print("PRIVATE_WEEKLY_SAXO=MARKET_INACTIVE")
        return 0
    try:
        week_id, position = current_weekly_eurusd(ROOT, now)
        if position is None:
            print("PRIVATE_WEEKLY_SAXO=NO_OPEN_POSITION")
            return 0
        quote = fetch_private_saxo_quote(now=now)
        report = private_assessment(
            root=ROOT, now=now, quote=quote, week_id=week_id, position=position
        )
        persist_private(D1(), report)
        print("PRIVATE_WEEKLY_SAXO=PRIVATE_D1_RECORDED")
        print("PRIVATE_WEEKLY_SAXO_BID_ASK=FRESH_INDICATIVE")
        print("PRIVATE_WEEKLY_SAXO_ORDER_AUTHORITY=FALSE")
        return 0
    except Exception:
        print("PRIVATE_WEEKLY_SAXO=RECOVERY_REQUIRED_FAIL_CLOSED")
        return 1


if __name__ == "__main__":
    sys.exit(main())

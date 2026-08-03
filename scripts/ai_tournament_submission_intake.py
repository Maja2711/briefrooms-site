#!/usr/bin/env python3
"""Validate and lock one blind AI Tournament participant submission.

Usage:
  python scripts/ai_tournament_submission_intake.py response.json

A valid response is copied into the campaign intake area together with a
canonical SHA-256 commitment. Existing locked files are never overwritten with
content that differs from the original hash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_PATH = ROOT / "data" / "ai_tournament" / "intake_campaign.json"


class IntakeError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise IntakeError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise IntakeError("JSON root must be an object")
    return value


def finite_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise IntakeError(f"Expected a number, received {value!r}") from exc
    if not math.isfinite(number):
        raise IntakeError("Weights and confidence must be finite")
    return number


def participant_map(campaign: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        row["participant_id"]: row
        for row in campaign.get("manual_participants", [])
        if isinstance(row, dict) and row.get("participant_id")
    }


def validate_submission(submission: dict[str, Any], campaign: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "schema_version", "campaign_id", "participant_id", "participant_display_name",
        "decision_date", "strategy", "execution_policy", "same_allocations_for_pln_and_usd",
        "allocations", "cash_weight_pct", "portfolio_thesis", "expected_three_month_driver",
        "biggest_portfolio_risk", "expected_best_performer", "expected_highest_risk_position",
        "confidence_pct", "final_decision_locked",
    }
    unknown = sorted(set(submission) - allowed_keys)
    if unknown:
        raise IntakeError(f"Unknown top-level fields: {unknown}")

    required = allowed_keys
    missing = sorted(field for field in required if field not in submission)
    if missing:
        raise IntakeError(f"Missing fields: {missing}")

    if submission["schema_version"] != "ai-tournament-submission-v2":
        raise IntakeError("Unsupported submission schema")
    if submission["campaign_id"] != campaign["campaign_id"]:
        raise IntakeError("Wrong campaign_id")
    participants = participant_map(campaign)
    participant_id = str(submission["participant_id"])
    if participant_id not in participants:
        raise IntakeError(f"Unknown participant_id: {participant_id}")
    if submission["participant_display_name"] != participants[participant_id]["display_name"]:
        raise IntakeError("participant_display_name does not match participant_id")
    if submission["decision_date"] != campaign["decision_date"]:
        raise IntakeError("Wrong decision_date")
    if submission["strategy"] != campaign["portfolio_policy"]["strategy"]:
        raise IntakeError("Wrong strategy")
    if submission["execution_policy"] != campaign["execution_policy"]:
        raise IntakeError("Wrong execution_policy")
    if submission["same_allocations_for_pln_and_usd"] is not True:
        raise IntakeError("PLN and USD allocations must be identical")
    if submission["final_decision_locked"] is not True:
        raise IntakeError("Submission must be final and locked")

    policy = campaign["portfolio_policy"]
    allocations = submission["allocations"]
    if not isinstance(allocations, list):
        raise IntakeError("allocations must be an array")
    if not policy["minimum_positions"] <= len(allocations) <= policy["maximum_positions"]:
        raise IntakeError("allocations must contain exactly 4, 5, or 6 positions")

    universe = set(campaign["universe"])
    seen: set[str] = set()
    stock_total = 0.0
    normalized_allocations: list[dict[str, Any]] = []
    for index, row in enumerate(allocations, start=1):
        if not isinstance(row, dict):
            raise IntakeError(f"Allocation {index} must be an object")
        if set(row) != {"ticker", "company", "weight_pct", "selection_reason"}:
            raise IntakeError(f"Allocation {index} must contain only ticker, company, weight_pct, selection_reason")
        ticker = str(row["ticker"]).upper().strip()
        if ticker not in universe:
            raise IntakeError(f"Ticker outside allowed universe: {ticker}")
        if ticker in seen:
            raise IntakeError(f"Duplicate ticker: {ticker}")
        seen.add(ticker)
        weight = finite_number(row["weight_pct"])
        if not policy["minimum_position_weight_pct"] <= weight <= policy["maximum_position_weight_pct"]:
            raise IntakeError(f"Invalid weight for {ticker}: {weight}")
        company = str(row["company"]).strip()
        reason = str(row["selection_reason"]).strip()
        if not company or not reason:
            raise IntakeError(f"Company and selection_reason are required for {ticker}")
        stock_total += weight
        normalized_allocations.append({
            "ticker": ticker,
            "company": company,
            "weight_pct": int(weight) if weight.is_integer() else weight,
            "selection_reason": reason,
        })

    cash = finite_number(submission["cash_weight_pct"])
    if not policy["minimum_cash_weight_pct"] <= cash <= policy["maximum_cash_weight_pct"]:
        raise IntakeError(f"Invalid cash weight: {cash}")
    if abs(stock_total + cash - policy["weights_must_total_pct"]) > 1e-9:
        raise IntakeError(f"Weights plus cash must equal 100; received {stock_total + cash}")

    for text_field in (
        "portfolio_thesis", "expected_three_month_driver", "biggest_portfolio_risk"
    ):
        text = str(submission[text_field]).strip()
        if not text:
            raise IntakeError(f"{text_field} cannot be empty")
        if len(text) > 1200:
            raise IntakeError(f"{text_field} is too long")

    best = str(submission["expected_best_performer"]).upper().strip()
    risky = str(submission["expected_highest_risk_position"]).upper().strip()
    if best not in seen or risky not in seen:
        raise IntakeError("Expected best/highest-risk tickers must be selected holdings")
    confidence = finite_number(submission["confidence_pct"])
    if confidence < 0 or confidence > 100:
        raise IntakeError("confidence_pct must be between 0 and 100")

    normalized = dict(submission)
    normalized["allocations"] = normalized_allocations
    normalized["cash_weight_pct"] = int(cash) if cash.is_integer() else cash
    normalized["expected_best_performer"] = best
    normalized["expected_highest_risk_position"] = risky
    normalized["confidence_pct"] = int(confidence) if confidence.is_integer() else confidence
    return normalized


def lock_submission(submission: dict[str, Any], campaign: dict[str, Any]) -> tuple[Path, Path, str]:
    storage = campaign["submission_storage"]
    participant_id = submission["participant_id"]
    submission_path = ROOT / storage["pending_submissions_dir"] / f"{slug(participant_id)}.json"
    commitment_path = ROOT / storage["pending_commitments_dir"] / f"{slug(participant_id)}.json"
    digest = sha256_text(canonical_json(submission))

    if submission_path.exists():
        existing = read_json(submission_path)
        existing_digest = sha256_text(canonical_json(existing))
        if existing_digest != digest:
            raise IntakeError(f"A different submission is already locked for {participant_id}")
    if commitment_path.exists():
        commitment = read_json(commitment_path)
        if commitment.get("sha256") != digest:
            raise IntakeError(f"Commitment mismatch for already locked participant {participant_id}")

    submission_path.parent.mkdir(parents=True, exist_ok=True)
    commitment_path.parent.mkdir(parents=True, exist_ok=True)
    submission_path.write_text(json.dumps(submission, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    commitment = {
        "schema_version": "ai-tournament-intake-commitment-v1",
        "campaign_id": campaign["campaign_id"],
        "participant_id": participant_id,
        "participant_display_name": submission["participant_display_name"],
        "locked": True,
        "locked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "canonicalization": storage["canonicalization"],
        "sha256": digest,
        "validation": {
            "valid": True,
            "positions_count": len(submission["allocations"]),
            "stock_weight_pct": sum(float(row["weight_pct"]) for row in submission["allocations"]),
            "cash_weight_pct": float(submission["cash_weight_pct"]),
            "total_weight_pct": 100.0,
            "all_tickers_allowed": True,
            "same_allocations_for_pln_and_usd": True,
            "final_decision_locked": True
        }
    }
    commitment_path.write_text(json.dumps(commitment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return submission_path, commitment_path, digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("response", type=Path)
    args = parser.parse_args()
    campaign = read_json(CAMPAIGN_PATH)
    submission = validate_submission(read_json(args.response), campaign)
    submission_path, commitment_path, digest = lock_submission(submission, campaign)
    print(json.dumps({
        "valid": True,
        "participant_id": submission["participant_id"],
        "submission": str(submission_path.relative_to(ROOT)),
        "commitment": str(commitment_path.relative_to(ROOT)),
        "sha256": digest
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

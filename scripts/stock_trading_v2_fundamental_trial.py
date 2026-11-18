#!/usr/bin/env python3
"""Immutable future-only US Fundamental Champion/Challenger decision receipts.

The receipt is frozen before outcomes, one prospective virtual free-slot trial
per US trading session. No outcome, price download or LLM during freeze.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import stock_trading_fundamental_policy as policy
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_experience_store as experiences
    from scripts import stock_trading_v2_opportunity_experience as opportunity_events
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_fundamental_policy as policy
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_experience_store as experiences
    import stock_trading_v2_opportunity_experience as opportunity_events

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "data/investments/stock_trading_v2_fundamental_trials"
EXPERIENCE_ROOT = ROOT / "data/investments/stock_trading_v2_experience"
OPPORTUNITY = ROOT / "data/investments/stock_trading_v2_opportunity/us.json"
EVIDENCE = ROOT / "data/investments/stock_trading_v2_evidence/us.json"
PROTOCOL_CONFIG = ROOT / "data/investments/stock_trading_v2_fundamental_trial_config.json"
SCHEMA = "stock-trading-v2-fundamental-trial-v1"


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object expected: {path}")
    return payload


def _event_receipt(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event["event_id"], "event_sha256": event["event_sha256"],
        "symbol": event["symbol"], "source_payload_sha256": event["source"]["payload_sha256"],
    }


def validate_receipt(receipt: Mapping[str, Any], protocol: Mapping[str, Any]) -> None:
    if receipt.get("schema_version") != SCHEMA or receipt.get("market") != "US":
        raise ValueError("fundamental trial schema/market mismatch")
    if receipt.get("policy_id") != policy.POLICY_ID or receipt.get("protocol_sha256") != contracts.payload_sha256(protocol):
        raise ValueError("fundamental trial protocol mismatch")
    if receipt.get("policy_sha256") != digest_file(Path(policy.__file__)):
        raise ValueError("research and production fundamental policy bytes differ")
    if receipt.get("governance") != {"immutable": True, "no_lookahead": True, "production_decision_influence": False}:
        raise ValueError("trial governance mismatch")
    decision = policy.utc(receipt.get("decision_at"))
    frozen = policy.utc(receipt.get("frozen_at"))
    if not decision <= frozen or (frozen - decision).total_seconds() > 3600:
        raise ValueError("trial not frozen promptly after prospective decision")
    if decision.strftime("%Y-%m-%d") != receipt.get("session_date"):
        raise ValueError("trial session mismatch")
    snapshot = receipt.get("decision_input")
    if not isinstance(snapshot, Mapping) or snapshot.get("market") != "US":
        raise ValueError("trial missing frozen decision input")
    if snapshot.get("generated_at") != receipt.get("decision_at"):
        raise ValueError("trial not linked to actual decision timestamp")
    if receipt.get("source_opportunity_sha256") != snapshot.get("source_opportunity_sha256"):
        raise ValueError("trial source opportunity mismatch")
    baseline = policy.select_one(snapshot, apply_fundamentals=False)
    challenger = policy.select_one(snapshot, apply_fundamentals=True)
    if (baseline or {}).get("symbol") != (receipt.get("baseline") or {}).get("candidate_symbol"):
        raise ValueError("trial baseline selection differs from frozen algorithm")
    if (challenger or {}).get("symbol") != (receipt.get("challenger") or {}).get("candidate_symbol"):
        raise ValueError("trial challenger differs from frozen algorithm")
    for decision_row in (receipt.get("baseline"), receipt.get("challenger")):
        if decision_row is None:
            continue
        event = decision_row.get("event")
        if not isinstance(event, Mapping) or not event.get("event_id") or not event.get("event_sha256"):
            raise ValueError("missing frozen underlying experience event")
    body = dict(receipt)
    claimed = body.pop("trial_sha256", None)
    if claimed != contracts.payload_sha256(body):
        raise ValueError("fundamental trial sha mismatch")


def freeze(
    opportunity: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    protocol: Mapping[str, Any],
    root: Path = DEFAULT_ROOT,
    experience_root: Path = EXPERIENCE_ROOT,
    frozen_at: datetime | None = None,
) -> dict[str, Any]:
    if protocol.get("schema_version") != "stock-trading-v2-fundamental-trial-config-v1":
        raise ValueError("fundamental protocol schema mismatch")
    if protocol.get("policy_id") != policy.POLICY_ID or protocol.get("fixed_n") != 30:
        raise ValueError("fundamental policy and fixed-N contract invalid")
    if opportunity.get("market") != "US" or evidence.get("market") != "US":
        raise ValueError("US only")
    if opportunity.get("source_evidence_sha256") != evidence.get("evidence_sha256"):
        raise ValueError("opportunity/evidence source mismatch")
    if evidence.get("fundamental_score_influence") is not False or not evidence.get("source_fundamentals_sha256"):
        raise ValueError("fundamental evidence source not bound")
    if (opportunity.get("decision_context") or {}).get("evidence_sha256") is not None:
        raise ValueError("unexpected unversioned evidence context")
    decision_at = str(opportunity.get("generated_at") or "")
    decision = policy.utc(decision_at)
    if decision < policy.utc(protocol["experiment_not_before"]):
        return {"status": "NOT_STARTED"}
    now = frozen_at or datetime.now(timezone.utc)
    if not decision <= now or (now - decision).total_seconds() > 3600:
        return {"status": "LATE_OR_FUTURE_DECISION"}
    day = decision.date().isoformat()
    path = root / f"{day}.json"
    if path.exists():
        existing = _read(path)
        validate_receipt(existing, protocol)
        return {"status": "ALREADY_FROZEN", "path": str(path), "trial_sha256": existing["trial_sha256"]}
    rows = policy.original_candidates(opportunity)
    if not rows:
        return {"status": "NO_BASELINE_OPPORTUNITY"}
    # Exactly reproduce the pre-specified research decision input from evidence,
    # never recompute fundamental facts after the event date.
    research_map = {str(c.get("symbol")): c for c in evidence.get("candidates") or []}
    for candidate in opportunity.get("evaluated_candidates") or []:
        if candidate.get("eligible") is not True:
            continue
        proof = research_map.get(str(candidate.get("symbol")))
        if not isinstance(proof, Mapping) or proof.get("fundamental_analysis") != candidate.get("fundamental_analysis"):
            return {"status": "UNBOUND_CANDIDATE_EVIDENCE"}

    baseline = policy.select_one(opportunity, apply_fundamentals=False)
    challenger = policy.select_one(opportunity, apply_fundamentals=True)
    events = opportunity_events.events_from_snapshots(opportunity, evidence, candidates_per_cycle=10)
    by_symbol = {str(e["symbol"]).upper(): e for e in events}
    def decision_choice(candidate: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if candidate is None:
            return None
        symbol = str(candidate.get("market_data_symbol") or candidate.get("symbol") or "").upper()
        event = by_symbol.get(symbol)
        if event is None:
            raise ValueError(f"candidate not frozen as an experience event: {symbol}")
        ep = experiences.event_path(experience_root, event)
        if not ep.exists():
            raise ValueError(f"cannot use candidate without immutable event: {ep}")
        stored = _read(ep)
        contracts.validate_experience_event(stored)
        if contracts.event_without_runtime_fields(stored) != contracts.event_without_runtime_fields(event):
            raise ValueError("underlying experience event is not the frozen decision")
        return {"candidate_symbol": candidate["symbol"], "event": _event_receipt(stored)}

    try:
        baseline_receipt = decision_choice(baseline)
        challenger_receipt = decision_choice(challenger)
    except ValueError as exc:
        return {"status": "WAITING_FROZEN_EXPERIENCE", "reason": str(exc)[:250]}
    # Preserve the entire observed eligible frontier so production can
    # independently replay the exact quality filter, including missing scores.
    frozen_candidates = [deepcopy(row) for row in opportunity.get("evaluated_candidates") or []]
    snapshot = {
        "market": "US", "generated_at": decision_at,
        "source_opportunity_sha256": opportunity["opportunity_sha256"],
        "cash": deepcopy(opportunity.get("cash") or {}),
        "evaluated_candidates": frozen_candidates,
    }
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA, "market": "US", "policy_id": policy.POLICY_ID,
        "policy_sha256": digest_file(Path(policy.__file__)),
        "protocol_sha256": contracts.payload_sha256(protocol),
        "decision_at": decision_at,
        "session_date": day, "frozen_at": contracts.iso_utc(now),
        "source_opportunity_sha256": opportunity["opportunity_sha256"],
        "source_evidence_sha256": evidence["evidence_sha256"],
        "source_fundamentals_sha256": evidence["source_fundamentals_sha256"],
        "decision_input": snapshot, "baseline": baseline_receipt, "challenger": challenger_receipt,
        "governance": {"immutable": True, "no_lookahead": True,
                       "production_decision_influence": False},
    }
    receipt["trial_sha256"] = contracts.payload_sha256(receipt)
    validate_receipt(receipt, protocol)
    root.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError("immutable trial appeared during freeze")
    # Exclusive create: never rewrite a recorded past decision.
    with path.open("x", encoding="utf-8") as file:
        json.dump(receipt, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    return {"status": "FROZEN", "path": str(path), "trial_sha256": receipt["trial_sha256"],
            "changed": (baseline or {}).get("symbol") != (challenger or {}).get("symbol")}


def verify_root(root: Path, protocol: Mapping[str, Any]) -> dict[str, Any]:
    count = 0
    days: set[str] = set()
    for path in sorted(root.glob("*.json")):
        receipt = _read(path)
        validate_receipt(receipt, protocol)
        if path.stem != receipt["session_date"] or receipt["session_date"] in days:
            raise ValueError("duplicate or misnamed fundamental trial")
        days.add(receipt["session_date"])
        count += 1
    return {"status": "OK", "frozen_trials": count}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--opportunity", type=Path, default=OPPORTUNITY)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    parser.add_argument("--protocol", type=Path, default=PROTOCOL_CONFIG)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--experience-root", type=Path, default=EXPERIENCE_ROOT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    protocol = _read(args.protocol)
    result = (verify_root(args.root, protocol) if args.verify
              else freeze(_read(args.opportunity), _read(args.evidence),
                          protocol=protocol, root=args.root, experience_root=args.experience_root))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

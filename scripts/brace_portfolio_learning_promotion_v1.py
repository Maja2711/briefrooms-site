#!/usr/bin/env python3
"""Safe automatic promotion gate for BRACE Portfolio Learning Loop v1.

A confirmation requires new settled evidence. Re-running the workflow against the
same evidence cannot advance confirmations. Candidate identity excludes changing
sample statistics, so a thesis can accumulate evidence without changing identity.
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import brace_portfolio_learning_loop_v1 as loop

ROOT = Path(__file__).resolve().parents[1]
STATE = loop.STATE_ROOT / "promotion_gate_state.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _candidate_identity(candidate: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not candidate:
        return None
    if candidate.get("kind") == "FX":
        return {
            "kind": "FX",
            "currency": candidate.get("currency"),
            "regime": candidate.get("regime"),
            "target_ratio": candidate.get("target_ratio"),
        }
    return {
        "kind": "OPTION",
        "regime": candidate.get("regime"),
        "strategy": candidate.get("strategy"),
    }


def _evidence_head() -> str:
    rows = [
        str(row.get("row_sha256") or "")
        for row in loop._jsonl(loop.SETTLEMENTS)
        if row.get("horizon_days") == loop.PROMOTION_HORIZON
        and row.get("economically_evaluable") is True
        and row.get("row_sha256")
    ]
    return loop._sha(sorted(rows))


def _read_state() -> dict[str, Any]:
    return loop._read(
        STATE,
        {
            "schema_version": "brace-portfolio-promotion-gate-state-v1",
            "candidate_identity": None,
            "candidate_signature": None,
            "last_confirmed_evidence_head": None,
            "consecutive_confirmations": 0,
            "last_promotion_id": None,
        },
    )


def evaluate() -> dict[str, Any]:
    envelope = loop._read(loop.ENVELOPE, {})
    policy = loop._read(loop.PRODUCTION_POLICY, {})
    loop._validate_envelope(envelope)
    loop._validate_policy(policy, envelope)
    report = loop.build_counterfactual_report()
    candidate = loop._candidate(report, policy, envelope)
    identity = _candidate_identity(candidate)
    signature = loop._sha(identity) if identity else None
    evidence_head = _evidence_head()
    state = _read_state()
    confirmations = int(state.get("consecutive_confirmations") or 0)
    new_evidence = evidence_head != state.get("last_confirmed_evidence_head")

    if identity is None:
        confirmations = 0
    elif signature != state.get("candidate_signature"):
        confirmations = 1 if new_evidence else 0
    elif new_evidence:
        confirmations += 1

    required = int((envelope.get("validation") or {}).get("required_consecutive_confirmations", 2))
    status = "NO_VALIDATED_PROMOTION"
    promotion_id = None
    change = None

    if candidate and confirmations >= required:
        updated = deepcopy(policy)
        regime = str(candidate.get("regime") or "DEFAULT")
        if candidate.get("kind") == "FX":
            currency = str(candidate.get("currency"))
            current = loop._policy_ratio(policy, regime, currency)
            target = float(candidate.get("target_ratio"))
            promoted = loop._step_ratio(current, target, envelope)
            default = deepcopy((updated.get("fx_hedge_ratio_by_regime") or {}).get("DEFAULT") or {"USD": 0.0, "EUR": 0.0})
            updated.setdefault("fx_hedge_ratio_by_regime", {}).setdefault(regime, default)[currency] = promoted
            change = {
                "kind": "FX",
                "currency": currency,
                "regime": regime,
                "from": current,
                "to": promoted,
                "validated_target": target,
            }
        else:
            old = loop._policy_option(policy, regime)
            promoted = str(candidate.get("strategy"))
            updated.setdefault("options_strategy_by_regime", {})[regime] = promoted
            change = {"kind": "OPTION", "regime": regime, "from": old, "to": promoted}

        promotion_id = f"brace10k-auto-{loop._sha({'change': change, 'evidence': evidence_head})[:20]}"
        updated["policy_version"] = int(policy.get("policy_version") or 0) + 1
        updated["status"] = "AUTO_PROMOTED_BOUNDED_POLICY"
        updated["promotion"] = {
            "automatic": True,
            "manual_approval_required": False,
            "last_promotion_id": promotion_id,
            "evidence_sha256": evidence_head,
            "validation_epoch_id": f"brace10k-epoch-{evidence_head[:16]}",
        }
        loop._validate_policy(updated, envelope)
        loop._write(loop.PRODUCTION_POLICY, updated)
        event = {
            "schema_version": loop.PROMOTION_SCHEMA,
            "promotion_id": promotion_id,
            "promoted_at": _now(),
            "engine_id": "brace_portfolio_10k",
            "change": change,
            "candidate_identity": identity,
            "candidate_statistics": candidate.get("statistics"),
            "evidence_sha256": evidence_head,
            "automatic": True,
            "manual_approval_required": False,
            "bounded_by_envelope_sha256": loop._sha(envelope),
            "trade_execution_authority": False,
            "real_broker_integration": False,
        }
        event["row_sha256"] = loop._sha(event)
        loop._append(loop.PROMOTIONS, event)
        confirmations = 0
        status = "AUTO_PROMOTED"

    state.update(
        {
            "schema_version": "brace-portfolio-promotion-gate-state-v1",
            "generated_at": _now(),
            "candidate_identity": identity,
            "candidate_signature": signature,
            "last_confirmed_evidence_head": evidence_head if identity and new_evidence else state.get("last_confirmed_evidence_head"),
            "consecutive_confirmations": confirmations,
            "required_confirmations": required,
            "last_promotion_id": promotion_id or state.get("last_promotion_id"),
            "status": status,
        }
    )
    loop._write(STATE, state)
    return {
        "status": status,
        "promotion_id": promotion_id,
        "change": change,
        "candidate_identity": identity,
        "confirmations": confirmations,
        "required_confirmations": required,
        "new_evidence": new_evidence,
        "evidence_head": evidence_head,
    }


def verify() -> dict[str, Any]:
    envelope = loop._read(loop.ENVELOPE, {})
    policy = loop._read(loop.PRODUCTION_POLICY, {})
    loop._validate_envelope(envelope)
    loop._validate_policy(policy, envelope)
    for event in loop._jsonl(loop.PROMOTIONS):
        if event.get("automatic") is not True or event.get("manual_approval_required") is not False:
            raise ValueError("promotion governance mismatch")
        if event.get("trade_execution_authority") is not False or event.get("real_broker_integration") is not False:
            raise ValueError("promotion gained execution authority")
    return {"ok": True, "promotion_events": len(loop._jsonl(loop.PROMOTIONS)), "policy_version": policy.get("policy_version")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = verify() if args.verify else evaluate()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

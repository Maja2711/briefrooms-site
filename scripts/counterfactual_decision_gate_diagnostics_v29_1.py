#!/usr/bin/env python3
"""PR29.1 bridge: consume producer-frozen Daily Stock rejected candidates.

The underlying PR29 engine remains the single ledger writer and diagnostics
owner.  This bridge replaces only the legacy GPW/US gate-only rejected rows
with the richer producer freeze created by ``daily_stock_rejected_candidate_freeze``.
All non-Daily-Stock adapters and all PR29 settlement/governance logic remain
unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

try:
    import counterfactual_decision_gate_diagnostics as base
    from daily_stock_rejected_candidate_freeze import FIELD, verify_payload as verify_freeze_payload
except ModuleNotFoundError:  # pragma: no cover
    from scripts import counterfactual_decision_gate_diagnostics as base
    from scripts.daily_stock_rejected_candidate_freeze import FIELD, verify_payload as verify_freeze_payload

_ORIGINAL_ADAPT_DAILY_STOCK = base.adapt_daily_stock


def _score(row: Mapping[str, Any]) -> Any:
    score_state = row.get("score_state") if isinstance(row.get("score_state"), Mapping) else {}
    return score_state.get("composite_score") if score_state.get("composite_score") is not None else score_state.get("quant_pre_score")


def _candidate_from_freeze(row: Mapping[str, Any], *, decision_id: str) -> dict[str, Any]:
    plan = row.get("risk_plan") if isinstance(row.get("risk_plan"), Mapping) else {}
    eligibility = row.get("settlement_eligibility") if isinstance(row.get("settlement_eligibility"), Mapping) else {}
    decision_path = row.get("decision_path") if isinstance(row.get("decision_path"), Mapping) else {}
    gates = [dict(gate) for gate in (decision_path.get("gates") or []) if isinstance(gate, Mapping)]
    mode = "risk_plan" if eligibility.get("eligible") is True else "insufficient_counterfactual_state"
    reference = plan.get("reference_price")
    return base.candidate_snapshot(
        str(row.get("candidate_id") or f"{decision_id}:{row.get('symbol')}:LONG"),
        action="LONG",
        selected=False,
        settlement_mode=mode,
        score=_score(row),
        market_symbol=str(row.get("symbol") or "") or None,
        reference_price=reference,
        entry=reference,
        stop=plan.get("stop"),
        target=plan.get("target"),
        reward_risk=plan.get("reward_risk"),
        gates=gates,
        metadata={
            "pr29_1_full_rejected_candidate_state": True,
            "first_blocking_gate": deepcopy(row.get("first_blocking_gate")),
            "entry_zone": deepcopy(plan.get("entry_zone")),
            "risk_percent": plan.get("risk_percent"),
            "atr": plan.get("atr"),
            "plan_source": plan.get("plan_source"),
            "activation_rule": plan.get("activation_rule"),
            "score_state": deepcopy(row.get("score_state") or {}),
            "market_state": deepcopy(row.get("market_state") or {}),
            "settlement_eligibility": deepcopy(eligibility),
            "producer_decision_path": deepcopy(decision_path),
            "state_sha256": row.get("state_sha256"),
        },
    )


def adapt_daily_stock_v29_1(payload: Mapping[str, Any], *, market: str) -> list[dict[str, Any]]:
    snapshots = _ORIGINAL_ADAPT_DAILY_STOCK(payload, market=market)
    freeze = payload.get(FIELD)
    if not isinstance(freeze, Mapping) or freeze.get("status") != "frozen":
        return snapshots

    verify_freeze_payload(payload, market)
    rebuilt: list[dict[str, Any]] = []
    for snapshot in snapshots:
        # Keep only candidates that were actually selected by the source engine
        # (including the explicit FLAT candidate). Legacy unselected rows are
        # replaced by the richer producer-frozen records to avoid double count.
        candidates = [dict(row) for row in (snapshot.get("candidates") or []) if bool(row.get("selected"))]
        seen = {str(row.get("candidate_id") or "") for row in candidates}
        for frozen in freeze.get("candidates") or []:
            if not isinstance(frozen, Mapping):
                continue
            candidate = _candidate_from_freeze(frozen, decision_id=str(snapshot.get("decision_id") or ""))
            cid = str(candidate.get("candidate_id") or "")
            if cid in seen:
                continue
            seen.add(cid)
            candidates.append(candidate)

        metadata = dict(snapshot.get("metadata") or {})
        metadata.update(
            {
                "pr29_1_freeze_sha256": freeze.get("freeze_sha256"),
                "pr29_1_frozen_at": freeze.get("frozen_at"),
                "pr29_1_rejected_candidate_count": freeze.get("candidate_count"),
                "pr29_1_economically_evaluable_count": freeze.get("economically_evaluable_count"),
            }
        )
        rebuilt.append(
            base.make_snapshot(
                engine_id=str(snapshot["engine_id"]),
                decision_id=str(snapshot["decision_id"]),
                decision_at=str(snapshot["decision_at"]),
                actual_action=str(snapshot["actual_action"]),
                decision_stage=str(snapshot["decision_stage"]),
                source_ref=str(snapshot.get("source_ref") or ""),
                candidates=candidates,
                instrument_id=snapshot.get("instrument_id"),
                upstream_subject_id=snapshot.get("upstream_subject_id"),
                target_at=snapshot.get("target_at"),
                coverage="selected_risk_plan_plus_full_rejected_candidate_state_v29_1",
                metadata=metadata,
            )
        )
    return rebuilt


def install() -> None:
    base.adapt_daily_stock = adapt_daily_stock_v29_1


def main() -> int:
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())

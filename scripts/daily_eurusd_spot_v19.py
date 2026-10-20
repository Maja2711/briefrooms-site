#!/usr/bin/env python3
"""Daily EUR/USD v1.9 — Belief-first production architecture.

Direction is decided once, after upstream adapters/Evidence/Belief Core.
Legacy v1-v1.8 modules remain for lifecycle/history compatibility only.

Production order:
Belief Core -> one final LONG/SHORT/FLAT decision -> execution admission
-> EPE verified fill -> canonical lifecycle.

A/B/C, FSE, EURUSD X, Arm A fallback and low-edge exploration have zero
production direction/timing authority in v1.9. The legacy contextual entry
learner is reset to shadow observation until it earns evidence under the new
Belief-first decision semantics.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from daily_engine_contract import DailyEngineOutput
import daily_eurusd_belief_decision as belief_decision
import daily_eurusd_lifecycle as lifecycle
import daily_eurusd_spot as base
import daily_eurusd_spot_v14 as v14
import daily_eurusd_spot_v16 as v16
import daily_eurusd_spot_v18 as v18
import investments_wes_macro_belief as legacy_macro_context
import daily_eurusd_decision_record as decision_record
import daily_eurusd_production_policy as production_policy

ENGINE_VERSION = decision_record.ENGINE_VERSION
ENTRY_THESIS_SOURCE_FAMILY = "NATIVE_BELIEF_FIRST"
_epe_prepare = base.prepare_entry_candidate
_v18_open_output = v18._open_output
_v18_closed_output = v18._closed_output
_cycle_context: dict[str, Any] | None = None


def _policy_state(when: datetime) -> Mapping[str, Any]:
    return (_cycle_context or {}).get("policy_state") or production_policy.default_state(when)


def _decision_manifest(candidate: DailyEngineOutput) -> dict[str, Any]:
    metadata = dict(candidate.metadata)
    final = metadata.get("final_decision") or {}
    when = belief_decision._parse_time((final.get("provenance") or {}).get("created_at") or candidate.timestamp)
    if not metadata.get("production_policy"):
        metadata["production_policy"] = production_policy.policy_context(
            _policy_state(when), score=float(final.get("score", candidate.score)),
            direction=str(final.get("direction", candidate.direction)), observed_at=when,
        )
    return decision_record.make_manifest(
        {**candidate.to_dict(), "metadata": metadata},
        snapshot_sha256=metadata["market_snapshot_sha256"],
    )


def _clone(
    output: DailyEngineOutput,
    *,
    direction: str | None = None,
    entry: float | None | object = ...,
    stop: float | None | object = ...,
    target: float | None | object = ...,
    status: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> DailyEngineOutput:
    return DailyEngineOutput(
        instrument=output.instrument,
        timestamp=output.timestamp,
        direction=direction if direction is not None else output.direction,
        score=float(output.score),
        confidence=float(output.confidence),
        entry=output.entry if entry is ... else entry,
        stop=output.stop if stop is ... else stop,
        target=output.target if target is ... else target,
        horizon="intraday_to_27h",
        engine_version=ENGINE_VERSION,
        status=status or output.status,
        decision_mode="WITH",
        metadata=dict(metadata if metadata is not None else output.metadata),
    ).validate()


def _market_geometry(snapshot: Any, direction: str) -> tuple[float | None, float | None, float | None]:
    rows = list((getattr(snapshot, "bars", {}) or {}).get(base.EURUSD) or [])
    if not rows or direction not in {"LONG", "SHORT"}:
        return None, None, None
    atr = base._atr(rows)
    if atr is None or float(atr) <= 0:
        return None, None, None
    entry = float(rows[-1].close)
    risk = max(float(atr) * 1.35, entry * 0.0027)
    reward_risk = 1.8
    if direction == "LONG":
        return round(entry, 5), round(entry - risk, 5), round(entry + reward_risk * risk, 5)
    return round(entry, 5), round(entry + risk, 5), round(entry - reward_risk * risk, 5)


def fetch_snapshot(client: Any | None = None) -> Any:
    """Fetch only the direct EUR/USD market fact needed after final Belief synthesis.

    UUP/TLT and all other explanatory inputs belong upstream in the adapter /
    Evidence / Belief pipeline and are intentionally not fetched by the
    production decision runtime.
    """
    client = client or base.YahooChartClient(timeout=15)
    bars = {base.EURUSD: client.bars(base.EURUSD, "10d", "30m")}
    return base.MarketSnapshot(bars)


def _execution_admission(
    candidate: DailyEngineOutput,
    history: Mapping[str, Any] | None,
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    reasons: list[str] = []
    details: dict[str, Any] = {
        "final_direction_preserved": candidate.direction,
        "direction_mutation_allowed": False,
    }

    calendar = belief_decision.calendar_safety(
        observed_at=observed_at,
        decision=(candidate.metadata or {}).get("final_decision") or {},
    )
    details["belief_calendar_safety"] = calendar
    if calendar.get("blocked") is True:
        reasons.append(str(calendar.get("reason") or "belief_calendar_safety_block"))

    # Re-use only the history/thesis-change part of the legacy re-entry guard.
    # v1.8 allowed macro/event side channels to count as "fresh external
    # evidence" and release a cross-family guard. Under Belief-first v1.9 all
    # such directional evidence must already have reached CF-07 before the
    # final decision, so remove those side-channel fields for this admission
    # check. They remain available later for open-position risk management.
    guard_metadata = dict(candidate.metadata)
    guard_metadata.pop("belief_macro", None)
    guard_metadata.pop("event_intelligence", None)
    guarded = v16._apply_same_thesis_guard(
        _clone(candidate, metadata=guard_metadata),
        history,
        source_family=ENTRY_THESIS_SOURCE_FAMILY,
    )
    guard_meta = (guarded.metadata or {}).get("same_thesis_reentry_guard") or {}
    details["same_thesis_reentry_guard"] = dict(guard_meta)
    if candidate.direction in {"LONG", "SHORT"} and guarded.direction == "FLAT":
        reasons.append(str(guard_meta.get("reason") or "same_thesis_reentry_blocked"))

    allowed = candidate.direction in {"LONG", "SHORT"} and not reasons
    return {
        "allowed": allowed,
        "status": "ALLOWED" if allowed else "BLOCKED",
        "reasons": reasons,
        **details,
    }


def build_output(
    snapshot: Any,
    history: Mapping[str, Any] | None = None,
    *,
    allow_entry: bool = True,
) -> DailyEngineOutput:
    rows = list((getattr(snapshot, "bars", {}) or {}).get(base.EURUSD) or [])
    if not rows:
        raise ValueError("EUR/USD market series is required for timestamp and risk geometry")
    # The bar carries market observation time; the decision has its own native
    # creation time. A completed 30m bar must not backdate current Evidence.
    observed_at = ((_cycle_context or {}).get("decision_at")
                   or rows[-1].timestamp.astimezone(timezone.utc))

    state, consumer_meta = belief_decision.load_effective_state(observed_at=observed_at)
    decision = belief_decision.synthesize(
        state,
        observed_at=observed_at,
        authoritative_consumer=consumer_meta,
    )
    decision["epistemic_consumer"] = dict(consumer_meta)
    direction = str(decision["direction"])
    entry, stop, target = _market_geometry(snapshot, direction)

    # If Belief direction is valid but current market geometry cannot be built,
    # fail closed. Geometry is an execution prerequisite, not a second direction.
    geometry_ok = direction == "FLAT" or all(value is not None for value in (entry, stop, target))
    if not geometry_ok:
        decision = {
            **decision,
            "direction": "FLAT",
            "reasons": [*list(decision.get("reasons") or []), "market_geometry_unavailable"],
        }
        direction = "FLAT"
        entry = stop = target = None

    # synthesize() emits provenance for its own complete payload, but v1.9 adds
    # authoritative consumer diagnostics and may fail closed on execution geometry.
    # Re-seal only metadata after those additions; decision logic is unchanged.
    decision = belief_decision.attach_decision_provenance(
        decision,
        state,
        observed_at=observed_at,
        authoritative_consumer=consumer_meta,
    )

    legacy_learning = lifecycle.learning_state(list((history or {}).get("trades") or []))
    metadata: dict[str, Any] = {
        "market_snapshot_sha256": decision_record.market_snapshot_hash(snapshot),
        "market": "FX_SPOT",
        "currency_pair": "EUR/USD",
        "rollout_stage": "paper",
        "decision_source": "NATIVE",
        "belief": {
            "mode": "WITH",
            "decision_influence": True,
            "source": "CF-07 Epistemic Consumer Interface over BriefRooms Belief Core",
            "consumer": "DAILY_EURUSD",
            "trade_execution_authority": False,
        },
        "belief_pipeline": {
            "architecture": "BELIEF_FIRST_V1",
            "order": [*decision["routing"][:-1], "EPISTEMIC_CONSUMER_INTERFACE", decision["routing"][-1]],
            "consumer_contract": consumer_meta.get("contract"),
            "consumer_available": consumer_meta.get("available"),
            "raw_market_direction_before_belief": False,
            "final_decision_after_belief": True,
            "missing_data_is_neutral_vote": False,
        },
        "final_decision": dict(decision),
        "candidate": {
            "source": "NATIVE",
            "direction": direction,
            "score": float(decision["score"]),
            "confidence": float(decision["confidence"]),
            "accepted": bool(direction in {"LONG", "SHORT"} and allow_entry),
            "gate_reasons": (
                []
                if direction in {"LONG", "SHORT"} and allow_entry
                else ["entry_disabled_this_cycle"]
                if not allow_entry
                else list(decision.get("reasons") or [])
            ),
        },
        "direction_authority": {
            "owner": "NATIVE_DAILY_EURUSD_BELIEF_FIRST_DECISION_ENGINE",
            "single_owner": True,
            "source": "NATIVE_BELIEF_FIRST",
            "epistemic_input": "BELIEF_CORE_CF07",
            "direction": direction,
            "allowed": True,
            "rule": "Final LONG/SHORT/FLAT is synthesized only after Belief Core; no shadow/fallback may manufacture direction.",
        },
        "shadow_boundary": {
            "A_B_C": {"production_direction_authority": False, "production_timing_authority": False},
            "FSE": {"production_direction_authority": False, "production_timing_authority": False},
            "EURUSD_X": {"production_direction_authority": False, "production_timing_authority": False},
            "A_TECHNICAL_FALLBACK": {"production_direction_authority": False},
            "LOW_EDGE_LEARNING_EXPLORATION": {"production_direction_authority": False},
        },
        "contextual_entry_policy": {
            "mode": "LEGACY_V18_CONTEXTUAL_LEARNER",
            "status": "SHADOW_AFTER_BELIEF_FIRST_MIGRATION",
            "decision_influence": False,
            "direction_mutation_allowed": False,
            "timing_mutation_allowed": False,
            "reason": "Legacy learner was trained under pre-v1.9 decision semantics; production authority reset prospectively.",
            "FSE_production_influence": False,
        },
        "same_thesis_reentry_guard": {
            "enabled": True,
            "evaluated": False,
            "blocked": False,
            "reason": "no_directional_candidate" if direction == "FLAT" else "awaiting_execution_admission",
            "direction_mutation_allowed": False,
        },
        "learning": {
            **legacy_learning,
            "production_direction_learning_applied": False,
            "note": "Legacy local adaptive weights remain audit history only; v1.9 direction consumes Belief Core.",
        },
        "risk": {
            "market_observed_at": rows[-1].timestamp.isoformat().replace("+00:00", "Z"),
            "market_reference_mid": float(rows[-1].close),
            "atr_30m_at_decision": base._atr(rows),
            "atr_30m_window": 26,
            "atr_multiple": 1.35,
            "risk_floor_percent": 0.0027,
            "reward_risk": 1.8,
            "soft_horizon_hours": 24.0,
            "hard_horizon_hours": 27.0,
        },
        "data": {
            "market_geometry_source": "EURUSD 30m market snapshot",
            "directional_evidence_source": "BELIEF_CORE_CF07",
            "executable_bid_ask_available": False,
        },
    }

    # Preserve the macro fast lane only for open-position/risk-management
    # consumers. It is explicitly NOT a source of v1.9 final direction.
    try:
        risk_context = legacy_macro_context.context(observed_at)
    except Exception:
        risk_context = {"available": False, "reason": "risk_context_unavailable"}
    metadata["belief_macro"] = {
        **dict(risk_context),
        "direction_decision_influence": False,
        "role": "OPEN_POSITION_RISK_ONLY",
    }

    provisional = DailyEngineOutput(
        instrument="EUR/USD",
        timestamp=observed_at.isoformat().replace("+00:00", "Z"),
        direction=direction,
        score=float(decision["score"]),
        confidence=float(decision["confidence"]),
        entry=entry,
        stop=stop,
        target=target,
        horizon="intraday_to_27h",
        engine_version=ENGINE_VERSION,
        status="SIGNAL" if direction in {"LONG", "SHORT"} and allow_entry else "NO_TRADE",
        decision_mode="WITH",
        metadata=v14._with_dynamic_metadata(metadata),
    ).validate()

    if allow_entry and provisional.direction in {"LONG", "SHORT"}:
        md = dict(provisional.metadata)
        md["execution_admission"] = _execution_admission(
            provisional,
            history,
            observed_at=observed_at,
        )
        guard = (md["execution_admission"].get("same_thesis_reentry_guard") or {})
        if guard:
            md["same_thesis_reentry_guard"] = dict(guard)
        provisional = _clone(provisional, metadata=md)
    elif not allow_entry:
        md = dict(provisional.metadata)
        md["execution_admission"] = {
            "allowed": False,
            "status": "DISABLED",
            "reasons": ["entry_disabled_this_cycle"],
            "final_direction_preserved": provisional.direction,
            "direction_mutation_allowed": False,
        }
        provisional = _clone(provisional, metadata=md)

    return provisional


def _execution_blocked_output(candidate: DailyEngineOutput) -> DailyEngineOutput:
    metadata = dict(candidate.metadata)
    final = dict(metadata.get("final_decision") or {})
    metadata["execution_blocked_decision"] = {
        "direction": final.get("direction", candidate.direction),
        "score": final.get("score", candidate.score),
        "confidence": final.get("confidence", candidate.confidence),
        "preserved": True,
    }
    candidate_meta = dict(metadata.get("candidate") or {})
    candidate_meta["accepted"] = False
    candidate_meta["gate_reasons"] = list(dict.fromkeys([
        *list(candidate_meta.get("gate_reasons") or []),
        *list((metadata.get("execution_admission") or {}).get("reasons") or []),
    ]))
    metadata["candidate"] = candidate_meta
    return _clone(
        candidate,
        direction="FLAT",
        entry=None,
        stop=None,
        target=None,
        status="NO_TRADE",
        metadata=metadata,
    )


def prepare_entry_candidate(
    candidate: DailyEngineOutput,
    monitor_bars: Any,
    observed_at: datetime,
) -> DailyEngineOutput:
    if candidate.direction not in {"LONG", "SHORT"}:
        return candidate
    admission = (candidate.metadata or {}).get("execution_admission") or {}
    if admission.get("allowed") is not True:
        return _execution_blocked_output(candidate)

    prepared = _epe_prepare(candidate, monitor_bars, observed_at)
    if prepared.direction not in {"LONG", "SHORT"}:
        metadata = dict(prepared.metadata)
        metadata.setdefault("execution_blocked_decision", {
            "direction": candidate.direction,
            "score": candidate.score,
            "confidence": candidate.confidence,
            "preserved": True,
        })
        return _clone(prepared, metadata=metadata)
    prepared = _clone(prepared)
    metadata = dict(prepared.metadata)
    context = production_policy.policy_context(_policy_state(observed_at),
        score=prepared.score, direction=prepared.direction,
        observed_at=belief_decision._parse_time(prepared.timestamp))
    metadata["production_policy"] = context
    if context.get("allowed") is not True:
        metadata["execution_admission"] = {
            **dict(metadata.get("execution_admission") or {}),
            "allowed": False, "status": "POLICY_FILTERED",
            "reasons": ["production_admission_policy_margin"],
        }
    prepared = _clone(prepared, metadata=metadata)
    manifest = _decision_manifest(prepared)
    metadata["decision_manifest"] = manifest
    prepared = _clone(prepared, metadata=metadata)
    position = _create_position_v19(prepared.to_dict())
    if _cycle_context is not None:
        state, _case_id = production_policy.freeze_case(
            _cycle_context["policy_state"], decision_manifest=manifest,
            position=position, score=prepared.score,
            now=(datetime.now(timezone.utc) if _cycle_context.get("live")
                 else max(_cycle_context["decision_at"], belief_decision._parse_time(prepared.timestamp))),
            slices={"session": _session(prepared.timestamp),
                    "macro": "clear"},
        )
        _cycle_context["policy_state"] = state
    if context.get("allowed") is not True:
        # Freeze the final admission record as well as the already-frozen case.
        # The case retains the pre-filter decision and independent policy votes.
        metadata["decision_manifest"] = manifest
        return _execution_blocked_output(_clone(prepared, metadata=metadata))
    return prepared


def _create_position_v19(payload: Mapping[str, Any]) -> dict[str, Any]:
    # Construct explicitly instead of inheriting mutable global hook chains.
    # Legacy isolated callers keep their own version/contract; they never gain
    # new-policy eligibility or a reconstructed manifest.
    position = v14.normalize_position(lifecycle.create_position(payload)) or {}
    if payload.get("engine_version") != ENGINE_VERSION:
        return position
    position["engine_version"] = ENGINE_VERSION
    metadata = payload.get("metadata") or {}
    execution = metadata.get("execution_price_engine") or {}
    if (execution.get("verified") is not True or execution.get("status") != "VERIFIED_FILL"
            or execution.get("execution_cost_contract") != lifecycle.EXECUTION_COST_CONTRACT):
        raise ValueError("verified EPE is required before production admission")
    position["execution_price_engine"] = copy.deepcopy(execution)
    position.update(decision_source="NATIVE", learning_eligible=True, learning_namespace="NATIVE")
    position["entry_thesis"] = v16._entry_thesis_from_payload(payload)
    manifest = metadata.get("decision_manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError("native production manifest is required before admission")
    decision_record.validate_manifest(manifest)
    position["decision_manifest"] = copy.deepcopy(manifest)
    thesis = dict(position.get("entry_thesis") or {})
    thesis.update({"source_family": ENTRY_THESIS_SOURCE_FAMILY, "decision_id": manifest["decision_id"],
        "policy_version_id": manifest["policy_version_id"], "manifest_sha256": manifest["manifest_sha256"]})
    position["entry_thesis"] = thesis
    return position


def _session(stamp: str) -> str:
    hour = belief_decision._parse_time(stamp).hour
    return "ASIA" if hour < 7 else "EUROPE" if hour < 13 else "US" if hour < 21 else "LATE"


def _fx_open(stamp: datetime) -> bool:
    local = stamp.astimezone(ZoneInfo("America/New_York"))
    return (local.weekday() < 4 or (local.weekday() == 4 and local.hour < 17)
            or (local.weekday() == 6 and local.hour >= 17))


def _counterfactual_evaluator(bars: Any, metadata: Mapping[str, Any], now: datetime):
    """Return a prospective, non-executable reference-lifecycle evaluator.

    Only an admission frozen before its market path may enter this evaluator.
    Historical retrieval resolves that frozen counterfactual; it never creates
    a canonical fill or replaces a real trade outcome.
    """
    rows = sorted(bars, key=lambda bar: bar.timestamp)
    def evaluate(position: Mapping[str, Any], case: Mapping[str, Any]) -> dict[str, Any]:
        opened = belief_decision._parse_time(position.get("opened_at"))
        if opened is None or not rows:
            return {"coverage_complete": False, "reason": "missing_reference_path"}
        first_minute = opened.replace(second=0, microsecond=0) + timedelta(minutes=1)
        if now < first_minute:
            return {"coverage_complete": True, "position": dict(position), "closed_trade": None,
                    "reason": "awaiting_first_forward_observation"}
        available = [bar for bar in rows if opened < bar.timestamp <= now]
        if not available and not _fx_open(now):
            return {"market_open": False, "coverage_complete": False}
        if not available:
            return {"coverage_complete": False, "reason": "reference_path_stale"}
        stale = _fx_open(now) and (now - available[-1].timestamp).total_seconds() > 180
        last_minute = min(now - timedelta(minutes=1), available[-1].timestamp).replace(second=0, microsecond=0)
        observed_minutes = {bar.timestamp.replace(second=0, microsecond=0) for bar in available}
        cursor = first_minute
        missing_at = None
        while cursor <= last_minute:
            if _fx_open(cursor) and cursor not in observed_minutes:
                missing_at = cursor
                break
            cursor += timedelta(minutes=1)
        reference = copy.deepcopy(dict(position))
        # Management information is usable only once it existed by the market
        # timestamp being evaluated. Current metadata is retained for the next
        # observation, rather than applied to an earlier candle retroactively.
        if missing_at is not None:
            available = [bar for bar in available if bar.timestamp < missing_at]
        if not available:
            return {"coverage_complete": False, "reason": "incomplete_reference_1m_path"}
        management_at = belief_decision._parse_time(reference.get("_management_asof"))
        if management_at is None or management_at > available[-1].timestamp:
            reference.pop("_management_candidate", None)
            reference.pop("_belief_macro_context", None)
        trade = v18._evaluate_position(reference, available, available[-1].timestamp)
        if trade is None and (missing_at is not None or stale):
            return {"coverage_complete": False,
                    "reason": "incomplete_reference_1m_path" if missing_at is not None else "reference_path_stale"}
        updated = copy.deepcopy(dict(position))
        candidate = metadata.get("candidate_at_refresh") or metadata.get("candidate")
        if isinstance(candidate, Mapping):
            updated["_management_candidate"] = dict(candidate)
        if isinstance(metadata.get("belief_macro"), Mapping):
            updated["_belief_macro_context"] = dict(metadata["belief_macro"])
        updated["_management_asof"] = now.isoformat()
        return {"position": updated, "closed_trade": trade,
            "coverage_complete": True, "reason": "prospectively_frozen_reference_lifecycle",
            "detected_at": now.isoformat()}
    return evaluate


class _CycleClient:
    def __init__(self, source: Any, *, live: bool) -> None:
        self.source = source
        self._daily_live_client = live
        self.cache: dict[tuple[str, str, str], Any] = {}

    def bars(self, symbol: str, period: str, interval: str) -> Any:
        key = (symbol, period, interval)
        if key not in self.cache:
            self.cache[key] = self.source.bars(symbol, period, interval)
        return self.cache[key]


def run_cycle(output_path: Path, history_path: Path, client: Any | None = None) -> DailyEngineOutput:
    """Single production owner for decision records and bounded policy changes.

    Realtime exits only append canonical history. This monitor binds those
    outcomes to frozen admissions, evaluates policy, and writes all new state
    in the same guarded Git persistence transaction as the Daily output.
    """
    global _cycle_context
    live = client is None
    cached = _CycleClient(client or base.YahooChartClient(timeout=15), live=live)
    bars = cached.bars(base.EURUSD, "5d", "1m")
    if not bars:
        raise ValueError("EURUSD monitor has no market observations")
    now = datetime.now(timezone.utc) if live else bars[-1].timestamp.astimezone(timezone.utc)
    state_path = output_path.parent / "eurusd_production_policy_state.json"
    ledger_path = output_path.parent / "eurusd_production_decisions.json"
    history = lifecycle.load_history(history_path)
    previous = base._load_json(output_path)
    previous_metadata = (previous.get("metadata") or {}) if isinstance(previous, Mapping) else {}
    try:
        if state_path.exists() != ledger_path.exists():
            raise ValueError("incomplete production policy/decision epoch")
        prior_epoch = (previous_metadata.get("production_policy_loop")
            or (previous_metadata.get("position") or {}).get("decision_manifest")
            or any(row.get("decision_manifest") for row in history.get("trades") or []))
        if not state_path.exists() and prior_epoch:
            raise ValueError("production learning epoch missing for recorded admissions")
        ledger = decision_record.load_ledger(ledger_path)
        policy = production_policy.load_state(state_path, now)
        for trade in history.get("trades") or []:
            if isinstance(trade.get("decision_manifest"), Mapping):
                decision_record.validate_trade_binding(trade)
        policy = production_policy.bind_actual_history(policy, history, now)
        production_policy.validate_history_bindings(policy, history)
    except (ValueError, TypeError, KeyError) as exc:
        # A corrupt learning record must stop new admissions/promotions, not
        # the deterministic risk exit of an already persisted position.
        position = (previous.get("metadata") or {}).get("position") or {}
        if str(position.get("status") or "").upper() != "OPEN":
            raise
        previous_context = _cycle_context
        _cycle_context = {"policy_state": production_policy.default_state(now), "decision_at": now, "live": live}
        try:
            output = v18.run_cycle(output_path, history_path, cached)
            metadata = dict(output.metadata)
            metadata["production_policy_loop"] = {
                "status": "DEGRADED_EXIT_ONLY", "entry_authority": False,
                "policy_changed": False, "reason": f"invalid_governance_state:{type(exc).__name__}",
            }
            output = _clone(output, metadata=metadata)
            output_path.write_text(json.dumps(output.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return output
        finally:
            _cycle_context = previous_context
    policy = production_policy.advance_governance(policy, now,
        runtime_code_sha256=decision_record.runtime_code_hash())
    previous_context = _cycle_context
    _cycle_context = {"policy_state": policy, "decision_at": now, "live": live}
    try:
        output = v18.run_cycle(output_path, history_path, cached)
        metadata = dict(output.metadata)
        manifest = metadata.get("decision_manifest")
        if not isinstance(manifest, Mapping):
            manifest = _decision_manifest(output)
            metadata["decision_manifest"] = manifest
        ledger = decision_record.append_manifest(ledger, manifest)
        policy = _cycle_context["policy_state"]
        completed_at = datetime.now(timezone.utc) if live else max(now, belief_decision._parse_time(output.timestamp))
        policy = production_policy.bind_actual_history(policy, lifecycle.load_history(history_path), completed_at)
        policy = production_policy.settle_cases(policy,
            evaluator=_counterfactual_evaluator(bars, metadata, completed_at), now=completed_at)
        # Governance is advanced once before the decision; a newly observed
        # outcome cannot silently change the policy used by that same decision.
        metadata["production_policy_loop"] = {
            "schema_version": "eurusd-production-policy-runtime-v1",
            "single_writer": "TR03_DAILY_MONITOR", "source_history_rewritten": False,
            "policy_application": "FUTURE_ADMISSIONS_ONLY", "counterfactual_execution_authority": False,
            "active_version_id": policy["active_version_id"],
            "active_minimum_score_distance": policy["versions"][policy["active_version_id"]]["policy"]["minimum_score_distance"],
            "candidates": [{"candidate_id": item["candidate_id"], "status": item["status"],
                "selected_cases": len(item["evaluation_case_ids"]),
                "resolved_cases": sum(policy["cases"][cid]["outcome"] is not None for cid in item["evaluation_case_ids"]),
                "gate_status": (item.get("gate") or {}).get("status")}
                for item in policy["candidates"].values()],
            "rollback_count": len(policy["rollback_events"]),
            "latest_rollback": policy["rollback_events"][-1] if policy["rollback_events"] else None,
            "production_performance": decision_record.production_measurements(lifecycle.load_history(history_path)),
        }
        learning = dict(metadata.get("learning") or {})
        learning["production_direction_learning_applied"] = False
        learning["production_admission_learning"] = True
        metadata["learning"] = learning
        output = _clone(output, metadata=metadata)
        production_policy.save_state(state_path, policy)
        decision_record.save_ledger(ledger_path, ledger)
        output_path.write_text(json.dumps(output.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return output
    finally:
        _cycle_context = previous_context


def _open_output_v19(
    candidate: DailyEngineOutput,
    position: Mapping[str, Any],
    mark_price: float,
) -> DailyEngineOutput:
    """Preserve legacy position facts while projecting them through v1.9 runtime."""
    output = _v18_open_output(candidate, position, mark_price)
    metadata = dict(output.metadata)
    metadata["runtime_projection"] = {
        "engine_version": ENGINE_VERSION,
        "decision_mode": "WITH",
        "historical_position_rewritten": False,
    }
    return _clone(output, metadata=metadata)


def _closed_output_v19(
    candidate: DailyEngineOutput,
    trade: Mapping[str, Any],
    history: Mapping[str, Any],
) -> DailyEngineOutput:
    output = _v18_closed_output(candidate, trade, history)
    metadata = dict(output.metadata)
    metadata["runtime_projection"] = {
        "engine_version": ENGINE_VERSION,
        "decision_mode": "WITH",
        "historical_trade_rewritten": False,
    }
    return _clone(output, metadata=metadata)


def _fresh_policy_shadow(
    candidate: DailyEngineOutput,
    snapshot: Any,
    monitor_bars: Any,
    observed_at: datetime,
) -> DailyEngineOutput:
    metadata = dict(candidate.metadata)
    metadata["contextual_entry_policy"] = {
        "mode": "LEGACY_V18_CONTEXTUAL_LEARNER",
        "status": "SHADOW_AFTER_BELIEF_FIRST_MIGRATION",
        "decision_influence": False,
        "direction_mutation_allowed": False,
        "timing_mutation_allowed": False,
        "reason": "Legacy learner was trained under pre-v1.9 decision semantics; production authority reset prospectively.",
        "FSE_production_influence": False,
    }
    return _clone(candidate, metadata=metadata)


def _resume_pending_v19(
    candidate: DailyEngineOutput,
    pending: Mapping[str, Any],
    snapshot: Any,
    monitor_bars: Any,
    observed_at: datetime,
) -> DailyEngineOutput:
    metadata = dict(candidate.metadata)
    metadata["contextual_entry_policy"] = {
        "mode": "LEGACY_V18_CONTEXTUAL_LEARNER",
        "status": "LEGACY_PENDING_CANCELLED_ON_V19_ACTIVATION",
        "decision_influence": False,
        "direction_mutation_allowed": False,
        "timing_mutation_allowed": False,
        "prior_pending_cancelled": True,
        "cancelled_policy_id": pending.get("policy_id"),
        "cancellation_reason": "belief_first_semantics_changed_prospectively",
        "historical_state_rewritten": False,
    }
    return _clone(candidate, metadata=metadata)


def _install() -> None:
    # v18 retains lifecycle/open-position management. Replace only the
    # new-entry decision/timing hooks with the Belief-first v1.9 contract.
    v18.build_output = build_output
    v18._clone = _clone
    v18._fresh_policy = _fresh_policy_shadow
    v18._resume_pending = _resume_pending_v19
    v18._original_prepare_entry_candidate = prepare_entry_candidate
    v18._open_output = _open_output_v19
    v18._closed_output = _closed_output_v19
    v18._create_position = _create_position_v19

    base.ENGINE_VERSION = ENGINE_VERSION
    base.fetch_snapshot = fetch_snapshot
    base.build_output = build_output
    base.prepare_entry_candidate = prepare_entry_candidate
    base._open_output = _open_output_v19
    base._closed_output = _closed_output_v19
    base.create_position = _create_position_v19
    base.run_cycle = run_cycle


_install()


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())

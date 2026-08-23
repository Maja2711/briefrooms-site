#!/usr/bin/env python3
"""PR29 — Counterfactual Decision & Gate Diagnostics.

One read-only, prospective diagnostics contract for BriefRooms decision engines.
It freezes what alternatives and gates actually existed at decision time, links
later outcomes only to snapshots captured on an earlier collector cycle, and
builds descriptive gate/FLAT diagnostics.  It never changes an engine decision,
weight, threshold, belief, causal edge, ranking, sizing or execution state.

The module writes only ``learning_observation`` events into the PR27 Learning
Ledger.  Raw PR29 state therefore inherits the ledger hash-chain and PR28 private
artifact durability.  Missing point-in-time state is explicitly marked as
insufficient; LONG/SHORT alternatives and risk plans are never invented after
the outcome is known.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Mapping, Optional, Sequence

try:
    from learning_ledger import append_event, read_events, safety_controls as ledger_safety, verify_chain
except ModuleNotFoundError:  # pragma: no cover - package-style execution
    from scripts.learning_ledger import append_event, read_events, safety_controls as ledger_safety, verify_chain

SCHEMA_VERSION = "counterfactual-decision-gate-v1"
ACTIVATION_FILENAME = "counterfactual_activation.json"
DIAGNOSTICS_FILENAME = "counterfactual_diagnostics.json"
STATUS_FILENAME = "counterfactual_status.json"
LEDGER_FILENAME = "learning_ledger.jsonl"

SNAPSHOT_OBSERVATION = "counterfactual_decision_snapshot"
OUTCOME_OBSERVATION = "counterfactual_candidate_outcome"

ENGINE_REGISTRY: dict[str, dict[str, Any]] = {
    "gpw_daily": {
        "family": "daily_stock",
        "source": "data/investments/gpw_daily_pick.json",
        "actions": ["LONG", "FLAT"],
        "stage": "paper_research",
        "coverage": "selected_risk_plan_plus_gate_only_rejections",
    },
    "us_daily": {
        "family": "daily_stock",
        "source": "data/investments/us_daily_stock.json",
        "actions": ["LONG", "FLAT"],
        "stage": "paper_research",
        "coverage": "selected_risk_plan_plus_gate_only_rejections",
    },
    "eurusd_daily": {
        "family": "daily_fx",
        "source": "data/investments/eurusd_daily_spot.json",
        "actions": ["LONG", "SHORT", "FLAT", "HOLD_OPEN"],
        "stage": "shadow",
        "coverage": "actual_position_plus_refresh_candidate_gates",
    },
    "wes": {
        "family": "weekly_multi_asset",
        "source": "data/investments/weekly/*.json",
        "actions": ["LONG", "SHORT", "FLAT"],
        "stage": "paper_research",
        "coverage": "rich_strategy_alternatives_directional_same_window_outcomes",
        "instruments": ["eurusd", "sp500_futures", "btcusd"],
    },
    "brace_portfolio_10k": {
        "family": "portfolio",
        "source": "data/portfolio10k/pending_decisions.json",
        "actions": ["HOLD", "WATCH", "REDUCE", "ADD", "REPLACE"],
        "stage": "paper_proposal",
        "coverage": "recommendations_and_explicit_decision_checks",
    },
    "brace_spx_g6": {
        "family": "long_view_research",
        "source": "data/public/brace_spx_generation6_public.json",
        "actions": ["WARMUP", "SHADOW_ACTIVE"],
        "stage": "research_shadow",
        "coverage": "research_gate_only_no_trade_counterfactual",
    },
}

UPSTREAM_NON_DECISION_LAYERS: dict[str, str] = {
    "belief_core": "forecast_and_verification_layer_already_collected_by_PR28",
    "gse": "upstream_geopolitical_forecast_layer_not_an_economic_decision_engine",
}

ALLOWED_SETTLEMENT_MODES = {
    "risk_plan",
    "directional_market_return",
    "directional_same_window",
    "portfolio_relative",
    "research_shadow",
    "flat_zero",
    "insufficient_counterfactual_state",
}


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}-{_sha([str(part) for part in parts])[:24]}"


def _parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("timestamp is required")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        name = handle.name
    Path(name).replace(path)


def safety_controls() -> dict[str, bool]:
    controls = dict(ledger_safety())
    controls.update(
        {
            "source_engine_writeback": False,
            "counterfactual_direction_synthesis": False,
            "risk_plan_reconstruction_after_outcome": False,
            "historical_backfill": False,
            "same_cycle_snapshot_outcome_binding": False,
            "gate_threshold_writeback": False,
            "flat_policy_writeback": False,
            "decision_influence": False,
        }
    )
    return controls


def _assert_safety() -> None:
    bad = [key for key, value in safety_controls().items() if value is not False]
    if bad:
        raise RuntimeError("PR29 zero-authority invariant violated: " + ",".join(bad))


def _pr29_events(events: Sequence[Mapping[str, Any]], observation_type: Optional[str] = None) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for row in events:
        if row.get("event_type") != "learning_observation":
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        kind = str(payload.get("observation_type") or "")
        if not kind.startswith("counterfactual_"):
            continue
        if observation_type and kind != observation_type:
            continue
        rows.append(row)
    return rows


def ensure_activation(state_dir: Path, ledger: Path, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Create the PR29 boundary once; never recreate it after PR29 history exists."""
    path = state_dir / ACTIVATION_FILENAME
    existing = _load_json(path)
    if isinstance(existing, dict):
        if existing.get("schema_version") != SCHEMA_VERSION or not existing.get("activated_at"):
            raise RuntimeError("invalid PR29 activation state")
        _parse_time(str(existing["activated_at"]))
        return existing

    events = read_events(ledger)
    if _pr29_events(events):
        raise RuntimeError("PR29 activation missing while PR29 ledger history exists; FAIL_CLOSED")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "activated_at": _iso_z(now or datetime.now(timezone.utc)),
        "anti_hindsight": {
            "historical_backfill": False,
            "snapshot_required_before_outcome": True,
            "same_cycle_snapshot_outcome_binding": False,
            "missing_alternative_policy": "INSUFFICIENT_COUNTERFACTUAL_STATE",
        },
        "zero_authority": safety_controls(),
    }
    _atomic_json(path, payload)
    return payload


def gate_snapshot(
    name: str,
    *,
    passed: bool,
    reason: str = "",
    hard: bool = True,
    gate_type: str = "admission",
) -> dict[str, Any]:
    return {
        "name": str(name),
        "passed": bool(passed),
        "hard": bool(hard),
        "gate_type": str(gate_type),
        "reason": str(reason or ""),
    }


def candidate_snapshot(
    candidate_id: str,
    *,
    action: str,
    selected: bool,
    settlement_mode: str,
    score: Any = None,
    confidence: Any = None,
    market_symbol: Optional[str] = None,
    reference_price: Any = None,
    entry: Any = None,
    stop: Any = None,
    target: Any = None,
    reward_risk: Any = None,
    gates: Optional[Iterable[Mapping[str, Any]]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    if settlement_mode not in ALLOWED_SETTLEMENT_MODES:
        raise ValueError(f"unsupported settlement_mode: {settlement_mode}")
    return {
        "candidate_id": str(candidate_id),
        "action": str(action).upper(),
        "selected": bool(selected),
        "settlement_mode": settlement_mode,
        "score": _finite(score),
        "confidence": _finite(confidence),
        "market_symbol": market_symbol,
        "reference_price": _finite(reference_price),
        "entry": _finite(entry),
        "stop": _finite(stop),
        "target": _finite(target),
        "reward_risk": _finite(reward_risk),
        "gates": [dict(row) for row in (gates or [])],
        "metadata": dict(metadata or {}),
    }


def make_snapshot(
    *,
    engine_id: str,
    decision_id: str,
    decision_at: str,
    actual_action: str,
    decision_stage: str,
    source_ref: str,
    candidates: Sequence[Mapping[str, Any]],
    instrument_id: Optional[str] = None,
    upstream_subject_id: Optional[str] = None,
    target_at: Optional[str] = None,
    coverage: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    if engine_id not in ENGINE_REGISTRY:
        raise ValueError(f"unknown PR29 engine: {engine_id}")
    _parse_time(decision_at)
    if target_at:
        _parse_time(target_at)
    rows = [dict(row) for row in candidates]
    ids = [str(row.get("candidate_id") or "") for row in rows]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("candidate ids must be unique and non-empty")
    for row in rows:
        if row.get("settlement_mode") not in ALLOWED_SETTLEMENT_MODES:
            raise ValueError("candidate has invalid settlement mode")
        for gate in row.get("gates") or []:
            if not str(gate.get("name") or "") or not isinstance(gate.get("passed"), bool):
                raise ValueError("invalid gate snapshot")
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": _stable_id("cfsnap", engine_id, decision_id, instrument_id or ""),
        "engine_id": engine_id,
        "engine_family": ENGINE_REGISTRY[engine_id]["family"],
        "decision_id": str(decision_id),
        "decision_at": _iso_z(_parse_time(decision_at)),
        "actual_action": str(actual_action).upper(),
        "decision_stage": str(decision_stage),
        "instrument_id": instrument_id,
        "source_ref": str(source_ref),
        "upstream_subject_id": upstream_subject_id,
        "target_at": None if not target_at else _iso_z(_parse_time(target_at)),
        "coverage": coverage or ENGINE_REGISTRY[engine_id]["coverage"],
        "candidates": rows,
        "metadata": dict(metadata or {}),
        "anti_hindsight": {
            "captured_prospectively": True,
            "unseen_directions_synthesized": False,
            "risk_plan_reconstructed_after_outcome": False,
        },
    }
    body["snapshot_sha256"] = _sha(body)
    return body


def validate_snapshot(snapshot: Mapping[str, Any]) -> None:
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("snapshot schema mismatch")
    _parse_time(str(snapshot.get("decision_at") or ""))
    body = dict(snapshot)
    stored = str(body.pop("snapshot_sha256", ""))
    if not stored or stored != _sha(body):
        raise ValueError("snapshot hash mismatch")
    if snapshot.get("engine_id") not in ENGINE_REGISTRY:
        raise ValueError("snapshot engine is not registered")
    seen: set[str] = set()
    for candidate in snapshot.get("candidates") or []:
        cid = str(candidate.get("candidate_id") or "")
        if not cid or cid in seen:
            raise ValueError("duplicate/empty candidate id")
        seen.add(cid)
        if candidate.get("settlement_mode") not in ALLOWED_SETTLEMENT_MODES:
            raise ValueError("invalid settlement mode")


def _normalize_stock_action(decision: str) -> str:
    text = str(decision or "").upper()
    if text in {"TRANSAKCJA", "TRADE"}:
        return "LONG"
    if text in {"BRAK_TRANSAKCJI", "NO_TRADE", "FLAT"}:
        return "FLAT"
    return "NO_DECISION"


def _flat_candidate(engine: str, decision_id: str) -> dict[str, Any]:
    return candidate_snapshot(
        f"{decision_id}:FLAT",
        action="FLAT",
        selected=True,
        settlement_mode="flat_zero",
        metadata={"engine": engine, "economic_return_percent": 0.0},
    )


def adapt_daily_stock(payload: Mapping[str, Any], *, market: str) -> list[dict[str, Any]]:
    engine_id = "us_daily" if market == "us" else "gpw_daily"
    decision_at = str(payload.get("generated_at") or "")
    day = str(payload.get("date") or "unknown-date")
    if not decision_at:
        return []
    actual_action = _normalize_stock_action(str(payload.get("decision") or ""))
    decision_id = f"{engine_id}:{day}"
    selection = payload.get("selection") if isinstance(payload.get("selection"), Mapping) else {}
    candidates: list[dict[str, Any]] = []

    if actual_action == "LONG" and selection:
        symbol = str(selection.get("symbol") or selection.get("ticker") or "UNKNOWN")
        candidates.append(
            candidate_snapshot(
                f"{decision_id}:{symbol}:LONG",
                action="LONG",
                selected=True,
                settlement_mode="risk_plan",
                score=selection.get("score"),
                confidence=selection.get("confidence"),
                market_symbol=symbol,
                reference_price=selection.get("reference_price"),
                entry=selection.get("reference_price"),
                stop=selection.get("stop"),
                target=selection.get("target"),
                reward_risk=selection.get("reward_risk"),
                gates=[gate_snapshot("admission", passed=True, reason="selected_by_engine")],
                metadata={"sector": selection.get("sector"), "conviction": selection.get("conviction")},
            )
        )
    elif actual_action == "FLAT":
        candidates.append(_flat_candidate(engine_id, decision_id))

    quality = payload.get("data_quality") if isinstance(payload.get("data_quality"), Mapping) else {}
    rejected: dict[str, tuple[str, str]] = {}
    for symbol, reason in (quality.get("screened_out") or {}).items():
        rejected[str(symbol)] = ("liquidity_atr_or_risk", str(reason))
    for symbol, reason in (quality.get("analysis_rejections") or {}).items():
        rejected[str(symbol)] = (str(reason).split(":", 1)[0] or "analysis", str(reason))
    review_rejections = quality.get("review_rejections") or []
    if isinstance(review_rejections, Sequence) and not isinstance(review_rejections, (str, bytes)):
        for row in review_rejections:
            if isinstance(row, Mapping) and row.get("symbol"):
                rejected[str(row["symbol"])] = ("independent_review", str(row.get("reason") or "review_rejected"))

    selected_symbol = str(selection.get("symbol") or "")
    for symbol, (gate_name, reason) in sorted(rejected.items()):
        if symbol == selected_symbol:
            continue
        candidates.append(
            candidate_snapshot(
                f"{decision_id}:{symbol}:LONG",
                action="LONG",
                selected=False,
                settlement_mode="insufficient_counterfactual_state",
                market_symbol=symbol,
                gates=[gate_snapshot(gate_name, passed=False, reason=reason, hard=True)],
                metadata={
                    "missing_for_full_counterfactual": ["frozen_reference_price", "frozen_risk_plan"],
                    "diagnostic_use": "gate_frequency_until_producer_exposes_point_in_time_candidate",
                },
            )
        )

    upstream_subject = None
    position = payload.get("position") if isinstance(payload.get("position"), Mapping) else {}
    if market == "us" and position.get("position_id"):
        upstream_subject = str(position["position_id"])
    else:
        symbol = str(selection.get("symbol") or selection.get("ticker") or "NO_TRADE")
        upstream_subject = f"{market}:{day}:{symbol}"

    target_at = None
    if selection.get("valid_until"):
        # Date-only horizons are retained as metadata; inventing a market-close
        # timestamp would be an unnecessary semantic assumption.
        target_at = None

    return [
        make_snapshot(
            engine_id=engine_id,
            decision_id=decision_id,
            decision_at=decision_at,
            actual_action=actual_action,
            decision_stage=ENGINE_REGISTRY[engine_id]["stage"],
            source_ref=ENGINE_REGISTRY[engine_id]["source"],
            candidates=candidates,
            instrument_id=str(selection.get("symbol") or selection.get("ticker") or "MARKET"),
            upstream_subject_id=upstream_subject,
            target_at=target_at,
            metadata={
                "reason": payload.get("reason"),
                "policy_version": payload.get("policy_version"),
                "date": payload.get("date"),
                "provider_failures": dict(quality.get("provider_failures") or {}),
            },
        )
    ]


def adapt_eurusd(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    position = metadata.get("position") if isinstance(metadata.get("position"), Mapping) else {}
    rows: list[dict[str, Any]] = []

    trade_id = str(position.get("trade_id") or "")
    opened_at = str(position.get("opened_at") or "")
    if trade_id and opened_at and str(position.get("status") or "").upper() == "OPEN":
        direction = str(position.get("direction") or payload.get("direction") or "").upper()
        rows.append(
            make_snapshot(
                engine_id="eurusd_daily",
                decision_id=trade_id,
                decision_at=opened_at,
                actual_action=direction,
                decision_stage="shadow_position",
                source_ref=ENGINE_REGISTRY["eurusd_daily"]["source"],
                upstream_subject_id=trade_id,
                instrument_id="EURUSD",
                target_at=str(position.get("expires_at") or "") or None,
                candidates=[
                    candidate_snapshot(
                        f"{trade_id}:{direction}",
                        action=direction,
                        selected=True,
                        settlement_mode="risk_plan",
                        score=position.get("entry_score"),
                        confidence=position.get("entry_confidence"),
                        market_symbol="EURUSD=X",
                        reference_price=position.get("entry"),
                        entry=position.get("entry"),
                        stop=position.get("stop"),
                        target=position.get("target"),
                        gates=[gate_snapshot("entry_admission", passed=True, reason="position_opened")],
                        metadata={"engine_version": position.get("engine_version")},
                    )
                ],
                metadata={"decision_mode": payload.get("decision_mode")},
            )
        )

    refresh_at = str(payload.get("timestamp") or "")
    candidate = metadata.get("candidate_at_refresh")
    if not isinstance(candidate, Mapping):
        candidate = metadata.get("candidate") if isinstance(metadata.get("candidate"), Mapping) else {}
    if refresh_at and candidate:
        direction = str(candidate.get("direction") or "FLAT").upper()
        accepted = bool(candidate.get("accepted"))
        actual_action = direction if accepted and direction in {"LONG", "SHORT"} else ("HOLD_OPEN" if trade_id else "FLAT")
        refresh_id = f"eurusd-refresh:{_iso_z(_parse_time(refresh_at))}"
        refresh_candidates: list[dict[str, Any]] = []
        if actual_action in {"FLAT", "HOLD_OPEN"}:
            refresh_candidates.append(_flat_candidate("eurusd_daily", refresh_id))
        mark = position.get("mark_price") if position else payload.get("entry")
        reasons = [str(value) for value in (candidate.get("gate_reasons") or [])]
        if direction in {"LONG", "SHORT"}:
            refresh_candidates.append(
                candidate_snapshot(
                    f"{refresh_id}:{direction}",
                    action=direction,
                    selected=accepted,
                    settlement_mode="directional_market_return" if _finite(mark) is not None else "insufficient_counterfactual_state",
                    score=candidate.get("score"),
                    confidence=candidate.get("confidence"),
                    market_symbol="EURUSD=X",
                    reference_price=mark,
                    gates=[gate_snapshot(reason, passed=False, reason=reason) for reason in reasons]
                    or [gate_snapshot("signal_admission", passed=accepted, reason="candidate_refresh")],
                    metadata={"accepted": accepted, "raw_score": candidate.get("raw_score")},
                )
            )
        elif direction == "FLAT":
            # FLAT is an observed candidate, not a fabricated opposite direction.
            refresh_candidates[0]["score"] = _finite(candidate.get("score"))
            refresh_candidates[0]["confidence"] = _finite(candidate.get("confidence"))
            refresh_candidates[0]["gates"] = [gate_snapshot(reason, passed=False, reason=reason) for reason in reasons]
        target_at = _iso_z(_parse_time(refresh_at) + timedelta(hours=24))
        rows.append(
            make_snapshot(
                engine_id="eurusd_daily",
                decision_id=refresh_id,
                decision_at=refresh_at,
                actual_action=actual_action,
                decision_stage="shadow_refresh",
                source_ref=ENGINE_REGISTRY["eurusd_daily"]["source"],
                candidates=refresh_candidates,
                instrument_id="EURUSD",
                target_at=target_at,
                metadata={"position_open": bool(trade_id), "decision_mode": payload.get("decision_mode")},
            )
        )
    return rows


def _latest_week_payload(investments_dir: Path) -> tuple[Optional[Path], Optional[Mapping[str, Any]]]:
    weekly_dir = investments_dir / "weekly"
    paths = sorted(weekly_dir.glob("????-W??.json"))
    if not paths:
        return None, None
    path = paths[-1]
    payload = _load_json(path)
    return (path, payload) if isinstance(payload, Mapping) else (None, None)


def adapt_wes(path: Path, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    week_id = str(payload.get("week_id") or path.stem)
    forecast_at = str(payload.get("forecast_locked_at") or payload.get("forecast_created_at") or "")
    target_at = str((payload.get("market_window") or {}).get("exit_target_local") or "")
    rows: list[dict[str, Any]] = []
    for item in payload.get("instruments") or []:
        if not isinstance(item, Mapping):
            continue
        instrument_id = str(item.get("instrument_id") or "")
        if not instrument_id:
            continue
        decision = item.get("continuous_entry_decision") if isinstance(item.get("continuous_entry_decision"), Mapping) else {}
        strategy_id = str(decision.get("strategy_id") or "")
        direction = str(decision.get("direction") or item.get("direction") or "neutral").upper()
        if direction == "NEUTRAL":
            direction = "FLAT"
        decision_at = str(item.get("entry_captured_at") or forecast_at)
        if not decision_at:
            continue
        decision_id = f"wes:{week_id}:{instrument_id}"
        candidates: list[dict[str, Any]] = []
        candidate_map = decision.get("candidates") if isinstance(decision.get("candidates"), Mapping) else {}
        for candidate_id, raw in candidate_map.items():
            if not isinstance(raw, Mapping):
                continue
            action = str(raw.get("direction") or "neutral").upper()
            if action == "NEUTRAL":
                action = "FLAT"
            selected = str(candidate_id) == strategy_id
            plan = item.get("risk_plan") if selected and isinstance(item.get("risk_plan"), Mapping) else {}
            settlement_mode = "risk_plan" if selected and plan and action in {"LONG", "SHORT"} else (
                "flat_zero" if action == "FLAT" else "directional_same_window"
            )
            candidates.append(
                candidate_snapshot(
                    f"{decision_id}:{candidate_id}",
                    action=action,
                    selected=selected,
                    settlement_mode=settlement_mode,
                    score=raw.get("raw_score"),
                    confidence=raw.get("conviction"),
                    market_symbol=str(item.get("symbol") or ""),
                    reference_price=item.get("entry_price"),
                    entry=item.get("entry_price"),
                    stop=plan.get("stop_loss_price"),
                    target=plan.get("take_profit_price"),
                    reward_risk=plan.get("reward_to_risk"),
                    gates=[
                        gate_snapshot(
                            "strategy_utility_selection",
                            passed=selected,
                            reason="selected_highest_governed_utility" if selected else "strategy_not_selected",
                            hard=False,
                            gate_type="selection",
                        )
                    ],
                    metadata={
                        "strategy_id": candidate_id,
                        "utility": raw.get("utility"),
                        "base_conviction": raw.get("base_conviction"),
                        "exploration_bonus": raw.get("exploration_bonus"),
                    },
                )
            )
        if not candidates:
            candidates.append(
                candidate_snapshot(
                    f"{decision_id}:{direction}",
                    action=direction,
                    selected=True,
                    settlement_mode="risk_plan" if item.get("risk_plan") else "insufficient_counterfactual_state",
                    score=item.get("score"),
                    confidence=item.get("confidence"),
                    market_symbol=str(item.get("symbol") or ""),
                    reference_price=item.get("entry_price"),
                    entry=item.get("entry_price"),
                    gates=[gate_snapshot("wes_admission", passed=True, reason=str(item.get("validation_gate") or ""))],
                )
            )
        rows.append(
            make_snapshot(
                engine_id="wes",
                decision_id=decision_id,
                decision_at=decision_at,
                actual_action=direction,
                decision_stage="paper_position" if item.get("entry_price") else "forecast",
                source_ref=path.as_posix(),
                candidates=candidates,
                instrument_id=instrument_id,
                target_at=target_at or None,
                metadata={
                    "week_id": week_id,
                    "strategy_id": strategy_id,
                    "trade_status": item.get("trade_status"),
                    "validation_gate": item.get("validation_gate"),
                },
            )
        )
    return rows


def adapt_brace_portfolio(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source_ref = ENGINE_REGISTRY["brace_portfolio_10k"]["source"]
    top_generated = str(payload.get("generated_at") or "")
    for rec in payload.get("recommendations") or []:
        if not isinstance(rec, Mapping) or not rec.get("instrument") or not top_generated:
            continue
        instrument = str(rec["instrument"])
        action = str(rec.get("action") or "HOLD").upper()
        decision_id = f"brace-rec:{top_generated}:{instrument}"
        factor_gates = [
            gate_snapshot(str(name), passed=False, reason="negative_factor", hard=False, gate_type="factor")
            for name in rec.get("negative_factors") or []
        ]
        rows.append(
            make_snapshot(
                engine_id="brace_portfolio_10k",
                decision_id=decision_id,
                decision_at=top_generated,
                actual_action=action,
                decision_stage="recommendation",
                source_ref=source_ref,
                instrument_id=instrument,
                candidates=[
                    candidate_snapshot(
                        f"{decision_id}:{action}",
                        action=action,
                        selected=True,
                        settlement_mode="portfolio_relative",
                        score=rec.get("final_score"),
                        confidence=rec.get("confidence"),
                        market_symbol=str(rec.get("broker_symbol") or ""),
                        reference_price=rec.get("signal_price"),
                        gates=factor_gates,
                        metadata={
                            "current_weight": rec.get("current_weight"),
                            "proposed_weight": rec.get("proposed_weight"),
                            "positive_factors": list(rec.get("positive_factors") or []),
                            "conditions_for_change": list(rec.get("conditions_for_change") or []),
                        },
                    )
                ],
                metadata={"methodology_version": payload.get("methodology_version")},
            )
        )

    for decision in payload.get("decisions") or []:
        if not isinstance(decision, Mapping) or not decision.get("decision_id"):
            continue
        decision_at = str(decision.get("generated_at") or top_generated)
        if not decision_at:
            continue
        decision_id = str(decision["decision_id"])
        action = str(decision.get("action") or "PROPOSAL").upper()
        checks = decision.get("checks") if isinstance(decision.get("checks"), Mapping) else {}
        gates = [
            gate_snapshot(str(name), passed=bool(value), reason="explicit_BRACE_check", hard=True)
            for name, value in sorted(checks.items())
        ]
        instrument = str(decision.get("instrument") or "")
        replacement = str(decision.get("replacement_instrument") or "")
        market_symbol = replacement or instrument
        rows.append(
            make_snapshot(
                engine_id="brace_portfolio_10k",
                decision_id=decision_id,
                decision_at=decision_at,
                actual_action=action,
                decision_stage="proposal" if str(decision.get("status") or "").upper() == "PROPOSED" else "paper_control",
                source_ref=source_ref,
                instrument_id=instrument or replacement or "portfolio",
                candidates=[
                    candidate_snapshot(
                        f"{decision_id}:{action}:{market_symbol or 'portfolio'}",
                        action=action,
                        selected=True,
                        settlement_mode="portfolio_relative",
                        confidence=decision.get("confidence"),
                        market_symbol=market_symbol or None,
                        reference_price=decision.get("replacement_signal_price") or decision.get("signal_price"),
                        gates=gates,
                        metadata={
                            "status": decision.get("status"),
                            "replacement_instrument": replacement or None,
                            "expected_benefit": decision.get("expected_benefit"),
                            "expected_risk": decision.get("expected_risk"),
                            "current_weight": decision.get("current_weight"),
                            "proposed_weight": decision.get("proposed_weight"),
                            "learning_eligible": decision.get("learning_eligible"),
                        },
                    )
                ],
                metadata={"methodology_version": decision.get("methodology_version") or payload.get("methodology_version")},
            )
        )
    return rows


def adapt_brace_spx(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    generated_at = str(payload.get("generated_at") or "")
    if not generated_at:
        return []
    shadow = payload.get("shadow") if isinstance(payload.get("shadow"), Mapping) else {}
    development = payload.get("development") if isinstance(payload.get("development"), Mapping) else {}
    sealed = payload.get("sealed_holdout") if isinstance(payload.get("sealed_holdout"), Mapping) else {}
    status = str(shadow.get("status") or "warming_up")
    actual_action = "SHADOW_ACTIVE" if status == "shadow_active_no_orders" else "WARMUP"
    decision_id = f"brace-spx-g6:{generated_at}"
    gates = [
        gate_snapshot("development_strict_gate", passed=bool(development.get("strict_gate_passed")), reason=str(development.get("status") or ""), hard=True, gate_type="research"),
        gate_snapshot("shadow_warmup_complete", passed=int(shadow.get("observations_collected") or 0) >= int(shadow.get("warmup_required") or 0), reason=status, hard=True, gate_type="research"),
        gate_snapshot("holdout_unaccessed", passed=not bool(sealed.get("accessed")), reason="sealed_holdout_integrity", hard=True, gate_type="research"),
    ]
    return [
        make_snapshot(
            engine_id="brace_spx_g6",
            decision_id=decision_id,
            decision_at=generated_at,
            actual_action=actual_action,
            decision_stage="research_shadow",
            source_ref=ENGINE_REGISTRY["brace_spx_g6"]["source"],
            instrument_id="SPX",
            candidates=[
                candidate_snapshot(
                    f"{decision_id}:{actual_action}",
                    action=actual_action,
                    selected=True,
                    settlement_mode="research_shadow",
                    gates=gates,
                    metadata={
                        "candidate_space_size": payload.get("candidate_space_size"),
                        "candidate_signature": payload.get("candidate_signature"),
                        "orders_allowed": (payload.get("governance") or {}).get("orders_allowed"),
                    },
                )
            ],
            metadata={"research_only": True, "live_activation": False},
        )
    ]


def collect_engine_snapshots(repo_root: Path) -> list[dict[str, Any]]:
    investments = repo_root / "data" / "investments"
    rows: list[dict[str, Any]] = []
    gpw = _load_json(investments / "gpw_daily_pick.json")
    if isinstance(gpw, Mapping):
        rows.extend(adapt_daily_stock(gpw, market="gpw"))
    us = _load_json(investments / "us_daily_stock.json")
    if isinstance(us, Mapping):
        rows.extend(adapt_daily_stock(us, market="us"))
    eurusd = _load_json(investments / "eurusd_daily_spot.json")
    if isinstance(eurusd, Mapping):
        rows.extend(adapt_eurusd(eurusd))
    week_path, week = _latest_week_payload(investments)
    if week_path and isinstance(week, Mapping):
        rows.extend(adapt_wes(week_path.relative_to(repo_root), week))
    portfolio = _load_json(repo_root / "data" / "portfolio10k" / "pending_decisions.json")
    if isinstance(portfolio, Mapping):
        rows.extend(adapt_brace_portfolio(portfolio))
    spx = _load_json(repo_root / "data" / "public" / "brace_spx_generation6_public.json")
    if isinstance(spx, Mapping):
        rows.extend(adapt_brace_spx(spx))
    for row in rows:
        validate_snapshot(row)
    return rows


def _snapshot_events(events: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for event in _pr29_events(events, SNAPSHOT_OBSERVATION):
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), Mapping) else {}
        sid = str(snapshot.get("snapshot_id") or "")
        if sid:
            out[sid] = event
    return out


def _outcome_key(snapshot_id: str, candidate_id: str) -> str:
    return f"{snapshot_id}|{candidate_id}"


def _outcome_events(events: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for event in _pr29_events(events, OUTCOME_OBSERVATION):
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        sid = str(payload.get("snapshot_id") or "")
        cid = str(payload.get("candidate_id") or "")
        if sid and cid:
            out[_outcome_key(sid, cid)] = event
    return out


def append_snapshot(ledger: Path, snapshot: Mapping[str, Any], *, existing: set[str]) -> bool:
    validate_snapshot(snapshot)
    sid = str(snapshot["snapshot_id"])
    if sid in existing:
        return False
    append_event(
        ledger,
        event_type="learning_observation",
        occurred_at=str(snapshot["decision_at"]),
        subject_id=sid,
        source_ref=str(snapshot.get("source_ref") or ""),
        payload={
            "observation_type": SNAPSHOT_OBSERVATION,
            "snapshot": dict(snapshot),
            "zero_authority": safety_controls(),
        },
    )
    existing.add(sid)
    return True


def append_candidate_outcome(
    ledger: Path,
    *,
    snapshot: Mapping[str, Any],
    candidate_id: str,
    occurred_at: str,
    return_percent: Optional[float],
    r_multiple: Optional[float] = None,
    exit_reason: Optional[str] = None,
    settlement_mode: Optional[str] = None,
    source_ref: str = "",
    metadata: Optional[Mapping[str, Any]] = None,
    existing_outcomes: Optional[set[str]] = None,
) -> bool:
    """Public PR29 settler API for any existing/future engine monitor.

    The caller must pass a snapshot that was already in the ledger before the
    current collector cycle.  The orchestration layer enforces that invariant;
    this function additionally verifies candidate identity and snapshot hash.
    """
    validate_snapshot(snapshot)
    candidate = next((row for row in snapshot.get("candidates") or [] if row.get("candidate_id") == candidate_id), None)
    if candidate is None:
        raise KeyError(f"candidate {candidate_id} is not in frozen snapshot")
    mode = settlement_mode or str(candidate.get("settlement_mode") or "")
    if mode not in ALLOWED_SETTLEMENT_MODES or mode == "insufficient_counterfactual_state":
        raise ValueError("candidate cannot be settled without legitimate frozen counterfactual state")
    key = _outcome_key(str(snapshot["snapshot_id"]), candidate_id)
    if existing_outcomes is not None and key in existing_outcomes:
        return False
    occurred = _iso_z(_parse_time(occurred_at))
    if _parse_time(occurred) < _parse_time(str(snapshot["decision_at"])):
        raise ValueError("counterfactual outcome predates decision snapshot")
    append_event(
        ledger,
        event_type="learning_observation",
        occurred_at=occurred,
        subject_id=_stable_id("cfout", snapshot["snapshot_id"], candidate_id),
        source_ref=source_ref or str(snapshot.get("source_ref") or ""),
        payload={
            "observation_type": OUTCOME_OBSERVATION,
            "snapshot_id": snapshot["snapshot_id"],
            "candidate_id": candidate_id,
            "engine_id": snapshot["engine_id"],
            "instrument_id": snapshot.get("instrument_id"),
            "action": candidate.get("action"),
            "selected": bool(candidate.get("selected")),
            "settlement_mode": mode,
            "return_percent": None if return_percent is None else round(float(return_percent), 8),
            "r_multiple": None if r_multiple is None else round(float(r_multiple), 8),
            "exit_reason": exit_reason,
            "metadata": dict(metadata or {}),
            "zero_authority": safety_controls(),
        },
    )
    if existing_outcomes is not None:
        existing_outcomes.add(key)
    return True


def settle_from_pr28(
    ledger: Path,
    *,
    all_events: Sequence[Mapping[str, Any]],
    preexisting_snapshots: Mapping[str, Mapping[str, Any]],
    existing_outcomes: set[str],
) -> int:
    """Bind PR28 actual trade outcomes to PR29 selected candidates only."""
    upstream: dict[str, Mapping[str, Any]] = {}
    for event in all_events:
        if event.get("event_type") == "outcome":
            upstream[str(event.get("subject_id") or "")] = event
    appended = 0
    for snapshot_event in preexisting_snapshots.values():
        payload = snapshot_event.get("payload") if isinstance(snapshot_event.get("payload"), Mapping) else {}
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), Mapping) else {}
        upstream_id = str(snapshot.get("upstream_subject_id") or "")
        if not upstream_id or upstream_id not in upstream:
            continue
        outcome_event = upstream[upstream_id]
        outcome_payload = outcome_event.get("payload") if isinstance(outcome_event.get("payload"), Mapping) else {}
        selected = [row for row in snapshot.get("candidates") or [] if bool(row.get("selected")) and row.get("action") not in {"FLAT", "HOLD_OPEN"}]
        if len(selected) != 1:
            continue
        candidate = selected[0]
        return_percent = _finite(outcome_payload.get("return_percent"))
        r_multiple = _finite(outcome_payload.get("r_multiple"))
        if return_percent is None and r_multiple is None:
            continue
        if append_candidate_outcome(
            ledger,
            snapshot=snapshot,
            candidate_id=str(candidate["candidate_id"]),
            occurred_at=str(outcome_event.get("occurred_at") or ""),
            return_percent=return_percent,
            r_multiple=r_multiple,
            exit_reason=str(outcome_payload.get("exit_reason") or "") or None,
            settlement_mode=str(candidate.get("settlement_mode") or "risk_plan"),
            source_ref=str(outcome_event.get("source_ref") or ""),
            metadata={"source": "PR28_actual_outcome"},
            existing_outcomes=existing_outcomes,
        ):
            appended += 1
    return appended


def _current_wes_by_instrument(repo_root: Path) -> dict[tuple[str, str], Mapping[str, Any]]:
    path, payload = _latest_week_payload(repo_root / "data" / "investments")
    if not path or not isinstance(payload, Mapping):
        return {}
    week_id = str(payload.get("week_id") or path.stem)
    return {
        (week_id, str(row.get("instrument_id") or "")): row
        for row in (payload.get("instruments") or [])
        if isinstance(row, Mapping) and row.get("instrument_id")
    }


def settle_wes(
    ledger: Path,
    *,
    repo_root: Path,
    preexisting_snapshots: Mapping[str, Mapping[str, Any]],
    existing_outcomes: set[str],
) -> int:
    """Evaluate frozen WES strategy directions on the same observed entry/exit window."""
    current = _current_wes_by_instrument(repo_root)
    appended = 0
    for event in preexisting_snapshots.values():
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), Mapping) else {}
        if snapshot.get("engine_id") != "wes":
            continue
        week_id = str((snapshot.get("metadata") or {}).get("week_id") or "")
        instrument_id = str(snapshot.get("instrument_id") or "")
        item = current.get((week_id, instrument_id))
        if not item or str(item.get("trade_status") or "").lower() != "closed":
            continue
        entry = _finite(item.get("entry_price"))
        exit_price = _finite(item.get("exit_price"))
        occurred_at = str(item.get("exit_captured_at") or "")
        if entry is None or exit_price is None or entry == 0 or not occurred_at:
            continue
        base_return = (exit_price - entry) / entry * 100.0
        actual_direction = str(item.get("direction") or "").upper()
        actual_result = _finite(item.get("result_percent"))
        for candidate in snapshot.get("candidates") or []:
            cid = str(candidate.get("candidate_id") or "")
            if not cid or _outcome_key(str(snapshot["snapshot_id"]), cid) in existing_outcomes:
                continue
            action = str(candidate.get("action") or "").upper()
            mode = str(candidate.get("settlement_mode") or "")
            if mode == "insufficient_counterfactual_state":
                continue
            if action == "LONG":
                result = base_return
            elif action == "SHORT":
                result = -base_return
            elif action == "FLAT":
                result = 0.0
            else:
                continue
            if bool(candidate.get("selected")) and action == actual_direction and actual_result is not None:
                result = actual_result
            if append_candidate_outcome(
                ledger,
                snapshot=snapshot,
                candidate_id=cid,
                occurred_at=occurred_at,
                return_percent=result,
                exit_reason=str(item.get("exit_reason") or "") or None,
                settlement_mode="directional_same_window" if not bool(candidate.get("selected")) else mode,
                source_ref=f"data/investments/weekly/{week_id}.json",
                metadata={
                    "comparison_window": "same_frozen_entry_to_observed_exit",
                    "not_full_strategy_replay": not bool(candidate.get("selected")),
                },
                existing_outcomes=existing_outcomes,
            ):
                appended += 1
    return appended


def _metric(outcome_payload: Mapping[str, Any]) -> Optional[float]:
    value = _finite(outcome_payload.get("return_percent"))
    if value is not None:
        return value
    r_value = _finite(outcome_payload.get("r_multiple"))
    return r_value


def build_diagnostics(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    snapshots = _snapshot_events(events)
    outcomes = _outcome_events(events)
    snapshot_payloads: dict[str, Mapping[str, Any]] = {}
    candidate_outcomes: dict[tuple[str, str], Mapping[str, Any]] = {}
    for sid, event in snapshots.items():
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        snap = payload.get("snapshot") if isinstance(payload.get("snapshot"), Mapping) else {}
        snapshot_payloads[sid] = snap
    for event in outcomes.values():
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        candidate_outcomes[(str(payload.get("snapshot_id") or ""), str(payload.get("candidate_id") or ""))] = payload

    engine_stats: dict[str, dict[str, Any]] = {}
    gate_stats: dict[str, dict[str, Any]] = {}
    action_stats: dict[str, dict[str, Any]] = {}
    flat_rows: list[dict[str, Any]] = []

    for sid, snap in snapshot_payloads.items():
        engine_id = str(snap.get("engine_id") or "unknown")
        est = engine_stats.setdefault(engine_id, {"snapshots": 0, "candidates": 0, "evaluable_candidates": 0, "insufficient_candidates": 0})
        est["snapshots"] += 1
        candidates = [row for row in snap.get("candidates") or [] if isinstance(row, Mapping)]
        est["candidates"] += len(candidates)
        actual_action = str(snap.get("actual_action") or "UNKNOWN")
        ast = action_stats.setdefault(f"{engine_id}:{actual_action}", {"snapshots": 0, "selected_outcomes": 0, "positive_selected": 0, "returns": []})
        ast["snapshots"] += 1

        alternative_returns: list[float] = []
        for candidate in candidates:
            cid = str(candidate.get("candidate_id") or "")
            outcome = candidate_outcomes.get((sid, cid))
            metric = _metric(outcome) if outcome else None
            if candidate.get("settlement_mode") == "insufficient_counterfactual_state":
                est["insufficient_candidates"] += 1
            if metric is not None:
                est["evaluable_candidates"] += 1
                if not bool(candidate.get("selected")) and str(candidate.get("action") or "") in {"LONG", "SHORT", "ADD", "REPLACE"}:
                    alternative_returns.append(metric)
                if bool(candidate.get("selected")):
                    ast["selected_outcomes"] += 1
                    ast["returns"].append(metric)
                    if metric > 0:
                        ast["positive_selected"] += 1

            for gate in candidate.get("gates") or []:
                if not isinstance(gate, Mapping):
                    continue
                name = str(gate.get("name") or "unknown")
                key = f"{engine_id}:{name}"
                gst = gate_stats.setdefault(
                    key,
                    {
                        "engine_id": engine_id,
                        "gate": name,
                        "gate_type": gate.get("gate_type"),
                        "hard": bool(gate.get("hard")),
                        "observations": 0,
                        "blocked": 0,
                        "blocked_evaluable": 0,
                        "false_negative": 0,
                        "true_negative": 0,
                        "blocked_returns": [],
                    },
                )
                gst["observations"] += 1
                if not bool(gate.get("passed")):
                    gst["blocked"] += 1
                    if metric is not None:
                        gst["blocked_evaluable"] += 1
                        gst["blocked_returns"].append(metric)
                        if metric > 0:
                            gst["false_negative"] += 1
                        else:
                            gst["true_negative"] += 1

        if actual_action in {"FLAT", "HOLD_OPEN"} and alternative_returns:
            best = max(alternative_returns)
            flat_rows.append(
                {
                    "snapshot_id": sid,
                    "engine_id": engine_id,
                    "actual_action": actual_action,
                    "best_frozen_alternative_return": round(best, 8),
                    "flat_value_percent": round(-best, 8),
                    "classification": "CORRECT_ABSTENTION" if best <= 0 else "MISSED_OPPORTUNITY",
                }
            )

    for stat in gate_stats.values():
        returns = stat.pop("blocked_returns")
        stat["false_negative_rate"] = round(stat["false_negative"] / stat["blocked_evaluable"], 6) if stat["blocked_evaluable"] else None
        stat["mean_blocked_counterfactual_return"] = round(fmean(returns), 8) if returns else None
    for stat in action_stats.values():
        returns = stat.pop("returns")
        stat["positive_rate"] = round(stat["positive_selected"] / stat["selected_outcomes"], 6) if stat["selected_outcomes"] else None
        stat["mean_selected_return"] = round(fmean(returns), 8) if returns else None

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso_z(datetime.now(timezone.utc)),
        "status": "collecting_prospective_counterfactuals" if not outcomes else "descriptive_diagnostics_available",
        "registry": ENGINE_REGISTRY,
        "upstream_non_decision_layers": UPSTREAM_NON_DECISION_LAYERS,
        "summary": {
            "snapshot_count": len(snapshot_payloads),
            "candidate_outcome_count": len(candidate_outcomes),
            "flat_evaluable_count": len(flat_rows),
            "automatic_calibration_enabled": False,
        },
        "by_engine": engine_stats,
        "by_gate": gate_stats,
        "by_action": action_stats,
        "flat_value": {
            "evaluated": len(flat_rows),
            "correct_abstention": sum(row["classification"] == "CORRECT_ABSTENTION" for row in flat_rows),
            "missed_opportunity": sum(row["classification"] == "MISSED_OPPORTUNITY" for row in flat_rows),
            "rows": flat_rows[-100:],
        },
        "governance": {
            "descriptive_only": True,
            "minimum_sample_before_policy_candidate": 30,
            "policy_candidate_creation_in_PR29": False,
            "automatic_tuning": False,
            "zero_authority": safety_controls(),
        },
    }


def verify_pr29_state(state_dir: Path) -> dict[str, Any]:
    _assert_safety()
    ledger = state_dir / LEDGER_FILENAME
    chain = verify_chain(ledger)
    if not chain.get("ok"):
        raise RuntimeError("invalid Learning Ledger chain: " + str(chain.get("error")))
    activation = _load_json(state_dir / ACTIVATION_FILENAME)
    if not isinstance(activation, Mapping) or activation.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("PR29 activation is missing or invalid")
    _parse_time(str(activation.get("activated_at") or ""))
    if any(value is not False for value in (activation.get("zero_authority") or {}).values()):
        raise RuntimeError("PR29 activation violates zero authority")

    events = read_events(ledger)
    snapshot_positions: dict[str, int] = {}
    snapshots: dict[str, Mapping[str, Any]] = {}
    for index, event in enumerate(events):
        if event.get("event_type") != "learning_observation":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        kind = payload.get("observation_type")
        if kind == SNAPSHOT_OBSERVATION:
            snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), Mapping) else {}
            validate_snapshot(snapshot)
            sid = str(snapshot["snapshot_id"])
            snapshot_positions[sid] = index
            snapshots[sid] = snapshot
        elif kind == OUTCOME_OBSERVATION:
            sid = str(payload.get("snapshot_id") or "")
            cid = str(payload.get("candidate_id") or "")
            if sid not in snapshot_positions or snapshot_positions[sid] >= index:
                raise RuntimeError("counterfactual outcome lacks a prior frozen snapshot")
            if cid not in {str(row.get("candidate_id") or "") for row in snapshots[sid].get("candidates") or []}:
                raise RuntimeError("counterfactual outcome references unknown frozen candidate")
            if payload.get("settlement_mode") == "insufficient_counterfactual_state":
                raise RuntimeError("insufficient counterfactual state was incorrectly settled")
            if any(value is not False for value in (payload.get("zero_authority") or {}).values()):
                raise RuntimeError("counterfactual outcome violates zero authority")

    diagnostics = build_diagnostics(events)
    stored = _load_json(state_dir / DIAGNOSTICS_FILENAME)
    if stored is not None and isinstance(stored, Mapping) and stored.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("stored PR29 diagnostics schema mismatch")
    return {
        "ok": True,
        "ledger_events": len(events),
        "pr29_snapshots": len(snapshots),
        "pr29_outcomes": len(_outcome_events(events)),
        "ledger_head_hash": chain.get("head_hash"),
        "diagnostic_status": diagnostics["status"],
    }


def run_cycle(state_dir: Path, repo_root: Path) -> dict[str, Any]:
    _assert_safety()
    state_dir.mkdir(parents=True, exist_ok=True)
    ledger = state_dir / LEDGER_FILENAME
    ledger.touch(exist_ok=True)
    activation = ensure_activation(state_dir, ledger)
    activated_at = _parse_time(str(activation["activated_at"]))

    pre_events = read_events(ledger)
    pre_snapshots = _snapshot_events(pre_events)
    pre_snapshot_ids = set(pre_snapshots)
    existing_snapshot_ids = set(pre_snapshot_ids)
    existing_outcome_keys = set(_outcome_events(pre_events))

    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "started_at": _iso_z(datetime.now(timezone.utc)),
        "events_before": len(pre_events),
        "snapshots_appended": 0,
        "snapshots_skipped_pre_activation": 0,
        "snapshots_skipped_hindsight": 0,
        "outcomes_appended": 0,
        "engines_seen": {},
        "zero_authority": safety_controls(),
    }

    for snapshot in collect_engine_snapshots(repo_root):
        engine_id = str(snapshot["engine_id"])
        summary["engines_seen"][engine_id] = int(summary["engines_seen"].get(engine_id, 0)) + 1
        if _parse_time(str(snapshot["decision_at"])) < activated_at:
            summary["snapshots_skipped_pre_activation"] += 1
            continue
        # If the source already identifies the economic decision as closed and
        # we did not freeze it earlier, do not reconstruct a T0 snapshot now.
        meta = snapshot.get("metadata") if isinstance(snapshot.get("metadata"), Mapping) else {}
        if str(meta.get("trade_status") or "").lower() == "closed" and snapshot["snapshot_id"] not in pre_snapshot_ids:
            summary["snapshots_skipped_hindsight"] += 1
            continue
        if append_snapshot(ledger, snapshot, existing=existing_snapshot_ids):
            summary["snapshots_appended"] += 1

    # Re-read because PR28 outcomes may already be present and new snapshots were
    # appended above.  Settlement is deliberately restricted to pre-cycle PR29
    # snapshots, preventing same-cycle hindsight binding.
    after_snapshots = read_events(ledger)
    summary["outcomes_appended"] += settle_from_pr28(
        ledger,
        all_events=after_snapshots,
        preexisting_snapshots=pre_snapshots,
        existing_outcomes=existing_outcome_keys,
    )
    summary["outcomes_appended"] += settle_wes(
        ledger,
        repo_root=repo_root,
        preexisting_snapshots=pre_snapshots,
        existing_outcomes=existing_outcome_keys,
    )

    final_events = read_events(ledger)
    diagnostics = build_diagnostics(final_events)
    _atomic_json(state_dir / DIAGNOSTICS_FILENAME, diagnostics)
    summary["events_after"] = len(final_events)
    summary["finished_at"] = _iso_z(datetime.now(timezone.utc))
    summary["activation_at"] = activation["activated_at"]
    summary["diagnostics"] = diagnostics["summary"]
    _atomic_json(state_dir / STATUS_FILENAME, summary)
    verify_pr29_state(state_dir)
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="PR29 prospective counterfactual decision and gate diagnostics")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--registry", action="store_true")
    args = parser.parse_args(argv)
    if args.registry:
        print(json.dumps({"engines": ENGINE_REGISTRY, "non_decision_layers": UPSTREAM_NON_DECISION_LAYERS}, indent=2, sort_keys=True))
        return 0
    if args.verify:
        print(json.dumps(verify_pr29_state(args.state_dir), indent=2, sort_keys=True))
        return 0
    result = run_cycle(args.state_dir, args.repo_root.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

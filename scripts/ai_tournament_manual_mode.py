#!/usr/bin/env python3
"""Locked manual-submission support for the AI Tournament.

The original tournament engine only understood live provider calls and BRACE.
The production season, however, was switched to immutable manual-chat
submissions. This module installs a fail-closed loader that:

* reads the full revealed submission from data/ai_tournament/submissions,
* verifies it against the pre-market SHA-256 commitment,
* validates weights without silently changing them,
* exposes a readiness report used by the bootstrap and workflow.

No portfolio is reconstructed from a hash and no missing allocation is guessed.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]


class ManualSubmissionError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def slug(value: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManualSubmissionError(f"missing file: {path.relative_to(ROOT)}") from exc
    except Exception as exc:
        raise ManualSubmissionError(f"invalid JSON: {path.relative_to(ROOT)}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManualSubmissionError(f"JSON root must be an object: {path.relative_to(ROOT)}")
    return value


def participant_slug(agent: dict[str, Any]) -> str:
    return slug(str(agent.get("id") or agent.get("agent_id") or ""))


def submission_path(agent: dict[str, Any], config: dict[str, Any]) -> Path:
    data_dir = ROOT / str(config.get("data_dir") or "data/ai_tournament")
    return data_dir / "submissions" / f"{participant_slug(agent)}.json"


def commitment_path(agent: dict[str, Any], config: dict[str, Any]) -> Path:
    data_dir = ROOT / str(config.get("data_dir") or "data/ai_tournament")
    return data_dir / "commitments" / f"{participant_slug(agent)}.json"


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def extract_target_weights(submission: dict[str, Any]) -> tuple[dict[str, float], float]:
    """Return stock weights and explicit cash weight from a revealed submission."""
    if isinstance(submission.get("target_weights"), dict):
        weights = {
            str(ticker).upper().strip(): float(weight)
            for ticker, weight in submission["target_weights"].items()
            if str(ticker).strip() and _finite(weight)
        }
        cash = float(submission.get("cash_weight", 1.0 - sum(weights.values())))
        return weights, cash

    allocations = submission.get("allocations")
    if not isinstance(allocations, list):
        raise ManualSubmissionError("submission must contain allocations or target_weights")
    weights: dict[str, float] = {}
    for row in allocations:
        if not isinstance(row, dict):
            raise ManualSubmissionError("each allocation must be an object")
        ticker = str(row.get("ticker") or "").upper().strip()
        raw_weight = row.get("weight_pct")
        if not ticker or not _finite(raw_weight):
            raise ManualSubmissionError("allocation ticker/weight_pct is missing or invalid")
        if ticker in weights:
            raise ManualSubmissionError(f"duplicate allocation: {ticker}")
        weights[ticker] = float(raw_weight) / 100.0
    cash = float(submission.get("cash_weight_pct", 0.0)) / 100.0
    return weights, cash


def validate_locked_submission(
    agent: dict[str, Any],
    config: dict[str, Any],
    sha256_text: Callable[[str], str],
) -> dict[str, Any]:
    submission = _read_json(submission_path(agent, config))
    commitment = _read_json(commitment_path(agent, config))
    if commitment.get("locked") is not True:
        raise ManualSubmissionError(f"commitment is not locked for {agent.get('id')}")
    if commitment.get("canonicalization") != "json_sort_keys_utf8_compact_v1":
        raise ManualSubmissionError(f"unsupported commitment canonicalization for {agent.get('id')}")
    expected_hash = str(commitment.get("sha256") or "")
    actual_hash = sha256_text(canonical_json(submission))
    if not expected_hash or actual_hash != expected_hash:
        raise ManualSubmissionError(
            f"submission hash mismatch for {agent.get('id')}: expected {expected_hash}, got {actual_hash}"
        )

    weights, cash_weight = extract_target_weights(submission)
    universe = set(str(ticker).upper() for ticker in config.get("universe", []))
    rules = config.get("rules") or {}
    if not weights:
        raise ManualSubmissionError(f"empty locked portfolio for {agent.get('id')}")
    if not set(weights).issubset(universe):
        bad = sorted(set(weights) - universe)
        raise ManualSubmissionError(f"out-of-universe tickers for {agent.get('id')}: {bad}")
    if len(weights) > int(rules.get("max_positions", 0)):
        raise ManualSubmissionError(f"too many positions for {agent.get('id')}")
    max_weight = float(rules.get("max_position_weight", 1.0))
    min_weight = float(rules.get("min_position_weight", 0.0))
    for ticker, weight in weights.items():
        if weight <= 0 or weight > max_weight + 1e-9:
            raise ManualSubmissionError(f"invalid weight for {agent.get('id')}:{ticker}: {weight}")
        if min_weight and weight + 1e-9 < min_weight:
            raise ManualSubmissionError(f"weight below minimum for {agent.get('id')}:{ticker}: {weight}")
    if cash_weight < -1e-9 or cash_weight > float(rules.get("max_cash_weight", 1.0)) + 1e-9:
        raise ManualSubmissionError(f"invalid cash weight for {agent.get('id')}: {cash_weight}")
    if abs(sum(weights.values()) + cash_weight - 1.0) > 1e-6:
        raise ManualSubmissionError(
            f"weights do not total 100% for {agent.get('id')}: stocks={sum(weights.values())}, cash={cash_weight}"
        )

    validation = commitment.get("validation") or {}
    if validation:
        if int(validation.get("positions_count", len(weights))) != len(weights):
            raise ManualSubmissionError(f"positions_count differs from commitment for {agent.get('id')}")
        committed_cash = float(validation.get("cash_weight_pct", cash_weight * 100.0)) / 100.0
        if abs(committed_cash - cash_weight) > 1e-9:
            raise ManualSubmissionError(f"cash weight differs from commitment for {agent.get('id')}")

    return {
        "submission": submission,
        "commitment": commitment,
        "target_weights": {ticker: round(weight, 8) for ticker, weight in sorted(weights.items())},
        "cash_weight": round(cash_weight, 8),
        "submission_hash": actual_hash,
    }


def readiness(config: dict[str, Any], sha256_text: Callable[[str], str]) -> dict[str, Any]:
    rows = []
    ready = True
    for agent in config.get("agents", []):
        try:
            locked = validate_locked_submission(agent, config, sha256_text)
            rows.append({
                "agent_id": agent.get("id"),
                "ready": True,
                "submission_hash": locked["submission_hash"],
                "positions_count": len(locked["target_weights"]),
                "cash_weight": locked["cash_weight"],
            })
        except Exception as exc:  # noqa: BLE001
            ready = False
            rows.append({"agent_id": agent.get("id"), "ready": False, "error": str(exc)})
    return {"ready": ready, "participants": rows}


def install(namespace: dict[str, Any]) -> None:
    """Install locked-submission collection into the assembled engine globals."""
    original_collect = namespace["collect_decision"]
    sha256_text = namespace["sha256_text"]
    engine_canonical_json = namespace["canonical_json"]
    engine_slug = namespace["slug"]
    validation_error = namespace["ValidationError"]

    # Keep one canonical implementation inside the engine namespace.
    namespace["manual_submission_readiness"] = lambda config: readiness(config, sha256_text)
    namespace["load_locked_submission"] = lambda agent, config: validate_locked_submission(agent, config, sha256_text)

    def collect_locked_or_original(
        agent: dict[str, Any], snapshot: dict[str, Any], state: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, Any]:
        path = submission_path(agent, config)
        commitment = commitment_path(agent, config)
        use_locked = path.exists() or str(agent.get("provider")) == "manual_chat"
        if not use_locked:
            return original_collect(agent, snapshot, state, config)
        if not path.exists() or not commitment.exists():
            raise ManualSubmissionError(
                f"full locked submission is unavailable for {agent.get('id')}; refusing to invent a portfolio"
            )
        locked = validate_locked_submission(agent, config, sha256_text)
        submission = locked["submission"]
        targets = locked["target_weights"]
        rationale = str(
            submission.get("portfolio_thesis")
            or submission.get("rationale")
            or submission.get("selection_reason")
            or "Locked manual buy-and-hold submission."
        ).strip()[:900]
        confidence = int(max(0, min(100, int(submission.get("confidence_pct", submission.get("confidence", 0))))))
        decision_id = sha256_text(engine_canonical_json({
            "agent": agent["id"],
            "session": snapshot["session_date"],
            "targets": targets,
            "locked_submission_hash": locked["submission_hash"],
        }))[:20]
        if not rationale:
            raise validation_error("empty locked decision rationale")
        return {
            "schema_version": "ai-tournament-decision-v1",
            "decision_id": decision_id,
            "agent_id": agent["id"],
            "provider": agent["provider"],
            "model": namespace["resolve_model"](agent),
            "created_at": str(locked["commitment"].get("received_at") or namespace["utc_now"]()),
            "session_date": snapshot["session_date"],
            "snapshot_hash": snapshot["snapshot_hash"],
            "target_weights": targets,
            "cash_weight": locked["cash_weight"],
            "rationale": rationale,
            "confidence": confidence,
            "normalization_adjustments": [],
            "execution_policy": "locked_before_open_execute_at_first_eligible_session_open",
            "locked_submission_hash": locked["submission_hash"],
            "commitment_path": str(commitment.relative_to(ROOT)),
            "submission_path": str(path.relative_to(ROOT)),
        }

    namespace["collect_decision"] = collect_locked_or_original
    namespace["slug"] = engine_slug

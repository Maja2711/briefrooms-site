#!/usr/bin/env python3
"""Build a sanitized, Polish-only public projection of the private EUR/USD A/B/C research state.

The projection is intentionally read-only and strips research internals such as
raw Belief rows, decision fingerprints, durability manifests and full technical
indicator payloads. It exposes only current arm summaries, prospective outcomes
and cumulative comparison metrics needed by the PL frontend.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "eurusd-abc-public-pl-v1"
HORIZONS = ("30m", "60m", "120m", "240m", "1440m")
HORIZON_LABELS = {
    "30m": "30m",
    "60m": "1h",
    "120m": "2h",
    "240m": "4h",
    "1440m": "24h",
}
ARM_LABELS = {
    "A": "Tylko techniczny",
    "B": "Tylko Belief",
    "C": "Hybrydowy",
}
DISALLOWED_PUBLIC_KEYS = {
    "beliefs",
    "decision_sha256",
    "research_boundary",
    "checkpoint_id",
    "parent_checkpoint_id",
    "durability_contract",
    "technical",
    "belief_context",
}


def _iso_z(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _number(value: Any, digits: int = 6) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _arm_summary(arm_id: str, arm: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "arm_id": arm_id,
        "label_pl": ARM_LABELS[arm_id],
        "available": bool(arm.get("available")),
        "direction": str(arm.get("direction") or "UNAVAILABLE"),
        "score": _number(arm.get("score"), 2),
        "confidence": _number(arm.get("confidence"), 4),
    }


def _outcome_arm(result: Mapping[str, Any] | None) -> dict[str, Any]:
    result = result or {}
    return {
        "available": bool(result.get("available")),
        "direction": str(result.get("direction") or "UNAVAILABLE"),
        "directional_correct": result.get("directional_correct") if result.get("directional_correct") in {True, False} else None,
        "signed_return_bps": _number(result.get("signed_return_bps"), 4),
    }


def _latest_horizons(latest: Mapping[str, Any]) -> dict[str, Any]:
    source = latest.get("horizons") or {}
    projected: dict[str, Any] = {}
    for key in HORIZONS:
        row = source.get(key) or {}
        outcome = row.get("outcome")
        item: dict[str, Any] = {
            "label": HORIZON_LABELS[key],
            "minutes": int(row.get("minutes") or int(key[:-1])),
            "target_at": row.get("target_at"),
            "status": "RESOLVED" if isinstance(outcome, Mapping) else "PENDING",
            "resolved_at": None,
            "price": None,
            "raw_return_bps": None,
            "arms": {arm_id: _outcome_arm(None) for arm_id in ("A", "B", "C")},
        }
        if isinstance(outcome, Mapping):
            item["resolved_at"] = outcome.get("resolved_at")
            item["price"] = _number(outcome.get("price"), 5)
            item["raw_return_bps"] = _number(outcome.get("raw_return_bps"), 4)
            arm_rows = outcome.get("arms") or {}
            item["arms"] = {
                arm_id: _outcome_arm(arm_rows.get(arm_id) if isinstance(arm_rows, Mapping) else None)
                for arm_id in ("A", "B", "C")
            }
        projected[key] = item
    return projected


def _metric(row: Mapping[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    return {
        "matured_captures": int(row.get("matured_captures") or 0),
        "available_captures": int(row.get("available_captures") or 0),
        "signals": int(row.get("signals") or 0),
        "decision_rate": _number(row.get("decision_rate"), 6),
        "hit_rate": _number(row.get("hit_rate"), 6),
        "mean_signed_return_bps_signal_only": _number(row.get("mean_signed_return_bps_signal_only"), 4),
        "mean_strategy_return_bps_all_available": _number(row.get("mean_strategy_return_bps_all_available"), 4),
    }


def _comparison(report: Mapping[str, Any]) -> dict[str, Any]:
    performance = report.get("performance") or {}
    projected: dict[str, Any] = {}
    for key in HORIZONS:
        projected[key] = {
            "label": HORIZON_LABELS[key],
            "A": _metric(((performance.get("A") or {}).get(key) if isinstance(performance, Mapping) else None)),
            "B": _metric(((performance.get("B") or {}).get(key) if isinstance(performance, Mapping) else None)),
            "C": _metric(((performance.get("C") or {}).get(key) if isinstance(performance, Mapping) else None)),
        }
    return projected


def build_public_projection(state: Mapping[str, Any], report: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    if state.get("mode") != "research_shadow" or report.get("mode") != "research_shadow":
        raise ValueError("PR21 projection accepts research_shadow input only")
    if report.get("decision_influence") is not False:
        raise ValueError("public projection requires zero decision influence")
    governance = report.get("governance") or {}
    if governance.get("active_daily_engine_influence") is not False:
        raise ValueError("public projection requires zero active Daily engine influence")

    captures = state.get("captures") or []
    if not isinstance(captures, list) or not captures:
        raise ValueError("A/B/C state contains no captures")
    latest = captures[-1]
    if not isinstance(latest, Mapping):
        raise ValueError("latest A/B/C capture must be an object")
    research_boundary = latest.get("research_boundary") or {}
    if research_boundary.get("decision_influence") is not False or research_boundary.get("trade_execution") is not False:
        raise ValueError("latest capture violates zero-authority boundary")

    arms = latest.get("arms") or {}
    if set(arms) != {"A", "B", "C"}:
        raise ValueError("latest capture must contain A/B/C")

    signal_generated_at = latest.get("captured_at") or latest.get("market_observed_at")
    generated_at = _iso_z(now) if now is not None else str(
        state.get("updated_at") or signal_generated_at or _iso_z()
    )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "language": "pl",
        "generated_at": generated_at,
        "engine_version": str(latest.get("engine_version") or report.get("engine_version") or ""),
        "mode": "LIVE_SHADOW",
        "public_boundary": {
            "read_only_projection": True,
            "decision_influence": False,
            "trade_execution": False,
            "belief_writeback": False,
            "raw_belief_state_exposed": False,
            "private_research_state_exposed": False,
        },
        "sample": {
            "captures": len(captures),
            "latest_market_observed_at": latest.get("market_observed_at"),
            "latest_signal_generated_at": signal_generated_at,
        },
        "latest": {
            "market_observed_at": latest.get("market_observed_at"),
            "signal_generated_at": signal_generated_at,
            "reference_price": _number(latest.get("reference_price"), 5),
            "arms": {arm_id: _arm_summary(arm_id, arms[arm_id]) for arm_id in ("A", "B", "C")},
            "horizons": _latest_horizons(latest),
        },
        "comparison": _comparison(report),
    }
    validate_public_projection(payload)
    return payload


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_walk_keys(child))
    return keys


def validate_public_projection(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unexpected public projection schema")
    if payload.get("language") != "pl" or payload.get("mode") != "LIVE_SHADOW":
        raise ValueError("public projection must be PL live-shadow")
    boundary = payload.get("public_boundary") or {}
    required_false = (
        "decision_influence",
        "trade_execution",
        "belief_writeback",
        "raw_belief_state_exposed",
        "private_research_state_exposed",
    )
    if boundary.get("read_only_projection") is not True or any(boundary.get(key) is not False for key in required_false):
        raise ValueError("invalid public read-only boundary")
    latest = payload.get("latest") or {}
    if not latest.get("signal_generated_at"):
        raise ValueError("public projection must expose signal generation time")
    arms = latest.get("arms") or {}
    if set(arms) != {"A", "B", "C"}:
        raise ValueError("public projection must contain A/B/C")
    horizons = latest.get("horizons") or {}
    comparison = payload.get("comparison") or {}
    if tuple(horizons.keys()) != HORIZONS or tuple(comparison.keys()) != HORIZONS:
        raise ValueError("public projection horizon contract mismatch")
    leaked = DISALLOWED_PUBLIC_KEYS.intersection(_walk_keys(payload))
    if leaked:
        raise ValueError(f"private research keys leaked into public projection: {sorted(leaked)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build sanitized PL EUR/USD A/B/C public projection")
    parser.add_argument("--state")
    parser.add_argument("--report")
    parser.add_argument("--output", required=True)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()

    output = Path(args.output)
    if args.validate:
        validate_public_projection(_load(output))
        print("EURUSD_ABC_PUBLIC_PL_OK", output)
        return 0
    if not args.state or not args.report:
        parser.error("--state and --report are required unless --validate is used")
    payload = build_public_projection(_load(Path(args.state)), _load(Path(args.report)))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("EURUSD_ABC_PUBLIC_PL_WRITTEN", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

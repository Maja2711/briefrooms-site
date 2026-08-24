#!/usr/bin/env python3
"""Runtime reader for PR35 autonomous policy state.

Production engines keep their checked-in JSON configuration as the immutable
baseline.  PR35 may overlay only explicitly allowlisted scalar parameters from a
private, hash-verified policy registry artifact.  Missing/invalid state always
falls back to the checked-in baseline; arbitrary code/config mutation is not
supported.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

REGISTRY_SCHEMA = "briefrooms-autonomous-policy-registry-v1"
ENV_REGISTRY = "BRIEFROOMS_POLICY_REGISTRY"

POLICY_ALLOWLIST: dict[str, dict[str, Any]] = {
    "gpw_daily": {
        "baseline_policy_field": "policy_version",
        "parameters": {
            "minimum_composite_score": {"min": 68.0, "max": 76.0},
        },
    },
    "us_daily": {
        "baseline_policy_field": "policy_version",
        "parameters": {
            "target_score": {"min": 68.0, "max": 76.0},
        },
    },
}


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def registry_hash(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("registry_sha256", None)
    return _sha(body)


def validate_registry(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != REGISTRY_SCHEMA:
        raise ValueError("policy registry schema mismatch")
    stored = str(payload.get("registry_sha256") or "")
    if not stored or stored != registry_hash(payload):
        raise ValueError("policy registry hash mismatch")
    controls = payload.get("controls") if isinstance(payload.get("controls"), Mapping) else {}
    required_true = ("autonomous_promotion_enabled", "automatic_rollback_enabled")
    required_false = ("code_mutation_enabled", "arbitrary_parameter_mutation_enabled", "trade_execution_enabled")
    if any(controls.get(key) is not True for key in required_true):
        raise ValueError("policy registry autonomous controls are incomplete")
    if any(controls.get(key) is not False for key in required_false):
        raise ValueError("policy registry safety controls are invalid")


def load_registry(path: str | Path | None = None) -> dict[str, Any] | None:
    value = path or os.environ.get(ENV_REGISTRY)
    if not value:
        return None
    candidate = Path(value)
    if not candidate.is_file():
        return None
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        validate_registry(payload)
        return payload
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric policy value")
    return float(value)


def apply_active_policy(
    engine_id: str,
    baseline_config: Mapping[str, Any],
    *,
    registry_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return an effective config, or the untouched baseline on any unsafe state."""
    config = copy.deepcopy(dict(baseline_config))
    baseline_version = str(config.get("policy_version") or "")
    config["_autonomous_policy"] = {
        "status": "BASELINE",
        "engine_id": engine_id,
        "baseline_policy_version": baseline_version,
        "effective_policy_version": baseline_version,
        "overrides": {},
    }

    profile = POLICY_ALLOWLIST.get(engine_id)
    registry = load_registry(registry_path)
    if not profile or registry is None:
        return config

    engines = registry.get("engines") if isinstance(registry.get("engines"), Mapping) else {}
    state = engines.get(engine_id) if isinstance(engines.get(engine_id), Mapping) else None
    if not state or str(state.get("status")) != "ACTIVE":
        return config
    if str(state.get("baseline_policy_version") or "") != baseline_version:
        config["_autonomous_policy"]["status"] = "BASELINE_VERSION_MISMATCH"
        return config

    overrides = state.get("overrides") if isinstance(state.get("overrides"), Mapping) else {}
    allowed = profile["parameters"]
    if any(key not in allowed for key in overrides):
        config["_autonomous_policy"]["status"] = "BASELINE_INVALID_OVERRIDE"
        return config

    clean: dict[str, float] = {}
    try:
        for key, value in overrides.items():
            number = _number(value)
            bounds = allowed[key]
            if number < float(bounds["min"]) or number > float(bounds["max"]):
                raise ValueError(f"override outside bounds: {key}")
            clean[key] = number
    except (TypeError, ValueError):
        config["_autonomous_policy"]["status"] = "BASELINE_INVALID_OVERRIDE"
        return config

    for key, number in clean.items():
        original = config.get(key)
        config[key] = int(number) if isinstance(original, int) and float(number).is_integer() else number

    effective = str(state.get("effective_policy_version") or baseline_version)
    config["policy_version"] = effective
    config["_autonomous_policy"] = {
        "status": "ACTIVE",
        "engine_id": engine_id,
        "policy_id": state.get("policy_id"),
        "revision": state.get("revision"),
        "baseline_policy_version": baseline_version,
        "effective_policy_version": effective,
        "activated_at": state.get("activated_at"),
        "source_candidate_id": state.get("source_candidate_id"),
        "overrides": clean,
    }
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify/apply BriefRooms PR35 policy registry")
    parser.add_argument("--registry")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    registry = load_registry(args.registry)
    if args.verify:
        if registry is None:
            raise SystemExit("NO_VALID_POLICY_REGISTRY")
        print("POLICY_REGISTRY_OK", registry.get("updated_at"))
        return 0
    print(json.dumps(registry or {}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

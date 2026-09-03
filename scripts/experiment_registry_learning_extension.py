#!/usr/bin/env python3
"""Canonical Experiment Registry extension for A/B/C learning evidence.

The logical experiment inventory remains unchanged. This adapter reuses the
existing Experiment Registry builder and enriches only the EURUSD A/B/C row
with sanitized evidence from its shared learning loop. No eighth experiment is
created and no promotion/production authority is added.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

try:
    from experiment_registry import DEFAULT_OUTPUT, build_registry as build_base_registry
except ModuleNotFoundError:
    from scripts.experiment_registry import DEFAULT_OUTPUT, build_registry as build_base_registry


def _load(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _learning_details(learning: Mapping[str, Any]) -> dict[str, Any]:
    if not learning:
        return {
            "available": False,
            "episode_count": 0,
            "automatic_policy_mutation": False,
            "decision_influence": False,
            "arms": {},
        }
    if learning.get("experiment_id") != "eurusd-abc-live-shadow":
        raise ValueError("A/B/C learning projection is not bound to canonical experiment")
    arms = learning.get("arms") if isinstance(learning.get("arms"), Mapping) else {}
    return {
        "available": True,
        "schema_version": learning.get("schema_version"),
        "mode": learning.get("mode"),
        "episode_count": int(learning.get("episode_count") or 0),
        "prospective_only": bool(learning.get("prospective_only")),
        "historical_backfill": bool(learning.get("historical_backfill")),
        "automatic_policy_mutation": bool(learning.get("automatic_policy_mutation")),
        "decision_influence": bool(learning.get("decision_influence")),
        "cross_arm_writeback": bool(learning.get("cross_arm_writeback")),
        "arms": {
            arm: {
                "episode_count": int((arms.get(arm) or {}).get("episode_count") or 0),
                "hit_rate": (arms.get(arm) or {}).get("hit_rate"),
                "mean_r": (arms.get(arm) or {}).get("mean_r"),
                "dominant_error": (arms.get(arm) or {}).get("dominant_error"),
                "error_recurrence_rate": (arms.get(arm) or {}).get("error_recurrence_rate"),
                "recent_vs_prior_mean_r_delta": (arms.get(arm) or {}).get("recent_vs_prior_mean_r_delta"),
                "policy_stability": (arms.get(arm) or {}).get("policy_stability"),
                "lesson_candidate": (arms.get(arm) or {}).get("lesson_candidate"),
            }
            for arm in ("A", "B", "C")
        },
    }


def build_registry(root: Path) -> dict[str, Any]:
    registry = build_base_registry(root)
    learning_public = _load(root, "data/investments/eurusd_abc_learning_public.json")
    learning = _learning_details(learning_public)
    row = next((item for item in registry.get("experiments", []) if item.get("id") == "eurusd-abc-live-shadow"), None)
    if row is None:
        raise ValueError("canonical registry missing eurusd-abc-live-shadow")
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    details["learning_loop"] = learning
    row["details"] = details
    notes = list(row.get("notes") or [])
    note = "A/B/C use one prospective LearningEpisode contract with isolated per-arm memory; policy mutation remains gated and disabled automatically."
    if note not in notes:
        notes.append(note)
    row["notes"] = notes
    return registry


def main() -> int:
    parser = argparse.ArgumentParser(description="Build canonical Experiment Registry with A/B/C learning evidence")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_registry(args.root)
    output = args.output if args.output.is_absolute() else args.root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Lesson/Hypothesis Registry v1.

This registry is the research-intent layer that sits before experiments.
It records what the system learned, turns lessons into falsifiable hypotheses,
and constrains which hypotheses may be compiled into prospective shadow
experiments.

Zero authority: the registry cannot alter production policy, rankings, sizing,
engine configuration, or execute trades.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "briefrooms-lesson-hypothesis-registry-v1"
DEFAULT_REGISTRY = Path("data/investments/lesson_hypothesis_registry_v1.json")

LESSON_STATUSES = {"OBSERVED", "CONFIRMED", "RETIRED"}
HYPOTHESIS_STATUSES = {
    "DRAFT",
    "READY_FOR_SHADOW",
    "RUNNING_SHADOW",
    "SUPPORTED",
    "REJECTED",
    "PARKED",
}
ALLOWED_STAGES = {"PR35", "PR36"}

REQUIRED_ZERO_AUTHORITY = {
    "production_policy_writeback": False,
    "production_ranking_writeback": False,
    "production_sizing_writeback": False,
    "trade_execution": False,
    "automatic_promotion": False,
    "learning_ledger_writeback": False,
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def registry_hash(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("registry_sha256", None)
    return _sha(body)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_metric_rule(rule: Mapping[str, Any], *, context: str) -> None:
    metric = str(rule.get("metric") or "")
    operator = str(rule.get("operator") or "")
    _require(bool(metric), f"{context}: metric is required")
    _require(operator in {">", ">=", "<", "<=", "==", "!="}, f"{context}: unsupported operator")
    _require(rule.get("value") is not None, f"{context}: comparison value is required")


def validate_registry(payload: Mapping[str, Any]) -> dict[str, Any]:
    _require(payload.get("schema_version") == SCHEMA_VERSION, "lesson/hypothesis registry schema mismatch")

    governance = payload.get("governance")
    _require(isinstance(governance, Mapping), "registry governance is required")
    for key, expected in REQUIRED_ZERO_AUTHORITY.items():
        _require(governance.get(key) is expected, f"governance invariant violated: {key}")

    lessons = payload.get("lessons")
    hypotheses = payload.get("hypotheses")
    _require(isinstance(lessons, list), "lessons must be a list")
    _require(isinstance(hypotheses, list), "hypotheses must be a list")

    lesson_ids: set[str] = set()
    for index, lesson in enumerate(lessons):
        _require(isinstance(lesson, Mapping), f"lesson {index} is not an object")
        lesson_id = str(lesson.get("lesson_id") or "")
        _require(bool(lesson_id), f"lesson {index} has no lesson_id")
        _require(lesson_id not in lesson_ids, f"duplicate lesson_id: {lesson_id}")
        lesson_ids.add(lesson_id)
        _require(lesson.get("status") in LESSON_STATUSES, f"invalid lesson status: {lesson_id}")
        _require(bool(str(lesson.get("statement") or "").strip()), f"lesson statement missing: {lesson_id}")
        evidence = lesson.get("evidence")
        _require(isinstance(evidence, list) and evidence, f"lesson evidence missing: {lesson_id}")
        for item in evidence:
            _require(isinstance(item, Mapping), f"lesson evidence item invalid: {lesson_id}")
            _require(bool(str(item.get("source") or "").strip()), f"lesson evidence source missing: {lesson_id}")

    hypothesis_ids: set[str] = set()
    for index, hypothesis in enumerate(hypotheses):
        _require(isinstance(hypothesis, Mapping), f"hypothesis {index} is not an object")
        hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
        _require(bool(hypothesis_id), f"hypothesis {index} has no hypothesis_id")
        _require(hypothesis_id not in hypothesis_ids, f"duplicate hypothesis_id: {hypothesis_id}")
        hypothesis_ids.add(hypothesis_id)
        _require(hypothesis.get("status") in HYPOTHESIS_STATUSES, f"invalid hypothesis status: {hypothesis_id}")
        _require(bool(str(hypothesis.get("claim") or "").strip()), f"hypothesis claim missing: {hypothesis_id}")

        parents = hypothesis.get("lesson_ids")
        _require(isinstance(parents, list) and parents, f"hypothesis lesson_ids missing: {hypothesis_id}")
        unknown = sorted(set(str(x) for x in parents) - lesson_ids)
        _require(not unknown, f"hypothesis references unknown lessons: {hypothesis_id}: {unknown}")

        spec = hypothesis.get("experiment_spec")
        _require(isinstance(spec, Mapping), f"experiment_spec missing: {hypothesis_id}")
        _require(str(spec.get("stage") or "") in ALLOWED_STAGES, f"invalid experiment stage: {hypothesis_id}")
        for key in ("engine_id", "parameter", "gate"):
            _require(bool(str(spec.get(key) or "")), f"{hypothesis_id}: {key} is required")
        _require(spec.get("from_value") is not None, f"{hypothesis_id}: from_value is required")
        _require(spec.get("to_value") is not None, f"{hypothesis_id}: to_value is required")
        _require(float(spec["from_value"]) != float(spec["to_value"]), f"{hypothesis_id}: transition is a no-op")
        _require(int(spec.get("validation_target_n") or 0) > 0, f"{hypothesis_id}: validation_target_n must be positive")
        _require(int(spec.get("promotion_methodology_version") or 0) > 0, f"{hypothesis_id}: methodology version required")

        criteria = hypothesis.get("success_criteria")
        _require(isinstance(criteria, list) and criteria, f"success_criteria missing: {hypothesis_id}")
        for rule in criteria:
            _require(isinstance(rule, Mapping), f"success criterion invalid: {hypothesis_id}")
            _validate_metric_rule(rule, context=hypothesis_id)

        falsifiers = hypothesis.get("falsification_criteria")
        _require(isinstance(falsifiers, list) and falsifiers, f"falsification_criteria missing: {hypothesis_id}")
        for rule in falsifiers:
            _require(isinstance(rule, Mapping), f"falsification criterion invalid: {hypothesis_id}")
            _validate_metric_rule(rule, context=hypothesis_id)

    stored = str(payload.get("registry_sha256") or "")
    _require(bool(stored), "registry_sha256 is required")
    actual = registry_hash(payload)
    _require(stored == actual, "lesson/hypothesis registry hash mismatch")

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "lessons": len(lessons),
        "hypotheses": len(hypotheses),
        "ready_for_shadow": sum(1 for row in hypotheses if row.get("status") == "READY_FOR_SHADOW"),
        "registry_sha256": actual,
        "zero_authority": True,
    }


def load_registry(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("lesson/hypothesis registry root must be an object")
    validate_registry(payload)
    return payload


def ready_hypotheses(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    validate_registry(payload)
    return [
        dict(row)
        for row in payload.get("hypotheses", [])
        if isinstance(row, Mapping) and row.get("status") == "READY_FOR_SHADOW"
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Lesson/Hypothesis Registry v1")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    args = parser.parse_args()
    print(json.dumps(validate_registry(load_registry(args.registry)), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Enrich the sanitized BRACE-SPX public snapshot with aggregate economics.

This post-processing step copies only pre-aggregated mandate, cost, exposure and
benchmark-comparison evidence. Candidate identity, parameters, forecasts and
daily paths remain excluded from the browser payload.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

FORBIDDEN_KEYS = {
    "candidate",
    "candidate_id",
    "params",
    "predictions",
    "experiments",
    "fold_metrics",
    "daily_paths",
}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def assert_no_forbidden_keys(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) in FORBIDDEN_KEYS:
                raise ValueError(f"Forbidden public key at {path}.{key}")
            assert_no_forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_no_forbidden_keys(child, f"{path}[{index}]")


def enrich(public: dict[str, Any], economics: dict[str, Any]) -> dict[str, Any]:
    architecture = public.get("architecture") or {}
    if economics.get("architecture_id") != architecture.get("id"):
        raise RuntimeError("Economics architecture does not match public snapshot")
    if economics.get("candidate_signature") != architecture.get("candidate_signature"):
        raise RuntimeError("Economics signature does not match public snapshot")

    boundary = economics.get("public_boundary") or {}
    for key in ("daily_paths_exposed", "candidate_identity_exposed", "parameters_exposed", "raw_predictions_exposed", "holdout_used"):
        if boundary.get(key):
            raise RuntimeError(f"Economics evidence violates public boundary: {key}")

    mandate = economics.get("mandate") or {}
    if mandate.get("short_allowed") or mandate.get("leverage_allowed") or mandate.get("orders_allowed"):
        raise RuntimeError("Architecture 2 public contract must remain long/flat, unlevered and no-order")

    diagnostics = economics.get("diagnostic_leader") or {}
    comparison = economics.get("comparison_vs_trend_200d") or {}
    leader = ((public.get("development") or {}).get("diagnostic_leader") or {})
    metrics = leader.get("metrics") or {}
    if diagnostics.get("average_exposure") is not None:
        metrics["average_exposure"] = diagnostics.get("average_exposure")
    leader["metrics"] = metrics
    leader["economics"] = diagnostics
    public.setdefault("development", {})["diagnostic_leader"] = leader

    edge_confirmed = bool(comparison.get("edge_confirmed", False))
    public["mandate"] = mandate
    public["cost_model"] = economics.get("cost_model") or {}
    public["comparison_assessment"] = {
        **comparison,
        "labels": {
            "pl": "Przewaga nad Trend 200D potwierdzona" if edge_confirmed else "Brak potwierdzonej przewagi nad Trend 200D",
            "en": "Edge over the 200D trend confirmed" if edge_confirmed else "No confirmed edge over the 200D trend",
        },
    }
    public_boundary = public.setdefault("public_boundary", {})
    public_boundary["daily_paths_exposed"] = False
    public_boundary["candidate_identity_exposed"] = False
    public["notes"] = {
        "pl": "Architecture 2 jest badaniem long/flat zarządzającym ekspozycją, a nie systemem long/short. Wynik ogranicza ryzyko, lecz nie potwierdza przewagi ekonomicznej nad prostym Trend 200D.",
        "en": "Architecture 2 is a long/flat exposure-management study, not a long/short system. It reduces risk, but does not confirm an economic edge over the simple 200D trend benchmark.",
    }
    assert_no_forbidden_keys(public)
    return public


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public", type=Path, required=True)
    parser.add_argument("--economics", type=Path, required=True)
    args = parser.parse_args()

    public = read_json(args.public)
    economics = read_json(args.economics)
    enriched = enrich(public, economics)
    args.public.write_text(json.dumps(enriched, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Enriched BRACE-SPX public economics: {args.public}")


if __name__ == "__main__":
    main()

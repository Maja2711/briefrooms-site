#!/usr/bin/env python3
"""Build public AI Outlook calibration counters with a minimum sample gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / "data" / "ai_outlook_history"
OUT = ROOT / "data" / "ai_outlook_metrics.json"
PUBLIC_BRIER_MIN_N = 30


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _outcome(record: dict[str, Any]) -> float | None:
    resolution = record.get("resolution")
    if not isinstance(resolution, dict):
        return None
    status = str(resolution.get("status") or "").lower()
    if status not in {"resolved_true", "resolved_false"}:
        return None
    return 1.0 if status == "resolved_true" else 0.0


def _forecast_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("schema_version") == 2 and payload.get("edition_policy") == "independent-per-language":
        return [
            section
            for language in ("pl", "en")
            if isinstance((section := payload.get(language)), dict)
        ]
    return [payload] if payload else []


def public_calibration_summary(records: list[dict[str, Any]], min_n: int = PUBLIC_BRIER_MIN_N) -> dict[str, Any]:
    published = len(records)
    resolved_rows: list[tuple[float, float]] = []
    in_progress = 0
    for record in records:
        outcome = _outcome(record)
        if outcome is None:
            in_progress += 1
            continue
        try:
            probability = float(record.get("probability")) / 100.0
        except (TypeError, ValueError):
            continue
        if 0.0 <= probability <= 1.0:
            resolved_rows.append((probability, outcome))
    resolved = len(resolved_rows)
    available = resolved >= min_n
    brier = None
    if available:
        brier = round(sum((probability - outcome) ** 2 for probability, outcome in resolved_rows) / resolved, 6)
    return {
        "schema_version": "ai-outlook-metrics-v2",
        "published": published,
        "in_progress": in_progress,
        "resolved": resolved,
        "public_brier_available": available,
        "public_brier_min_resolved": min_n,
        "brier_score": brier,
        "counting_policy": "one forecast per language edition",
        "message_pl": (
            f"Brier Score zostanie opublikowany po {min_n} rozstrzygniętych prognozach."
            if not available else "Brier Score oparty na wymaganej minimalnej próbie."
        ),
        "message_en": (
            f"Brier Score will be published after {min_n} resolved forecasts."
            if not available else "Brier Score based on the required minimum sample."
        ),
    }


def build() -> dict[str, Any]:
    payloads = [_load(path) for path in sorted(HISTORY_DIR.glob("*.json"))] if HISTORY_DIR.exists() else []
    records = [record for payload in payloads if payload for record in _forecast_records(payload)]
    summary = public_calibration_summary(records)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    summary = _load(OUT) if args.validate_only else build()
    required = {"published", "in_progress", "resolved", "public_brier_available", "public_brier_min_resolved", "brier_score"}
    if not required.issubset(summary):
        raise ValueError("invalid AI Outlook metrics file")
    if summary["resolved"] < summary["public_brier_min_resolved"] and summary["brier_score"] is not None:
        raise ValueError("Brier Score exposed below minimum sample")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

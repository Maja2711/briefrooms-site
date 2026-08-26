#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "gse-v2-public-lab-v2"
HORIZON_LABELS = {24: "24h", 168: "7d", 720: "30d"}


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _num(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def latest_timestamp(values: Iterable[Any]) -> str | None:
    parsed = [(dt, str(value)) for value in values if (dt := _parse_time(value)) is not None]
    if not parsed:
        return None
    dt, _ = max(parsed, key=lambda item: item[0])
    return dt.isoformat().replace("+00:00", "Z")


def improvement_pct(baseline: Any, challenger: Any) -> float | None:
    base = _num(baseline)
    test = _num(challenger)
    if base is None or test is None or base <= 0:
        return None
    return round((base - test) / base * 100.0, 4)


def horizon_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    by_horizon = report.get("by_horizon") or {}
    for hours in (24, 168, 720):
        row = by_horizon.get(str(hours)) or by_horizon.get(hours) or {}
        regime = row.get("regime_aware") or {}
        plain = row.get("unweighted_analogue") or {}
        out.append(
            {
                "hours": hours,
                "label": HORIZON_LABELS[hours],
                "n": int(regime.get("n") or 0),
                "regime_brier": _num(regime.get("brier")),
                "baseline_brier": _num(plain.get("brier")),
                "brier_improvement_pct": improvement_pct(plain.get("brier"), regime.get("brier")),
                "regime_log_loss": _num(regime.get("log_loss")),
                "baseline_log_loss": _num(plain.get("log_loss")),
                "hit_rate": _num(regime.get("hit_rate_50")),
                "calibration_bias": _num(regime.get("calibration_bias")),
            }
        )
    valid = [row for row in out if row["n"] > 0 and row["regime_brier"] is not None]
    if valid:
        best = min(valid, key=lambda row: float(row["regime_brier"]))
        for row in out:
            row["best"] = row["hours"] == best["hours"]
    else:
        for row in out:
            row["best"] = False
    return out


def scenario_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, row in (report.get("by_scenario") or {}).items():
        regime = row.get("regime_aware") or {}
        plain = row.get("unweighted_analogue") or {}
        out.append(
            {
                "scenario_type": str(name),
                "n": int(regime.get("n") or 0),
                "regime_brier": _num(regime.get("brier")),
                "baseline_brier": _num(plain.get("brier")),
                "brier_improvement_pct": improvement_pct(plain.get("brier"), regime.get("brier")),
                "hit_rate": _num(regime.get("hit_rate_50")),
            }
        )
    return sorted(out, key=lambda row: (-row["n"], row["regime_brier"] if row["regime_brier"] is not None else 999.0, row["scenario_type"]))


def public_episodes(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    episodes: list[dict[str, Any]] = []
    for event in catalog.get("events") or []:
        episodes.append(
            {
                "event_id": event.get("event_id"),
                "event_cluster_id": event.get("event_cluster_id") or event.get("event_id"),
                "event_at": event.get("event_at"),
                "label": event.get("label"),
                "scenario_types": list(event.get("scenario_types") or []),
                "source": event.get("source"),
                "source_ref": event.get("source_ref"),
                "source_reliability": _num(event.get("source_reliability")),
            }
        )
    episodes.sort(key=lambda row: str(row.get("event_at") or ""), reverse=True)
    return episodes[:160]


def learning_timeline(ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = [row for row in ledger if _parse_time(row.get("recorded_at")) is not None]
    valid.sort(key=lambda row: _parse_time(row.get("recorded_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    out: list[dict[str, Any]] = []
    for row in valid[:16]:
        out.append(
            {
                "recorded_at": row.get("recorded_at"),
                "candidates_added": int(row.get("candidates_added") or 0),
                "verifications_added": int(row.get("verifications_added") or 0),
                "record_hash": row.get("record_hash"),
            }
        )
    return out


def build_projection(state_dir: Path, catalog_path: Path) -> dict[str, Any]:
    state = read_json(state_dir / "gse_v2_learning_state.json", {})
    gse_state = read_json(state_dir / "gse_state.json", {})
    historical = read_json(state_dir / "gse_v2_historical_walkforward.json", {})
    policy = read_json(state_dir / "gse_v2_policy_proposal.json", {})
    calibration = read_json(state_dir / "gse_v2_regime_calibration.json", {})
    enriched = read_json(state_dir / "gse_v2_enriched_library.json", {})
    discovery = read_json(state_dir / "gse_historical_discovery_state.json", {})
    ledger = read_jsonl(state_dir / "gse_v2_learning_ledger.jsonl")
    base_verifications = read_jsonl(state_dir / "gse_verifications.jsonl")
    v2_verifications = read_jsonl(state_dir / "gse_v2_regime_verifications.jsonl")
    catalog = read_json(catalog_path, {})

    episodes = public_episodes(catalog)
    clusters = {str(row.get("event_cluster_id") or row.get("event_id")) for row in episodes if row.get("event_cluster_id") or row.get("event_id")}
    scenario_counts = Counter(s for row in episodes for s in row.get("scenario_types") or [])
    coverage = enriched.get("coverage") or {}
    prospective = calibration.get("overall") or state.get("prospective") or {}
    overall = historical.get("overall") or {}
    regime = overall.get("regime_aware") or {}
    plain = overall.get("unweighted_analogue") or {}
    horizons = horizon_rows(historical)
    best_horizon = next((row for row in horizons if row.get("best")), None)
    readiness = state.get("readiness") or {}
    timeline = learning_timeline(ledger)

    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    last_learning_at = latest_timestamp(row.get("recorded_at") for row in ledger)
    last_base_verification_at = latest_timestamp(row.get("verified_at") for row in base_verifications)
    last_v2_verification_at = latest_timestamp(row.get("verified_at") for row in v2_verifications)
    last_verification_at = latest_timestamp((last_base_verification_at, last_v2_verification_at))
    last_scan_at = latest_timestamp((gse_state.get("last_run_at"),))

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "activity": {
            "last_scan_at": last_scan_at,
            "last_learning_at": last_learning_at,
            "last_verification_at": last_verification_at,
            "last_base_verification_at": last_base_verification_at,
            "last_v2_verification_at": last_v2_verification_at,
            "projection_generated_at": generated_at,
            "scan_cadence": "hourly",
            "learning_cadence": "after_successful_gse_shadow_run",
            "verification_cadence": "hourly_when_due",
        },
        "engine": {
            "short_name": "GSE v2",
            "full_name": "Geopolitical Scenario Engine",
            "mode": state.get("mode") or "shadow",
            "decision_influence": False,
            "automatic_promotion": False,
        },
        "summary": {
            "verified_clusters": int(discovery.get("effective_verified_cluster_n") or len(clusters)),
            "target_verified_clusters": int(discovery.get("target_verified_clusters") or 100),
            "catalog_events": len(episodes),
            "historical_response_rows": int(coverage.get("response_rows") or 0),
            "walk_forward_n": int(historical.get("evaluable_predictions") or regime.get("n") or 0),
            "prospective_paired_n": int(prospective.get("paired_n") or 0),
            "learning_cycles": len(ledger),
            "scenario_family_count": len(scenario_counts),
        },
        "historical_overall": {
            "regime_brier": _num(regime.get("brier")),
            "baseline_brier": _num(plain.get("brier")),
            "brier_improvement_pct": improvement_pct(plain.get("brier"), regime.get("brier")),
            "regime_log_loss": _num(regime.get("log_loss")),
            "baseline_log_loss": _num(plain.get("log_loss")),
            "log_loss_improvement_pct": improvement_pct(plain.get("log_loss"), regime.get("log_loss")),
            "hit_rate": _num(regime.get("hit_rate_50")),
            "calibration_bias": _num(regime.get("calibration_bias")),
        },
        "horizons": horizons,
        "best_horizon": best_horizon,
        "scenarios": scenario_rows(historical),
        "scenario_catalog_counts": dict(sorted(scenario_counts.items())),
        "prospective": {
            "paired_n": int(prospective.get("paired_n") or 0),
            "mean_brier_v1": _num(prospective.get("mean_brier_v1")),
            "mean_brier_v2": _num(prospective.get("mean_brier_v2_regime")),
            "delta_brier_v2_minus_v1": _num(prospective.get("delta_brier_v2_minus_v1")),
            "mean_log_loss_v1": _num(prospective.get("mean_log_loss_v1")),
            "mean_log_loss_v2": _num(prospective.get("mean_log_loss_v2_regime")),
            "delta_log_loss_v2_minus_v1": _num(prospective.get("delta_log_loss_v2_minus_v1")),
            "calibration_bias_v2": _num(prospective.get("calibration_bias_v2_regime")),
        },
        "challenger": {
            "status": policy.get("status"),
            "candidate": policy.get("candidate"),
            "train_metrics": policy.get("train_metrics"),
            "holdout_metrics": policy.get("holdout_metrics"),
            "active_policy_holdout_metrics": policy.get("active_policy_holdout_metrics"),
            "holdout_delta_brier_candidate_minus_active": _num(policy.get("holdout_delta_brier_candidate_minus_active")),
            "automatically_applied": False,
        },
        "readiness": {
            "status": readiness.get("status") or "shadow_learning",
            "reasons": list(readiness.get("reasons") or []),
            "automatic_promotion": False,
        },
        "discovery": {
            "status": discovery.get("status"),
            "target_met": bool(discovery.get("target_met")),
            "effective_verified_cluster_n": discovery.get("effective_verified_cluster_n"),
        },
        "episodes": episodes,
        "learning_timeline": timeline,
        "public_boundary": {
            "read_only_projection": True,
            "raw_evidence_exposed": False,
            "private_forecasts_exposed": False,
            "trade_execution": False,
            "belief_writeback": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a safe public GSE v2 Lab projection")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_projection(args.state_dir, args.catalog)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"generated_at": payload["generated_at"], "activity": payload["activity"], "summary": payload["summary"], "best_horizon": payload["best_horizon"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

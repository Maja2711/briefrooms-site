#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "gse-v2-public-lab-v3"
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



def event_threat_projection(state_dir: Path) -> dict[str, Any] | None:
    """Sanitize the GSE Event Probability / Threat Engine for public Lab display.

    Raw evidence IDs, realization evidence and private frozen forecast IDs remain private.
    """
    state = read_json(state_dir / "gse_event_threat_state.json", {})
    if not state:
        return None
    calibration = read_json(state_dir / "gse_event_probability_calibration.json", {})
    rows: list[dict[str, Any]] = []
    for row in state.get("current_estimates") or []:
        try:
            horizon = int(row.get("horizon_hours") or 0)
        except (TypeError, ValueError):
            continue
        probability = _num(row.get("predicted_probability"))
        prior = _num(row.get("prior_probability"))
        rows.append(
            {
                "event_type": str(row.get("event_type") or ""),
                "label": str(row.get("label") or ""),
                "target": str(row.get("target") or ""),
                "horizon_hours": horizon,
                "horizon_label": {168: "7d", 720: "30d", 2160: "90d"}.get(horizon, f"{horizon}h"),
                "probability": probability,
                "prior_probability": prior,
                "delta_vs_prior": None if probability is None or prior is None else round(probability - prior, 6),
                "confidence": _num(row.get("confidence")),
                "signal_score": _num(row.get("signal_score")),
                "evidence_24h": int(row.get("evidence_24h") or 0),
                "evidence_7d": int(row.get("evidence_7d") or 0),
                "independent_sources_7d": int(row.get("independent_sources_7d") or 0),
                "precursor_categories": list(row.get("precursor_categories") or []),
                "calibration_status": str(row.get("calibration_status") or "uncalibrated_seed"),
            }
        )
    rows.sort(key=lambda row: (str(row.get("target")), int(row.get("horizon_hours") or 0)))
    overall = calibration.get("overall") or {}
    return {
        "schema_version": str(state.get("schema_version") or "gse-event-threat-v1"),
        "generated_at": state.get("generated_at"),
        "model_status": str(state.get("model_status") or "prospective_uncalibrated_seed"),
        "probability_semantics": str(state.get("probability_semantics") or ""),
        "estimates": rows,
        "calibration": {
            "count": int(overall.get("count") or 0),
            "positive_count": int(overall.get("positive_count") or 0),
            "status": overall.get("status"),
            "mean_brier": _num(overall.get("mean_brier")),
            "mean_prior_brier": _num(overall.get("mean_prior_brier")),
            "delta_brier_vs_prior": _num(overall.get("delta_brier_vs_prior")),
            "bias": _num(overall.get("bias")),
        },
        "research_only": True,
        "decision_influence": False,
        "trade_execution": False,
        "raw_evidence_exposed": False,
    }



def asset_outlook_projection(
    state_dir: Path,
    asset: str,
    *,
    generated_at: str | None = None,
    max_candidate_age_hours: float = 12.0,
) -> dict[str, Any] | None:
    """Expose a sanitized latest frozen GSE v2 market outlook for one asset.

    Only the latest candidate per supported horizon is published. Private forecast IDs,
    neighbours and raw evidence remain private.
    """
    asset = str(asset or "").strip().upper()
    if not asset:
        return None
    reference_at = _parse_time(generated_at) or datetime.now(timezone.utc)
    candidates = [
        row for row in read_jsonl(state_dir / "gse_v2_regime_forecasts.jsonl")
        if str(row.get("asset") or "").upper() == asset
        and int(row.get("horizon_hours") or 0) in HORIZON_LABELS
        and int(row.get("direction") or 0) in (-1, 1)
        and _parse_time(row.get("forecast_at")) is not None
    ]
    if not candidates:
        return None

    rows: list[dict[str, Any]] = []
    for horizon in HORIZON_LABELS:
        horizon_rows = [row for row in candidates if int(row.get("horizon_hours") or 0) == horizon]
        if not horizon_rows:
            continue
        horizon_rows.sort(
            key=lambda row: _parse_time(row.get("forecast_at")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        row = horizon_rows[0]
        probability = _num(row.get("v2_regime_candidate_probability"))
        baseline = _num(row.get("baseline_v1_probability"))
        if probability is None or not 0.0 <= probability <= 1.0:
            continue
        forecast_at = _parse_time(row.get("forecast_at"))
        age_hours = None if forecast_at is None else max(0.0, (reference_at - forecast_at).total_seconds() / 3600.0)
        scenario_types: list[str] = []
        for diagnostic in row.get("scenario_diagnostics") or []:
            name = str((diagnostic or {}).get("scenario_type") or "").strip()
            if name and name not in scenario_types:
                scenario_types.append(name)
        rows.append({
            "asset": asset,
            "symbol": row.get("symbol"),
            "horizon_hours": horizon,
            "horizon_label": HORIZON_LABELS[horizon],
            "direction": "UP" if int(row.get("direction")) > 0 else "DOWN",
            "probability": round(float(probability), 6),
            "baseline_v1_probability": None if baseline is None else round(float(baseline), 6),
            "epistemic_confidence": _num(row.get("epistemic_confidence")),
            "effective_cluster_n": int(row.get("effective_cluster_n") or 0),
            "forecast_at": row.get("forecast_at"),
            "target_at": row.get("target_at"),
            "age_hours": None if age_hours is None else round(age_hours, 3),
            "freshness": "fresh" if age_hours is not None and age_hours <= max_candidate_age_hours else "stale",
            "scenario_types": scenario_types[:6],
        })

    if not rows:
        return None
    rows.sort(key=lambda row: int(row["horizon_hours"]))
    primary = next((row for row in rows if int(row["horizon_hours"]) == 720), rows[-1])
    return {
        "schema_version": "gse-v2-asset-outlook-public-v1",
        "asset": asset,
        "generated_at": generated_at,
        "max_candidate_age_hours": float(max_candidate_age_hours),
        "primary_horizon": primary,
        "horizons": rows,
        "research_only": True,
        "decision_influence": False,
        "trade_execution": False,
        "private_forecasts_exposed": False,
        "raw_evidence_exposed": False,
    }


def featured_thesis_projection(
    state_dir: Path,
    config_path: Path | None,
    *,
    generated_at: str | None = None,
) -> dict[str, Any] | None:
    """Expose one configured, sanitized market-reaction thesis from frozen GSE v2 research.

    This is intentionally not an arbitrary geopolitical-event probability. The public
    thesis is defined as asset + direction + horizon and is read from an already-frozen
    prospective GSE v2 candidate. Raw evidence, neighbour rows and private forecast IDs
    remain private.
    """
    if config_path is None:
        return None
    config = read_json(config_path, {})
    if not config or not bool(config.get("enabled", True)):
        return None

    asset = str(config.get("asset") or "").strip().upper()
    try:
        horizon = int(config.get("horizon_hours"))
        expected_direction = int(config.get("expected_direction"))
    except (TypeError, ValueError):
        return None
    if not asset or horizon <= 0 or expected_direction not in (-1, 1):
        return None

    candidates = [
        row for row in read_jsonl(state_dir / "gse_v2_regime_forecasts.jsonl")
        if str(row.get("asset") or "").upper() == asset
        and int(row.get("horizon_hours") or 0) == horizon
        and int(row.get("direction") or 0) in (-1, 1)
        and _parse_time(row.get("forecast_at")) is not None
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda row: _parse_time(row.get("forecast_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    row = candidates[0]
    p2 = _num(row.get("v2_regime_candidate_probability"))
    p1 = _num(row.get("baseline_v1_probability"))
    if p2 is None or not 0.0 <= p2 <= 1.0:
        return None

    same_direction = int(row.get("direction")) == expected_direction
    thesis_probability = p2 if same_direction else 1.0 - p2
    baseline_probability = None if p1 is None else (p1 if same_direction else 1.0 - p1)

    forecast_at = _parse_time(row.get("forecast_at"))
    reference_at = _parse_time(generated_at) or datetime.now(timezone.utc)
    age_hours = None if forecast_at is None else max(0.0, (reference_at - forecast_at).total_seconds() / 3600.0)
    max_age_hours = _num(config.get("max_candidate_age_hours"))
    if max_age_hours is None or max_age_hours <= 0:
        max_age_hours = 12.0
    freshness = "fresh" if age_hours is not None and age_hours <= max_age_hours else "stale"

    scenario_types: list[str] = []
    for diagnostic in row.get("scenario_diagnostics") or []:
        name = str((diagnostic or {}).get("scenario_type") or "").strip()
        if name and name not in scenario_types:
            scenario_types.append(name)

    return {
        "schema_version": "gse-v2-featured-thesis-public-v1",
        "thesis_id": str(config.get("thesis_id") or f"{asset}-{horizon}-{expected_direction}"),
        "question_pl": str(config.get("question_pl") or ""),
        "question_en": str(config.get("question_en") or ""),
        "asset": asset,
        "symbol": row.get("symbol"),
        "horizon_hours": horizon,
        "horizon_label": HORIZON_LABELS.get(horizon, f"{horizon}h"),
        "expected_direction": "UP" if expected_direction > 0 else "DOWN",
        "probability": round(float(thesis_probability), 6),
        "baseline_v1_probability": None if baseline_probability is None else round(float(baseline_probability), 6),
        "epistemic_confidence": _num(row.get("epistemic_confidence")),
        "effective_cluster_n": int(row.get("effective_cluster_n") or 0),
        "forecast_at": row.get("forecast_at"),
        "target_at": row.get("target_at"),
        "age_hours": None if age_hours is None else round(age_hours, 3),
        "freshness": freshness,
        "max_candidate_age_hours": float(max_age_hours),
        "scenario_types": scenario_types[:6],
        "href_pl": str(config.get("href_pl") or "/pl/geo/gse-lab.html"),
        "href_en": str(config.get("href_en") or "/en/geo/gse-lab.html"),
        "research_only": True,
        "decision_influence": False,
        "trade_execution": False,
    }


def build_projection(state_dir: Path, catalog_path: Path, featured_thesis_config: Path | None = None) -> dict[str, Any]:
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
    featured_thesis = featured_thesis_projection(state_dir, featured_thesis_config, generated_at=generated_at)
    spx_long_view = asset_outlook_projection(state_dir, "SPX", generated_at=generated_at)
    event_threat = event_threat_projection(state_dir)
    last_learning_at = latest_timestamp(row.get("recorded_at") for row in ledger)
    last_base_verification_at = latest_timestamp(row.get("verified_at") for row in base_verifications)
    last_v2_verification_at = latest_timestamp(row.get("verified_at") for row in v2_verifications)
    last_verification_at = latest_timestamp((last_base_verification_at, last_v2_verification_at))
    last_scan_at = latest_timestamp((gse_state.get("last_run_at"),))

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "featured_thesis": featured_thesis,
        "spx_long_view": spx_long_view,
        "event_threat": event_threat,
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
            "featured_thesis_projection": True,
            "spx_long_view_projection": True,
            "event_threat_projection": True,
            "event_threat_raw_evidence_exposed": False,
            "trade_execution": False,
            "belief_writeback": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a safe public GSE v2 Lab projection")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--featured-thesis-config", type=Path)
    args = parser.parse_args()
    payload = build_projection(args.state_dir, args.catalog, args.featured_thesis_config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"generated_at": payload["generated_at"], "activity": payload["activity"], "summary": payload["summary"], "best_horizon": payload["best_horizon"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

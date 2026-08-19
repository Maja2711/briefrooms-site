#!/usr/bin/env python3
"""GSE Historical Analogue / Geopolitical Forecast v2 research layer.

GSE v1 remains immutable. This module builds a versioned historical analogue
library and freezes a separate v2 candidate probability for prospective paired
calibration. It cannot trade, change v1 forecasts, tune transmission weights,
or connect to Belief Core.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import geopolitical_scenario_engine as gse

MODE = "shadow"
SCHEMA_VERSION = "gse-historical-analogue-v2"
LIBRARY_VERSION = "gse-analogue-library-v1"
CANDIDATE_VERSION = "gse-v2-candidate-v1"
CALIBRATION_VERSION = "gse-v2-calibration-v1"
MAX_OVERLAY_WEIGHT = 0.20
WEIGHT_PER_EFFECTIVE_ANALOGUE = 0.025
MIN_ANALOGUES_FOR_EXPLORATORY = 3
MIN_ANALOGUES_FOR_MEASURING = 8
HORIZONS_H = (24, 168, 720)


def parse_time(value: Any) -> datetime:
    return gse.parse_time(value)


def iso_z(value: datetime) -> str:
    return gse.iso_z(value)


def stable_id(prefix: str, *parts: Any) -> str:
    return gse.stable_id(prefix, *parts)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def canonical_sha256(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def append_unique(path: Path, rows: Iterable[Mapping[str, Any]], id_key: str) -> int:
    existing = {str(x.get(id_key)) for x in read_jsonl(path)}
    pending = [dict(x) for x in rows if str(x.get(id_key)) not in existing]
    if not pending:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in pending:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(pending)


@dataclass(frozen=True)
class DailyClose:
    timestamp: datetime
    close: float


class HistoricalMarketClient:
    """Yahoo daily-history reader. Tests inject a deterministic replacement."""

    def __init__(self, client: Optional[gse.HttpClient] = None) -> None:
        self.client = client or gse.HttpClient()

    def daily_closes(self, symbol: str, start: datetime, end: datetime) -> List[DailyClose]:
        period1 = int(start.astimezone(timezone.utc).timestamp())
        period2 = int(end.astimezone(timezone.utc).timestamp())
        url = (
            gse.YAHOO_CHART.format(symbol=urllib.parse.quote(symbol, safe=""))
            + f"?period1={period1}&period2={period2}&interval=1d&events=history&includeAdjustedClose=true"
        )
        try:
            payload = self.client.get_json(url)
            result = ((payload.get("chart") or {}).get("result") or [None])[0] or {}
            timestamps = result.get("timestamp") or []
            quote = (((result.get("indicators") or {}).get("quote") or [{}])[0])
            closes = quote.get("close") or []
        except Exception:
            return []
        out: List[DailyClose] = []
        for ts, close in zip(timestamps, closes):
            if close is None:
                continue
            try:
                value = float(close)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value) or value <= 0:
                continue
            out.append(DailyClose(datetime.fromtimestamp(int(ts), tz=timezone.utc), value))
        return sorted(out, key=lambda x: x.timestamp)


def load_catalog(path: Path) -> Dict[str, Any]:
    payload = read_json(path, {})
    events = list(payload.get("events") or [])
    if not payload.get("catalog_version") or not events:
        raise ValueError("historical analogue catalogue must have catalog_version and events")
    seen = set()
    for event in events:
        eid = str(event.get("event_id") or "")
        if not eid or eid in seen:
            raise ValueError("event_id must be unique and non-empty")
        seen.add(eid)
        parse_time(event["event_at"])
        scenario_types = tuple(event.get("scenario_types") or ())
        if not scenario_types:
            raise ValueError(f"event {eid} has no scenario_types")
        unknown = [x for x in scenario_types if x not in gse.SCENARIO_RULES]
        if unknown:
            raise ValueError(f"event {eid} has unknown scenario types: {unknown}")
    return payload


def _previous_close(points: Sequence[DailyClose], event_at: datetime) -> Optional[DailyClose]:
    rows = [x for x in points if x.timestamp.date() < event_at.date()]
    return rows[-1] if rows else None


def _target_close(points: Sequence[DailyClose], target: datetime) -> Optional[DailyClose]:
    rows = [x for x in points if x.timestamp.date() >= target.date()]
    return rows[0] if rows else None


def _threshold(asset: str) -> float:
    return 0.0005 if asset == "US10Y" else 0.001


def event_response_rows(
    catalog: Mapping[str, Any],
    histories: Mapping[str, Sequence[DailyClose]],
) -> List[Dict[str, Any]]:
    """Compute outcomes independently of the curated event catalogue.

    The catalogue contains no market outcomes. Baseline is the last daily close
    strictly before the event date; target is the first close on/after the fixed
    horizon. That convention is frozen in library metadata.
    """
    rows: List[Dict[str, Any]] = []
    for event in catalog.get("events") or []:
        event_at = parse_time(event["event_at"])
        for scenario_type in event.get("scenario_types") or []:
            impacts = gse.TRANSMISSION_GRAPH.get(str(scenario_type), {})
            for asset, impact_raw in impacts.items():
                impact = float(impact_raw)
                if not impact or asset not in gse.ASSETS:
                    continue
                points = list(histories.get(asset) or [])
                baseline = _previous_close(points, event_at)
                if baseline is None:
                    continue
                for horizon_h in HORIZONS_H:
                    target_at = event_at + timedelta(hours=horizon_h)
                    target = _target_close(points, target_at)
                    if target is None:
                        continue
                    raw_return = target.close / baseline.close - 1.0
                    expected_direction = 1 if impact > 0 else -1
                    aligned_return = raw_return * expected_direction
                    rows.append({
                        "response_id": stable_id("gse-analogue-response", event["event_id"], scenario_type, asset, horizon_h),
                        "event_id": event["event_id"],
                        "market_anchor_key": f"{event_at.date().isoformat()}|{asset}|{horizon_h}",
                        "event_at": iso_z(event_at),
                        "label": event.get("label"),
                        "scenario_type": scenario_type,
                        "asset": asset,
                        "symbol": gse.ASSETS[asset]["symbol"],
                        "horizon_hours": horizon_h,
                        "transmission_weight": impact,
                        "expected_direction": expected_direction,
                        "baseline_at": iso_z(baseline.timestamp),
                        "baseline_value": round(baseline.close, 8),
                        "target_at": iso_z(target.timestamp),
                        "target_value": round(target.close, 8),
                        "raw_return": round(raw_return, 8),
                        "aligned_return": round(aligned_return, 8),
                        "directional_success": aligned_return >= _threshold(asset),
                        "response_complete_at": iso_z(target.timestamp + timedelta(days=1)),
                        "source": event.get("source"),
                        "source_ref": event.get("source_ref"),
                    })
    return rows


def build_library(
    catalog: Mapping[str, Any],
    market: HistoricalMarketClient,
    *,
    built_at: datetime,
) -> Dict[str, Any]:
    events = list(catalog.get("events") or [])
    earliest = min(parse_time(x["event_at"]) for x in events) - timedelta(days=10)
    latest = max(parse_time(x["event_at"]) for x in events) + timedelta(days=45)
    histories: Dict[str, List[DailyClose]] = {}
    for asset, meta in gse.ASSETS.items():
        histories[asset] = market.daily_closes(str(meta["symbol"]), earliest, latest)
    responses = event_response_rows(catalog, histories)
    missing_assets = sorted(asset for asset, points in histories.items() if not points)
    catalog_projection = {
        "schema_version": catalog.get("schema_version"),
        "catalog_version": catalog.get("catalog_version"),
        "events": catalog.get("events"),
    }
    return {
        "schema_version": LIBRARY_VERSION,
        "catalog_version": catalog.get("catalog_version"),
        "catalog_sha256": canonical_sha256(catalog_projection),
        "built_at": iso_z(built_at),
        "mode": MODE,
        "market_data_source": "Yahoo Finance chart daily OHLC",
        "baseline_rule": "last_daily_close_strictly_before_event_date",
        "target_rule": "first_daily_close_on_or_after_event_plus_horizon",
        "same_date_effective_sample_cap": True,
        "automatic_event_selection_enabled": False,
        "automatic_tuning_enabled": False,
        "decision_influence": False,
        "events": events,
        "responses": responses,
        "coverage": {
            "catalog_events": len(events),
            "response_rows": len(responses),
            "assets_with_history": len(gse.ASSETS) - len(missing_assets),
            "assets_missing_history": missing_assets,
        },
    }


def library_needs_refresh(existing: Mapping[str, Any], catalog: Mapping[str, Any]) -> bool:
    projection = {
        "schema_version": catalog.get("schema_version"),
        "catalog_version": catalog.get("catalog_version"),
        "events": catalog.get("events"),
    }
    return (
        existing.get("schema_version") != LIBRARY_VERSION
        or existing.get("catalog_sha256") != canonical_sha256(projection)
    )


def eligible_analogue_rows(
    library: Mapping[str, Any],
    *,
    scenario_type: str,
    asset: str,
    horizon_hours: int,
    forecast_at: datetime,
) -> List[Dict[str, Any]]:
    """Return only analogue outcomes completely known before forecast_at."""
    rows = [
        dict(x)
        for x in library.get("responses") or []
        if str(x.get("scenario_type")) == scenario_type
        and str(x.get("asset")) == asset
        and int(x.get("horizon_hours") or 0) == int(horizon_hours)
        and parse_time(x.get("response_complete_at")) <= forecast_at
    ]
    # Same-date catalogue entries can be different descriptions of one market
    # shock. Keep only the strongest transmission as one effective episode.
    dedup: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("market_anchor_key"))
        current = dedup.get(key)
        if current is None or abs(float(row.get("transmission_weight") or 0.0)) > abs(float(current.get("transmission_weight") or 0.0)):
            dedup[key] = row
    return sorted(dedup.values(), key=lambda x: (x.get("event_at"), x.get("event_id")))


def analogue_stats(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {
            "effective_n": 0,
            "status": "no_eligible_analogues",
            "directional_hit_rate": None,
            "shrunk_direction_probability": None,
            "mean_raw_return": None,
            "median_raw_return": None,
            "mean_aligned_return": None,
            "dispersion": None,
            "event_ids": [],
            "market_anchor_keys": [],
        }
    raw = [float(x["raw_return"]) for x in rows]
    aligned = [float(x["aligned_return"]) for x in rows]
    hits = sum(1 for x in rows if bool(x["directional_success"]))
    n = len(rows)
    # Beta(2,2) shrinkage: tiny samples cannot create extreme probabilities.
    shrunk = (hits + 2.0) / (n + 4.0)
    status = (
        "insufficient_sample"
        if n < MIN_ANALOGUES_FOR_EXPLORATORY
        else "exploratory"
        if n < MIN_ANALOGUES_FOR_MEASURING
        else "measuring"
    )
    return {
        "effective_n": n,
        "status": status,
        "directional_hit_rate": round(hits / n, 6),
        "shrunk_direction_probability": round(shrunk, 6),
        "mean_raw_return": round(mean(raw), 8),
        "median_raw_return": round(median(raw), 8),
        "mean_aligned_return": round(mean(aligned), 8),
        "dispersion": round(pstdev(aligned), 8) if n > 1 else 0.0,
        "event_ids": [str(x["event_id"]) for x in rows],
        "market_anchor_keys": [str(x["market_anchor_key"]) for x in rows],
        "latest_analogue_response_complete_at": max(str(x["response_complete_at"]) for x in rows),
    }


def candidate_from_baseline(
    baseline: Mapping[str, Any],
    library: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    forecast_at = parse_time(baseline["forecast_at"])
    asset = str(baseline["asset"])
    horizon = int(baseline["horizon_hours"])
    baseline_direction = int(baseline["direction"])
    p0 = float(baseline["predicted_probability"])
    scenario_snapshot = list(baseline.get("scenario_snapshot") or [])
    weighted: List[Tuple[float, float, Dict[str, Any]]] = []
    scenario_diagnostics: List[Dict[str, Any]] = []

    for scenario in scenario_snapshot:
        scenario_type = str(scenario.get("scenario_type") or "")
        impact = float(gse.TRANSMISSION_GRAPH.get(scenario_type, {}).get(asset, 0.0))
        if not impact:
            continue
        rows = eligible_analogue_rows(
            library,
            scenario_type=scenario_type,
            asset=asset,
            horizon_hours=horizon,
            forecast_at=forecast_at,
        )
        stats = analogue_stats(rows)
        pa = stats.get("shrunk_direction_probability")
        n = int(stats.get("effective_n") or 0)
        expected_direction = 1 if impact > 0 else -1
        p_for_baseline = None
        if pa is not None:
            p_for_baseline = float(pa) if expected_direction == baseline_direction else 1.0 - float(pa)
        diag = {
            "scenario_type": scenario_type,
            "transmission_weight": impact,
            "expected_direction": expected_direction,
            "probability_for_baseline_direction": None if p_for_baseline is None else round(p_for_baseline, 6),
            **stats,
        }
        scenario_diagnostics.append(diag)
        if p_for_baseline is None or n <= 0:
            continue
        scenario_weight = (
            abs(impact)
            * float(scenario.get("probability") or 0.0)
            * float(scenario.get("confidence") or 0.0)
        )
        if scenario_weight > 0:
            weighted.append((scenario_weight, p_for_baseline, diag))

    if not weighted:
        return None
    total_weight = sum(x[0] for x in weighted)
    analogue_probability = sum(w * p for w, p, _ in weighted) / total_weight

    # Cap effective N across scenarios by unique market episodes, not catalogue
    # labels. This prevents invasion + same-day sanctions from counting twice.
    effective_anchors = set()
    for _, _, diag in weighted:
        effective_anchors.update(diag.get("market_anchor_keys") or [])
    effective_n = len(effective_anchors)
    overlay_weight = min(MAX_OVERLAY_WEIGHT, WEIGHT_PER_EFFECTIVE_ANALOGUE * effective_n)
    p2 = clamp(p0 + overlay_weight * (analogue_probability - p0), 0.50, 0.85)

    payload = {
        "schema_version": CANDIDATE_VERSION,
        "candidate_id": stable_id("gse-v2", baseline["forecast_id"]),
        "baseline_forecast_id": baseline["forecast_id"],
        "batch_id": baseline.get("batch_id"),
        "asset": asset,
        "symbol": baseline.get("symbol"),
        "forecast_at": baseline["forecast_at"],
        "target_at": baseline["target_at"],
        "horizon_hours": horizon,
        "direction": baseline_direction,
        "baseline_v1_probability": round(p0, 6),
        "historical_analogue_probability": round(analogue_probability, 6),
        "v2_candidate_probability": round(p2, 6),
        "overlay_weight": round(overlay_weight, 6),
        "effective_analogue_n": effective_n,
        "analogue_status": (
            "insufficient_sample"
            if effective_n < MIN_ANALOGUES_FOR_EXPLORATORY
            else "exploratory"
            if effective_n < MIN_ANALOGUES_FOR_MEASURING
            else "measuring"
        ),
        "conditional_market_response": {
            "probability_of_baseline_direction": round(analogue_probability, 6),
            "effective_market_episodes": effective_n,
            "scenario_slices": scenario_diagnostics,
        },
        "scenario_analogue_diagnostics": scenario_diagnostics,
        "catalog_version": library.get("catalog_version"),
        "catalog_sha256": library.get("catalog_sha256"),
        "formula": "p_v2=p_v1+min(0.20,0.025*N_effective)*(p_analogue_for_v1_direction-p_v1); clamp[0.50,0.85]",
        "v1_forecast_modified": False,
        "trade_execution_enabled": False,
        "policy_output_enabled": False,
        "automatic_tuning_enabled": False,
        "decision_influence": False,
    }
    payload["candidate_sha256"] = canonical_sha256(
        {k: v for k, v in payload.items() if k != "candidate_sha256"}
    )
    return payload


def freeze_candidates(state_dir: Path, library: Mapping[str, Any]) -> int:
    baseline_rows = read_jsonl(state_dir / "gse_forecasts.jsonl")
    existing = {
        str(x.get("baseline_forecast_id"))
        for x in read_jsonl(state_dir / "gse_v2_forecasts.jsonl")
    }
    pending = []
    for baseline in baseline_rows:
        if str(baseline.get("forecast_id")) in existing:
            continue
        candidate = candidate_from_baseline(baseline, library)
        if candidate is not None:
            pending.append(candidate)
    return append_unique(state_dir / "gse_v2_forecasts.jsonl", pending, "candidate_id")


def verify_candidates(state_dir: Path, now: datetime) -> int:
    candidates = read_jsonl(state_dir / "gse_v2_forecasts.jsonl")
    baseline_verifications = {
        str(x.get("forecast_id")): x
        for x in read_jsonl(state_dir / "gse_verifications.jsonl")
        if x.get("forecast_id")
    }
    existing = {
        str(x.get("candidate_id"))
        for x in read_jsonl(state_dir / "gse_v2_verifications.jsonl")
    }
    rows = []
    for candidate in candidates:
        cid = str(candidate["candidate_id"])
        if cid in existing:
            continue
        baseline_v = baseline_verifications.get(str(candidate["baseline_forecast_id"]))
        if baseline_v is None:
            continue
        outcome = bool(baseline_v["outcome"])
        y = 1.0 if outcome else 0.0
        p0 = float(candidate["baseline_v1_probability"])
        p2 = float(candidate["v2_candidate_probability"])
        brier0 = (p0 - y) ** 2
        brier2 = (p2 - y) ** 2
        ll0 = gse._log_loss(p0, outcome)
        ll2 = gse._log_loss(p2, outcome)
        rows.append({
            "verification_id": stable_id("gse-v2-verification", cid),
            "candidate_id": cid,
            "baseline_forecast_id": candidate["baseline_forecast_id"],
            "asset": candidate["asset"],
            "horizon_hours": candidate["horizon_hours"],
            "outcome": outcome,
            "verified_at": baseline_v.get("verified_at") or iso_z(now),
            "baseline_v1_probability": round(p0, 6),
            "v2_candidate_probability": round(p2, 6),
            "baseline_brier": round(brier0, 8),
            "candidate_brier": round(brier2, 8),
            "delta_brier_v2_minus_v1": round(brier2 - brier0, 8),
            "baseline_log_loss": round(ll0, 8),
            "candidate_log_loss": round(ll2, 8),
            "delta_log_loss_v2_minus_v1": round(ll2 - ll0, 8),
            "effective_analogue_n": candidate.get("effective_analogue_n"),
            "calibration_eligible": True,
            "decision_influence": False,
        })
    return append_unique(state_dir / "gse_v2_verifications.jsonl", rows, "verification_id")


def _paired_metrics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {
            "paired_n": 0,
            "status": "awaiting_outcomes",
            "baseline_mean_brier": None,
            "candidate_mean_brier": None,
            "delta_brier_v2_minus_v1": None,
            "baseline_mean_log_loss": None,
            "candidate_mean_log_loss": None,
            "delta_log_loss_v2_minus_v1": None,
            "v2_brier_better_rate": None,
        }
    n = len(rows)
    base_b = mean(float(x["baseline_brier"]) for x in rows)
    cand_b = mean(float(x["candidate_brier"]) for x in rows)
    base_l = mean(float(x["baseline_log_loss"]) for x in rows)
    cand_l = mean(float(x["candidate_log_loss"]) for x in rows)
    return {
        "paired_n": n,
        "status": "insufficient_sample" if n < 30 else "measuring",
        "baseline_mean_brier": round(base_b, 6),
        "candidate_mean_brier": round(cand_b, 6),
        "delta_brier_v2_minus_v1": round(cand_b - base_b, 6),
        "baseline_mean_log_loss": round(base_l, 6),
        "candidate_mean_log_loss": round(cand_l, 6),
        "delta_log_loss_v2_minus_v1": round(cand_l - base_l, 6),
        "v2_brier_better_rate": round(
            sum(float(x["candidate_brier"]) < float(x["baseline_brier"]) for x in rows) / n,
            6,
        ),
    }


def build_calibration(state_dir: Path) -> Dict[str, Any]:
    rows = [
        x
        for x in read_jsonl(state_dir / "gse_v2_verifications.jsonl")
        if bool(x.get("calibration_eligible", True))
    ]
    by_asset: Dict[str, Any] = {}
    by_horizon: Dict[str, Any] = {}
    for asset in sorted({str(x["asset"]) for x in rows}):
        by_asset[asset] = _paired_metrics([x for x in rows if str(x["asset"]) == asset])
    for horizon in sorted({int(x["horizon_hours"]) for x in rows}):
        by_horizon[str(horizon)] = _paired_metrics(
            [x for x in rows if int(x["horizon_hours"]) == horizon]
        )
    report = {
        "schema_version": CALIBRATION_VERSION,
        "mode": MODE,
        "comparison": "GSE_v1_without_historical_analogues_vs_GSE_v2_with_historical_analogue_overlay",
        "overall": _paired_metrics(rows),
        "by_asset": by_asset,
        "by_horizon_hours": by_horizon,
        "interpretation": {
            "delta_brier_v2_minus_v1": "Negative is better for v2.",
            "delta_log_loss_v2_minus_v1": "Negative is better for v2.",
            "sample": "Descriptive only; no automatic promotion or transmission-weight tuning.",
        },
        "controls": {
            "v1_forecast_modified": False,
            "trade_execution_enabled": False,
            "policy_output_enabled": False,
            "automatic_tuning_enabled": False,
            "decision_engine_connected": False,
            "belief_core_connected": False,
        },
    }
    write_json(state_dir / "gse_v2_calibration.json", report)
    return report


def run(
    state_dir: Path,
    catalog_path: Path,
    now: datetime,
    market: Optional[HistoricalMarketClient] = None,
) -> Dict[str, Any]:
    state_dir.mkdir(parents=True, exist_ok=True)
    catalog = load_catalog(catalog_path)
    library_path = state_dir / "gse_historical_analogue_library.json"
    library = read_json(library_path, {})
    refreshed = False
    if library_needs_refresh(library, catalog):
        library = build_library(catalog, market or HistoricalMarketClient(), built_at=now)
        write_json(library_path, library)
        refreshed = True
    frozen = freeze_candidates(state_dir, library)
    verified = verify_candidates(state_dir, now)
    calibration = build_calibration(state_dir)
    state = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "generated_at": iso_z(now),
        "library_refreshed": refreshed,
        "library_coverage": library.get("coverage") or {},
        "candidates_frozen_this_run": frozen,
        "candidates_total": len(read_jsonl(state_dir / "gse_v2_forecasts.jsonl")),
        "candidates_verified_this_run": verified,
        "candidate_verifications_total": calibration["overall"]["paired_n"],
        "controls": {
            "v1_forecast_modified": False,
            "trade_execution_enabled": False,
            "policy_output_enabled": False,
            "automatic_tuning_enabled": False,
            "decision_engine_connected": False,
            "belief_core_connected": False,
        },
    }
    write_json(state_dir / "gse_v2_state.json", state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run GSE historical analogue v2 shadow research layer"
    )
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--catalog", default="data/gse/historical_event_catalog.json")
    parser.add_argument("--now", help="ISO timestamp override")
    args = parser.parse_args()
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    state = run(Path(args.state_dir), Path(args.catalog), now)
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

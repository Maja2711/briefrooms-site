#!/usr/bin/env python3
"""PR #11 — BRACE Sector / Factor Belief Foundation.

Prospective, research-shadow sector/factor Belief layer that sits between the
broad-market foundation and later company/entity beliefs.

This module deliberately does *not* influence BRACE. It collects and calibrates
sector/factor leadership beliefs only. Any future Engine ↔ Belief bridge must
produce paired WITH vs WITHOUT BELIEF economic evidence before promotion review.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from belief_adapter_contract import (
    AdapterResult,
    EvidenceAssessment,
    Observation,
    clamp,
    observation_to_evidence,
    stable_id,
)
from belief_core import BeliefCore, BeliefDefinition, Evidence, iso_z, parse_time
from belief_market_data_adapter import Bar, MarketSnapshot, YahooChartClient

NY = ZoneInfo("America/New_York")
MODE = "research_shadow"
SCHEMA_VERSION = "brace-sector-factor-belief-v1"
REPORT_VERSION = "brace-sector-factor-belief-report-v1"
CAPTURE_AFTER_NY = time(16, 10)
MIN_DESCRIPTIVE_N = 12
MIN_RELATIONSHIP_N = 30

# The taxonomy intentionally starts small and liquid. It covers the main sectors
# represented by the governed BRACE universe plus a small set of style factors.
# Company/entity beliefs remain a later reviewed layer.
SPEC_ROWS: Tuple[Mapping[str, Any], ...] = (
    {"belief_id": "sector.technology.leadership", "layer": "sector", "entity": "SECTOR:TECHNOLOGY", "domain": "sector_leadership", "numerator": "XLK", "denominator": "SPY", "label": "US Technology"},
    {"belief_id": "sector.financials.leadership", "layer": "sector", "entity": "SECTOR:FINANCIALS", "domain": "sector_leadership", "numerator": "XLF", "denominator": "SPY", "label": "US Financials"},
    {"belief_id": "sector.health_care.leadership", "layer": "sector", "entity": "SECTOR:HEALTH_CARE", "domain": "sector_leadership", "numerator": "XLV", "denominator": "SPY", "label": "US Health Care"},
    {"belief_id": "sector.consumer_discretionary.leadership", "layer": "sector", "entity": "SECTOR:CONSUMER_DISCRETIONARY", "domain": "sector_leadership", "numerator": "XLY", "denominator": "SPY", "label": "US Consumer Discretionary"},
    {"belief_id": "sector.consumer_staples.leadership", "layer": "sector", "entity": "SECTOR:CONSUMER_STAPLES", "domain": "sector_leadership", "numerator": "XLP", "denominator": "SPY", "label": "US Consumer Staples"},
    {"belief_id": "sector.communication_services.leadership", "layer": "sector", "entity": "SECTOR:COMMUNICATION_SERVICES", "domain": "sector_leadership", "numerator": "XLC", "denominator": "SPY", "label": "US Communication Services"},
    {"belief_id": "sector.semiconductors.leadership", "layer": "sector", "entity": "SECTOR:SEMICONDUCTORS", "domain": "sector_leadership", "numerator": "SOXX", "denominator": "SPY", "label": "US Semiconductors"},
    {"belief_id": "factor.growth.leadership", "layer": "factor", "entity": "FACTOR:GROWTH", "domain": "factor_leadership", "numerator": "IWF", "denominator": "IWD", "label": "US Growth vs Value"},
    {"belief_id": "factor.quality.leadership", "layer": "factor", "entity": "FACTOR:QUALITY", "domain": "factor_leadership", "numerator": "QUAL", "denominator": "SPY", "label": "US Quality"},
    {"belief_id": "factor.momentum.leadership", "layer": "factor", "entity": "FACTOR:MOMENTUM", "domain": "factor_leadership", "numerator": "MTUM", "denominator": "SPY", "label": "US Momentum"},
    {"belief_id": "factor.small_cap.leadership", "layer": "factor", "entity": "FACTOR:SMALL_CAP", "domain": "factor_leadership", "numerator": "IWM", "denominator": "SPY", "label": "US Small Cap"},
)

SPEC_BY_ID: Dict[str, Mapping[str, Any]] = {str(row["belief_id"]): row for row in SPEC_ROWS}
SECTOR_FACTOR_BELIEF_IDS: Tuple[str, ...] = tuple(str(row["belief_id"]) for row in SPEC_ROWS)
REQUIRED_SYMBOLS: Tuple[str, ...] = tuple(sorted({str(row[k]) for row in SPEC_ROWS for k in ("numerator", "denominator")}))

SECTOR_FACTOR_BELIEFS: Tuple[BeliefDefinition, ...] = tuple(
    BeliefDefinition(
        belief_id=str(row["belief_id"]),
        claim=f"{row['label']} maintains relative leadership into the next available US trading session",
        prior_probability=.50,
        half_life_hours=36.0,
        entity=str(row["entity"]),
        domain=str(row["domain"]),
        tags=("BRACE", "sector_factor", f"layer:{row['layer']}", "taxonomy:v1"),
        horizon_hours=24.0,
        outcome_rule="relative_ratio_not_below_frozen_reference",
    )
    for row in SPEC_ROWS
)


def safety_controls() -> Dict[str, bool]:
    """Controls that must stay false in PR #11."""
    return {
        "active_decision_influence": False,
        "candidate_ranking_change": False,
        "target_exposure_change": False,
        "score_change": False,
        "sizing_change": False,
        "veto": False,
        "direction_reversal": False,
        "forced_exit": False,
        "trade_execution": False,
        "policy_output": False,
        "automatic_tuning": False,
        "bounded_influence": False,
        "historical_backfill": False,
        "company_entity_beliefs_enabled": False,
    }


def capabilities() -> Dict[str, bool]:
    return {
        "sector_factor_shadow_collection_enabled": True,
        "sector_factor_calibration_enabled": True,
        "with_without_bridge_enabled": False,
        "promotion_gate_enabled": False,
        "company_entity_beliefs_enabled": False,
    }


def _assert_safety() -> None:
    enabled = [key for key, value in safety_controls().items() if value is not False]
    if enabled:
        raise RuntimeError("PR11 zero-influence invariant violated: " + ",".join(enabled))


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _ratio_return(snapshot: MarketSnapshot, numerator: str, denominator: str, bars: int) -> float:
    return float(snapshot.ratio_return(numerator, denominator, bars))


def _leadership_score(snapshot: MarketSnapshot, numerator: str, denominator: str) -> Tuple[float, float, float]:
    """Blend short and multi-session relative momentum without claiming independence."""
    rel_1d = _ratio_return(snapshot, numerator, denominator, 13)
    rel_4d = _ratio_return(snapshot, numerator, denominator, 52)
    score = clamp(.60 * clamp(rel_1d / .015, -1.0, 1.0) + .40 * clamp(rel_4d / .040, -1.0, 1.0), -1.0, 1.0)
    return rel_1d, rel_4d, score


def _observation(row: Mapping[str, Any], snapshot: MarketSnapshot) -> Observation:
    numerator, denominator = str(row["numerator"]), str(row["denominator"])
    observed_at = iso_z(min(snapshot.observed_at(numerator), snapshot.observed_at(denominator)))
    rel_1d, rel_4d, score = _leadership_score(snapshot, numerator, denominator)
    return Observation.make(
        adapter="brace_sector_factor",
        metric="relative_leadership_score",
        entity=str(row["entity"]),
        observed_at=observed_at,
        value={
            "score": round(score, 8),
            "relative_return_1d": round(rel_1d, 8),
            "relative_return_4d": round(rel_4d, 8),
            "numerator": numerator,
            "denominator": denominator,
        },
        unit="score",
        source="Derived ETF relative-performance view from Yahoo Finance charts",
        source_type="derived",
        source_ref=f"derived:yahoo:{numerator}/{denominator}:{observed_at}:pr11",
        reliability=.80,
        independence_cluster=f"market:{numerator}-{denominator}:relative-price",
        tags=("BRACE", "sector_factor", f"layer:{row['layer']}", "shadow"),
        metadata={
            "proxy_only": True,
            "same_price_cluster": True,
            "interpretation": "ETF relative performance is a leadership proxy, not a complete sector/factor fundamental model",
            "historical_backfill": False,
        },
    )


def build_evidence(snapshot: MarketSnapshot) -> AdapterResult:
    observations = []
    evidence: list[Evidence] = []
    for row in SPEC_ROWS:
        numerator, denominator = str(row["numerator"]), str(row["denominator"])
        if numerator not in snapshot.bars or denominator not in snapshot.bars:
            continue
        obs = _observation(row, snapshot)
        observations.append(obs)
        score = float((obs.value or {}).get("score", 0.0))
        evidence.append(observation_to_evidence(
            obs,
            EvidenceAssessment(
                str(row["belief_id"]),
                1 if score >= 0 else -1,
                clamp(abs(score), .05, .75),
                "relative_leadership_proxy",
                f"{numerator}/{denominator} blended relative-leadership score={score:+.4f}.",
                metadata={
                    "layer": row["layer"],
                    "numerator": numerator,
                    "denominator": denominator,
                    "with_without_required_before_promotion": True,
                },
            ),
        ))
    return AdapterResult("brace_sector_factor", tuple(observations), tuple(evidence))


def outcome_spec(belief_id: str, snapshot: MarketSnapshot) -> Dict[str, Any]:
    row = SPEC_BY_ID[belief_id]
    numerator, denominator = str(row["numerator"]), str(row["denominator"])
    return {
        "kind": "relative_ratio_not_below",
        "numerator": numerator,
        "denominator": denominator,
        "reference": snapshot.ratio(numerator, denominator),
        "target_contract": "first_available_us_trading_session_on_or_after_target_date",
    }


def evaluate_outcome(spec: Mapping[str, Any], closes: Mapping[str, float]) -> bool:
    if str(spec.get("kind")) != "relative_ratio_not_below":
        raise ValueError(f"Unsupported PR11 outcome kind: {spec.get('kind')}")
    numerator, denominator = str(spec["numerator"]), str(spec["denominator"])
    if numerator not in closes or denominator not in closes:
        raise KeyError("Missing target close for relative outcome")
    target_ratio = float(closes[numerator]) / float(closes[denominator])
    return target_ratio >= float(spec["reference"])


def next_weekday(day: date) -> date:
    target = day + timedelta(days=1)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target


def target_at_for_market_date(market_date: date) -> datetime:
    target_day = next_weekday(market_date)
    return datetime.combine(target_day, time(16, 5), tzinfo=NY).astimezone(timezone.utc)


def analysis_stage(n: int) -> str:
    if n < MIN_DESCRIPTIVE_N:
        return "collecting_warmup"
    if n < MIN_RELATIONSHIP_N:
        return "descriptive_only"
    return "sector_factor_calibration_analysis_available"


def serial_effective_n(rows: Sequence[Mapping[str, Any]]) -> float:
    """Conservative lag-1 residual ESS estimate; descriptive, never a promotion gate."""
    ordered = sorted(rows, key=lambda x: (str(x.get("target_at", "")), str(x.get("forecast_id", ""))))
    residuals = [float(bool(x.get("outcome"))) - float(x.get("predicted_probability", .5)) for x in ordered]
    n = len(residuals)
    if n < 4:
        return float(n)
    mean = sum(residuals) / n
    denom = sum((x - mean) ** 2 for x in residuals)
    if denom <= 1e-12:
        return float(n)
    rho_num = sum((residuals[i] - mean) * (residuals[i - 1] - mean) for i in range(1, n))
    rho = clamp(rho_num / denom, -.80, .80)
    ess = n * (1.0 - rho) / (1.0 + rho)
    return round(max(1.0, min(float(n), ess)), 3)


def _fetch_snapshot(client: YahooChartClient) -> Tuple[MarketSnapshot, Dict[str, str]]:
    bars: Dict[str, Sequence[Bar]] = {}
    failures: Dict[str, str] = {}
    for symbol in REQUIRED_SYMBOLS:
        try:
            bars[symbol] = client.bars(symbol, "10d", "30m")
        except Exception as exc:
            failures[symbol] = f"{type(exc).__name__}: {exc}"
    return MarketSnapshot(bars), failures


def _daily_close_on_or_after(client: YahooChartClient, symbol: str, target_day: date, max_days: int = 4) -> Optional[Tuple[date, float]]:
    rows = client.bars(symbol, "1mo", "1d")
    candidates = []
    for bar in rows:
        day = bar.timestamp.astimezone(NY).date()
        if target_day <= day <= target_day + timedelta(days=max_days):
            candidates.append((day, float(bar.close)))
    return min(candidates, key=lambda x: x[0]) if candidates else None


def _resolve_due_forecasts(core: BeliefCore, client: YahooChartClient, now: datetime) -> int:
    resolved = 0
    cache: Dict[Tuple[str, date], Optional[Tuple[date, float]]] = {}
    verified_ids = {str(v.forecast_id) for v in core.verifications.values() if v.forecast_id}
    for forecast in sorted(core.forecasts.values(), key=lambda x: (x.target_at, x.forecast_id)):
        if forecast.forecast_id in verified_ids or parse_time(forecast.target_at) > now:
            continue
        metadata = dict(forecast.metadata or {})
        spec = dict(metadata.get("outcome_spec") or {})
        if spec.get("kind") != "relative_ratio_not_below":
            continue
        target_day = date.fromisoformat(str(metadata.get("target_session_floor") or forecast.target_at)[:10])
        closes: Dict[str, float] = {}
        resolved_days = set()
        unavailable = False
        for symbol in (str(spec["numerator"]), str(spec["denominator"])):
            key = (symbol, target_day)
            if key not in cache:
                try:
                    cache[key] = _daily_close_on_or_after(client, symbol, target_day)
                except Exception:
                    cache[key] = None
            row = cache[key]
            if row is None:
                unavailable = True
                break
            resolved_days.add(row[0])
            closes[symbol] = row[1]
        if unavailable or len(resolved_days) != 1:
            continue
        actual_day = next(iter(resolved_days))
        outcome = evaluate_outcome(spec, closes)
        core.verify_forecast(
            forecast.forecast_id,
            outcome,
            verified_at=now,
            note=f"PR11 target relative ratio resolved on {actual_day.isoformat()}",
            outcome_source="Yahoo Finance daily chart",
            outcome_ref=f"yahoo:daily:{spec['numerator']}/{spec['denominator']}:{actual_day.isoformat()}",
        )
        resolved += 1
    return resolved


def _wrapper_default() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "activated_at": None,
        "activation_market_date": None,
        "last_run_at": None,
        "last_capture_at": None,
    }


def _current_market_date(snapshot: MarketSnapshot) -> Optional[date]:
    if "SPY" not in snapshot.bars or not snapshot.bars["SPY"]:
        return None
    return snapshot.observed_at("SPY").astimezone(NY).date()


def should_capture(wrapper: Mapping[str, Any], snapshot: MarketSnapshot, now: datetime) -> bool:
    market_date = _current_market_date(snapshot)
    if market_date is None or now.astimezone(NY).weekday() >= 5:
        return False
    if now.astimezone(NY).time() < CAPTURE_AFTER_NY:
        return False
    if not snapshot.is_current_session(now):
        return False
    activation_market_date = wrapper.get("activation_market_date")
    if not activation_market_date or market_date <= date.fromisoformat(str(activation_market_date)):
        return False
    return True


def _capture_for_available(core: BeliefCore, snapshot: MarketSnapshot, now: datetime) -> int:
    market_date = _current_market_date(snapshot)
    if market_date is None:
        return 0
    existing = {
        (str(f.belief_id), str((f.metadata or {}).get("market_date")))
        for f in core.forecasts.values()
    }
    target_at = target_at_for_market_date(market_date)
    count = 0
    for belief_id in SECTOR_FACTOR_BELIEF_IDS:
        row = SPEC_BY_ID[belief_id]
        numerator, denominator = str(row["numerator"]), str(row["denominator"])
        if numerator not in snapshot.bars or denominator not in snapshot.bars:
            continue
        key = (belief_id, market_date.isoformat())
        if key in existing:
            continue
        spec = outcome_spec(belief_id, snapshot)
        core.capture_forecast(
            belief_id,
            as_of=now,
            target_at=target_at,
            regime="sector_factor_shadow",
            forecast_id=stable_id("pr11-forecast", belief_id, market_date.isoformat()),
            metadata={
                "schema_version": SCHEMA_VERSION,
                "layer": str(row["layer"]),
                "market_date": market_date.isoformat(),
                "target_session_floor": target_at.astimezone(NY).date().isoformat(),
                "outcome_spec": spec,
                "historical_backfill": False,
                "with_without_required_before_promotion": True,
            },
        )
        count += 1
    return count


def _sample_report(core: BeliefCore) -> Dict[str, Any]:
    rows = [v.to_dict() for v in core.verifications.values() if v.calibration_eligible and not v.legacy]
    by_belief: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_belief[str(row["belief_id"])].append(row)
    raw_by_belief = {belief_id: len(by_belief.get(belief_id, [])) for belief_id in SECTOR_FACTOR_BELIEF_IDS}
    ess_by_belief = {belief_id: serial_effective_n(by_belief.get(belief_id, [])) for belief_id in SECTOR_FACTOR_BELIEF_IDS}
    positive_ess = [value for value in ess_by_belief.values() if value > 0]
    layer_effective_n = min(positive_ess) if len(positive_ess) == len(SECTOR_FACTOR_BELIEF_IDS) else 0.0
    unique_target_sessions = len({str(row.get("target_at", ""))[:10] for row in rows})
    return {
        "raw_verifications": len(rows),
        "unique_target_sessions": unique_target_sessions,
        "raw_n_by_belief": raw_by_belief,
        "serial_effective_n_by_belief": ess_by_belief,
        "conservative_layer_effective_n": layer_effective_n,
        "effective_n_method": "lag1_forecast_residual_ess_per_belief; layer=min(all belief ESS), descriptive only",
        "stage": analysis_stage(unique_target_sessions),
        "thresholds_are_analysis_only": True,
    }


def _report(core: BeliefCore, snapshot: MarketSnapshot, failures: Mapping[str, str], wrapper: Mapping[str, Any], now: datetime) -> Dict[str, Any]:
    current = []
    for belief_id in SECTOR_FACTOR_BELIEF_IDS:
        state = core.beliefs.get(belief_id)
        row = SPEC_BY_ID[belief_id]
        current.append({
            "belief_id": belief_id,
            "layer": row["layer"],
            "label": row["label"],
            "numerator": row["numerator"],
            "denominator": row["denominator"],
            "probability": None if state is None else state.probability,
            "confidence": None if state is None else state.confidence,
            "audit_status": None if state is None else state.audit_status,
            "data_available": row["numerator"] in snapshot.bars and row["denominator"] in snapshot.bars,
        })
    sample = _sample_report(core)
    return {
        "report_version": REPORT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso_z(now),
        "mode": MODE,
        "active_decision_influence": False,
        "hierarchy": {
            "broad_market": "foundation_precedes_pr11",
            "active_layer": "sector_factor",
            "company_entity_beliefs": "deferred_to_pr12_plus_reviewed_framework",
            "order": ["broad_market", "sector_factor", "company_entity"],
        },
        "taxonomy": {
            "belief_count": len(SECTOR_FACTOR_BELIEF_IDS),
            "sector_count": sum(1 for row in SPEC_ROWS if row["layer"] == "sector"),
            "factor_count": sum(1 for row in SPEC_ROWS if row["layer"] == "factor"),
            "belief_ids": list(SECTOR_FACTOR_BELIEF_IDS),
            "proxy_contract": "liquid ETF relative leadership; not a complete fundamental sector model",
        },
        "activation": {
            "activated_at": wrapper.get("activated_at"),
            "activation_market_date": wrapper.get("activation_market_date"),
            "first_run_activation_only": True,
            "historical_backfill": False,
        },
        "data_quality": {
            "required_symbols": list(REQUIRED_SYMBOLS),
            "available_symbols": sorted(snapshot.bars),
            "missing_symbols": sorted(set(REQUIRED_SYMBOLS) - set(snapshot.bars)),
            "fetch_failures": dict(sorted(failures.items())),
        },
        "current_beliefs": current,
        "sample": sample,
        "calibration": core.calibration_summary(),
        "promotion_evidence_standard": {
            "with_without_required": True,
            "bridge_status": "not_part_of_foundation_pr11",
            "raw_confidence_is_not_promotion_evidence": True,
            "required_before_promotion_review": [
                "sufficient_effective_n",
                "stable_positive_with_without_uplift",
                "uplift_across_regimes",
                "no_one_or_two_observation_concentration",
                "no_material_drawdown_deterioration",
                "belief_calibration_acceptable",
                "no_material_drift",
                "data_quality_and_provenance_healthy",
                "prospective_paired_counterfactual_only",
            ],
            "automatic_promotion": False,
        },
        "future_bounded_modifier": {
            "authorized": False,
            "initial_design_ceiling_score_points": 2,
            "no_veto": True,
            "no_forced_exit": True,
            "no_direction_reversal": True,
            "no_direct_sizing_command": True,
            "paper_shadow_first": True,
        },
        "capabilities": capabilities(),
        "safety_controls": safety_controls(),
    }


def run_cycle(state_dir: Path, now: datetime, *, market_client: Optional[YahooChartClient] = None) -> Dict[str, Any]:
    _assert_safety()
    now = parse_time(now)
    state_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = state_dir / "pr11_state.json"
    wrapper = _read_json(wrapper_path, _wrapper_default())
    core = BeliefCore(state_dir / "belief_core")
    core.register_beliefs(SECTOR_FACTOR_BELIEFS)
    client = market_client or YahooChartClient()
    snapshot, failures = _fetch_snapshot(client)

    evidence_result = build_evidence(snapshot)
    core.ingest(evidence_result.evidence)
    core.recompute(now)
    _resolve_due_forecasts(core, client, now)

    market_date = _current_market_date(snapshot)
    if wrapper.get("activated_at") is None:
        wrapper["activated_at"] = iso_z(now)
        wrapper["activation_market_date"] = None if market_date is None else market_date.isoformat()
    elif wrapper.get("activation_market_date") is None and market_date is not None:
        # If the activation run had no usable SPY session, the first later valid
        # session becomes the prospective boundary and still freezes nothing.
        wrapper["activation_market_date"] = market_date.isoformat()
    elif should_capture(wrapper, snapshot, now):
        captured = _capture_for_available(core, snapshot, now)
        if captured:
            wrapper["last_capture_at"] = iso_z(now)

    wrapper["last_run_at"] = iso_z(now)
    core.save()
    _write_json(wrapper_path, wrapper)
    report = _report(core, snapshot, failures, wrapper, now)
    _write_json(state_dir / "BRACE_SECTOR_FACTOR_BELIEF_REPORT.json", report)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--now", help="Optional ISO timestamp for deterministic validation")
    args = parser.parse_args(argv)
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    report = run_cycle(args.state_dir, now)
    print(json.dumps({
        "mode": report["mode"],
        "active_layer": report["hierarchy"]["active_layer"],
        "belief_count": report["taxonomy"]["belief_count"],
        "sample": report["sample"],
        "missing_symbols": report["data_quality"]["missing_symbols"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

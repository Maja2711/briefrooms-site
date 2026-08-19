#!/usr/bin/env python3
"""PR #10 — BRACE broad-market Belief foundation.

This layer deliberately comes before company/entity beliefs. It creates four
prospective, calibrated broad-market beliefs for BRACE:

- market.rates.supportive
- market.liquidity.supportive
- market.macro_regime.supportive
- market.risk_regime.supportive

The layer is research-shadow only. It cannot change BRACE decisions, candidate
ranking, target exposure, sizing, vetoes, execution, or policy. Company/entity
beliefs are hard-disabled and require a later reviewed PR.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from belief_adapter_contract import (
    AdapterResult,
    EvidenceAssessment,
    Observation,
    clamp,
    observation_to_evidence,
    stable_id,
    strength_from_return,
)
from belief_calibration import dimension_report, metrics
from belief_core import BeliefCore, BeliefDefinition, Evidence, iso_z, parse_time
from belief_macro_data_adapter import MacroDataAdapter
from belief_market_data_adapter import Bar, MarketDataAdapter, MarketSnapshot, YahooChartClient
from belief_regime_adapter import RegimeCrossAssetAdapter

NY = ZoneInfo("America/New_York")
MODE = "research_shadow"
SCHEMA_VERSION = "brace-broad-market-belief-v1"
REPORT_VERSION = "brace-broad-market-belief-report-v1"
CAPTURE_AFTER_NY = time(16, 5)
MIN_DESCRIPTIVE_N = 12
MIN_RELATIONSHIP_N = 30

RATES = "market.rates.supportive"
LIQUIDITY = "market.liquidity.supportive"
MACRO = "market.macro_regime.supportive"
RISK = "market.risk_regime.supportive"

BROAD_MARKET_BELIEFS: Tuple[BeliefDefinition, ...] = (
    BeliefDefinition(
        RATES,
        "US rates pressure remains supportive for risk assets into the target horizon",
        prior_probability=.50,
        half_life_hours=36,
        entity="US_MARKET",
        domain="rates",
        tags=("BRACE", "broad_market", "layer:market"),
        horizon_hours=24,
        outcome_rule="tlt_not_below_frozen_reference",
    ),
    BeliefDefinition(
        LIQUIDITY,
        "Cross-asset credit/liquidity conditions remain supportive into the target horizon",
        prior_probability=.50,
        half_life_hours=36,
        entity="US_MARKET",
        domain="liquidity",
        tags=("BRACE", "broad_market", "layer:market"),
        horizon_hours=24,
        outcome_rule="hyg_lqd_not_below_frozen_reference",
    ),
    BeliefDefinition(
        MACRO,
        "The US macro/financial backdrop remains supportive for risk assets into the target horizon",
        prior_probability=.50,
        half_life_hours=96,
        entity="US_MACRO",
        domain="macro_regime",
        tags=("BRACE", "broad_market", "layer:market"),
        horizon_hours=24,
        outcome_rule="macro_cross_asset_majority_supportive",
    ),
    BeliefDefinition(
        RISK,
        "The broad US risk regime remains non-defensive into the target horizon",
        prior_probability=.50,
        half_life_hours=24,
        entity="US_RISK",
        domain="risk_regime",
        tags=("BRACE", "broad_market", "layer:market"),
        horizon_hours=24,
        outcome_rule="risk_regime_majority_supportive",
    ),
)


def safety_controls() -> Dict[str, bool]:
    return {
        "active_decision_influence": False,
        "candidate_ranking_change": False,
        "target_exposure_change": False,
        "sizing_change": False,
        "veto": False,
        "trade_execution": False,
        "policy_output": False,
        "automatic_tuning": False,
        "bounded_influence": False,
        "historical_backfill": False,
        "sector_factor_beliefs_enabled": False,
        "company_entity_beliefs_enabled": False,
    }


def _assert_safety() -> None:
    enabled = [key for key, value in safety_controls().items() if value is not False]
    if enabled:
        raise RuntimeError("BRACE broad-market Belief safety invariant violated: " + ",".join(enabled))


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


def _market_observation(
    *,
    metric: str,
    entity: str,
    observed_at: str,
    value: Any,
    unit: str,
    cluster: str,
    source_ref: str,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Observation:
    return Observation.make(
        adapter="brace_broad_market",
        metric=metric,
        entity=entity,
        observed_at=observed_at,
        value=value,
        unit=unit,
        source="Derived cross-asset view from Yahoo Finance charts",
        source_type="derived",
        source_ref=source_ref,
        reliability=.78,
        independence_cluster=cluster,
        tags=("BRACE", "broad_market", "shadow"),
        metadata={"proxy_only": True, **dict(metadata or {})},
    )


def _market_proxy_result(snapshot: MarketSnapshot) -> AdapterResult:
    observed_at = iso_z(snapshot.observed_at("SPY"))
    observations = []
    evidence: list[Evidence] = []

    tlt_return = snapshot.return_over_bars("TLT", 13)
    rates_obs = _market_observation(
        metric="tlt_return_1d_rates_pressure_proxy",
        entity="US_RATES",
        observed_at=observed_at,
        value=tlt_return,
        unit="return",
        cluster="market:TLT:rates",
        source_ref=f"derived:yahoo:TLT:{observed_at}:brace-market",
        metadata={
            "interpretation": "TLT is a duration/rates-pressure proxy, not a policy-rate series",
            "higher_is_more_supportive": True,
        },
    )
    observations.append(rates_obs)
    evidence.append(observation_to_evidence(
        rates_obs,
        EvidenceAssessment(
            RATES,
            1 if tlt_return >= 0 else -1,
            strength_from_return(tlt_return, .015),
            "duration_rates_pressure_proxy",
            f"TLT 1d return={tlt_return:.4%}; positive duration return is treated as easing rates pressure.",
        ),
    ))

    credit_return = snapshot.ratio_return("HYG", "LQD", 13)
    liquidity_obs = _market_observation(
        metric="hyg_lqd_relative_1d_liquidity_proxy",
        entity="US_CREDIT",
        observed_at=observed_at,
        value=credit_return,
        unit="return",
        cluster="market:HYG-LQD:credit-liquidity",
        source_ref=f"derived:yahoo:HYG-LQD:{observed_at}:brace-market",
        metadata={
            "interpretation": "credit-risk appetite/liquidity proxy; no dealer balance-sheet or order-book claim",
            "higher_is_more_supportive": True,
        },
    )
    observations.append(liquidity_obs)
    evidence.append(observation_to_evidence(
        liquidity_obs,
        EvidenceAssessment(
            LIQUIDITY,
            1 if credit_return >= 0 else -1,
            strength_from_return(credit_return, .008),
            "credit_liquidity_proxy",
            f"HYG/LQD 1d relative return={credit_return:.4%}.",
        ),
    ))

    regime_score = RegimeCrossAssetAdapter.regime_score(snapshot)
    regime_label = RegimeCrossAssetAdapter.classify(snapshot)
    risk_obs = _market_observation(
        metric="broad_risk_regime_score",
        entity="US_RISK",
        observed_at=observed_at,
        value={"score": regime_score, "label": regime_label},
        unit="score",
        cluster="derived:US:risk-regime",
        source_ref=f"derived:yahoo:US-RISK:{observed_at}:brace-market",
        metadata={"components": ["SPY", "RSP/SPY", "HYG/LQD", "VIX", "UUP"]},
    )
    observations.append(risk_obs)
    evidence.append(observation_to_evidence(
        risk_obs,
        EvidenceAssessment(
            RISK,
            1 if regime_score >= 0 else -1,
            clamp(abs(regime_score), .10, 1.0),
            "cross_asset_risk_regime",
            f"Cross-asset risk-regime score={regime_score:.4f}, label={regime_label}.",
        ),
    ))

    tlt = snapshot.return_over_bars("TLT", 13)
    hyg = snapshot.return_over_bars("HYG", 13)
    uup = snapshot.return_over_bars("UUP", 13)
    macro_score = clamp(
        .40 * clamp(tlt / .015, -1.0, 1.0)
        + .35 * clamp(hyg / .012, -1.0, 1.0)
        + .25 * clamp(-uup / .008, -1.0, 1.0),
        -1.0,
        1.0,
    )
    macro_obs = _market_observation(
        metric="market_implied_macro_support_score",
        entity="US_MACRO",
        observed_at=observed_at,
        value={"score": macro_score, "TLT_return": tlt, "HYG_return": hyg, "UUP_return": uup},
        unit="score",
        cluster="derived:US:market-implied-macro",
        source_ref=f"derived:yahoo:US-MACRO:{observed_at}:brace-market",
        metadata={
            "interpretation": "market-implied macro/financial backdrop proxy; primary BLS evidence is added separately when available",
        },
    )
    observations.append(macro_obs)
    evidence.append(observation_to_evidence(
        macro_obs,
        EvidenceAssessment(
            MACRO,
            1 if macro_score >= 0 else -1,
            clamp(abs(macro_score), .08, .70),
            "market_implied_macro_proxy",
            f"Market-implied macro support score={macro_score:.4f} from TLT, HYG and UUP.",
        ),
    ))

    return AdapterResult("brace_broad_market", tuple(observations), tuple(evidence))


def _macro_primary_evidence(macro_result: Optional[AdapterResult]) -> Tuple[Evidence, ...]:
    """Map existing BLS-derived macro observations into the broad macro belief.

    The underlying BLS primary observations remain untouched. Only deterministic
    directional observations emitted by MacroDataAdapter are promoted here, with
    their original provenance and independence clusters preserved.
    """
    if macro_result is None:
        return ()
    out: list[Evidence] = []
    for row in macro_result.observations:
        if row.status != "ok":
            continue
        if row.metric == "cpi_policy_pressure":
            try:
                value = float(row.value)
            except (TypeError, ValueError):
                continue
            if value <= .022:
                direction = 1
                strength = clamp(.16 + (.022 - value) / .04 * .22, .16, .36)
            elif value >= .035:
                direction = -1
                strength = clamp(.18 + (value - .035) / .06 * .28, .18, .46)
            else:
                continue
            out.append(observation_to_evidence(
                row,
                EvidenceAssessment(
                    MACRO,
                    direction,
                    strength,
                    "bls_inflation_regime",
                    f"BLS CPI 3m annualized policy-pressure proxy={value:.2%}.",
                    independence_cluster=row.independence_cluster,
                    metadata={"primary_source_family": "BLS", "broad_market_mapping": True},
                ),
            ))
        elif row.metric == "labor_growth_regime" and isinstance(row.value, Mapping):
            try:
                payroll = float(row.value["payroll_3m_average_change_thousands"])
                unemployment = float(row.value["unemployment_3m_change_percentage_points"])
            except (KeyError, TypeError, ValueError):
                continue
            if payroll >= 125.0 and unemployment <= .10:
                direction = 1
                strength = clamp(.15 + (payroll - 125.0) / 250.0 * .18, .15, .33)
            elif payroll <= 50.0 or unemployment >= .30:
                weakness = max((50.0 - payroll) / 150.0, (unemployment - .30) / .70, 0.0)
                direction = -1
                strength = clamp(.17 + weakness * .18, .17, .35)
            else:
                continue
            out.append(observation_to_evidence(
                row,
                EvidenceAssessment(
                    MACRO,
                    direction,
                    strength,
                    "bls_labor_regime",
                    f"BLS labor regime: payroll 3m avg={payroll:.0f}k, unemployment 3m change={unemployment:+.2f}pp.",
                    independence_cluster=row.independence_cluster,
                    metadata={"primary_source_family": "BLS", "broad_market_mapping": True},
                ),
            ))
    return tuple(out)


def build_evidence(snapshot: MarketSnapshot, macro_result: Optional[AdapterResult] = None) -> AdapterResult:
    market = _market_proxy_result(snapshot)
    macro_evidence = _macro_primary_evidence(macro_result)
    macro_observations = tuple(macro_result.observations) if macro_result is not None else ()
    return AdapterResult(
        "brace_broad_market_combined",
        tuple(market.observations) + macro_observations,
        tuple(market.evidence) + macro_evidence,
    )


def outcome_spec(belief_id: str, snapshot: MarketSnapshot) -> Dict[str, Any]:
    if belief_id == RATES:
        return {"kind": "value_not_below", "symbol": "TLT", "reference": snapshot.latest("TLT")}
    if belief_id == LIQUIDITY:
        return {
            "kind": "ratio_not_below",
            "numerator": "HYG",
            "denominator": "LQD",
            "reference": snapshot.ratio("HYG", "LQD"),
        }
    if belief_id == MACRO:
        return {
            "kind": "macro_majority_supportive",
            "reference": {
                "TLT": snapshot.latest("TLT"),
                "HYG": snapshot.latest("HYG"),
                "UUP": snapshot.latest("UUP"),
            },
            "minimum_votes": 2,
        }
    if belief_id == RISK:
        return {
            "kind": "risk_regime_majority_supportive",
            "reference": {
                "SPY": snapshot.latest("SPY"),
                "VIX": snapshot.latest("^VIX"),
                "credit_ratio": snapshot.ratio("HYG", "LQD"),
            },
            "minimum_votes": 2,
            "spy_floor_multiplier": .99,
            "credit_floor_multiplier": .995,
            "vix_cap": max(25.0, snapshot.latest("^VIX") * 1.10),
        }
    raise KeyError(belief_id)


def required_symbols(spec: Mapping[str, Any]) -> Tuple[str, ...]:
    kind = str(spec.get("kind") or "")
    if kind == "value_not_below":
        return (str(spec["symbol"]),)
    if kind == "ratio_not_below":
        return (str(spec["numerator"]), str(spec["denominator"]))
    if kind == "macro_majority_supportive":
        return ("TLT", "HYG", "UUP")
    if kind == "risk_regime_majority_supportive":
        return ("SPY", "^VIX", "HYG", "LQD")
    raise ValueError(f"unknown outcome spec: {kind}")


def evaluate_outcome(spec: Mapping[str, Any], values: Mapping[str, float]) -> bool:
    kind = str(spec.get("kind") or "")
    if kind == "value_not_below":
        return float(values[str(spec["symbol"])]) >= float(spec["reference"])
    if kind == "ratio_not_below":
        ratio = float(values[str(spec["numerator"])]) / float(values[str(spec["denominator"])])
        return ratio >= float(spec["reference"])
    if kind == "macro_majority_supportive":
        ref = spec["reference"]
        votes = (
            float(values["TLT"]) >= float(ref["TLT"]),
            float(values["HYG"]) >= float(ref["HYG"]),
            float(values["UUP"]) <= float(ref["UUP"]),
        )
        return sum(bool(x) for x in votes) >= int(spec.get("minimum_votes", 2))
    if kind == "risk_regime_majority_supportive":
        ref = spec["reference"]
        credit = float(values["HYG"]) / float(values["LQD"])
        votes = (
            float(values["SPY"]) >= float(ref["SPY"]) * float(spec.get("spy_floor_multiplier", .99)),
            float(values["^VIX"]) <= float(spec["vix_cap"]),
            credit >= float(ref["credit_ratio"]) * float(spec.get("credit_floor_multiplier", .995)),
        )
        return sum(bool(x) for x in votes) >= int(spec.get("minimum_votes", 2))
    raise ValueError(f"unknown outcome spec: {kind}")


def _next_weekday_close(local_dt: datetime) -> datetime:
    day = local_dt.date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, time(16, 0), tzinfo=NY)


def _capture_due(snapshot: MarketSnapshot, now: datetime) -> bool:
    now_local = now.astimezone(NY)
    observed_local = snapshot.observed_at("SPY").astimezone(NY)
    return (
        now_local.weekday() < 5
        and now_local.time().replace(tzinfo=None) >= CAPTURE_AFTER_NY
        and observed_local.date() == now_local.date()
    )


def _target_values(
    client: YahooChartClient,
    spec: Mapping[str, Any],
    target_at: datetime,
) -> Optional[Dict[str, float]]:
    target_date = target_at.astimezone(NY).date()
    values: Dict[str, float] = {}
    for symbol in required_symbols(spec):
        try:
            rows = client.bars(symbol, "3mo", "1d")
        except Exception:
            return None
        candidates = [bar for bar in rows if bar.timestamp.astimezone(NY).date() >= target_date]
        if not candidates:
            return None
        values[symbol] = float(candidates[0].close)
    return values


def verify_due(core: BeliefCore, client: YahooChartClient, now: datetime) -> int:
    verified = {row.forecast_id for row in core.verifications.values() if row.forecast_id}
    count = 0
    ids = {item.belief_id for item in BROAD_MARKET_BELIEFS}
    for forecast in sorted(core.forecasts.values(), key=lambda row: row.target_at):
        if forecast.belief_id not in ids or forecast.forecast_id in verified:
            continue
        target = parse_time(forecast.target_at)
        if target > now:
            continue
        spec = dict(forecast.metadata.get("outcome_spec") or {})
        if not spec:
            continue
        values = _target_values(client, spec, target)
        if values is None:
            continue
        outcome = evaluate_outcome(spec, values)
        core.verify_forecast(
            forecast.forecast_id,
            outcome,
            verified_at=now,
            note="Deterministic PR #10 broad-market outcome contract",
            outcome_source="Yahoo Finance chart",
            outcome_ref=f"yahoo:daily:{target.astimezone(NY).date().isoformat()}",
        )
        count += 1
    return count


def capture_daily_set(core: BeliefCore, snapshot: MarketSnapshot, now: datetime) -> int:
    if not _capture_due(snapshot, now):
        return 0
    slot_key = snapshot.observed_at("SPY").astimezone(NY).date().isoformat()
    layer_set_id = stable_id("brace-market-set", slot_key)
    target = _next_weekday_close(now.astimezone(NY))
    regime = RegimeCrossAssetAdapter.classify(snapshot)
    count = 0
    for definition in BROAD_MARKET_BELIEFS:
        forecast_id = stable_id("brace-market-forecast", slot_key, definition.belief_id)
        if forecast_id in core.forecasts:
            continue
        spec = outcome_spec(definition.belief_id, snapshot)
        core.capture_forecast(
            definition.belief_id,
            as_of=now,
            target_at=target,
            regime=regime,
            forecast_id=forecast_id,
            metadata={
                "consumer": "BRACE",
                "layer": "broad_market",
                "pr_stage": 10,
                "slot_key": slot_key,
                "layer_forecast_set_id": layer_set_id,
                "outcome_spec": spec,
                "prospective_only": True,
                "company_entity_beliefs_enabled": False,
                "decision_influence": False,
            },
        )
        count += 1
    return count


def _sample_status(n: int) -> str:
    if n < MIN_DESCRIPTIVE_N:
        return "collecting_warmup"
    if n < MIN_RELATIONSHIP_N:
        return "descriptive_only"
    return "broad_market_analysis_available"


def _report(core: BeliefCore, layer_state: Mapping[str, Any], generated_at: datetime) -> Dict[str, Any]:
    verifications = [
        row.to_dict()
        for row in core.verifications.values()
        if row.belief_id in {item.belief_id for item in BROAD_MARKET_BELIEFS}
    ]
    eligible = [row for row in verifications if bool(row.get("calibration_eligible"))]
    current = {}
    for definition in BROAD_MARKET_BELIEFS:
        state = core.beliefs.get(definition.belief_id)
        current[definition.belief_id] = None if state is None else {
            "probability": state.probability,
            "confidence": state.confidence,
            "audit_status": state.audit_status,
            "independent_clusters": state.independent_clusters,
            "source_diversity": state.source_diversity,
            "contradiction_score": state.contradiction_score,
        }
    forecasts = [
        row for row in core.forecasts.values()
        if row.belief_id in {item.belief_id for item in BROAD_MARKET_BELIEFS}
    ]
    return {
        "schema_version": REPORT_VERSION,
        "report_name": "BRACE_BROAD_MARKET_BELIEF_REPORT",
        "generated_at": iso_z(generated_at),
        "mode": MODE,
        "hierarchy": {
            "stage": 10,
            "active_layer": "broad_market",
            "broad_market_beliefs": [item.belief_id for item in BROAD_MARKET_BELIEFS],
            "sector_factor_beliefs": "deferred",
            "company_entity_beliefs": "deferred_to_later_reviewed_pr",
            "future_entity_dimensions": [
                "earnings_momentum",
                "margins",
                "revenue_durability",
                "valuation",
                "regulatory_risk",
                "capex_returns",
                "earnings_quality",
            ],
        },
        "activation": {
            "activated_at": layer_state.get("activated_at"),
            "historical_backfill_allowed": False,
            "prospective_only": True,
        },
        "safety_controls": safety_controls(),
        "active_decision_influence": False,
        "current_beliefs": current,
        "sample": {
            "forecasts_total": len(forecasts),
            "verifications_total": len(verifications),
            "calibration_eligible": len(eligible),
            "status": _sample_status(len(eligible)),
            "thresholds": {
                "descriptive": MIN_DESCRIPTIVE_N,
                "broad_market_analysis": MIN_RELATIONSHIP_N,
            },
        },
        "calibration": {
            "overall": metrics(eligible),
            "by_belief": dimension_report(eligible, "belief_id"),
            "automatic_tuning_allowed": False,
        },
        "promotion": {
            "company_entity_activation_authorized": False,
            "requires_separate_reviewed_pr": True,
            "automatic_promotion": False,
        },
    }


def _initial_layer_state(now: datetime) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "activated_at": iso_z(now),
        "activation_only_completed": False,
        "last_run_at": None,
        "last_capture_at": None,
        "safety_controls": safety_controls(),
    }


def run_cycle(
    state_dir: Path,
    now: datetime,
    *,
    market_client: Optional[YahooChartClient] = None,
    macro_adapter: Optional[MacroDataAdapter] = None,
) -> Dict[str, Any]:
    _assert_safety()
    now = now.astimezone(timezone.utc)
    state_dir.mkdir(parents=True, exist_ok=True)
    layer_path = state_dir / "layer_state.json"
    existed = layer_path.exists()
    layer_state = _read_json(layer_path, _initial_layer_state(now))
    if layer_state.get("safety_controls") and any(layer_state["safety_controls"].values()):
        raise RuntimeError("persisted BRACE broad-market safety controls are not hard-off")

    client = market_client or YahooChartClient()
    snapshot = MarketDataAdapter(client=client).fetch_snapshot()
    macro = (macro_adapter or MacroDataAdapter()).run(now)
    combined = build_evidence(snapshot, macro)

    core = BeliefCore(state_dir)
    core.register_beliefs(BROAD_MARKET_BELIEFS)
    if combined.evidence:
        core.ingest(combined.evidence)
    core.recompute(now)

    verified_now = verify_due(core, client, now)
    captured_now = 0
    if existed and bool(layer_state.get("activation_only_completed")):
        captured_now = capture_daily_set(core, snapshot, now)
    else:
        # First production run establishes the anti-hindsight boundary. Current
        # evidence can seed state, but no forecast is frozen retroactively.
        layer_state["activation_only_completed"] = True

    layer_state["last_run_at"] = iso_z(now)
    if captured_now:
        layer_state["last_capture_at"] = iso_z(now)
    layer_state["safety_controls"] = safety_controls()
    layer_state["last_run_summary"] = {
        "market_observations": len(combined.observations),
        "evidence_ingested": len(combined.evidence),
        "forecasts_captured": captured_now,
        "forecasts_verified": verified_now,
        "company_entity_beliefs_enabled": False,
    }
    _write_json(layer_path, layer_state)
    core.save()
    report = _report(core, layer_state, now)
    _write_json(state_dir / "BRACE_BROAD_MARKET_BELIEF_REPORT.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PR #10 BRACE broad-market Belief research-shadow layer")
    parser.add_argument("--state-dir", default=os.environ.get("BRACE_BROAD_MARKET_STATE_DIR", ".belief_runtime/brace_broad_market"))
    parser.add_argument("--now", help="ISO timestamp override")
    args = parser.parse_args()
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    report = run_cycle(Path(args.state_dir), now)
    print(json.dumps({
        "mode": report["mode"],
        "sample": report["sample"],
        "hierarchy": report["hierarchy"],
        "active_decision_influence": report["active_decision_influence"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Live shadow orchestrator using small Observation -> Evidence adapters."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from belief_adapter_contract import stable_id, strength_from_return
from belief_core import BeliefCore, BeliefDefinition, iso_z, parse_time
from belief_liquidity_adapter import LiquidityEvidenceAdapter
from belief_market_data_adapter import Bar, MarketDataAdapter, MarketSnapshot, YahooChartClient
from belief_regime_adapter import RegimeCrossAssetAdapter
from belief_technical_adapter import TechnicalEvidenceAdapter

NY = ZoneInfo("America/New_York")
MODE = "shadow"
TRADE_EXECUTION_ENABLED = False
POLICY_OUTPUT_ENABLED = False
AUTOMATIC_TUNING_ENABLED = False

MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 20)
FORECAST_SLOTS = (time(10, 0), time(13, 0), time(16, 0))
SLOT_GRACE_MINUTES = 45

BELIEFS: Tuple[BeliefDefinition, ...] = (
    BeliefDefinition(
        "spx.trend.bullish", "SPX/US equity trend is bullish into the target horizon",
        prior_probability=.50, half_life_hours=18, entity="SPX", domain="trend",
        tags=("shared", "BRACE", "BRACE-SPX", "WES"), horizon_hours=24,
        outcome_rule="spy_close_above_reference",
    ),
    BeliefDefinition(
        "spx.breadth.healthy", "US equity breadth improves into the target horizon",
        prior_probability=.50, half_life_hours=24, entity="SPX", domain="breadth",
        tags=("shared", "BRACE", "BRACE-SPX", "WES"), horizon_hours=24,
        outcome_rule="breadth_ratio_above_reference",
    ),
    BeliefDefinition(
        "spx.volatility.benign", "Equity volatility remains contained into the target horizon",
        prior_probability=.55, half_life_hours=12, entity="SPX", domain="volatility",
        tags=("shared", "BRACE", "BRACE-SPX", "WES"), horizon_hours=24,
        outcome_rule="vix_below_dynamic_cap",
    ),
    BeliefDefinition(
        "spx.liquidity.supportive", "Credit/liquidity conditions remain supportive into the target horizon",
        prior_probability=.52, half_life_hours=36, entity="US_RISK", domain="liquidity",
        tags=("shared", "BRACE", "BRACE-SPX", "WES"), horizon_hours=24,
        outcome_rule="credit_ratio_above_reference",
    ),
    BeliefDefinition(
        "spx.financial_conditions.supportive", "Rates/USD/credit financial conditions remain supportive into the target horizon",
        prior_probability=.50, half_life_hours=48, entity="US_MACRO", domain="macro",
        tags=("shared", "BRACE", "BRACE-SPX", "WES"), horizon_hours=24,
        outcome_rule="financial_conditions_majority_supportive",
    ),
)


def floor_half_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0 if dt.minute < 30 else 30, second=0, microsecond=0)


def in_market_window(local_dt: datetime) -> bool:
    return local_dt.weekday() < 5 and MARKET_OPEN <= local_dt.time().replace(tzinfo=None) <= MARKET_CLOSE


def due_planned_slot(local_dt: datetime, planned: time, already_done: bool) -> bool:
    if already_done or local_dt.weekday() >= 5:
        return False
    planned_dt = datetime.combine(local_dt.date(), planned, tzinfo=NY)
    return planned_dt <= local_dt < planned_dt + timedelta(minutes=SLOT_GRACE_MINUTES)


def next_weekday_close(local_dt: datetime) -> datetime:
    day = local_dt.date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, time(16, 0), tzinfo=NY)


def weekly_target(local_dt: datetime) -> datetime:
    return datetime.combine(local_dt.date() + timedelta(days=7), time(16, 0), tzinfo=NY)


def fetch_snapshot(client: YahooChartClient) -> MarketSnapshot:
    return MarketDataAdapter(client=client).fetch_snapshot()


def build_adapter_payload(snapshot: MarketSnapshot) -> Dict[str, Any]:
    adapters = (
        MarketDataAdapter(),
        TechnicalEvidenceAdapter(),
        LiquidityEvidenceAdapter(),
        RegimeCrossAssetAdapter(),
    )
    observations = []
    evidence = []
    counts: Dict[str, Dict[str, int]] = {}
    for adapter in adapters:
        result = adapter.run(snapshot)
        observations.extend(result.observations)
        evidence.extend(result.evidence)
        counts[result.adapter] = {"observations": len(result.observations), "evidence": len(result.evidence)}
    return {
        "observations": observations,
        "evidence": evidence,
        "adapter_counts": counts,
        "regime": RegimeCrossAssetAdapter.classify(snapshot),
    }


def build_market_evidence(snapshot: MarketSnapshot):
    """Backward-compatible helper: evidence now comes from the adapter pipeline."""
    return list(build_adapter_payload(snapshot)["evidence"])


def classify_regime(snapshot: MarketSnapshot) -> str:
    return RegimeCrossAssetAdapter.classify(snapshot)


def outcome_spec(belief_id: str, snapshot: MarketSnapshot) -> Dict[str, Any]:
    if belief_id == "spx.trend.bullish":
        return {"kind": "price_above", "symbol": "SPY", "reference": snapshot.latest("SPY")}
    if belief_id == "spx.breadth.healthy":
        return {"kind": "ratio_above", "numerator": "RSP", "denominator": "SPY", "reference": snapshot.ratio("RSP", "SPY")}
    if belief_id == "spx.volatility.benign":
        reference = snapshot.latest("^VIX")
        return {"kind": "value_below", "symbol": "^VIX", "reference": reference, "threshold": max(20.0, reference * 1.10)}
    if belief_id == "spx.liquidity.supportive":
        return {"kind": "ratio_above", "numerator": "HYG", "denominator": "LQD", "reference": snapshot.ratio("HYG", "LQD")}
    if belief_id == "spx.financial_conditions.supportive":
        return {"kind": "majority_supportive", "reference": {
            "TLT": snapshot.latest("TLT"), "HYG": snapshot.latest("HYG"), "UUP": snapshot.latest("UUP")}}
    raise KeyError(belief_id)


def evaluate_spec(spec: Mapping[str, Any], values: Mapping[str, float]) -> bool:
    kind = spec["kind"]
    if kind == "price_above":
        return float(values[str(spec["symbol"])]) > float(spec["reference"])
    if kind == "ratio_above":
        ratio = float(values[str(spec["numerator"])]) / float(values[str(spec["denominator"])])
        return ratio > float(spec["reference"])
    if kind == "value_below":
        return float(values[str(spec["symbol"])]) <= float(spec["threshold"])
    if kind == "majority_supportive":
        reference = spec["reference"]
        votes = [
            float(values["TLT"]) >= float(reference["TLT"]),
            float(values["HYG"]) >= float(reference["HYG"]),
            float(values["UUP"]) <= float(reference["UUP"]),
        ]
        return sum(bool(value) for value in votes) >= 2
    raise ValueError(f"unknown outcome spec: {kind}")


def required_symbols(spec: Mapping[str, Any]) -> List[str]:
    kind = spec["kind"]
    if kind in {"price_above", "value_below"}:
        return [str(spec["symbol"])]
    if kind == "ratio_above":
        return [str(spec["numerator"]), str(spec["denominator"])]
    if kind == "majority_supportive":
        return ["TLT", "HYG", "UUP"]
    raise ValueError(str(kind))


def target_values(client: YahooChartClient, spec: Mapping[str, Any], target_at: datetime, now: datetime,
                  live_snapshot: Optional[MarketSnapshot]) -> Optional[Dict[str, float]]:
    target_local = target_at.astimezone(NY)
    now_local = now.astimezone(NY)
    symbols = required_symbols(spec)
    if live_snapshot is not None and target_local.date() == now_local.date() and live_snapshot.is_current_session(now):
        return {symbol: live_snapshot.latest(symbol) for symbol in symbols}
    values: Dict[str, float] = {}
    for symbol in symbols:
        rows = client.bars(symbol, "3mo", "1d")
        candidates = [bar for bar in rows if bar.timestamp.astimezone(NY).date() >= target_local.date()]
        if not candidates:
            return None
        values[symbol] = candidates[0].close
    return values


def load_scheduler(state_dir: Path) -> Dict[str, Any]:
    path = state_dir / "scheduler.json"
    if not path.exists():
        return {"schema_version": 2, "completed_slots": {}, "gaps": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_scheduler(state_dir: Path, payload: Mapping[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "scheduler.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_observations(state_dir: Path, observations) -> None:
    """Private runtime telemetry only; this path is stored in the workflow artifact."""
    path = state_dir / "observations.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        for row in observations:
            payload = {
                "observation_id": row.observation_id,
                "adapter": row.adapter,
                "metric": row.metric,
                "entity": row.entity,
                "observed_at": row.observed_at,
                "value": row.value,
                "unit": row.unit,
                "source": row.source,
                "source_type": row.source_type,
                "source_ref": row.source_ref,
                "reliability": row.reliability,
                "independence_cluster": row.independence_cluster,
                "status": row.status,
                "tags": list(row.tags),
                "metadata": dict(row.metadata),
            }
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def append_world_state(state_dir: Path, core: BeliefCore, when: datetime, regime: str) -> None:
    path = state_dir / "world_state_history.jsonl"
    row = {
        "timestamp": iso_z(when), "regime": regime, "mode": MODE,
        "beliefs": {key: {"probability": value.probability, "confidence": value.confidence,
                          "audit_status": value.audit_status, "domain": value.domain}
                    for key, value in sorted(core.beliefs.items())},
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def freeze_set(core: BeliefCore, snapshot: MarketSnapshot, when: datetime, target: datetime,
               consumer: str, slot_key: str, regime: str) -> int:
    count = 0
    for belief_id in sorted(core.beliefs):
        forecast_id = stable_id("forecast", consumer, slot_key, belief_id)
        if forecast_id in core.forecasts:
            continue
        metadata = {
            "consumer": consumer,
            "slot_key": slot_key,
            "market_observed_at": iso_z(snapshot.observed_at("SPY")),
            "outcome_spec": outcome_spec(belief_id, snapshot),
            "adapter_contract": "Observation->Evidence/v1",
            "shadow_only": True,
            "trade_execution_enabled": False,
            "policy_output_enabled": False,
        }
        core.capture_forecast(belief_id, as_of=when, target_at=target, regime=regime,
                              forecast_id=forecast_id, metadata=metadata)
        count += 1
    return count


def verify_due(core: BeliefCore, client: YahooChartClient, now: datetime,
               live_snapshot: Optional[MarketSnapshot]) -> int:
    verified_ids = {value.forecast_id for value in core.verifications.values() if value.forecast_id}
    count = 0
    for forecast in sorted(core.forecasts.values(), key=lambda item: item.target_at):
        if forecast.forecast_id in verified_ids or parse_time(forecast.target_at) > now:
            continue
        spec = dict(forecast.metadata.get("outcome_spec") or {})
        if not spec:
            continue
        values = target_values(client, spec, parse_time(forecast.target_at), now, live_snapshot)
        if values is None:
            continue
        outcome = evaluate_spec(spec, values)
        outcome_ref = "yahoo:" + ",".join(required_symbols(spec)) + ":target=" + forecast.target_at
        core.verify_forecast(forecast.forecast_id, outcome, verified_at=now,
                             outcome_source="Yahoo Finance chart", outcome_ref=outcome_ref,
                             note="Automatic deterministic shadow verification")
        count += 1
    return count


def run_cycle(state_dir: Path, now: datetime, client: YahooChartClient) -> Dict[str, Any]:
    if TRADE_EXECUTION_ENABLED or POLICY_OUTPUT_ENABLED or AUTOMATIC_TUNING_ENABLED:
        raise RuntimeError("Belief Core live adapter safety invariant violated")
    state_dir.mkdir(parents=True, exist_ok=True)
    core = BeliefCore(state_dir)
    core.register_beliefs(BELIEFS)
    scheduler = load_scheduler(state_dir)
    completed = scheduler.setdefault("completed_slots", {})
    local = now.astimezone(NY)
    snapshot: Optional[MarketSnapshot] = None
    evidence_count = observation_count = world_count = forecast_count = wes_count = 0
    adapter_counts: Dict[str, Dict[str, int]] = {}

    if in_market_window(local):
        snapshot = fetch_snapshot(client)
        if snapshot.is_current_session(now):
            payload = build_adapter_payload(snapshot)
            observations = payload["observations"]
            evidence = payload["evidence"]
            append_observations(state_dir, observations)
            core.ingest(evidence)
            core.save()
            observation_count = len(observations)
            evidence_count = len(evidence)
            adapter_counts = payload["adapter_counts"]
            regime = payload["regime"]

            half_slot = floor_half_hour(local)
            hour_key = f"world:{local.date().isoformat()}:{half_slot.hour:02d}"
            if half_slot.minute == 0 and hour_key not in completed:
                core.recompute(now)
                append_world_state(state_dir, core, now, regime)
                completed[hour_key] = iso_z(now)
                world_count = 1

            for planned in FORECAST_SLOTS:
                key = f"shared:{local.date().isoformat()}:{planned.hour:02d}{planned.minute:02d}"
                if due_planned_slot(local, planned, key in completed):
                    core.recompute(now)
                    target = datetime.combine(local.date(), time(16, 0), tzinfo=NY) if planned.hour < 16 else next_weekday_close(local)
                    forecast_count += freeze_set(core, snapshot, now, target, "BRACE+BRACE-SPX", key, regime)
                    completed[key] = iso_z(now)

            wes_key = f"wes:{local.date().isoformat()}:1600"
            if local.weekday() == 4 and due_planned_slot(local, time(16, 0), wes_key in completed):
                core.recompute(now)
                wes_count += freeze_set(core, snapshot, now, weekly_target(local), "WES", wes_key, regime)
                completed[wes_key] = iso_z(now)
        else:
            scheduler.setdefault("gaps", []).append({"timestamp": iso_z(now), "reason": "no_current_us_session_bar"})

    verified = verify_due(core, client, now, snapshot)
    scheduler["schema_version"] = 2
    scheduler["last_run_at"] = iso_z(now)
    scheduler["last_status"] = {
        "observations_collected": observation_count,
        "evidence_ingested": evidence_count,
        "adapter_counts": adapter_counts,
        "world_state_snapshots": world_count,
        "shared_forecasts_frozen": forecast_count,
        "wes_forecasts_frozen": wes_count,
        "forecasts_verified": verified,
        "mode": MODE,
    }
    scheduler["gaps"] = scheduler.get("gaps", [])[-100:]
    save_scheduler(state_dir, scheduler)
    core.save(); core.write_dashboard(now)
    return scheduler["last_status"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run BriefRooms Belief Core live shadow collection cycle")
    parser.add_argument("--state-dir", default=os.environ.get("BELIEF_CORE_STATE_DIR", ".belief_runtime/core"))
    parser.add_argument("--now", help="ISO timestamp override for deterministic testing/manual replay")
    args = parser.parse_args()
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    status = run_cycle(Path(args.state_dir), now, YahooChartClient())
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

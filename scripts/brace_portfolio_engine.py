#!/usr/bin/env python3
"""BRACE Portfolio Engine orchestration with baseline-preserving boundaries."""
from __future__ import annotations

import argparse
import copy
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from brace_portfolio_backtest import run_walk_forward
from brace_portfolio_candidates import rank_candidates
from brace_portfolio_config import EngineConfig, load_config
from brace_portfolio_data import (
    BASELINE_PORTFOLIO_PATH,
    ENGINE_DATA_ROOT,
    YFinanceProvider,
    assert_baseline_unchanged,
    baseline_invariants,
    canonical_sha256,
    data_freshness_report,
    read_json,
    source_metadata,
    universe_records,
    write_json_atomic,
)
from brace_portfolio_decision import build_pending_decisions, shadow_record
from brace_portfolio_features import build_features
from brace_portfolio_learning import update_learning_state
from brace_portfolio_optimizer import optimize
from brace_portfolio_promotion_controller import evaluate_and_apply
from brace_portfolio_publish import build_public_snapshot, publish
from brace_portfolio_scoring import score_instrument
from brace_portfolio_state_sync import live_analysis_portfolio

REGISTRY_PATH = ENGINE_DATA_ROOT / "methodology_registry.json"
UNIVERSE_PATH = ENGINE_DATA_ROOT / "universe.json"
ANALYSIS_PATH = ENGINE_DATA_ROOT / "analysis.json"
PENDING_PATH = ENGINE_DATA_ROOT / "pending_decisions.json"
SHADOW_PATH = ENGINE_DATA_ROOT / "shadow_log.json"
LEARNING_PATH = ENGINE_DATA_ROOT / "learning_state.json"
VALIDATION_PATH = ENGINE_DATA_ROOT / "research_validation.json"
PROMOTION_HISTORY_PATH = ENGINE_DATA_ROOT / "promotion_history.json"
OPERATIONAL_PATH = ENGINE_DATA_ROOT / "operational_state.json"
PAPER_PATH = ENGINE_DATA_ROOT / "paper_portfolio.json"
ROOT = Path(__file__).resolve().parents[1]
MARKET_CACHE_PATH = ROOT / ".cache" / "brace_portfolio_market.json"


def _methodology(registry: Mapping[str, Any], methodology_id: Any) -> Dict[str, Any]:
    for item in registry.get("methodologies", []) or []:
        if item.get("methodology_id") == methodology_id:
            return dict(item)
    return {}


def _record_baseline_validation(
    registry: Dict[str, Any],
    baseline: Mapping[str, Any],
    validated_at: datetime,
) -> None:
    metadata = source_metadata()
    for item in registry.get("methodologies", []) or []:
        if item.get("methodology_id") != "portfolio-10k-baseline":
            continue
        results = dict(item.get("validation_results") or {})
        results.update(
            {
                "status": "production_baseline",
                "history_preserved": True,
                "entry_history_sha256": canonical_sha256(
                    baseline_invariants(baseline)
                ),
                "source_snapshot_sha256": metadata["baseline_portfolio_sha256"],
                "validated_at": validated_at.isoformat(timespec="seconds"),
            }
        )
        item["validation_results"] = results
        return
    raise ValueError("Baseline methodology is missing from the registry")


def _universe_by_id(universe: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(item.get("instrument_id")): dict(item)
        for item in universe_records(universe)
    }


def _baseline_weights(baseline: Mapping[str, Any]) -> Dict[str, float]:
    rows = {
        str(item.get("id")): float(
            item.get("current_weight")
            if item.get("current_weight") is not None
            else item.get("target_weight")
            or 0.0
        )
        for item in baseline.get("positions", []) or []
        if item.get("id")
    }
    cash = float(baseline.get("cash_pln") or 0.0)
    value = float(baseline.get("total_value_pln") or 0.0)
    if cash > 0 and value > 0:
        rows["CASH"] = cash / value
    total = sum(rows.values())
    return (
        {key: value / total for key, value in rows.items()}
        if total > 0
        else {"CASH": 1.0}
    )


def _group_weights(
    baseline: Mapping[str, Any],
    universe_by_id: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Dict[str, float]]:
    result = {"sector": {}, "currency": {}, "region": {}}
    for position in baseline.get("positions", []) or []:
        instrument_id = str(position.get("id") or "")
        meta = universe_by_id.get(instrument_id, {})
        weight = float(position.get("current_weight") or 0.0)
        for field in result:
            key = str(meta.get(field) or position.get(field) or "Unknown")
            result[field][key] = result[field].get(key, 0.0) + weight
    return result


def _merge_holding(
    meta: Mapping[str, Any],
    position: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    merged = dict(meta)
    if position:
        for key, value in position.items():
            if value is not None:
                merged[key] = value
    merged["instrument_id"] = meta.get("instrument_id") or merged.get("id")
    merged["data_symbol"] = meta.get("data_symbol") or merged.get("market_symbol")
    merged["market_symbol"] = merged.get("data_symbol")
    merged["availability"] = meta.get("availability", "AVAILABLE")
    merged["active"] = meta.get("active", True)
    return merged


def _fetch_market(
    universe_by_id: Mapping[str, Mapping[str, Any]],
    provider: Any,
    generated_at: datetime,
) -> Dict[str, Any]:
    instruments: Dict[str, Any] = {}
    for instrument_id, item in sorted(universe_by_id.items()):
        fetched = provider.fetch(str(item.get("data_symbol")))
        instruments[instrument_id] = {
            "symbol": fetched.symbol,
            "history": fetched.history,
            "fundamentals": fetched.fundamentals,
            "observed_at": fetched.observed_at,
            "errors": fetched.errors,
        }
    payload = {
        "schema_version": "1.0.0",
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "methodology_version": "market-cache-v1.0.0",
        "data_freshness": "current",
        "source_metadata": {"provider": type(provider).__name__},
        "instruments": instruments,
    }
    payload["content_sha256"] = canonical_sha256(payload)
    return payload


def _merge_market_refresh(
    fresh: Mapping[str, Any],
    cached: Mapping[str, Any],
    *,
    minimum_history_rows: int = 60,
) -> Dict[str, Any]:
    fresh_instruments = fresh.get("instruments") or {}
    cached_instruments = cached.get("instruments") or {}
    merged: Dict[str, Any] = {}
    refreshed = 0
    preserved = 0

    for instrument_id in sorted(set(fresh_instruments) | set(cached_instruments)):
        fresh_item = dict(fresh_instruments.get(instrument_id) or {})
        cached_item = dict(cached_instruments.get(instrument_id) or {})
        if len(fresh_item.get("history") or []) >= minimum_history_rows:
            merged[instrument_id] = fresh_item
            refreshed += 1
            continue
        if len(cached_item.get("history") or []) >= minimum_history_rows:
            errors = list(fresh_item.get("errors") or [])
            errors.append("NETWORK_REFRESH_INCOMPLETE_USING_LAST_GOOD_CACHE")
            cached_item["errors"] = sorted(set(errors))
            merged[instrument_id] = cached_item
            preserved += 1
            continue
        merged[instrument_id] = fresh_item or cached_item

    usable = sum(
        len((item or {}).get("history") or []) >= minimum_history_rows
        for item in merged.values()
    )
    if usable == 0:
        raise ValueError(
            "Market refresh returned no usable histories and no last-good cache exists"
        )

    payload = {
        **dict(fresh),
        "data_freshness": "current" if preserved == 0 else "partial_last_good_fallback",
        "source_metadata": {
            **dict(fresh.get("source_metadata") or {}),
            "refreshed_instruments": refreshed,
            "preserved_from_last_good_cache": preserved,
            "usable_instruments": usable,
        },
        "instruments": merged,
    }
    payload.pop("content_sha256", None)
    payload["content_sha256"] = canonical_sha256(payload)
    return payload


def _analysis_from_market(
    baseline: Mapping[str, Any],
    universe: Mapping[str, Any],
    market: Mapping[str, Any],
    config: EngineConfig,
    generated_at: datetime,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    by_id = _universe_by_id(universe)
    baseline_positions = {
        str(item.get("id")): dict(item)
        for item in baseline.get("positions", []) or []
    }
    group_weights = _group_weights(baseline, by_id)
    histories = {
        instrument_id: (market.get("instruments", {}).get(instrument_id) or {}).get(
            "history", []
        )
        for instrument_id in by_id
    }
    benchmark_id = "fwia"
    benchmark_history = histories.get(benchmark_id, [])
    holding_histories = {
        instrument_id: histories.get(instrument_id, [])
        for instrument_id in baseline_positions
    }
    analyses = []
    for instrument_id, meta in sorted(by_id.items()):
        instrument = _merge_holding(meta, baseline_positions.get(instrument_id))
        fetched = market.get("instruments", {}).get(instrument_id) or {}
        features = build_features(
            instrument,
            fetched.get("history") or [],
            benchmark_history,
            fetched.get("fundamentals") or {},
            holding_histories,
            group_weights,
            generated_at.date(),
        )
        score = score_instrument(features, instrument, config)
        analyses.append(
            {
                **instrument,
                **features,
                **score,
                "source_errors": list(fetched.get("errors") or []),
            }
        )
    current_ids = set(baseline_positions)
    positions = [
        item for item in analyses if item.get("instrument_id") in current_ids
    ]
    candidates = rank_candidates(
        analyses,
        current_ids,
        [by_id[item] for item in current_ids if item in by_id],
        config,
        limit=10,
    )
    optimization = optimize(_baseline_weights(baseline), positions + candidates, config)
    selected_metrics = optimization.get("comparisons") or []
    selected = next(
        (
            row.get("metrics") or {}
            for row in selected_metrics
            if row.get("name") == optimization.get("selected")
        ),
        {},
    )
    average_probability = (
        sum(float(item.get("probability_of_reaching_target") or 0.0) for item in positions)
        / len(positions)
        if positions
        else 0.0
    )
    report = {
        "schema_version": "1.0.0",
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "methodology_version": "brace-portfolio-v3.0.0",
        "data_freshness": market.get("data_freshness"),
        "source_metadata": source_metadata(),
        "positions": positions,
        "candidates": candidates,
        "optimization": optimization,
        "expected_annual_return": selected.get("expected_return"),
        "expected_drawdown": selected.get("expected_drawdown"),
        "annual_turnover": selected.get("turnover"),
        "probability_of_reaching_target": round(average_probability, 6),
        "target_shortfall": max(
            0.0, config.target_annual_return - float(selected.get("expected_return") or 0.0)
        ),
        "required_risk_to_target": (
            max(
                0.0,
                config.target_annual_return
                - float(selected.get("expected_return") or 0.0),
            )
            / max(float(selected.get("volatility_proxy") or 0.01), 0.01)
        ),
        "risk_status": (
            "WITHIN_LIMITS"
            if optimization.get("rules_passed")
            else "LIMIT_REVIEW_REQUIRED"
        ),
    }
    return report, optimization


def _append_shadow(
    current: Mapping[str, Any],
    record: Mapping[str, Any],
    baseline: Mapping[str, Any],
    generated_at: datetime,
) -> Dict[str, Any]:
    updated = copy.deepcopy(
        dict(
            current
            or {
                "schema_version": "1.0.0",
                "methodology_version": "brace-portfolio-v3.0.0",
                "data_freshness": "current",
                "source_metadata": {
                    "engine": "brace_portfolio_engine.py",
                    "append_only": True,
                },
                "runs": [],
            }
        )
    )
    existing = {item.get("shadow_run_id") for item in updated.get("runs", [])}
    existing_days = {
        str(item.get("generated_at") or "")[:10]
        for item in updated.get("runs", [])
    }
    if (
        record.get("shadow_run_id") not in existing
        and str(record.get("generated_at") or "")[:10] not in existing_days
    ):
        updated.setdefault("runs", []).append(copy.deepcopy(dict(record)))
    challenger = _methodology(read_json(REGISTRY_PATH), "brace-portfolio-engine")
    started_text = (
        (challenger.get("validation_results") or {}).get("shadow_started_at")
        or challenger.get("activated_at")
        or generated_at.isoformat()
    )
    try:
        started = datetime.fromisoformat(str(started_text).replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
    except ValueError:
        started = generated_at
    baseline_return = float(baseline.get("total_return_percent") or 0.0)
    decisions = sum(
        1
        for run in updated.get("runs", [])
        for item in run.get("decisions", [])
        if item.get("brace_decision") not in {None, "HOLD", "WATCH"}
    )
    completed = sum(
        1
        for run in updated.get("runs", [])
        for item in run.get("decisions", [])
        if item.get("later_outcome") is not None
    )
    updated["statistics"] = {
        "calendar_days": max(0, (generated_at.date() - started.date()).days),
        "decisions": decisions,
        "completed_trades": completed,
        "shadow_return": 0.0,
        "baseline_return": baseline_return,
        "shadow_risk_adjusted_return": 0.0,
        "baseline_risk_adjusted_return": float(
            baseline.get("baseline_risk_adjusted_return") or 0.0
        ),
        "shadow_max_drawdown": 0.0,
        "shadow_turnover": 0.0,
        "excess_return_ci_low": -1.0,
    }
    updated["generated_at"] = generated_at.isoformat(timespec="seconds")
    return updated


def _operational_state(
    freshness: Mapping[str, Any],
    baseline_unchanged: bool,
    generated_at: datetime,
) -> Dict[str, Any]:
    safe_mode = bool(freshness.get("safe_mode"))
    reasons = list(freshness.get("reasons") or [])
    return {
        "schema_version": "1.0.0",
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "methodology_version": "brace-portfolio-v3.0.0",
        "data_freshness": "unsafe" if safe_mode else "current",
        "source_metadata": {"engine": "brace_portfolio_engine.py"},
        "safe_mode": safe_mode,
        "safe_mode_reasons": reasons,
        "stale_data": "STALE_MARKET_DATA" in reasons,
        "consecutive_workflow_failures": int(
            os.environ.get("BRACE_CONSECUTIVE_WORKFLOW_FAILURES", "0")
        ),
        "price_fx_inconsistent": any(
            reason
            in {
                "PRICE_TIMESTAMP_IN_FUTURE",
                "FX_TIMESTAMP_IN_FUTURE",
                "PRICE_JUMP_REQUIRES_CORPORATE_ACTION_REVIEW",
            }
            for reason in reasons
        ),
        "history_integrity_failed": not baseline_unchanged,
        "unexplained_parameter_change": False,
        "missing_rationale": False,
        "risk_data_missing": False,
        "live_validation_divergence": False,
        "critical_data_errors": len(reasons),
        "decisions_reproducible": True,
        "entry_history_unchanged": baseline_unchanged,
        "timestamps_complete": not safe_mode,
        "workflow_stable": int(
            os.environ.get("BRACE_CONSECUTIVE_WORKFLOW_FAILURES", "0")
        )
        < 3,
        "public_internal_consistent": True,
        "integrity_tests_pass": os.environ.get("BRACE_TESTS_PASSED", "1") == "1",
    }


def _live_risk(analysis: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "maximum_drawdown": -abs(float(analysis.get("expected_drawdown") or 0.0)),
        "current_drawdown": float(analysis.get("current_drawdown") or 0.0),
        "annual_turnover": float(analysis.get("annual_turnover") or 0.0),
    }


def run_cycle(
    mode: str,
    *,
    now: Optional[datetime] = None,
    provider: Optional[Any] = None,
) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    config, _ = load_config()
    baseline_before = read_json(BASELINE_PORTFOLIO_PATH)
    baseline_copy = copy.deepcopy(baseline_before)
    registry = read_json(REGISTRY_PATH)
    paper_portfolio = read_json(PAPER_PATH)
    analysis_portfolio = (
        baseline_before
        if mode == "research"
        else live_analysis_portfolio(registry, baseline_before, paper_portfolio)
    )
    universe = read_json(UNIVERSE_PATH)
    previous_analysis = read_json(ANALYSIS_PATH)
    freshness = data_freshness_report(
        baseline_before,
        config,
        now,
        "monitor" if mode == "monitor" else "analysis",
        previous_analysis,
    )
    analysis = previous_analysis or {
        "schema_version": "1.0.0",
        "generated_at": now.isoformat(timespec="seconds"),
        "methodology_version": "brace-portfolio-v3.0.0",
        "data_freshness": "not_yet_analyzed",
        "source_metadata": source_metadata(),
        "positions": [],
        "candidates": [],
        "expected_annual_return": 0.0,
        "expected_drawdown": 0.0,
        "annual_turnover": 0.0,
    }
    pending = read_json(PENDING_PATH) or {
        "schema_version": "1.0.0",
        "generated_at": now.isoformat(timespec="seconds"),
        "methodology_version": "brace-portfolio-v3.0.0",
        "data_freshness": "not_yet_analyzed",
        "source_metadata": {"engine": "brace_portfolio_decision.py"},
        "safe_mode": True,
        "recommendations": [],
        "decisions": [],
    }
    shadow = read_json(SHADOW_PATH)
    market = read_json(MARKET_CACHE_PATH)

    if mode in {"daily", "weekly", "research"}:
        if provider is not None:
            fresh_market = _fetch_market(_universe_by_id(universe), provider, now)
            market = _merge_market_refresh(fresh_market, market)
            write_json_atomic(MARKET_CACHE_PATH, market)
        if not market.get("instruments"):
            raise ValueError("Market cache is empty; a data provider is required")
        analysis, optimization = _analysis_from_market(
            analysis_portfolio, universe, market, config, now
        )
        analysis["last_incremental_learning"] = (
            now.isoformat(timespec="seconds") if mode == "daily" else previous_analysis.get(
                "last_incremental_learning"
            )
        )
        analysis["last_research_run"] = (
            now.isoformat(timespec="seconds") if mode == "research" else previous_analysis.get(
                "last_research_run"
            )
        )
        analysis["next_scheduled_analysis"] = (
            now + timedelta(days=1 if mode == "daily" else 7)
        ).isoformat(timespec="seconds")
        write_json_atomic(ANALYSIS_PATH, analysis)
        if mode == "weekly":
            current_challenger = _methodology(
                registry, registry.get("challenger_methodology_id")
            )
            pending = build_pending_decisions(
                analysis.get("positions") or [],
                analysis.get("candidates") or [],
                optimization,
                config,
                now,
                str(current_challenger.get("version") or "3.0.0"),
                str(market.get("generated_at") or now.isoformat()),
                bool(freshness.get("safe_mode")),
                read_json(PENDING_PATH),
                [
                    item
                    for run in (shadow.get("runs", []) or [])
                    for item in run.get("decisions", [])
                ],
            )
            write_json_atomic(PENDING_PATH, pending)
            record = shadow_record(pending, analysis_portfolio.get("positions") or [], now)
            prices = {
                item.get("instrument_id"): item.get("current_price")
                for item in analysis.get("positions", [])
            }
            for item in record.get("decisions", []):
                item["signal_price"] = prices.get(item.get("instrument"))
                item["hypothetical_execution_status"] = "AWAITING_FUTURE_OUTCOME"
                item["costs"] = config.transaction_cost_buffer
            shadow = _append_shadow(shadow, record, baseline_before, now)
            write_json_atomic(SHADOW_PATH, shadow)
        if mode == "daily":
            learning = read_json(LEARNING_PATH)
            analysis["learning_statistics"] = learning.get("statistics") or {}
            write_json_atomic(ANALYSIS_PATH, analysis)
        if mode == "research":
            histories = {
                instrument_id: item.get("history") or []
                for instrument_id, item in market.get("instruments", {}).items()
            }
            validation = run_walk_forward(
                histories,
                _baseline_weights(baseline_before),
                risk_free_rate=config.risk_free_rate,
                generated_at=now,
            )
            write_json_atomic(VALIDATION_PATH, validation)

    assert_baseline_unchanged(baseline_copy, read_json(BASELINE_PORTFOLIO_PATH))
    _record_baseline_validation(registry, baseline_before, now)
    operational = _operational_state(freshness, True, now)
    write_json_atomic(OPERATIONAL_PATH, operational)
    validation = read_json(VALIDATION_PATH)
    promotion_history = read_json(PROMOTION_HISTORY_PATH)
    registry, promotion_history, promotion_record = evaluate_and_apply(
        registry,
        validation,
        shadow.get("statistics") or {},
        read_json(ENGINE_DATA_ROOT / "probation_metrics.json"),
        operational,
        _live_risk(analysis),
        config,
        now,
        promotion_history,
    )
    write_json_atomic(REGISTRY_PATH, registry)
    write_json_atomic(PROMOTION_HISTORY_PATH, promotion_history)
    public = build_public_snapshot(
        registry,
        analysis,
        pending,
        shadow,
        promotion_history,
        operational,
        config,
        now,
        PAPER_PATH.exists(),
    )
    publish(public)
    assert_baseline_unchanged(baseline_copy, read_json(BASELINE_PORTFOLIO_PATH))
    return {
        "mode": mode,
        "controller_status": registry.get("controller_state"),
        "challenger_status": _methodology(
            registry, registry.get("challenger_methodology_id")
        ).get("status"),
        "safe_mode": operational.get("safe_mode"),
        "promotion_record": promotion_record,
        "public_snapshot": str(
            (ENGINE_DATA_ROOT / "public" / "brace_engine_public.json")
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("monitor", "daily", "weekly", "research", "bootstrap"),
        required=True,
    )
    parser.add_argument(
        "--network",
        action="store_true",
        help="Fetch current market history through yfinance.",
    )
    args = parser.parse_args()
    mode = "monitor" if args.mode == "bootstrap" else args.mode
    provider = YFinanceProvider() if args.network else None
    result = run_cycle(mode, provider=provider)
    print(
        f"BRACE {result['mode']}: {result['controller_status']} / "
        f"{result['challenger_status']} / safe_mode={result['safe_mode']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

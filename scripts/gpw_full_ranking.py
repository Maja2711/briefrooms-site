#!/usr/bin/env python3
"""Canonical full-universe GPW Daily quantitative ranking (P0.3).

The final trade still uses catalyst evidence, Opening Confirmation and empirical
EV. This artifact exposes the entire pre-news quantitative universe so the
selection process is observable instead of only publishing the winner/top-N.
Every configured symbol is represented: ranked, investment-screened-out or
rejected by the canonical P0.4 data gates.
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

try:
    from scripts import daily_stock_core as core
    from scripts import gpw_data_gates as gates
    from scripts import gpw_daily_pick as gpw
    from scripts import gpw_event_driven_loop as runtime
    from scripts import gpw_provider_v2 as provider
except ModuleNotFoundError:
    import daily_stock_core as core
    import gpw_data_gates as gates
    import gpw_daily_pick as gpw
    import gpw_event_driven_loop as runtime
    import gpw_provider_v2 as provider


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_PATH = ROOT / "data/investments/gpw_daily_candidate_ranking.json"
ENGINE = "gpw-full-ranking-v1"


def _detail(exc: Exception) -> str:
    return f"{type(exc).__name__}: {' '.join(str(exc).split())}"[:500]


def _screen_reason(
    bars: list[gpw.Bar], feature_day, config: dict[str, Any]
) -> str:
    completed = [bar for bar in bars if bar.day <= feature_day]
    if len(completed) < 60:
        return "insufficient_completed_history"
    close = float(completed[-1].close or 0.0)
    if close <= 0.0:
        return "non_positive_close"
    turnover = statistics.median(
        float(bar.close) * max(int(bar.volume or 0), 0) for bar in completed[-20:]
    )
    if turnover < float(config["minimum_median_turnover_pln"]):
        return "minimum_liquidity"
    atr = core.true_range(completed)
    if atr <= 0.0 or not math.isfinite(atr):
        return "invalid_atr"
    risk = max(atr * core.GPW_PROFILE.risk_atr_multiple, close * core.GPW_PROFILE.risk_floor_percent)
    if risk / close > float(config["maximum_risk_percent"]):
        return "maximum_risk_percent"
    return "quant_screen_rejected"


def _ranked_row(candidate: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
    momentum = candidate.get("relative_momentum_detail") or {}
    return {
        "rank": candidate.get("quant_rank"),
        "status": "RANKED",
        "eligible": True,
        "symbol": candidate["symbol"],
        "ticker": str(candidate["symbol"]).removesuffix(".WA"),
        "name": candidate["name"],
        "sector": candidate["sector"],
        "quant_pre_score": candidate.get("quant_pre_score"),
        "scores": dict(candidate.get("scores") or {}),
        "returns": dict(candidate.get("returns") or {}),
        "relative_momentum_detail": momentum,
        "reference_price": candidate.get("reference_price"),
        "risk_percent": candidate.get("risk_percent"),
        "median_turnover_pln": candidate.get("median_turnover_pln"),
        "volume_ratio": candidate.get("volume_ratio"),
        "feature_session": gate.get("feature_session"),
        "historical_lag_sessions": gate.get("lag_sessions"),
        "data_gate_status": gate.get("status"),
        "quant_engine": candidate.get("quant_engine"),
    }


def build(
    *,
    now=None,
    cache: dict[str, list[gpw.Bar]] | None = None,
    provider_failures: dict[str, str] | None = None,
) -> dict[str, Any]:
    now = now or gpw.now_warsaw()
    runtime.install()
    config = gpw.load_config()
    expected = gpw.previous_session(now.date(), config)
    history = gpw.all_history()
    if cache is None:
        cache = provider.prefetch_market(config)
    provider_failures = provider_failures or dict(provider.LAST_AUDIT.get("failures") or {})

    report = gates.historical_universe_report(
        config,
        cache,
        expected_day=expected,
        provider_failures=provider_failures,
    )
    gates.require_market_coverage(report)

    ranked_candidates: list[dict[str, Any]] = []
    rejected: dict[str, str] = {}
    gate_by_symbol = report["by_symbol"]

    for company in config["universe"]:
        symbol = str(company["symbol"])
        gate = gate_by_symbol[symbol]
        if not gate.get("accepted"):
            continue
        feature_day = gpw.date.fromisoformat(gate["feature_session"]) if hasattr(gpw, "date") else None
        if feature_day is None:
            from datetime import date
            feature_day = date.fromisoformat(gate["feature_session"])
        completed = [bar for bar in list(cache.get(symbol) or []) if bar.day <= feature_day]
        try:
            candidate = gpw.build_quant_candidate(company, completed, feature_day, config, history)
        except Exception as exc:
            rejected[symbol] = "quant_exception:" + _detail(exc)
            continue
        if candidate is None:
            rejected[symbol] = _screen_reason(completed, feature_day, config)
            continue
        candidate["historical_feature_session"] = feature_day.isoformat()
        candidate["historical_feature_lag_sessions"] = gate.get("lag_sessions")
        ranked_candidates.append(candidate)

    gpw.normalize_cross_section(ranked_candidates)
    ranked_candidates.sort(
        key=lambda row: (
            float(row.get("quant_pre_score") or 0.0),
            float((row.get("scores") or {}).get("relative_momentum") or 0.0),
        ),
        reverse=True,
    )
    for rank, candidate in enumerate(ranked_candidates, start=1):
        candidate["quant_rank"] = rank

    ranked_by_symbol = {str(row["symbol"]): row for row in ranked_candidates}
    rows: list[dict[str, Any]] = []
    for company in config["universe"]:
        symbol = str(company["symbol"])
        gate = gate_by_symbol[symbol]
        candidate = ranked_by_symbol.get(symbol)
        if candidate is not None:
            rows.append(_ranked_row(candidate, gate))
            continue
        if not gate.get("accepted"):
            rows.append(
                {
                    "rank": None,
                    "status": "DATA_REJECTED",
                    "eligible": False,
                    "symbol": symbol,
                    "ticker": symbol.removesuffix(".WA"),
                    "name": company["name"],
                    "sector": company["sector"],
                    "reason": gate.get("reason"),
                    "feature_session": gate.get("feature_session"),
                    "historical_lag_sessions": gate.get("lag_sessions"),
                    "data_gate_status": gate.get("status"),
                }
            )
            continue
        rows.append(
            {
                "rank": None,
                "status": "SCREENED_OUT",
                "eligible": False,
                "symbol": symbol,
                "ticker": symbol.removesuffix(".WA"),
                "name": company["name"],
                "sector": company["sector"],
                "reason": rejected.get(symbol, "quant_screen_rejected"),
                "feature_session": gate.get("feature_session"),
                "historical_lag_sessions": gate.get("lag_sessions"),
                "data_gate_status": gate.get("status"),
            }
        )

    rows.sort(
        key=lambda row: (
            0 if row["status"] == "RANKED" else 1 if row["status"] == "SCREENED_OUT" else 2,
            int(row["rank"] or 10_000),
            str(row["ticker"]),
        )
    )
    ranking_config = config.get("full_ranking") or {}
    return {
        "schema_version": "gpw-daily-candidate-ranking-v1",
        "engine": str(ranking_config.get("engine") or ENGINE),
        "policy_version": config.get("policy_version"),
        "date": now.date().isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "timezone": "Europe/Warsaw",
        "expected_session": expected.isoformat(),
        "universe_size": len(config["universe"]),
        "ranked_count": len(ranked_candidates),
        "screened_out_count": sum(row["status"] == "SCREENED_OUT" for row in rows),
        "data_rejected_count": sum(row["status"] == "DATA_REJECTED" for row in rows),
        "data_quality": report,
        "relative_momentum": config.get("relative_momentum") or {},
        "ranking_scope": "pre_news_pre_opening_quantitative",
        "rows": rows,
    }


def persist(payload: dict[str, Any]) -> None:
    if len(payload.get("rows") or []) != int(payload.get("universe_size") or 0):
        raise gpw.PublicationError("P0.3 ranking does not cover the entire configured GPW universe")
    gpw.atomic_json(PUBLIC_PATH, payload)


def reference(payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
    payload = payload or gpw.load_json(PUBLIC_PATH)
    if not isinstance(payload, dict):
        return None
    return {
        "engine": payload.get("engine"),
        "date": payload.get("date"),
        "generated_at": payload.get("generated_at"),
        "expected_session": payload.get("expected_session"),
        "universe_size": payload.get("universe_size"),
        "ranked_count": payload.get("ranked_count"),
        "artifact": "/data/investments/gpw_daily_candidate_ranking.json",
    }


def main() -> int:
    payload = build()
    persist(payload)
    print(
        "GPW_FULL_RANKING_OK",
        payload["ranked_count"],
        "/",
        payload["universe_size"],
        "coverage=",
        payload["data_quality"]["complete_ratio"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

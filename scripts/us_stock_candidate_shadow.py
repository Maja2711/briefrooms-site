#!/usr/bin/env python3
"""Continuous-session US selector for the legacy v1 shadow challenger.

This intentionally does not inherit the obsolete 09:45 publication cutoff.
The only time boundary is the exchange's regular session.  Candidate rejection
is non-terminal: the selector continues through the ranked shortlist until a
candidate passes every evidence, execution and risk gate or the shortlist is
exhausted.
"""
from __future__ import annotations

from datetime import datetime, time as clock_time
from typing import Any, Iterable

try:
    from scripts import us_daily_stock as us
except ModuleNotFoundError:  # pragma: no cover
    import us_daily_stock as us


def generate(now: datetime | None = None, *, exclude_symbols: Iterable[str] = ()) -> dict[str, Any]:
    now = now or us.now_ny()
    config = us.load_config()
    excluded = {str(symbol).upper() for symbol in exclude_symbols}
    if not us.is_session_day(now.date(), config):
        return us.base_payload(now, config, "NO_TRADE", "US cash market is closed today.")
    if now.time() < us.parse_clock(config["analysis_not_before"]):
        payload = us.base_payload(now, config, "PENDING", "Waiting for the US regular-session analysis window.")
        payload["locked"] = False
        return payload
    # Natural exchange boundary only.  There is deliberately no morning
    # publication cutoff in the challenger.
    if now.time() >= clock_time(16, 0):
        return us.base_payload(now, config, "NO_TRADE", "US regular session has ended; no new shadow entry is opened after the close.")

    expected = us.previous_session(now.date(), config)
    valid_market = 0
    failures: dict[str, str] = {}
    providers: dict[str, str] = {}
    candidates: list[dict[str, Any]] = []
    for company in config["universe"]:
        symbol = str(company["symbol"]).upper()
        if symbol in excluded:
            continue
        try:
            bars, meta = us.fetch_resilient_bars(symbol)
            completed = [bar for bar in bars if bar.day <= expected]
            if not completed or completed[-1].day != expected:
                raise us.PublicationError(f"stale latest session {completed[-1].day if completed else 'none'}")
            valid_market += 1
            providers[symbol] = str(meta.get("provider") or "unknown")
            candidate = us.build_candidate(company, bars, expected, config)
            if candidate:
                candidates.append(candidate)
        except Exception as exc:
            failures[symbol] = f"{type(exc).__name__}: {str(exc)[:240]}"

    eligible_universe = max(len(config["universe"]) - len(excluded), 1)
    ratio = valid_market / eligible_universe
    if ratio < float(config["minimum_data_completeness"]):
        payload = us.base_payload(now, config, "DATA_ERROR", f"Fresh US market-data completeness {ratio:.0%} is below the required threshold.")
        payload["data_quality"] = {"status": "failed", "complete_ratio": round(ratio, 4), "expected_session": expected.isoformat(), "provider_failures": failures}
        return payload
    if not candidates:
        payload = us.base_payload(now, config, "NO_TRADE", "No non-held US stock passed liquidity and risk screening.")
        payload["data_quality"] = {"status": "healthy", "complete_ratio": round(ratio, 4), "expected_session": expected.isoformat(), "ranked_candidates": 0}
        return payload

    us.normalize_cross_section(candidates)
    search_depth = min(len(candidates), max(12, int(config.get("top_candidates_for_news") or 0)))
    shortlist = sorted(candidates, key=lambda item: item["quant_pre_score"], reverse=True)[:search_depth]
    for row in shortlist:
        try:
            row["sources"] = us.news_items(row, now=now)
        except Exception:
            row["sources"] = []
    if not any(row.get("sources") for row in shortlist):
        payload = us.base_payload(now, config, "NO_TRADE", "No fresh verifiable catalyst was available for the ranked US shortlist.")
        payload["data_quality"] = {"status": "healthy", "complete_ratio": round(ratio, 4), "expected_session": expected.isoformat(), "ranked_candidates": len(candidates), "reviewed_candidates": len(shortlist)}
        return payload

    analyses = us.gemini_analysis(shortlist)
    eligible: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    analysis_rejections: dict[str, str] = {}
    for row in shortlist:
        analysis = analyses.get(row["symbol"])
        if not analysis:
            analysis_rejections[row["symbol"]] = "missing_analysis"
            continue
        if not us.source_gate(row, analysis):
            analysis_rejections[row["symbol"]] = "source_gate"
            continue
        score = us.composite(row, analysis, config)
        if float(row["reward_risk"]) < float(config["minimum_reward_risk"]):
            analysis_rejections[row["symbol"]] = "reward_risk"
            continue
        eligible.append((score, row, analysis))
    eligible.sort(key=lambda item: item[0], reverse=True)

    review_rejections: list[dict[str, Any]] = []
    execution_rejections: list[dict[str, Any]] = []
    for score, candidate, analysis in eligible:
        review = us.gemini_review(candidate, analysis, score)
        if review.get("approved") is not True:
            review_rejections.append({"symbol": candidate["symbol"], "score": score, "reason": review.get("reason")})
            continue
        try:
            candidate = us.reprice(candidate, now=now)
            entry = float((candidate.get("market_snapshot") or {}).get("last") or candidate.get("reference_price") or 0.0)
            stop = float(candidate.get("stop") or 0.0)
            risk_percent = (entry - stop) / entry if entry > 0 else 99.0
            if entry <= 0 or not stop < entry or not (0 < risk_percent <= float(config["maximum_risk_percent"])):
                raise us.PublicationError("fresh execution geometry outside hard risk limit")
        except Exception as exc:
            execution_rejections.append({"symbol": candidate.get("symbol"), "score": score, "reason": f"{type(exc).__name__}: {str(exc)[:180]}"})
            continue

        by_id = {source["id"]: source for source in candidate.get("sources", [])}
        approved_sources = [by_id[source_id] for source_id in review.get("supported_source_ids", []) if source_id in by_id]
        if not approved_sources:
            execution_rejections.append({"symbol": candidate.get("symbol"), "score": score, "reason": "approved_source_resolution_empty"})
            continue
        target_score = float(config["target_score"])
        conviction = "high" if score >= target_score + 8 else "solid" if score >= target_score else "moderate"
        payload = us.base_payload(now, config, "TRADE", "Best non-held v1 shadow candidate passed all hard gates; score is ranking metadata, not a veto.")
        payload["locked"] = False
        payload["selection"] = {
            "symbol": candidate["symbol"],
            "ticker": candidate["symbol"],
            "name": candidate["name"],
            "sector": candidate["sector"],
            "score": score,
            "score_target": target_score,
            "score_target_met": score >= target_score,
            "conviction": conviction,
            "reference_price": candidate["reference_price"],
            "entry_zone": candidate["entry_zone"],
            "stop": candidate["stop"],
            "target": candidate["target"],
            "risk_percent": round(risk_percent, 6),
            "reward_risk": candidate["reward_risk"],
            "selection_mode": "V1_SHADOW_CONTINUOUS",
            "holding_policy": "OPEN_ENDED_MODEL_CONTROLLED",
            "valid_until": None,
            "time_stop": None,
            "early_exit": "Shadow portfolio applies the same model-controlled SL/TP and thesis review contract.",
            "thesis": analysis["thesis"],
            "why_now": analysis["why_now"],
            "risk_factors": analysis["risk_factors"],
            "scores": {**candidate["scores"], "catalyst": analysis["catalyst_score"]},
            "sources": approved_sources,
            "review": review,
            "market_snapshot": candidate["market_snapshot"],
        }
        payload["data_quality"] = {
            "status": "healthy",
            "complete_ratio": round(ratio, 4),
            "expected_session": expected.isoformat(),
            "ranked_candidates": len(candidates),
            "reviewed_candidates": len(shortlist),
            "eligible_candidates": len(eligible),
            "excluded_held_symbols": sorted(excluded),
            "analysis_rejections": analysis_rejections,
            "review_rejections": review_rejections,
            "execution_rejections": execution_rejections,
            "provider_failures": failures,
            "provider_usage": providers,
        }
        return payload

    payload = us.base_payload(now, config, "NO_TRADE", "All ranked non-held candidates failed evidence, review or fresh execution gates.")
    payload["data_quality"] = {
        "status": "healthy",
        "complete_ratio": round(ratio, 4),
        "expected_session": expected.isoformat(),
        "analysis_rejections": analysis_rejections,
        "review_rejections": review_rejections,
        "execution_rejections": execution_rejections,
        "excluded_held_symbols": sorted(excluded),
    }
    return payload

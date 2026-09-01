#!/usr/bin/env python3
"""GPW Daily v2 integrity layer.

Fixes two failure modes of v1.3:
1. market-data completeness is measured from valid/fresh bars, not from the
   number of securities surviving investment screening;
2. provider failures and screening rejections are kept separately so an
   operational outage cannot be confused with an investment decision.

It also supports an explicit, date-bound recovery cutoff through
GPW_RECOVERY_DATE + GPW_RECOVERY_CUTOFF. The override is inert in normal runs.
"""
from __future__ import annotations

import copy
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as clock_time
from typing import Any

try:
    from scripts import gpw_daily_pick as gpw
    from scripts import gpw_market_data as market
    from scripts import gpw_opening_confirmation as opening
except ModuleNotFoundError:
    import gpw_daily_pick as gpw
    import gpw_market_data as market
    import gpw_opening_confirmation as opening


LAST_PREFETCH_DIAGNOSTICS: dict[str, Any] = {}


def cutoff_for(day: date, config: dict[str, Any]) -> datetime:
    value = str(config["publication_cutoff"])
    recovery_date = str(os.environ.get("GPW_RECOVERY_DATE") or "").strip()
    recovery_cutoff = str(os.environ.get("GPW_RECOVERY_CUTOFF") or "").strip()
    if recovery_date == day.isoformat() and recovery_cutoff:
        value = recovery_cutoff
    hour, minute = (int(part) for part in value.split(":"))
    return datetime.combine(day, clock_time(hour, minute), tzinfo=gpw.WARSAW)


def _detail(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text}"[:600]


def prefetch_market(config: dict[str, Any]) -> dict[str, list[gpw.Bar]]:
    global LAST_PREFETCH_DIAGNOSTICS
    symbols = [str(row["symbol"]) for row in config["universe"]]
    result: dict[str, list[gpw.Bar]] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as pool:
        futures = {
            pool.submit(market.fetch_resilient_bars, symbol): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                bars = future.result()
                result[symbol] = bars
            except Exception as exc:
                failures[symbol] = _detail(exc)
    LAST_PREFETCH_DIAGNOSTICS = {
        "requested": len(symbols),
        "received": len(result),
        "provider_failures": failures,
        "latest_sessions": {
            symbol: bars[-1].day.isoformat() if bars else None
            for symbol, bars in result.items()
        },
    }
    return result


def _screen_reason(company: dict[str, str], bars: list[gpw.Bar], expected: date, config: dict[str, Any], history: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    if not bars:
        return None, "empty_bars"
    if bars[-1].day < expected:
        return None, f"stale_latest_session:{bars[-1].day.isoformat()}"
    candidate = gpw.build_quant_candidate(company, bars, expected, config, history)
    if candidate is None:
        return None, "screened_by_liquidity_atr_or_risk"
    return candidate, None


def _quality_payload(payload: dict[str, Any], *, cutoff: datetime, market_ratio: float, expected: date, candidates: list[dict[str, Any]], market_failures: dict[str, str], screened_out: dict[str, str]) -> dict[str, Any]:
    payload["publication_cutoff"] = cutoff.strftime("%H:%M")
    payload.setdefault("data_quality", {}).update(
        {
            "complete_ratio": round(market_ratio, 4),
            "expected_session": expected.isoformat(),
            "market_data_symbols": round(market_ratio * len(market_failures | screened_out | {c['symbol']: '' for c in candidates})),
            "ranked_candidates": len(candidates),
            "provider_failures": market_failures,
            "screened_out": screened_out,
        }
    )
    return payload


def _opening_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("opening_confirmation") or {}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "engine": str(raw.get("engine") or opening.ENGINE),
        "top_candidates": max(1, int(raw.get("top_candidates", 6))),
        "weight": opening.bounded_weight(raw.get("weight", 0.25)),
        "role": str(raw.get("role") or "bounded_reranking_overlay_not_hard_gate"),
    }


def _fresh_opening_snapshot(snapshot: dict[str, Any], now: datetime) -> None:
    if float(snapshot.get("last") or 0.0) <= 0.0:
        raise ValueError("non-positive current-session quote")
    if str(snapshot.get("date") or "") != now.date().isoformat():
        raise ValueError(f"stale execution session: {snapshot.get('date')}")
    crosscheck = snapshot.get("crosscheck") or {}
    if str(crosscheck.get("status") or "").lower() in {"conflict", "rejected"}:
        raise ValueError("execution quote cross-check conflict")


def _rerank_with_opening(
    eligible: list[tuple[float, dict[str, Any], dict[str, Any]]],
    *,
    config: dict[str, Any],
    now: datetime,
) -> tuple[list[tuple[float, dict[str, Any], dict[str, Any]]], dict[str, Any]]:
    """Rerank eligible primary candidates using live post-open confirmation."""
    settings = _opening_config(config)
    diagnostics: dict[str, Any] = {
        **settings,
        "base_eligible_candidates": len(eligible),
        "evaluated_candidates": 0,
        "failures": {},
    }
    if not settings["enabled"] or not eligible:
        return eligible, diagnostics

    shortlist = eligible[: int(settings["top_candidates"])]
    assessed: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    failures: dict[str, str] = {}

    def one(item: tuple[float, dict[str, Any], dict[str, Any]]):
        base_score, candidate, analysis = item
        symbol = str(candidate["symbol"])
        snapshot = market.opening_snapshot(symbol, now=now)
        _fresh_opening_snapshot(snapshot, now)
        confirmation = opening.score(candidate, snapshot)
        adjusted = opening.blend(
            base_score,
            float(confirmation["score"]),
            float(settings["weight"]),
        )
        enriched = copy.deepcopy(candidate)
        enriched["legacy_composite_score"] = gpw.round2(base_score)
        enriched["opening_adjusted_score"] = adjusted
        enriched["opening_confirmation"] = confirmation
        enriched["opening_confirmation_engine"] = opening.ENGINE
        enriched["opening_market_snapshot"] = snapshot
        return adjusted, enriched, analysis

    with ThreadPoolExecutor(max_workers=min(4, len(shortlist))) as pool:
        futures = {
            pool.submit(one, item): str(item[1]["symbol"])
            for item in shortlist
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                assessed.append(future.result())
            except Exception as exc:
                failures[symbol] = _detail(exc)

    assessed.sort(
        key=lambda item: (
            float(item[0]),
            float(item[1].get("quant_pre_score") or 0.0),
        ),
        reverse=True,
    )
    diagnostics["evaluated_candidates"] = len(assessed)
    diagnostics["failures"] = failures
    diagnostics["selected_symbol"] = assessed[0][1]["symbol"] if assessed else None
    diagnostics["selected_opening_score"] = (
        assessed[0][1]["opening_confirmation"]["score"] if assessed else None
    )
    return assessed, diagnostics


def generate(*, now: datetime | None = None, market_fetcher=None, news_fetcher=None) -> dict[str, Any]:
    now = now or gpw.now_warsaw()
    config = gpw.load_config()
    cutoff = cutoff_for(now.date(), config)
    if market_fetcher is None:
        market_fetcher = market.fetch_resilient_bars
    if news_fetcher is None:
        news_fetcher = gpw.news_items

    if not gpw.is_session_day(now.date(), config):
        payload = gpw.common_payload(now, config, "BRAK_TRANSAKCJI", "Dziś nie ma sesji GPW.")
        payload["locked"] = True
        payload["publication_cutoff"] = cutoff.strftime("%H:%M")
        payload["data_quality"] = {"status": "not_applicable", "complete_ratio": 1.0}
        return payload
    if now >= cutoff:
        payload = gpw.failure_payload(now, config, f"Nie utworzono nowego sygnału przed {cutoff:%H:%M}.", "cutoff")
        payload["publication_cutoff"] = cutoff.strftime("%H:%M")
        return payload

    expected = gpw.previous_session(now.date(), config)
    history = gpw.all_history()
    candidates: list[dict[str, Any]] = []
    market_failures: dict[str, str] = {}
    screened_out: dict[str, str] = {}
    valid_market = 0

    for company in config["universe"]:
        symbol = str(company["symbol"])
        try:
            bars = market_fetcher(symbol)
            if not bars:
                raise gpw.PublicationError("empty history")
            if bars[-1].day < expected:
                raise gpw.PublicationError(
                    f"stale history: latest={bars[-1].day.isoformat()} expected={expected.isoformat()}"
                )
            valid_market += 1
            candidate, reason = _screen_reason(company, bars, expected, config, history)
            if candidate:
                candidates.append(candidate)
            elif reason:
                screened_out[symbol] = reason
        except Exception as exc:
            market_failures[symbol] = _detail(exc)

    universe_size = len(config["universe"])
    market_ratio = valid_market / max(universe_size, 1)
    if market_ratio < float(config["minimum_data_completeness"]):
        payload = gpw.failure_payload(
            now,
            config,
            f"Kompletność świeżych danych rynkowych {market_ratio:.0%} jest poniżej wymaganego progu.",
            "market_data",
        )
        payload["publication_cutoff"] = cutoff.strftime("%H:%M")
        payload["data_quality"].update(
            {
                "complete_ratio": round(market_ratio, 4),
                "expected_session": expected.isoformat(),
                "valid_market_symbols": valid_market,
                "failed_symbols": sorted(market_failures),
                "provider_failures": market_failures,
                "screened_out": screened_out,
                "prefetch": LAST_PREFETCH_DIAGNOSTICS,
            }
        )
        return payload

    if not candidates:
        payload = gpw.common_payload(
            now,
            config,
            "BRAK_TRANSAKCJI",
            "Dane rynkowe są kompletne, ale żaden walor nie przeszedł screeningu płynności i ryzyka.",
        )
        payload["publication_cutoff"] = cutoff.strftime("%H:%M")
        payload["data_quality"] = {
            "status": "healthy",
            "complete_ratio": round(market_ratio, 4),
            "expected_session": expected.isoformat(),
            "valid_market_symbols": valid_market,
            "ranked_candidates": 0,
            "provider_failures": market_failures,
            "screened_out": screened_out,
        }
        return payload

    gpw.normalize_cross_section(candidates)
    ranked_candidates = sorted(
        candidates,
        key=lambda item: item["quant_pre_score"],
        reverse=True,
    )
    for rank, candidate in enumerate(ranked_candidates, start=1):
        candidate["quant_rank"] = rank
    shortlist = ranked_candidates[: int(config["top_candidates_for_news"])]
    source_errors: dict[str, str] = {}
    for candidate in shortlist:
        try:
            candidate["sources"] = news_fetcher(candidate, now=now)
        except Exception as exc:
            candidate["sources"] = []
            source_errors[candidate["symbol"]] = _detail(exc)

    if not any(candidate.get("sources") for candidate in shortlist):
        payload = gpw.common_payload(now, config, "BRAK_TRANSAKCJI", "Brak świeżego, możliwego do zweryfikowania katalizatora dla kandydatów.")
        payload["publication_cutoff"] = cutoff.strftime("%H:%M")
        payload["data_quality"] = {
            "status": "healthy",
            "complete_ratio": round(market_ratio, 4),
            "expected_session": expected.isoformat(),
            "valid_market_symbols": valid_market,
            "ranked_candidates": len(candidates),
            "reviewed_candidates": len(shortlist),
            "source_errors": source_errors,
            "screened_out": screened_out,
        }
        return payload

    analyses = gpw.gemini_analysis(shortlist)
    eligible: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    rejected_analysis: dict[str, str] = {}
    for candidate in shortlist:
        analysis = analyses.get(candidate["symbol"])
        if not analysis:
            rejected_analysis[candidate["symbol"]] = "missing_analysis"
            continue
        if not gpw.source_gate(candidate, analysis):
            rejected_analysis[candidate["symbol"]] = "source_gate"
            continue
        score = gpw.composite(candidate, analysis, config)
        if score < float(config["minimum_composite_score"]):
            rejected_analysis[candidate["symbol"]] = f"score:{score}"
            continue
        if candidate["reward_risk"] < float(config["minimum_reward_risk"]):
            rejected_analysis[candidate["symbol"]] = f"reward_risk:{candidate['reward_risk']}"
            continue
        eligible.append((score, candidate, analysis))
    eligible.sort(key=lambda item: item[0], reverse=True)

    base_eligible_count = len(eligible)
    eligible, opening_diagnostics = _rerank_with_opening(
        eligible,
        config=config,
        now=now,
    )

    payload = gpw.common_payload(now, config, "BRAK_TRANSAKCJI", "Żaden kandydat nie przeszedł pełnego progu jakości i ryzyka.")
    payload["publication_cutoff"] = cutoff.strftime("%H:%M")
    payload.setdefault("methodology", {})["opening_confirmation"] = {
        "enabled": opening_diagnostics["enabled"],
        "engine": opening_diagnostics["engine"],
        "weight": opening_diagnostics["weight"],
        "top_candidates": opening_diagnostics["top_candidates"],
        "component_weights": dict(opening.COMPONENT_WEIGHTS),
        "role": opening_diagnostics["role"],
    }
    payload["data_quality"] = {
        "status": "healthy",
        "complete_ratio": round(market_ratio, 4),
        "expected_session": expected.isoformat(),
        "valid_market_symbols": valid_market,
        "ranked_candidates": len(candidates),
        "reviewed_candidates": len(shortlist),
        "base_eligible_candidates": base_eligible_count,
        "eligible_candidates": len(eligible),
        "opening_confirmation": opening_diagnostics,
        "provider_failures": market_failures,
        "screened_out": screened_out,
        "analysis_rejections": rejected_analysis,
    }
    if not eligible:
        if base_eligible_count and opening_diagnostics["enabled"]:
            payload["reason"] = (
                "Kandydaci przeszli analizę bazową, ale żaden nie miał poprawnego, "
                "świeżego potwierdzenia bieżącej sesji."
            )
        return payload

    review_rejections: list[dict[str, Any]] = []
    approved = None
    # Review the Opening-Confirmation-adjusted order until one candidate passes.
    for score, candidate, analysis in eligible:
        review = gpw.gemini_review(candidate, analysis, score)
        if review.get("approved"):
            approved = (score, candidate, analysis, review)
            break
        review_rejections.append(
            {"symbol": candidate["symbol"], "score": score, "reason": review.get("reason")}
        )
    payload["data_quality"]["review_rejections"] = review_rejections
    if approved is None:
        payload["reason"] = "Wszyscy kandydaci spełniający progi zostali odrzuceni w niezależnej recenzji."
        return payload

    if gpw.now_warsaw() >= cutoff:
        failed = gpw.failure_payload(gpw.now_warsaw(), config, f"Analiza zakończyła się po {cutoff:%H:%M}; sygnał nie został opublikowany.", "cutoff_after_review")
        failed["publication_cutoff"] = cutoff.strftime("%H:%M")
        return failed

    score, candidate, analysis, review = approved
    valid_until = gpw.add_sessions(now.date(), 2, config)
    sources_by_id = {source["id"]: source for source in candidate["sources"]}
    approved_sources = [sources_by_id[source_id] for source_id in review["supported_source_ids"] if source_id in sources_by_id]
    confirmation = candidate.get("opening_confirmation") or {}
    payload.update(
        {
            "decision": "TRANSAKCJA",
            "reason": "Kandydat przeszedł ranking, Opening Confirmation, bramki źródłowe, kontrolę ryzyka i niezależną recenzję Gemini.",
            "locked": True,
            "selection": {
                "symbol": candidate["symbol"],
                "ticker": candidate["symbol"].removesuffix(".WA"),
                "name": candidate["name"],
                "sector": candidate["sector"],
                "score": score,
                "legacy_composite_score": candidate.get("legacy_composite_score", score),
                "quant_pre_score": candidate.get("quant_pre_score"),
                "quant_rank": candidate.get("quant_rank"),
                "opening_confirmation_score": confirmation.get("score"),
                "opening_confirmation": confirmation,
                "opening_confirmation_engine": candidate.get("opening_confirmation_engine"),
                "reference_price": candidate["reference_price"],
                "entry_zone": candidate["entry_zone"],
                "activation": "Setup po otwarciu: wejście tylko w podanej strefie; powyżej górnej granicy nie gonić ceny.",
                "stop": candidate["stop"],
                "target": candidate["target"],
                "reward_risk": candidate["reward_risk"],
                "valid_until": valid_until.isoformat(),
                "thesis": analysis["thesis"],
                "why_now": analysis["why_now"],
                "risk_factors": analysis["risk_factors"],
                "scores": {
                    **candidate["scores"],
                    "catalyst": analysis["catalyst_score"],
                    "opening_confirmation": confirmation.get("score"),
                },
                "sources": approved_sources,
                "review": review,
                "market_snapshot": candidate.get("opening_market_snapshot"),
            },
            "outcome": {"status": "PENDING", "activated": None},
        }
    )
    return payload
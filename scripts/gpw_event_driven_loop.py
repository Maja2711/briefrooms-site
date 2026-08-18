#!/usr/bin/env python3
from __future__ import annotations

import copy
from datetime import time as clock_time
from typing import Any

try:
    from scripts import gpw_daily_control_loop as loop
    from scripts import gpw_daily_pick as gpw
    from scripts import gpw_event_layer as events
    from scripts import gpw_market_data as market
except ModuleNotFoundError:
    import gpw_daily_control_loop as loop
    import gpw_daily_pick as gpw
    import gpw_event_layer as events
    import gpw_market_data as market

_ORIGINAL_GENERATE = gpw.generate
_ORIGINAL_GEMINI_ANALYSIS = gpw.gemini_analysis
_ORIGINAL_BUILD_LEARNING_SNAPSHOT = loop.build_learning_snapshot
_ORIGINAL_PUBLIC_LEARNING = loop._public_learning
_ORIGINAL_ENRICH_PAYLOAD = loop._enrich_payload
_INSTALLED = False
_EVENT_CONTEXT_BY_SYMBOL: dict[str, dict[str, Any]] = {}


def _event_aware_analysis(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = _ORIGINAL_GEMINI_ANALYSIS(candidates)
    history = gpw.all_history()
    config = gpw.load_config()
    _EVENT_CONTEXT_BY_SYMBOL.clear()
    for candidate in candidates:
        symbol = str(candidate.get("symbol") or "")
        analysis = result.get(symbol)
        if not analysis:
            continue
        context = events.build_event_context(candidate, analysis, history, config)
        raw_score = float(analysis.get("catalyst_score") or 0.0)
        adjustment = float(context.get("event_learning", {}).get("catalyst_adjustment") or 0.0)
        analysis["raw_catalyst_score"] = gpw.round2(raw_score)
        analysis["catalyst_score"] = gpw.round2(gpw.clamp(raw_score + adjustment))
        analysis["event_context"] = context
        _EVENT_CONTEXT_BY_SYMBOL[symbol] = copy.deepcopy(context)
    return result


def _event_generate(**kwargs):
    forwarded = dict(kwargs)
    forwarded["news_fetcher"] = events.combined_news_items
    payload = _ORIGINAL_GENERATE(**forwarded)
    now = forwarded.get("now") or gpw.now_warsaw()
    if payload.get("decision") == "TRANSAKCJA":
        payload = market.reprice_transaction(payload, now=now)
    if payload.get("decision") == "AWARIA_DANYCH" and "08:30" in str(payload.get("reason") or ""):
        payload["reason"] = str(payload["reason"]).replace("08:30", "09:10")
    return payload


def _build_learning_snapshot(history, config, *, now):
    snapshot = _ORIGINAL_BUILD_LEARNING_SNAPSHOT(history, config, now=now)
    snapshot["event_expectancy"] = events.public_event_learning(history, config)
    snapshot["event_learning_method"] = "event_family_bayesian_shrinkage_v1"
    last = snapshot.get("last_lesson")
    if isinstance(last, dict):
        for row in reversed(history):
            selection = row.get("selection") or {}
            outcome = row.get("outcome") or {}
            if row.get("decision") == "TRANSAKCJA" and outcome.get("status") == "RESOLVED" and outcome.get("activated") is True:
                event_context = selection.get("event_context") or {}
                if event_context:
                    last["event_type"] = event_context.get("primary_type")
                    last["event_label"] = event_context.get("primary_label")
                break
    return snapshot


def _public_learning(snapshot: dict[str, Any]) -> dict[str, Any]:
    value = _ORIGINAL_PUBLIC_LEARNING(snapshot)
    value["event_learning_method"] = snapshot.get("event_learning_method")
    value["event_expectancy"] = copy.deepcopy(snapshot.get("event_expectancy") or [])
    return value


def _context_from_final_sources(context: dict[str, Any], final_sources: list[dict[str, Any]]) -> dict[str, Any]:
    value = copy.deepcopy(context)
    approved = sorted(
        [source for source in final_sources if source.get("id")],
        key=lambda source: (
            0 if source.get("source_kind") == "issuer_report" else 1,
            -int(source.get("materiality") or 0),
            float(source.get("age_hours") or 9999),
        ),
    )
    value["selected_event_source_ids"] = [str(source["id"]) for source in approved]
    primary = approved[0] if approved else None
    if primary:
        event_type = str(primary.get("event_type") or "other")
        value["primary_type"] = event_type
        value["primary_label"] = str(primary.get("event_label") or events.EVENT_LABELS.get(event_type, "inne zdarzenie"))
        value["primary_source_id"] = primary.get("id")
        value["primary_source_kind"] = primary.get("source_kind")
        value["primary_channel"] = primary.get("channel")
        value["materiality"] = int(primary.get("materiality") or 0)
        value["official_report_used"] = primary.get("source_kind") == "issuer_report"
    else:
        value["primary_source_id"] = None
        value["primary_source_kind"] = None
        value["primary_channel"] = None
        value["materiality"] = 0
        value["official_report_used"] = False
    return value


def _enrich_payload(payload: dict[str, Any], snapshot: dict[str, Any], *, attempts: int) -> dict[str, Any]:
    enriched = _ORIGINAL_ENRICH_PAYLOAD(payload, snapshot, attempts=attempts)
    enriched["methodology"]["event_layer"] = {
        "enabled": True,
        "channels": ["ESPI/EBI przez PAP", "niezależne newsy"],
        "market_reaction": "ostatnia zakończona sesja: cena, relatywny zwrot i wolumen",
        "event_learning": "bayesian shrinkage po typie zdarzenia",
        "weights_frozen": True,
    }
    enriched["methodology"]["market_data"] = {
        "historical_provider_order": ["Yahoo", "Stooq"],
        "opening_quote_providers": ["Yahoo", "Stooq"],
        "opening_quote_not_before": "09:05 Europe/Warsaw",
        "publication_cutoff": "09:10 Europe/Warsaw",
        "crosscheck_max_deviation": market.MAX_OPENING_CROSSCHECK_DEVIATION,
    }
    enriched.setdefault("learning", {})["event_expectancy"] = copy.deepcopy(snapshot.get("event_expectancy") or [])
    selection = enriched.get("selection")
    if isinstance(selection, dict):
        context = _EVENT_CONTEXT_BY_SYMBOL.get(str(selection.get("symbol") or ""))
        if context:
            final_sources = list(selection.get("sources") or [])
            selection["event_context"] = _context_from_final_sources(context, final_sources)
            selection["evidence_summary"] = {
                "official_reports": sum(1 for source in final_sources if source.get("source_kind") == "issuer_report"),
                "independent_news": sum(1 for source in final_sources if source.get("source_kind") != "issuer_report"),
                "primary_event": selection["event_context"].get("primary_label"),
            }
    return enriched


def _publish_current_failure(now, config, snapshot, stage):
    payload = gpw.failure_payload(now, config, "Brak dzisiaj wyboru.", stage)
    payload["reason"] = (
        f"Brak dzisiaj wyboru — sygnał po otwarciu nie został potwierdzony przed {config['publication_cutoff']}."
    )
    payload["locked"] = True
    payload = loop._enrich_payload(payload, snapshot, attempts=0)
    gpw.publish(payload)
    return payload


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    # Research/ranking starts only after the continuous session is open long
    # enough to obtain a first executable market snapshot.  The 09:10 cutoff
    # keeps the decision short-horizon while avoiding a stale previous-close
    # entry zone.
    loop.EARLIEST_GENERATION = clock_time(9, 5)
    gpw.fetch_yahoo_bars = market.fetch_resilient_bars
    gpw.generate = _event_generate
    gpw.gemini_analysis = _event_aware_analysis
    loop.build_learning_snapshot = _build_learning_snapshot
    loop._public_learning = _public_learning
    loop._enrich_payload = _enrich_payload
    loop._publish_current_failure = _publish_current_failure
    _INSTALLED = True


def main() -> int:
    install()
    return loop.main()


if __name__ == "__main__":
    raise SystemExit(main())

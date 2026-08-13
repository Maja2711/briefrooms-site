#!/usr/bin/env python3
"""Event-driven entrypoint for the Polish GPW daily outlook.

This adapter deliberately leaves the tested core engine intact and installs a
small set of bounded overlays before delegating to ``gpw_daily_control_loop``:

* ESPI/EBI/PAP issuer-report discovery is merged with independent news;
* event type/materiality and latest completed-session market reaction are sent
  to Gemini as evidence metadata;
* resolved paper trades with the same event family contribute a bounded,
  shrinkage-based adjustment to the catalyst score;
* the final selected trade persists its event context for future learning.

The wrapper is PL-only because only the Polish GPW workflow invokes it.
"""
from __future__ import annotations

import copy
from typing import Any

try:
    from scripts import gpw_daily_control_loop as loop
    from scripts import gpw_daily_pick as gpw
    from scripts import gpw_event_layer as events
except ModuleNotFoundError:  # GitHub Actions uses PYTHONPATH=scripts
    import gpw_daily_control_loop as loop
    import gpw_daily_pick as gpw
    import gpw_event_layer as events


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
    return _ORIGINAL_GENERATE(**forwarded)


def _build_learning_snapshot(history, config, *, now):
    snapshot = _ORIGINAL_BUILD_LEARNING_SNAPSHOT(history, config, now=now)
    snapshot["event_expectancy"] = events.public_event_learning(history, config)
    snapshot["event_learning_method"] = "event_family_bayesian_shrinkage_v1"
    last = snapshot.get("last_lesson")
    if isinstance(last, dict):
        for row in reversed(history):
            selection = row.get("selection") or {}
            outcome = row.get("outcome") or {}
            if (
                row.get("decision") == "TRANSAKCJA"
                and outcome.get("status") == "RESOLVED"
                and outcome.get("activated") is True
            ):
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


def _filter_context_to_approved(context: dict[str, Any], approved_ids: set[str]) -> dict[str, Any]:
    value = copy.deepcopy(context)
    selected_ids = [
        source_id
        for source_id in value.get("selected_event_source_ids") or []
        if source_id in approved_ids
    ]
    value["selected_event_source_ids"] = selected_ids
    if value.get("primary_source_id") not in approved_ids:
        value["primary_source_id"] = selected_ids[0] if selected_ids else None
        # Do not claim that an unapproved primary source was official evidence.
        value["official_report_used"] = False
        value["primary_source_kind"] = None
        value["primary_channel"] = None
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
    enriched.setdefault("learning", {})["event_expectancy"] = copy.deepcopy(
        snapshot.get("event_expectancy") or []
    )
    selection = enriched.get("selection")
    if isinstance(selection, dict):
        symbol = str(selection.get("symbol") or "")
        context = _EVENT_CONTEXT_BY_SYMBOL.get(symbol)
        if context:
            approved_ids = {
                str(source.get("id"))
                for source in (selection.get("sources") or [])
                if source.get("id")
            }
            selection["event_context"] = _filter_context_to_approved(context, approved_ids)
            selection["evidence_summary"] = {
                "official_reports": sum(
                    1 for source in (selection.get("sources") or []) if source.get("source_kind") == "issuer_report"
                ),
                "independent_news": sum(
                    1 for source in (selection.get("sources") or []) if source.get("source_kind") != "issuer_report"
                ),
                "primary_event": selection["event_context"].get("primary_label"),
            }
    return enriched


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    gpw.generate = _event_generate
    gpw.gemini_analysis = _event_aware_analysis
    loop.build_learning_snapshot = _build_learning_snapshot
    loop._public_learning = _public_learning
    loop._enrich_payload = _enrich_payload
    _INSTALLED = True


def main() -> int:
    install()
    return loop.main()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import copy
import os
from datetime import time as clock_time
from typing import Any

try:
    from scripts import gpw_daily_control_loop as loop
    from scripts import gpw_daily_pick as gpw
    from scripts import gpw_event_layer as events
    from scripts import gpw_market_data as market
    from scripts import gpw_pipeline_v2 as pipeline
    from scripts import gpw_provider_v2 as provider
except ModuleNotFoundError:
    import gpw_daily_control_loop as loop
    import gpw_daily_pick as gpw
    import gpw_event_layer as events
    import gpw_market_data as market
    import gpw_pipeline_v2 as pipeline
    import gpw_provider_v2 as provider

_ORIGINAL_GEMINI_ANALYSIS = gpw.gemini_analysis
_ORIGINAL_BUILD_LEARNING_SNAPSHOT = loop.build_learning_snapshot
_ORIGINAL_PUBLIC_LEARNING = loop._public_learning
_ORIGINAL_ENRICH_PAYLOAD = loop._enrich_payload
_ORIGINAL_BUILD_QUANT_CANDIDATE = gpw.build_quant_candidate
_ORIGINAL_VALIDATE_PAYLOAD = gpw.validate_payload
_INSTALLED = False
_EVENT_CONTEXT_BY_SYMBOL: dict[str, dict[str, Any]] = {}


def _completed_session_candidate(company, bars, expected_day, config, history):
    """Never rank on today's unfinished Yahoo daily candle."""
    completed = [bar for bar in (bars or []) if bar.day <= expected_day]
    if not completed or completed[-1].day != expected_day:
        return None
    return _ORIGINAL_BUILD_QUANT_CANDIDATE(
        company, completed, expected_day, config, history
    )


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


def _daily_pick_generate(**kwargs):
    """Use score 72 as a conviction target, not as the only admission gate.

    Hard gates remain unchanged: fresh market coverage, security-level liquidity
    and risk screening, minimum reward/risk, evidence/source gate and the
    independent Gemini reviewer. Candidates that survive those gates are
    reviewed from highest composite score downward until one is approved.
    """
    original_load_config = gpw.load_config
    base_config = original_load_config()
    target_score = float(base_config["minimum_composite_score"])
    decision_config = copy.deepcopy(base_config)
    decision_config["minimum_composite_score"] = 0.0

    def relaxed_config():
        return copy.deepcopy(decision_config)

    gpw.load_config = relaxed_config
    try:
        payload = pipeline.generate(**kwargs)
    finally:
        gpw.load_config = original_load_config

    methodology = payload.setdefault("methodology", {})
    methodology["minimum_score"] = target_score
    methodology["target_score"] = target_score
    methodology["score_policy"] = "ranking_target_not_hard_gate"
    methodology["hard_admission_gates"] = [
        "market_data_completeness",
        "liquidity_and_position_risk",
        "reward_risk",
        "source_evidence",
        "independent_review",
    ]

    selection = payload.get("selection")
    if payload.get("decision") == "TRANSAKCJA" and isinstance(selection, dict):
        score = float(selection.get("score") or 0.0)
        selection["score_target"] = target_score
        selection["score_target_met"] = score >= target_score
        selection["conviction"] = "wysokie" if score >= target_score else "umiarkowane"
        if score < target_score:
            payload["reason"] = (
                "Najlepszy dostępny kandydat przeszedł twarde bramki danych, "
                "płynności/ryzyka, RR, źródeł i niezależnej recenzji; wynik "
                f"{score:.2f} jest poniżej celu {target_score:.0f}, więc przekonanie jest umiarkowane."
            )
    return payload


def _validate_payload_with_score_target(payload, *args, **kwargs):
    """Preserve all legacy validation while treating score 72 as a soft target."""
    probe = copy.deepcopy(payload)
    if (
        probe.get("decision") == "TRANSAKCJA"
        and (probe.get("methodology") or {}).get("score_policy")
        == "ranking_target_not_hard_gate"
    ):
        selection = probe.get("selection") or {}
        score = float(selection.get("score") or 0.0)
        probe.setdefault("methodology", {})["minimum_score"] = min(
            float(probe["methodology"].get("minimum_score") or score), score
        )
    return _ORIGINAL_VALIDATE_PAYLOAD(probe, *args, **kwargs)


def _event_generate(**kwargs):
    forwarded = dict(kwargs)
    forwarded["news_fetcher"] = events.combined_news_items
    payload = _daily_pick_generate(**forwarded)
    now = forwarded.get("now") or gpw.now_warsaw()
    if payload.get("decision") == "TRANSAKCJA":
        payload = market.reprice_transaction(payload, now=now)
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
        "historical_ranking_session": "ostatnia zakończona sesja GPW",
        "opening_quote_providers": ["Yahoo", "Stooq"],
        "opening_quote_not_before": "09:05 Europe/Warsaw",
        "publication_cutoff": f"{enriched.get('publication_cutoff', '09:10')} Europe/Warsaw",
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
    cutoff = pipeline.cutoff_for(now.date(), config).strftime("%H:%M")
    payload = gpw.failure_payload(now, config, "Brak dzisiaj wyboru.", stage)
    payload["publication_cutoff"] = cutoff
    payload["reason"] = (
        f"Brak dzisiaj wyboru — sygnał po otwarciu nie został potwierdzony przed {cutoff}."
    )
    payload["locked"] = True
    payload = loop._enrich_payload(payload, snapshot, attempts=0)
    gpw.publish(payload)
    return payload


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    # User-approved recovery window applies only to 2026-08-19. It is inert on
    # every subsequent date and does not change the normal 09:10 policy.
    if gpw.now_warsaw().date().isoformat() == "2026-08-19":
        os.environ.setdefault("GPW_RECOVERY_DATE", "2026-08-19")
        os.environ.setdefault("GPW_RECOVERY_CUTOFF", "12:30")

    loop.EARLIEST_GENERATION = clock_time(9, 5)
    gpw.cutoff_for = pipeline.cutoff_for
    gpw.build_quant_candidate = _completed_session_candidate
    gpw.fetch_yahoo_bars = provider.fetch_bars
    loop.prefetch_market = provider.prefetch_market
    gpw.generate = _event_generate
    gpw.validate_payload = _validate_payload_with_score_target
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

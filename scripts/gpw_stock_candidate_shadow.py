#!/usr/bin/env python3
"""Continuous-session GPW selector for the legacy v1 shadow challenger.

The wrapper reuses the audited v1 ranking/data-gate/Opening Confirmation/EV
machinery, but removes the old "pick rank #1 then let the portfolio reject it"
failure mode.  Non-positive conservative EV is treated as a candidate rejection
inside the search, so the selector continues to the next ranked company.
"""
from __future__ import annotations

from datetime import datetime, time as clock_time
from typing import Any, Iterable

try:
    from scripts import gpw_daily_pick as gpw
    from scripts import gpw_mandatory_daily as mandatory
    from scripts import gpw_provider_v2 as provider
except ModuleNotFoundError:  # pragma: no cover
    import gpw_daily_pick as gpw
    import gpw_mandatory_daily as mandatory
    import gpw_provider_v2 as provider


def generate(now: datetime | None = None, *, exclude_symbols: Iterable[str] = ()) -> dict[str, Any]:
    now = now or gpw.now_warsaw()
    config = gpw.load_config()
    excluded = {str(symbol).upper() for symbol in exclude_symbols}
    if not gpw.is_session_day(now.date(), config):
        return gpw.common_payload(now, config, "BRAK_TRANSAKCJI", "Rynek GPW jest dziś zamknięty.")
    if now.time().replace(tzinfo=None) < clock_time(9, 15):
        return gpw.common_payload(now, config, "BRAK_TRANSAKCJI", "Oczekiwanie na okno analizy regularnej sesji GPW.")
    # Natural session boundary, not an arbitrary morning publication cutoff.
    if now.time().replace(tzinfo=None) >= clock_time(17, 0):
        return gpw.common_payload(now, config, "BRAK_TRANSAKCJI", "Regularna sesja GPW zakończona; brak nowych wejść shadow po zamknięciu.")

    policy = dict(mandatory.load_policy())
    policy["not_before"] = "09:15"
    policy["cutoff"] = "17:00"
    policy["recovery_cutoff"] = "17:00"
    # Zero in the public policy historically meant a bounded list in older
    # code.  Use a deliberately unreachable cap here so the challenger may
    # search the whole ranked universe when earlier names fail.
    policy["opening_confirmation_top_candidates"] = 1000000

    current = mandatory._fresh_base_payload(None, now=now, config=config)
    current["decision"] = "BRAK_TRANSAKCJI"
    current["locked"] = False
    cache = provider.prefetch_market(config)

    original_estimate = mandatory.ev.estimate
    original_build_ranked = mandatory.build_ranked_candidates

    def positive_conservative_ev(*args, **kwargs):
        model = original_estimate(*args, **kwargs)
        if model.get("status") == "ready":
            value = model.get("conservative_ev_r")
            if value is not None and float(value) <= 0.0:
                raise ValueError("non_positive_conservative_expected_value")
        return model

    def filtered_ranked(*args, **kwargs):
        rows = original_build_ranked(*args, **kwargs)
        return [row for row in rows if str(row.get("symbol") or "").upper() not in excluded]

    mandatory.ev.estimate = positive_conservative_ev
    mandatory.build_ranked_candidates = filtered_ranked
    try:
        payload = mandatory.make_forced_payload(
            current,
            now=now,
            config=config,
            policy=policy,
            cache=cache,
        )
    except Exception as exc:
        payload = gpw.common_payload(
            now,
            config,
            "BRAK_TRANSAKCJI",
            f"Pełne przeszukanie v1 shadow nie znalazło kandydata przechodzącego wszystkie twarde bramki ({type(exc).__name__}).",
        )
        payload["data_quality"] = {
            "status": "search_exhausted",
            "error": str(exc)[:500],
            "excluded_held_symbols": sorted(excluded),
        }
        return payload
    finally:
        mandatory.ev.estimate = original_estimate
        mandatory.build_ranked_candidates = original_build_ranked

    if not isinstance(payload, dict) or payload.get("decision") != "TRANSAKCJA":
        result = gpw.common_payload(now, config, "BRAK_TRANSAKCJI", "Pełne przeszukanie v1 shadow nie znalazło kwalifikującej się spółki.")
        result["data_quality"] = {"status": "search_exhausted", "excluded_held_symbols": sorted(excluded)}
        return result

    selection = dict(payload.get("selection") or {})
    selection["selection_mode"] = "V1_SHADOW_CONTINUOUS_FULL_SEARCH"
    selection["holding_policy"] = "OPEN_ENDED_MODEL_CONTROLLED"
    selection["valid_until"] = None
    selection["time_stop"] = None
    selection["early_exit"] = "Shadow portfolio applies the same model-controlled SL/TP and thesis review contract."
    payload["selection"] = selection
    payload["locked"] = False
    payload["reason"] = "V1 shadow: najlepszy nieutrzymywany kandydat po pełnym przeszukaniu i wszystkich twardych bramkach, w tym dodatnim conservative EV."
    payload.setdefault("data_quality", {})["excluded_held_symbols"] = sorted(excluded)
    payload.setdefault("methodology", {})["candidate_search_policy"] = "continue_after_rejection_until_ranked_universe_exhausted"
    return payload

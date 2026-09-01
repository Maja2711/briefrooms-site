#!/usr/bin/env python3
"""Final deterministic selector for the Polish GPW Daily Trading adapter.

The primary GPW engine may abstain while it performs its richer catalyst/reviewer
process. This final stage enforces the product contract: after the configured
opening window publish one best valid GPW stock on each live session unless a
hard data-quality or execution-safety condition prevents a defensible choice.

P0.1 adds bounded current-session Opening Confirmation. P0.2 adds an empirical,
walk-forward Expected Value model which selects the target R/R from historical
analogues instead of mechanically imposing 1.8 R.
"""
from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, time as clock_time
from pathlib import Path
from typing import Any, Callable

try:
    from scripts import daily_stock_core as core
    from scripts import gpw_daily_pick as gpw
    from scripts import gpw_expected_value as ev
    from scripts import gpw_market_data as market
    from scripts import gpw_opening_confirmation as opening
    from scripts import gpw_provider_v2 as provider
except ModuleNotFoundError:
    import daily_stock_core as core
    import gpw_daily_pick as gpw
    import gpw_expected_value as ev
    import gpw_market_data as market
    import gpw_opening_confirmation as opening
    import gpw_provider_v2 as provider

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data/investments/gpw_mandatory_daily_config.json"
OPENING_CONFIRMATION_ENGINE = opening.ENGINE
OPENING_COMPONENT_WEIGHTS = opening.COMPONENT_WEIGHTS
# Backward-compatible public alias used by tests and audit tooling.
opening_confirmation_score = opening.score


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not value.get("enabled", False):
        return value
    required = (
        "not_before",
        "cutoff",
        "recovery_cutoff",
        "minimum_market_coverage",
        "minimum_median_turnover_pln",
        "maximum_candidate_risk_percent",
        "maximum_published_risk_percent",
        "maximum_historical_lag_sessions",
    )
    missing = [key for key in required if key not in value]
    if missing:
        raise gpw.PublicationError(
            "Mandatory GPW policy missing: " + ", ".join(missing)
        )
    return value


def _clock(value: str) -> clock_time:
    hour, minute = (int(part) for part in value.split(":"))
    return clock_time(hour, minute)


def _selector_config(config: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Relax conviction only; keep execution safety at least as strict."""
    value = copy.deepcopy(config)
    value["minimum_median_turnover_pln"] = max(
        float(config.get("minimum_median_turnover_pln", core.GPW_PROFILE.turnover_floor)),
        float(policy.get("minimum_median_turnover_pln", core.GPW_PROFILE.turnover_floor)),
    )
    value["maximum_risk_percent"] = min(
        float(config.get("maximum_risk_percent", policy["maximum_published_risk_percent"])),
        float(policy["maximum_candidate_risk_percent"]),
        float(policy["maximum_published_risk_percent"]),
    )
    return value


def _accepted_feature_day(
    latest_day,
    expected_day,
    config: dict[str, Any],
    maximum_lag_sessions: int,
) -> tuple[Any, int] | None:
    """Accept 0..N completed-session lag for historical features only."""
    cursor = expected_day
    for lag in range(max(0, int(maximum_lag_sessions)) + 1):
        if latest_day == cursor:
            return cursor, lag
        cursor = gpw.previous_session(cursor, config)
    return None


def _learning_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("selection"), dict)
    ]


def build_ranked_candidates(
    config: dict[str, Any],
    policy: dict[str, Any],
    history: list[dict[str, Any]],
    cache: dict[str, list[gpw.Bar]],
    expected_day,
) -> list[dict[str, Any]]:
    selector_config = _selector_config(config, policy)
    max_lag = int(policy["maximum_historical_lag_sessions"])
    learning_history = _learning_history(history)
    rows: list[dict[str, Any]] = []

    for company in config["universe"]:
        symbol = str(company["symbol"])
        bars = list(cache.get(symbol) or [])
        completed = [bar for bar in bars if bar.day <= expected_day]
        if not completed:
            continue
        accepted = _accepted_feature_day(
            completed[-1].day,
            expected_day,
            config,
            max_lag,
        )
        if accepted is None:
            continue
        feature_day, lag_sessions = accepted
        candidate = core.build_quant_candidate(
            company,
            completed,
            feature_day,
            selector_config,
            core.GPW_PROFILE,
            history=learning_history,
            history_scorer=gpw.history_expectancy_score,
        )
        if candidate is None:
            continue
        candidate["quant_engine"] = "daily-stock-core-v1-final-selector"
        candidate["mandatory_selection"] = True
        candidate["historical_feature_session"] = feature_day.isoformat()
        candidate["historical_feature_lag_sessions"] = lag_sessions
        rows.append(candidate)

    core.normalize_cross_section(rows)
    ranked = sorted(
        rows,
        key=lambda row: float(row.get("quant_pre_score") or 0.0),
        reverse=True,
    )
    for rank, candidate in enumerate(ranked, start=1):
        candidate["quant_rank"] = rank
    return ranked


def _source_for(symbol: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    provider_name = str(snapshot.get("provider") or "market data")
    return {
        "id": f"market-open:{symbol}:{snapshot.get('date')}",
        "title": f"{provider_name} — bieżące kwotowanie {symbol}",
        "publisher": provider_name,
        "url": f"https://finance.yahoo.com/quote/{symbol}",
        "source_kind": "market_quote",
        "channel": provider_name,
        "published_at": snapshot.get("observed_at"),
        "age_hours": 0,
        "materiality": 0,
    }


def _fresh_execution_snapshot(snapshot: dict[str, Any], now: datetime) -> None:
    if float(snapshot.get("last") or 0.0) <= 0.0:
        raise ValueError("non-positive current-session quote")
    if str(snapshot.get("date") or "") != now.date().isoformat():
        raise ValueError(f"stale execution session: {snapshot.get('date')}")
    crosscheck = snapshot.get("crosscheck") or {}
    if str(crosscheck.get("status") or "").lower() in {"conflict", "rejected"}:
        raise ValueError("execution quote cross-check conflict")


def _fresh_base_payload(
    current: dict[str, Any] | None,
    *,
    now: datetime,
    config: dict[str, Any],
) -> dict[str, Any]:
    if isinstance(current, dict) and current.get("date") == now.date().isoformat():
        return current
    previous = current if isinstance(current, dict) else {}
    payload = gpw.common_payload(
        now,
        config,
        "BRAK_TRANSAKCJI",
        "Główny pipeline nie opublikował dzisiejszego wyboru; uruchomiono końcowy selektor odzyskujący.",
    )
    payload["data_quality"] = {
        "status": "recovery",
        "recovered_from_stale_publication": True,
        "previous_publication_date": previous.get("date"),
        "previous_decision": previous.get("decision"),
    }
    return payload


def _opening_weight(policy: dict[str, Any]) -> float:
    raw = float(policy.get("opening_confirmation_weight", 0.25))
    if raw > 1.0:
        raw /= 100.0
    return opening.bounded_weight(raw)


def _completed_for_ev(
    cache: dict[str, list[gpw.Bar]], symbol: str, expected_day
) -> list[gpw.Bar]:
    return [bar for bar in list(cache.get(symbol) or []) if bar.day <= expected_day]


def make_forced_payload(
    current: dict[str, Any],
    *,
    now: datetime,
    config: dict[str, Any],
    policy: dict[str, Any],
    cache: dict[str, list[gpw.Bar]],
    opening_fetcher: Callable[..., dict[str, Any]] = market.opening_snapshot,
) -> dict[str, Any] | None:
    today = now.date().isoformat()
    if current.get("decision") == "TRANSAKCJA" and current.get("date") == today:
        return None
    if current.get("decision") not in {"BRAK_TRANSAKCJI", "AWARIA_DANYCH"}:
        return None
    if current.get("date") != today:
        return None
    if not gpw.is_session_day(now.date(), config):
        return None

    local_time = now.time().replace(tzinfo=None)
    if local_time < _clock(str(policy["not_before"])):
        return None
    if local_time > _clock(str(policy["recovery_cutoff"])):
        raise gpw.PublicationError(
            f"Mandatory GPW recovery window closed at {policy['recovery_cutoff']} Europe/Warsaw"
        )

    coverage = len(cache) / max(len(config["universe"]), 1)
    if coverage < float(policy["minimum_market_coverage"]):
        raise gpw.PublicationError(
            f"Mandatory GPW market coverage {coverage:.0%} below minimum "
            f"{float(policy['minimum_market_coverage']):.0%}"
        )

    expected = gpw.previous_session(now.date(), config)
    history = gpw.all_history()
    ranked = build_ranked_candidates(config, policy, history, cache, expected)
    if not ranked:
        raise gpw.PublicationError(
            "Mandatory GPW selector found no liquid candidate with finite completed-session risk geometry"
        )

    neutral_catalyst = float(policy.get("neutral_catalyst_score", 50.0))
    opening_weight = _opening_weight(policy)
    opening_top_candidates = max(
        1, int(policy.get("opening_confirmation_top_candidates", 8))
    )
    ev_settings = ev.settings_from(config)

    evaluated: list[tuple[dict[str, Any], dict[str, Any]]] = []
    candidate_errors: dict[str, str] = {}

    # P0.1 + P0.2: current-session confirmation first, then empirical EV.
    for candidate in ranked:
        if len(evaluated) >= opening_top_candidates:
            break
        symbol = str(candidate["symbol"])
        try:
            candidate_snapshot = opening_fetcher(symbol, now=now)
            _fresh_execution_snapshot(candidate_snapshot, now)
            confirmation = opening.score(candidate, candidate_snapshot)
            legacy_score = core.composite_score(
                candidate,
                {"catalyst_score": neutral_catalyst},
                config,
            )
            opening_adjusted = opening.blend(
                legacy_score,
                float(confirmation["score"]),
                opening_weight,
            )
            enriched = copy.deepcopy(candidate)
            enriched["opening_confirmation"] = confirmation
            enriched["legacy_composite_score"] = legacy_score
            enriched["opening_adjusted_score"] = opening_adjusted
            enriched["opening_confirmation_engine"] = opening.ENGINE

            if ev_settings["enabled"]:
                model = ev.estimate(
                    enriched,
                    _completed_for_ev(cache, symbol, expected),
                    config,
                )
                if model.get("status") != "ready":
                    raise ValueError(
                        f"expected value unavailable: {model.get('status')} "
                        f"n={model.get('analogue_count', 0)}"
                    )
                enriched = ev.apply_dynamic_target(enriched, model)
                final_score, ev_weight = ev.blend_score(opening_adjusted, model, config)
                enriched["expected_value_weight"] = ev_weight
                enriched["ev_adjusted_score"] = final_score
            else:
                enriched["ev_adjusted_score"] = opening_adjusted
                enriched["expected_value_weight"] = 0.0

            evaluated.append((enriched, candidate_snapshot))
        except Exception as exc:
            candidate_errors[symbol] = f"{type(exc).__name__}: {str(exc)[:220]}"

    if not evaluated:
        raise gpw.PublicationError(
            "Mandatory GPW selector could not confirm a current-session quote and empirical decision geometry for any valid ranked candidate"
        )

    evaluated.sort(
        key=lambda pair: (
            float(pair[0].get("ev_adjusted_score") or 0.0),
            float(pair[0].get("opening_adjusted_score") or 0.0),
            float(pair[0].get("quant_pre_score") or 0.0),
        ),
        reverse=True,
    )
    selected, snapshot = evaluated[0]

    reference = float(snapshot["last"])
    raw_risk = float(selected.get("risk_percent") or 0.0)
    max_published_risk = float(policy["maximum_published_risk_percent"])
    if raw_risk <= 0.0 or raw_risk > max_published_risk:
        raise gpw.PublicationError(
            f"Selected GPW candidate risk {raw_risk:.2%} outside published hard limit "
            f"{max_published_risk:.2%}"
        )

    risk_fraction = max(core.GPW_PROFILE.risk_floor_percent, raw_risk)
    if ev_settings["enabled"]:
        reward_risk = float(selected["expected_value_model"]["selected_reward_risk"])
    else:
        reward_risk = float(
            selected.get("reward_risk")
            or policy.get("reward_risk")
            or config.get("minimum_reward_risk", 1.5)
        )
    stop = reference * (1.0 - risk_fraction)
    target = reference + (reference - stop) * reward_risk
    entry_low = reference * 0.997
    entry_high = reference * 1.006
    score = float(selected["ev_adjusted_score"])
    legacy_score = float(selected["legacy_composite_score"])
    confirmation = selected["opening_confirmation"]
    model = selected.get("expected_value_model") or {}
    source = _source_for(str(selected["symbol"]), snapshot)
    late_recovery = local_time > _clock(str(policy["cutoff"]))

    payload = copy.deepcopy(current)
    payload["generated_at"] = now.isoformat(timespec="seconds")
    payload["decision"] = "TRANSAKCJA"
    payload["reason"] = (
        "Końcowy wybór dnia: ranking ilościowy połączono z zachowaniem po otwarciu "
        "oraz empirycznym Expected Value dla horyzontu 1–2 sesji."
        if ev_settings["enabled"]
        else "Końcowy wybór dnia: wybrano najwyżej sklasyfikowaną spółkę po połączeniu rankingu ilościowego z potwierdzeniem zachowania po dzisiejszym otwarciu."
    )
    payload["locked"] = True
    scores = {
        **selected["scores"],
        "catalyst": neutral_catalyst,
        "opening_confirmation": confirmation["score"],
    }
    if model.get("status") == "ready":
        scores["expected_value"] = model["score"]

    payload["selection"] = {
        "symbol": selected["symbol"],
        "ticker": str(selected["symbol"]).removesuffix(".WA"),
        "name": selected["name"],
        "sector": selected["sector"],
        "score": core.round2(score),
        "legacy_composite_score": core.round2(legacy_score),
        "opening_adjusted_score": core.round2(selected["opening_adjusted_score"]),
        "quant_pre_score": selected.get("quant_pre_score"),
        "quant_rank": selected.get("quant_rank"),
        "opening_confirmation_score": confirmation["score"],
        "opening_confirmation": confirmation,
        "expected_value_score": model.get("score"),
        "expected_value_model": model if model else None,
        "expected_value_weight": selected.get("expected_value_weight", 0.0),
        "target_method": ev.ENGINE if model.get("status") == "ready" else "legacy_fallback",
        "selection_mode": "MANDATORY_DAILY_FINAL",
        "reference_price": core.round2(reference),
        "entry_zone": [core.round2(entry_low), core.round2(entry_high)],
        "skip_above": core.round2(entry_high),
        "activation": "Wybór dnia po otwarciu; nie gonić ceny powyżej górnej granicy strefy wejścia.",
        "stop": core.round2(stop),
        "target": core.round2(target),
        "risk_percent": round(risk_fraction, 4),
        "reward_risk": reward_risk,
        "valid_until": gpw.add_sessions(now.date(), 2, config).isoformat(),
        "time_stop": "Maksymalnie 2 sesje od dnia wyboru.",
        "early_exit": "Wyjście przy SL, unieważnieniu setupu lub pogorszeniu danych wykonawczych; nie rozszerzać SL.",
        "thesis": (
            f"{selected['name']} ma najwyższy ranking po połączeniu sygnału ilościowego, "
            "bieżącego zachowania po otwarciu i empirycznej geometrii TP/SL."
            if ev_settings["enabled"]
            else f"{selected['name']} ma najwyższy ranking po połączeniu sygnału ilościowego z bieżącym potwierdzeniem po otwarciu wśród ocenionych kandydatów GPW."
        ),
        "why_now": (
            "Wybór łączy momentum, kontekst rynku, płynność i Opening Confirmation. "
            "Target nie jest stałym 1,8R: wybiera go walk-forward EV na podstawie podobnych historycznych setupów, z kosztem transakcyjnym i karą za niepewność."
            if ev_settings["enabled"]
            else "Wybór łączy relatywne momentum, kontekst rynku, płynność, geometrię ryzyka i historię z zachowaniem bieżącej sesji."
        ),
        "risk_factors": [
            "Opening Confirmation jest krótkoterminowym overlayem i nie może dominować bazowego rankingu.",
            "Empiryczne prawdopodobieństwa TP/SL nie są skalibrowaną prognozą; wynik zależy od jakości i liczby historycznych analogów.",
            "Dla świecy z jednoczesnym TP i SL model konserwatywnie przyjmuje SL jako pierwszy.",
            "Minimalna płynność i maksymalne ryzyko pozostają twardymi bramkami; SL nie może być rozszerzany po wejściu.",
        ],
        "scores": scores,
        "sources": [source],
        "review": {
            "approved": True,
            "mode": (
                "mandatory_daily_final_quant_opening_empirical_ev"
                if ev_settings["enabled"]
                else "mandatory_daily_final_quant_plus_opening_confirmation"
            ),
            "reason": (
                "Najwyższy wynik po ograniczonym połączeniu rankingu bazowego, bieżącej sesji i empirycznego EV; zachowane twarde bramki danych, płynności i ryzyka."
                if ev_settings["enabled"]
                else "Najwyższy wynik po deterministycznym połączeniu rankingu bazowego z potwierdzeniem bieżącej sesji; zachowane twarde bramki danych, płynności i ryzyka."
            ),
            "supported_source_ids": [source["id"]],
        },
        "market_snapshot": snapshot,
        "forced_from_reason": current.get("reason"),
        "quant_engine": selected.get("quant_engine"),
        "opening_confirmation_engine": opening.ENGINE,
        "expected_value_engine": ev.ENGINE if model.get("status") == "ready" else None,
        "historical_feature_session": selected.get("historical_feature_session"),
        "historical_feature_lag_sessions": selected.get("historical_feature_lag_sessions"),
    }
    payload["outcome"] = {"status": "PENDING", "activated": None}

    methodology = payload.setdefault("methodology", {})
    methodology["minimum_score"] = 0.0
    methodology["score_policy"] = (
        "mandatory_daily_quant_opening_empirical_ev"
        if ev_settings["enabled"]
        else "mandatory_daily_quant_plus_opening_confirmation"
    )
    methodology["expected_value"] = {
        **ev_settings,
        "same_bar_policy": "stop_first_conservative",
        "entry_proxy": "next_session_open",
        "target_selection": "maximize_uncertainty_adjusted_net_expected_R",
    }
    methodology["mandatory_daily_selection"] = {
        "enabled": True,
        "integrated_final_stage": True,
        "trigger": "primary_not_transaction_after_not_before",
        "liquidity_role": "hard_gate_and_ranking_feature",
        "minimum_median_turnover_pln": float(
            _selector_config(config, policy)["minimum_median_turnover_pln"]
        ),
        "maximum_published_risk_percent": max_published_risk,
        "maximum_historical_lag_sessions": int(policy["maximum_historical_lag_sessions"]),
        "opening_quote_required": True,
        "opening_confirmation": {
            "engine": opening.ENGINE,
            "weight": opening_weight,
            "top_candidates": opening_top_candidates,
            "component_weights": dict(opening.COMPONENT_WEIGHTS),
            "role": "bounded_reranking_overlay_not_hard_gate",
        },
        "expected_value": {
            "engine": ev.ENGINE,
            "enabled": ev_settings["enabled"],
            "base_score_weight": ev_settings["score_weight"],
            "effective_selected_weight": selected.get("expected_value_weight", 0.0),
            "rr_grid": ev_settings["rr_grid"],
            "minimum_analogs": ev_settings["minimum_analogs"],
            "maximum_analogs": ev_settings["maximum_analogs"],
            "cost_assumption_percent": ev_settings["cost_assumption_percent"],
            "role": ev_settings["role"],
        },
        "recovery_cutoff": str(policy["recovery_cutoff"]),
        "late_recovery": late_recovery,
    }
    methodology["hard_admission_gates"] = [
        "market_data_completeness",
        "historical_feature_freshness_max_1_session",
        "minimum_liquidity",
        "finite_atr_and_price",
        "fresh_current_session_quote",
        "bounded_published_risk",
    ]
    if ev_settings["enabled"]:
        methodology["hard_admission_gates"].append("expected_value_history_quality")

    quality = payload.setdefault("data_quality", {})
    quality["status"] = "healthy"
    quality["expected_session"] = expected.isoformat()
    quality["mandatory_selection"] = {
        "applied": True,
        "market_coverage": round(coverage, 4),
        "ranked_candidates": len(ranked),
        "opening_candidates_evaluated": len(evaluated),
        "selected_rank": 1,
        "selected_quant_rank": selected.get("quant_rank"),
        "opening_confirmation_score": confirmation["score"],
        "opening_adjusted_score": core.round2(selected["opening_adjusted_score"]),
        "expected_value_score": model.get("score"),
        "expected_value_r": model.get("expected_net_r"),
        "conservative_ev_r": model.get("conservative_ev_r"),
        "selected_reward_risk": reward_risk,
        "tp_before_sl_probability": model.get("tp_before_sl_probability"),
        "sl_before_tp_probability": model.get("sl_before_tp_probability"),
        "time_exit_probability": model.get("time_exit_probability"),
        "final_score": core.round2(score),
        "historical_feature_session": selected.get("historical_feature_session"),
        "historical_feature_lag_sessions": selected.get("historical_feature_lag_sessions"),
        "candidate_failures_before_selection": candidate_errors,
        "late_recovery": late_recovery,
        "ignored_non_trade_history_records": len(history) - len(_learning_history(history)),
    }
    return payload


def persist(payload: dict[str, Any]) -> None:
    gpw.validate_payload(payload, require_today=False)
    history_path = gpw.HISTORY_DIR / f"{payload['date']}.json"
    gpw.atomic_json(history_path, payload)
    metrics = gpw.metric_summary(gpw.all_history())
    payload["metrics"] = metrics
    gpw.atomic_json(history_path, payload)
    gpw.atomic_json(gpw.PUBLIC_PATH, payload)
    gpw.atomic_json(gpw.METRICS_PATH, metrics)

    selection = payload.get("selection") or {}
    model = selection.get("expected_value_model") or {}
    audit = {
        "date": payload["date"],
        "generated_at": payload["generated_at"],
        "decision": payload["decision"],
        "selection_mode": "MANDATORY_DAILY_FINAL",
        "symbol": selection.get("symbol"),
        "previous_reason": selection.get("forced_from_reason"),
        "historical_feature_lag_sessions": selection.get("historical_feature_lag_sessions"),
        "quant_rank": selection.get("quant_rank"),
        "opening_confirmation_score": selection.get("opening_confirmation_score"),
        "opening_adjusted_score": selection.get("opening_adjusted_score"),
        "expected_value_score": selection.get("expected_value_score"),
        "expected_net_r": model.get("expected_net_r"),
        "conservative_ev_r": model.get("conservative_ev_r"),
        "selected_reward_risk": selection.get("reward_risk"),
        "tp_before_sl_probability": model.get("tp_before_sl_probability"),
        "sl_before_tp_probability": model.get("sl_before_tp_probability"),
        "final_score": selection.get("score"),
    }
    gpw.atomic_json(gpw.AUDIT_DIR / f"{payload['date']}-mandatory.json", audit)


def run(*, now: datetime | None = None) -> dict[str, Any] | None:
    now = now or gpw.now_warsaw()
    policy = load_policy()
    if not policy.get("enabled", False):
        return None
    config = gpw.load_config()
    if not gpw.is_session_day(now.date(), config):
        return None
    if now.time().replace(tzinfo=None) < _clock(str(policy["not_before"])):
        return None

    current = gpw.load_json(gpw.PUBLIC_PATH)
    current = _fresh_base_payload(current, now=now, config=config)
    if current.get("decision") == "TRANSAKCJA":
        return None

    cache = provider.prefetch_market(config)
    payload = make_forced_payload(
        current,
        now=now,
        config=config,
        policy=policy,
        cache=cache,
    )
    if payload is None:
        return None
    persist(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()

    if args.validate:
        payload = gpw.load_json(gpw.PUBLIC_PATH)
        gpw.validate_payload(payload, require_today=True)
        now = gpw.now_warsaw()
        config = gpw.load_config()
        policy = load_policy()
        if (
            gpw.is_session_day(now.date(), config)
            and now.time().replace(tzinfo=None) >= _clock(str(policy["not_before"]))
            and payload.get("decision") != "TRANSAKCJA"
        ):
            raise gpw.PublicationError(
                f"GPW final selector contract violated: expected TRANSAKCJA, got {payload.get('decision')}"
            )
        print(
            "GPW_MANDATORY_VALIDATE_OK",
            payload.get("decision"),
            (payload.get("selection") or {}).get("selection_mode"),
        )
        return 0

    payload = run()
    if payload is None:
        current = gpw.load_json(gpw.PUBLIC_PATH, {})
        print("GPW_MANDATORY_NOOP", current.get("decision"), current.get("date"))
    else:
        selection = payload.get("selection") or {}
        model = selection.get("expected_value_model") or {}
        print(
            "GPW_MANDATORY_SELECTED",
            selection.get("ticker"),
            selection.get("score"),
            selection.get("reference_price"),
            "opening=",
            selection.get("opening_confirmation_score"),
            "ev=",
            model.get("expected_net_r"),
            "rr=",
            selection.get("reward_risk"),
            "quant_rank=",
            selection.get("quant_rank"),
            "lag=",
            selection.get("historical_feature_lag_sessions"),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

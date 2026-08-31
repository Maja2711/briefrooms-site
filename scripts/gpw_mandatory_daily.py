#!/usr/bin/env python3
"""Final deterministic selector for the Polish GPW Daily Trading adapter.

This module is the last stage of the primary GPW pipeline. On a live GPW
session it must publish one best *valid* candidate once the mandatory window
opens, unless a hard data-quality or execution-price condition makes a safe
selection impossible.

Soft conviction is never a veto here. Hard gates remain hard:
- sufficient universe coverage,
- at most the configured historical-feature lag,
- minimum liquidity,
- finite ATR/risk geometry,
- bounded published risk,
- a positive quote from the current GPW session.

There is no random selection and no fabricated price.
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
    from scripts import gpw_market_data as market
    from scripts import gpw_provider_v2 as provider
except ModuleNotFoundError:
    import daily_stock_core as core
    import gpw_daily_pick as gpw
    import gpw_market_data as market
    import gpw_provider_v2 as provider

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data/investments/gpw_mandatory_daily_config.json"


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not value.get("enabled", False):
        return value
    required = (
        "not_before",
        "cutoff",
        "recovery_cutoff",
        "minimum_market_coverage",
        "maximum_candidate_risk_percent",
        "maximum_published_risk_percent",
        "reward_risk",
        "maximum_historical_lag_sessions",
    )
    for key in required:
        if key not in value:
            raise gpw.PublicationError(f"Mandatory GPW policy missing {key}")
    return value


def _clock(value: str) -> clock_time:
    hour, minute = (int(part) for part in value.split(":"))
    return clock_time(hour, minute)


def _selector_config(config: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Relax conviction, never execution safety.

    Liquidity stays at the normal GPW floor. Candidate risk cannot exceed the
    normal configured maximum nor the published maximum. This prevents the old
    mandatory mode from selecting illiquid names or clipping an unsafe raw stop
    into an apparently safe published stop.
    """
    value = copy.deepcopy(config)
    value["minimum_median_turnover_pln"] = max(
        float(config.get("minimum_median_turnover_pln", core.GPW_PROFILE.turnover_floor)),
        float(policy.get("minimum_median_turnover_pln", 0.0)),
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
    """Return (feature_day, lag_sessions) when history is usable.

    Historical features may lag the expected completed session by a small,
    explicit number of sessions. Current-session execution price never inherits
    this tolerance and must still be fresh.
    """
    cursor = expected_day
    for lag in range(max(0, int(maximum_lag_sessions)) + 1):
        if latest_day == cursor:
            return cursor, lag
        cursor = gpw.previous_session(cursor, config)
    return None


def build_ranked_candidates(
    config: dict[str, Any],
    policy: dict[str, Any],
    history: list[dict[str, Any]],
    cache: dict[str, list[gpw.Bar]],
    expected_day,
) -> list[dict[str, Any]]:
    selector_config = _selector_config(config, policy)
    max_lag = int(policy["maximum_historical_lag_sessions"])
    rows: list[dict[str, Any]] = []

    for company in config["universe"]:
        bars = list(cache.get(str(company["symbol"])) or [])
        completed = [bar for bar in bars if bar.day <= expected_day]
        if not completed:
            continue
        accepted = _accepted_feature_day(completed[-1].day, expected_day, config, max_lag)
        if accepted is None:
            continue
        feature_day, lag_sessions = accepted

        candidate = core.build_quant_candidate(
            company,
            completed,
            feature_day,
            selector_config,
            core.GPW_PROFILE,
            history=history,
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
    return sorted(rows, key=lambda row: float(row.get("quant_pre_score") or 0.0), reverse=True)


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


def make_forced_payload(
    current: dict[str, Any],
    *,
    now: datetime,
    config: dict[str, Any],
    policy: dict[str, Any],
    cache: dict[str, list[gpw.Bar]],
    opening_fetcher: Callable[..., dict[str, Any]] = market.opening_snapshot,
) -> dict[str, Any] | None:
    if current.get("decision") == "TRANSAKCJA" and current.get("date") == now.date().isoformat():
        return None
    if current.get("decision") not in {"BRAK_TRANSAKCJI", "AWARIA_DANYCH"}:
        return None
    if current.get("date") != now.date().isoformat():
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
            f"Mandatory GPW market coverage {coverage:.0%} below minimum {float(policy['minimum_market_coverage']):.0%}"
        )

    expected = gpw.previous_session(now.date(), config)
    history = gpw.all_history()
    ranked = build_ranked_candidates(config, policy, history, cache, expected)
    if not ranked:
        raise gpw.PublicationError(
            "Mandatory GPW selector found no liquid candidate with finite completed-session risk geometry"
        )

    selected = None
    snapshot = None
    quote_errors: dict[str, str] = {}
    for candidate in ranked:
        symbol = str(candidate["symbol"])
        try:
            candidate_snapshot = opening_fetcher(symbol, now=now)
            _fresh_execution_snapshot(candidate_snapshot, now)
            selected = candidate
            snapshot = candidate_snapshot
            break
        except Exception as exc:
            quote_errors[symbol] = f"{type(exc).__name__}: {str(exc)[:180]}"

    if selected is None or snapshot is None:
        raise gpw.PublicationError(
            "Mandatory GPW selector could not confirm a current-session execution quote for any valid ranked candidate"
        )

    reference = float(snapshot["last"])
    raw_risk = float(selected.get("risk_percent") or 0.0)
    max_published_risk = float(policy["maximum_published_risk_percent"])
    if raw_risk <= 0.0 or raw_risk > max_published_risk:
        raise gpw.PublicationError(
            f"Selected GPW candidate risk {raw_risk:.2%} outside published hard limit {max_published_risk:.2%}"
        )

    risk_fraction = max(core.GPW_PROFILE.risk_floor_percent, raw_risk)
    reward_risk = float(policy["reward_risk"])
    stop = reference * (1.0 - risk_fraction)
    target = reference + (reference - stop) * reward_risk
    entry_low = reference * 0.997
    entry_high = reference * 1.006
    neutral_catalyst = float(policy.get("neutral_catalyst_score", 50.0))
    score = core.composite_score(selected, {"catalyst_score": neutral_catalyst}, config)
    source = _source_for(str(selected["symbol"]), snapshot)
    late_recovery = local_time > _clock(str(policy["cutoff"]))

    payload = copy.deepcopy(current)
    payload["generated_at"] = now.isoformat(timespec="seconds")
    payload["decision"] = "TRANSAKCJA"
    payload["reason"] = (
        "Końcowy wybór dnia: wybrano najwyżej sklasyfikowaną spółkę spełniającą twarde warunki danych, "
        "płynności, ryzyka i świeżej ceny wykonawczej."
    )
    payload["locked"] = True
    payload["selection"] = {
        "symbol": selected["symbol"],
        "ticker": str(selected["symbol"]).removesuffix(".WA"),
        "name": selected["name"],
        "sector": selected["sector"],
        "score": score,
        "quant_pre_score": selected.get("quant_pre_score"),
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
            f"{selected['name']} ma najwyższy ranking ilościowy wśród aktualnie poprawnych i płynnych kandydatów GPW."
        ),
        "why_now": (
            "Wybór opiera się na relatywnym momentum, kontekście rynku, płynności, geometrii ryzyka i historycznym overlayu. "
            "Brak silnego katalizatora nie jest twardym veto dla obowiązkowego wyboru Daily."
        ),
        "risk_factors": [
            "Tryb końcowego wyboru może mieć niższe przekonanie niż standardowa selekcja katalizatorowa.",
            "Minimalna płynność i maksymalne ryzyko pozostają twardymi bramkami.",
            "SL pozostaje nadrzędnym warunkiem wyjścia i nie może być rozszerzany po wejściu.",
        ],
        "scores": {**selected["scores"], "catalyst": neutral_catalyst},
        "sources": [source],
        "review": {
            "approved": True,
            "mode": "mandatory_daily_final_quant_policy",
            "reason": "Najwyższy ranking spośród kandydatów, którzy przeszli twarde bramki danych, płynności, ryzyka i bieżącej ceny.",
            "supported_source_ids": [source["id"]],
        },
        "market_snapshot": snapshot,
        "forced_from_reason": current.get("reason"),
        "quant_engine": selected.get("quant_engine"),
        "historical_feature_session": selected.get("historical_feature_session"),
        "historical_feature_lag_sessions": selected.get("historical_feature_lag_sessions"),
    }
    payload["outcome"] = {"status": "PENDING", "activated": None}

    methodology = payload.setdefault("methodology", {})
    methodology["minimum_score"] = 0.0
    methodology["score_policy"] = "mandatory_daily_best_valid_ranked"
    methodology["mandatory_daily_selection"] = {
        "enabled": True,
        "integrated_final_stage": True,
        "trigger": "primary_not_transaction_after_not_before",
        "liquidity_role": "hard_gate_and_ranking_feature",
        "minimum_median_turnover_pln": float(_selector_config(config, policy)["minimum_median_turnover_pln"]),
        "maximum_published_risk_percent": max_published_risk,
        "maximum_historical_lag_sessions": int(policy["maximum_historical_lag_sessions"]),
        "opening_quote_required": True,
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

    quality = payload.setdefault("data_quality", {})
    quality["status"] = "healthy"
    quality["expected_session"] = expected.isoformat()
    quality["mandatory_selection"] = {
        "applied": True,
        "market_coverage": round(coverage, 4),
        "ranked_candidates": len(ranked),
        "selected_rank": 1,
        "historical_feature_session": selected.get("historical_feature_session"),
        "historical_feature_lag_sessions": selected.get("historical_feature_lag_sessions"),
        "opening_quote_failures_before_selection": quote_errors,
        "late_recovery": late_recovery,
    }
    return payload


def persist(payload: dict[str, Any]) -> None:
    gpw.validate_payload(payload, require_today=False)
    history_path = gpw.HISTORY_DIR / f"{payload['date']}.json"

    # The final selector intentionally upgrades a same-session BRAK/AWARIA state
    # into TRANSAKCJA. It is the only final writer inside the primary workflow.
    gpw.atomic_json(history_path, payload)
    metrics = gpw.metric_summary(gpw.all_history())
    payload["metrics"] = metrics
    gpw.atomic_json(history_path, payload)
    gpw.atomic_json(gpw.PUBLIC_PATH, payload)
    gpw.atomic_json(gpw.METRICS_PATH, metrics)

    audit = {
        "date": payload["date"],
        "generated_at": payload["generated_at"],
        "decision": payload["decision"],
        "selection_mode": "MANDATORY_DAILY_FINAL",
        "symbol": (payload.get("selection") or {}).get("symbol"),
        "previous_reason": (payload.get("selection") or {}).get("forced_from_reason"),
        "historical_feature_lag_sessions": (payload.get("selection") or {}).get("historical_feature_lag_sessions"),
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
        print(
            "GPW_MANDATORY_SELECTED",
            selection.get("ticker"),
            selection.get("score"),
            selection.get("reference_price"),
            "lag=",
            selection.get("historical_feature_lag_sessions"),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

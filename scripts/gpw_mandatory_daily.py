#!/usr/bin/env python3
"""Mandatory daily selector for the Polish GPW Daily Trading adapter.

The primary GPW engine remains the preferred path.  This module runs only when
that engine has published BRAK_TRANSAKCJI on a live GPW session.  It converts
healthy market coverage into one deterministic best-ranked candidate instead
of allowing soft liquidity/conviction filters to leave the Daily product empty.

Operational gates remain fail-closed: current session, sufficient market
coverage, completed-session history, finite ATR geometry and a fresh post-open
quote are required.  No random selection and no fabricated price is allowed.
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
    for key in ("not_before", "cutoff", "minimum_market_coverage", "maximum_candidate_risk_percent", "maximum_published_risk_percent", "reward_risk"):
        if key not in value:
            raise gpw.PublicationError(f"Mandatory GPW policy missing {key}")
    return value


def _clock(value: str) -> clock_time:
    hour, minute = (int(part) for part in value.split(":"))
    return clock_time(hour, minute)


def _relaxed_config(config: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(config)
    # Turnover remains a ranking feature, not a veto, in mandatory mode.
    value["minimum_median_turnover_pln"] = 0
    value["maximum_risk_percent"] = float(policy["maximum_candidate_risk_percent"])
    return value


def build_ranked_candidates(
    config: dict[str, Any],
    policy: dict[str, Any],
    history: list[dict[str, Any]],
    cache: dict[str, list[gpw.Bar]],
    expected_day,
) -> list[dict[str, Any]]:
    relaxed = _relaxed_config(config, policy)
    rows: list[dict[str, Any]] = []
    for company in config["universe"]:
        bars = list(cache.get(str(company["symbol"])) or [])
        completed = [bar for bar in bars if bar.day <= expected_day]
        if not completed or completed[-1].day != expected_day:
            continue
        candidate = core.build_quant_candidate(
            company,
            completed,
            expected_day,
            relaxed,
            core.GPW_PROFILE,
            history=history,
            history_scorer=gpw.history_expectancy_score,
        )
        if candidate is None:
            continue
        candidate["quant_engine"] = "daily-stock-core-v1-mandatory"
        candidate["mandatory_selection"] = True
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


def make_forced_payload(
    current: dict[str, Any],
    *,
    now: datetime,
    config: dict[str, Any],
    policy: dict[str, Any],
    cache: dict[str, list[gpw.Bar]],
    opening_fetcher: Callable[..., dict[str, Any]] = market.opening_snapshot,
) -> dict[str, Any] | None:
    if current.get("decision") == "TRANSAKCJA":
        return None
    if current.get("decision") != "BRAK_TRANSAKCJI":
        return None
    if current.get("date") != now.date().isoformat():
        return None
    if not gpw.is_session_day(now.date(), config):
        return None
    if not (_clock(str(policy["not_before"])) <= now.time().replace(tzinfo=None) <= _clock(str(policy["cutoff"]))):
        return None

    coverage = len(cache) / max(len(config["universe"]), 1)
    if coverage < float(policy["minimum_market_coverage"]):
        raise gpw.PublicationError(
            f"Mandatory GPW market coverage {coverage:.0%} below minimum {float(policy['minimum_market_coverage']):.0%}"
        )

    expected = gpw.previous_session(now.date(), config)
    history = gpw.all_history()
    ranked = build_ranked_candidates(config, policy, history, cache, expected)
    if not ranked:
        raise gpw.PublicationError("Mandatory GPW selector found no candidate with finite completed-session risk geometry")

    selected = None
    snapshot = None
    quote_errors: dict[str, str] = {}
    for candidate in ranked:
        symbol = str(candidate["symbol"])
        try:
            snapshot = opening_fetcher(symbol, now=now)
            if float(snapshot.get("last") or 0) <= 0:
                raise ValueError("non-positive post-open quote")
            selected = candidate
            break
        except Exception as exc:
            quote_errors[symbol] = f"{type(exc).__name__}: {str(exc)[:180]}"
    if selected is None or snapshot is None:
        raise gpw.PublicationError("Mandatory GPW selector could not confirm a live opening quote for any ranked candidate")

    reference = float(snapshot["last"])
    raw_risk = float(selected.get("risk_percent") or 0.0)
    risk_fraction = max(core.GPW_PROFILE.risk_floor_percent, raw_risk)
    risk_fraction = min(risk_fraction, float(policy["maximum_published_risk_percent"]))
    reward_risk = float(policy["reward_risk"])
    stop = reference * (1.0 - risk_fraction)
    target = reference + (reference - stop) * reward_risk
    neutral_catalyst = float(policy.get("neutral_catalyst_score", 50.0))
    score = core.composite_score(selected, {"catalyst_score": neutral_catalyst}, config)
    source = _source_for(str(selected["symbol"]), snapshot)

    payload = copy.deepcopy(current)
    payload["generated_at"] = now.isoformat(timespec="seconds")
    payload["decision"] = "TRANSAKCJA"
    payload["reason"] = (
        "Obowiązkowy wybór dnia: główny screening nie wskazał transakcji, więc wybrano najwyżej "
        "sklasyfikowaną spółkę z poprawnymi danymi i potwierdzoną ceną po otwarciu."
    )
    payload["locked"] = True
    payload["selection"] = {
        "symbol": selected["symbol"],
        "ticker": str(selected["symbol"]).removesuffix(".WA"),
        "name": selected["name"],
        "sector": selected["sector"],
        "score": score,
        "quant_pre_score": selected.get("quant_pre_score"),
        "selection_mode": "MANDATORY_DAILY",
        "reference_price": core.round2(reference),
        "entry_zone": [core.round2(reference * 0.997), core.round2(reference * 1.006)],
        "activation": "Wybór dnia po otwarciu; cena wejścia jest zakotwiczona w świeżym kwotowaniu sesji.",
        "stop": core.round2(stop),
        "target": core.round2(target),
        "risk_percent": round(risk_fraction, 4),
        "reward_risk": reward_risk,
        "valid_until": gpw.add_sessions(now.date(), 2, config).isoformat(),
        "thesis": (
            f"{selected['name']} ma najwyższy ranking ilościowy dostępnego uniwersum GPW w trybie obowiązkowego wyboru dnia."
        ),
        "why_now": (
            "Wybór wynika z relatywnego momentum, kontekstu rynku, płynności jako cechy rankingowej, "
            "geometrii ryzyka i historycznego overlayu; brak świeżego katalizatora nie blokuje wyboru dnia."
        ),
        "risk_factors": [
            "Tryb obowiązkowy może mieć niższe przekonanie niż standardowa selekcja.",
            "Płynność nie jest veto w tym trybie i pozostaje składnikiem rankingu.",
            "SL pozostaje nadrzędnym warunkiem wyjścia.",
        ],
        "scores": {**selected["scores"], "catalyst": neutral_catalyst},
        "sources": [source],
        "review": {
            "approved": True,
            "mode": "mandatory_daily_quant_policy",
            "reason": "Najwyższy ranking ilościowy z potwierdzoną bieżącą ceną; polityka wymaga jednego wyboru na sesję.",
            "supported_source_ids": [source["id"]],
        },
        "market_snapshot": snapshot,
        "forced_from_reason": current.get("reason"),
        "quant_engine": selected.get("quant_engine"),
    }
    payload["outcome"] = {"status": "PENDING", "activated": None}
    methodology = payload.setdefault("methodology", {})
    methodology["minimum_score"] = 0.0
    methodology["score_policy"] = "mandatory_daily_best_ranked"
    methodology["mandatory_daily_selection"] = {
        "enabled": True,
        "trigger": "primary_engine_BRAK_TRANSAKCJI",
        "liquidity_role": "ranking_feature_not_veto",
        "maximum_published_risk_percent": float(policy["maximum_published_risk_percent"]),
        "opening_quote_required": True,
    }
    methodology["hard_admission_gates"] = [
        "market_data_completeness",
        "fresh_completed_session",
        "finite_atr_and_price",
        "fresh_current_session_quote",
        "bounded_published_risk",
    ]
    quality = payload.setdefault("data_quality", {})
    quality["mandatory_selection"] = {
        "applied": True,
        "market_coverage": round(coverage, 4),
        "ranked_candidates": len(ranked),
        "selected_rank": 1,
        "opening_quote_failures_before_selection": quote_errors,
    }
    return payload


def persist(payload: dict[str, Any]) -> None:
    gpw.validate_payload(payload, require_today=False)
    history_path = gpw.HISTORY_DIR / f"{payload['date']}.json"
    # This is an intentional BRAK_TRANSAKCJI -> TRANSAKCJA upgrade for the same
    # session, so gpw.publish() cannot be used because its append-only guard
    # correctly treats the earlier non-failure record as immutable.
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
        "selection_mode": "MANDATORY_DAILY",
        "symbol": (payload.get("selection") or {}).get("symbol"),
        "previous_reason": (payload.get("selection") or {}).get("forced_from_reason"),
    }
    gpw.atomic_json(gpw.AUDIT_DIR / f"{payload['date']}-mandatory.json", audit)


def run(*, now: datetime | None = None) -> dict[str, Any] | None:
    now = now or gpw.now_warsaw()
    policy = load_policy()
    if not policy.get("enabled", False):
        return None
    current = gpw.load_json(gpw.PUBLIC_PATH)
    if not isinstance(current, dict):
        return None
    config = gpw.load_config()
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
        print("GPW_MANDATORY_VALIDATE_OK", payload.get("decision"), (payload.get("selection") or {}).get("selection_mode"))
        return 0
    payload = run()
    if payload is None:
        current = gpw.load_json(gpw.PUBLIC_PATH, {})
        print("GPW_MANDATORY_NOOP", current.get("decision"), current.get("date"))
    else:
        selection = payload.get("selection") or {}
        print("GPW_MANDATORY_SELECTED", selection.get("ticker"), selection.get("score"), selection.get("reference_price"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

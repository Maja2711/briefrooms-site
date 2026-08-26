#!/usr/bin/env python3
"""Apply explicit site-owner authorization for BRACE paper control.

This never connects to a broker. BRACE controls a separate paper portfolio,
while the original portfolio remains the automatic fallback.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from brace_portfolio_config import load_config
from brace_portfolio_data import BASELINE_PORTFOLIO_PATH, ENGINE_DATA_ROOT, read_json, write_json_atomic
from brace_portfolio_execution import initialize_paper_portfolio
from brace_portfolio_publish import build_public_snapshot, publish
from brace_portfolio_state_sync import (
    active_position_ids,
    filter_position_recommendations,
    reconcile_public_decisions,
)

AUTH = ENGINE_DATA_ROOT / "control_authorization.json"
REGISTRY = ENGINE_DATA_ROOT / "methodology_registry.json"
PAPER = ENGINE_DATA_ROOT / "paper_portfolio.json"
ANALYSIS = ENGINE_DATA_ROOT / "analysis.json"
PENDING = ENGINE_DATA_ROOT / "pending_decisions.json"
ORDERS = ENGINE_DATA_ROOT / "paper_orders.json"
SHADOW = ENGINE_DATA_ROOT / "shadow_log.json"
HISTORY = ENGINE_DATA_ROOT / "promotion_history.json"
OPERATIONAL = ENGINE_DATA_ROOT / "operational_state.json"
ADAPTIVE = ENGINE_DATA_ROOT / "adaptive_policy.json"
LEARNING = ENGINE_DATA_ROOT / "learning_state.json"
LEARNING_WEEKDAY_UTC = 6  # Sunday
LEARNING_HOUR_UTC = 8
LEARNING_MINUTE_UTC = 40


def methodology(registry: Mapping[str, Any], methodology_id: str) -> dict[str, Any]:
    for item in registry.get("methodologies") or []:
        if item.get("methodology_id") == methodology_id:
            return item
    raise ValueError(f"Missing methodology: {methodology_id}")


def validate(auth: Mapping[str, Any]) -> None:
    if auth.get("schema_version") != "brace-control-authorization-v1" or auth.get("enabled") is not True:
        raise ValueError("BRACE paper-control authorization is missing or disabled")
    if auth.get("paper_only") is not True or auth.get("real_broker_connected") is not False:
        raise ValueError("Authorization must remain paper-only with no broker connection")
    if auth.get("controller_status") != "PROBATIONARY_CONTROL":
        raise ValueError("Only probationary paper control can be explicitly authorized")


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def weekly_learning_schedule(now: datetime, last_review_at: Any) -> dict[str, Any]:
    """Return the actual workflow cadence and whether scheduled reviews were missed."""
    now = now.astimezone(timezone.utc)
    days_since_sunday = (now.weekday() - LEARNING_WEEKDAY_UTC) % 7
    previous = (now - timedelta(days=days_since_sunday)).replace(
        hour=LEARNING_HOUR_UTC,
        minute=LEARNING_MINUTE_UTC,
        second=0,
        microsecond=0,
    )
    if previous > now:
        previous -= timedelta(days=7)
    next_review = previous + timedelta(days=7)
    last_review = _parse_utc(last_review_at)
    overdue = last_review is None or last_review < previous
    missed = 0
    if overdue:
        cursor = previous
        while cursor > (last_review or datetime.min.replace(tzinfo=timezone.utc)):
            missed += 1
            cursor -= timedelta(days=7)
            if missed >= 52:
                break
    return {
        "cadence": "weekly",
        "cron_utc": "40 8 * * 0",
        "timezone": "UTC",
        "previous_scheduled_review_at": previous.isoformat(timespec="seconds"),
        "next_scheduled_review_at": next_review.isoformat(timespec="seconds"),
        "overdue": overdue,
        "missed_scheduled_reviews": missed,
    }


def learning_public_state(now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    policy = read_json(ADAPTIVE)
    state = read_json(LEARNING)
    statistics = policy.get("statistics") or {}
    active = policy.get("active_overrides") or {}
    status = str(policy.get("status") or "NOT_CONFIGURED")
    effective = float(statistics.get("effective_samples") or 0.0)
    minimum = float(policy.get("minimum_effective_samples") or 12.0)
    active_now = bool(policy.get("apply_to_shadow_decisions") and active)
    last_review_at = policy.get("generated_at") or state.get("generated_at")
    schedule = weekly_learning_schedule(now, last_review_at)
    return {
        "status": status,
        "active_parameters": active_now,
        "effective_samples": effective,
        "minimum_effective_samples": minimum,
        "outcome_events": int(statistics.get("outcome_events") or 0),
        "eligible_events": int(statistics.get("eligible_events") or 0),
        "active_overrides": deepcopy(active),
        "candidate_overrides": deepcopy(policy.get("candidate_overrides") or {}),
        "reason": policy.get("learning_reason"),
        "last_review_at": last_review_at,
        **schedule,
        "next_changes_apply_weekly": True,
        "real_broker_prohibited": True,
        "explanation_pl": (
            "Pętla uczenia jest zaplanowana cotygodniowo w niedzielę o 08:40 UTC. "
            f"Stan {status}, dojrzałe próbki {effective:g}/{minimum:g}. "
            + (
                f"Wykryto opóźnienie: pominięte planowe przeglądy: {schedule['missed_scheduled_reviews']}. "
                if schedule["overdue"]
                else "Ostatni planowy przegląd został zarejestrowany. "
            )
            + "Zmiany parametrów mogą zostać aktywowane dopiero po zebraniu wymaganych wyników, "
            "dwukrotnym potwierdzeniu i przejściu bramki badawczej."
        ),
        "explanation_en": (
            "The learning loop is scheduled weekly on Sunday at 08:40 UTC. "
            f"Status {status}, mature effective samples {effective:g}/{minimum:g}. "
            + (
                f"A delay is detected: missed scheduled reviews: {schedule['missed_scheduled_reviews']}. "
                if schedule["overdue"]
                else "The latest scheduled review is recorded. "
            )
            + "Parameter changes may activate only after the sample requirement, two consecutive confirmations "
            "and the research gate are satisfied."
        ),
    }


def public_snapshot(registry: Mapping[str, Any], auth: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    config, _ = load_config()
    pending = read_json(PENDING)
    paper = read_json(PAPER)
    orders = read_json(ORDERS)
    snapshot = build_public_snapshot(
        registry, read_json(ANALYSIS), pending, read_json(SHADOW), read_json(HISTORY),
        read_json(OPERATIONAL), config, now, PAPER.exists(),
    )
    snapshot["control_authorization"] = deepcopy(dict(auth))
    snapshot["active_portfolio_ids"] = sorted(active_position_ids(paper))
    snapshot["portfolio_state_source"] = "data/portfolio10k/paper_portfolio.json"
    snapshot["position_recommendations"] = filter_position_recommendations(
        pending.get("recommendations") or [], paper
    )[:20]
    snapshot["pending_decisions"] = reconcile_public_decisions(
        pending.get("decisions") or [], paper, orders, limit=10
    )
    snapshot["learning_loop"] = learning_public_state(now)
    snapshot["display_status"] = "BRACE_PROBATIONARY_PAPER_CONTROL"
    snapshot["control_summary_pl"] = (
        "BRACE steruje oddzielnym portfelem modelowym w trybie próbnym. Co tydzień aktualizuje dane, "
        "uwzględnia zweryfikowane raporty istotne, ocenia pozycje i może wykonywać wyłącznie transakcje paper. "
        "Limity ryzyka oraz automatyczny powrót do baseline pozostają aktywne."
    )
    snapshot["control_summary_en"] = (
        "BRACE controls a separate model portfolio in probationary mode. Each week it refreshes data, "
        "incorporates verified material reports, assesses every position and may execute paper trades only. "
        "Risk limits and automatic baseline fallback remain active."
    )
    return snapshot


def activate(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc); auth = read_json(AUTH); validate(auth)
    baseline = read_json(BASELINE_PORTFOLIO_PATH); registry = read_json(REGISTRY)
    challenger_id = str(registry.get("challenger_methodology_id") or "brace-portfolio-engine")
    baseline_id = str(registry.get("baseline_methodology_id") or "portfolio-10k-baseline")
    challenger = methodology(registry, challenger_id); baseline_method = methodology(registry, baseline_id)
    previous = str(registry.get("controller_state") or "ACTIVE_BASELINE")
    registry["controller_state"] = "PROBATIONARY_CONTROL"; registry["champion_methodology_id"] = challenger_id
    registry["generated_at"] = now.isoformat(timespec="seconds")
    challenger["status"] = "PROBATIONARY_CONTROL"
    challenger.setdefault("parameters", {})["autonomy_mode"] = "PAPER_EXECUTION"
    challenger["parameters"]["real_broker_access"] = False
    challenger.setdefault("validation_results", {})["user_authorized_paper_control"] = {
        "authorized_at":auth.get("authorized_at") or now.isoformat(timespec="seconds"),
        "authorized_by":auth.get("authorized_by"), "reason_pl":auth.get("reason_pl"), "reason_en":auth.get("reason_en"),
        "remaining_automatic_promotion_gates_preserved":True, "paper_only":True,
    }
    challenger["validation_results"]["probation_started_at"] = challenger["validation_results"].get("probation_started_at") or auth.get("authorized_at") or now.isoformat(timespec="seconds")
    baseline_method["status"] = "FALLBACK_BASELINE"; write_json_atomic(REGISTRY, registry)

    paper = read_json(PAPER) or initialize_paper_portfolio(baseline, now)
    paper.update({"status":"probationary_paper_control","controller":challenger_id,"controller_status":"PROBATIONARY_CONTROL","control_authorization":deepcopy(dict(auth)),"baseline_fallback_portfolio_id":baseline.get("portfolio_id"),"paper_only":True,"real_broker_connected":False})
    write_json_atomic(PAPER, paper)

    history = read_json(HISTORY) or {"schema_version":"1.0.0","records":[]}
    record_id = f"user-paper-control-{str(auth.get('authorized_at') or now.isoformat())[:10]}"
    if not any(row.get("promotion_id") == record_id for row in history.get("records") or []):
        history.setdefault("records", []).append({"promotion_id":record_id,"previous_status":previous,"new_status":"PROBATIONARY_CONTROL","evaluated_at":now.isoformat(timespec="seconds"),"all_conditions_passed":False,"reason":"Explicit site-owner authorization for paper-only probationary control; automatic quality gates remain visible.","authorization":deepcopy(dict(auth))})
    history["generated_at"] = now.isoformat(timespec="seconds"); write_json_atomic(HISTORY, history)
    publish(public_snapshot(registry, auth, now))
    return {"controller_status":"PROBATIONARY_CONTROL","previous_controller":previous}


def republish(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc); auth = read_json(AUTH); validate(auth); registry = read_json(REGISTRY)
    publish(public_snapshot(registry, auth, now)); return {"controller_status":registry.get("controller_state")}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--republish", action="store_true"); args = parser.parse_args()
    result = republish() if args.republish else activate(); print(f"BRACE paper control: {result['controller_status']}"); return 0


if __name__ == "__main__":
    raise SystemExit(main())

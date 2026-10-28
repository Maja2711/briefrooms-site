#!/usr/bin/env python3
"""Exact replay adapters for deployable Stock Trading v2 Challenger artifacts."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Mapping
from copy import deepcopy
from datetime import timedelta

try:
    from scripts import stock_trading_v2_contracts as contracts
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_contracts as contracts

ENTRY_BLOCKERS = {
    "entry_score_below_threshold",
    "score",
    "entry_score",
    "minimum_composite_score",
}

# Reviewed copies of every CASH experiment/evaluation source and the native
# production runner are bound together. Promotion executes only main's copies.
CASH_FINGERPRINT_FILES = (
    "stock_trading_v2_production_replay.py", "stock_trading_portfolio.py",
    "stock_trading_v2_production_bridge.py", "stock_trading_portfolio_analytics.py",
    "stock_trading_quote_enricher.py", "stock_trading_v2_reentry_learning.py",
    "stock_trading_v2_opportunity_engine.py", "stock_trading_v2_opportunity_experience.py",
    "stock_trading_v2_admission_ledger.py", "stock_trading_v2_experience_store.py",
    "stock_trading_v2_exact_replay.py", "stock_trading_v2_exact_challenger_eval.py",
    "stock_trading_v2_challenger_eval.py", "stock_trading_v2_contracts.py",
    "stock_trading_v2_policy_learner.py", "stock_trading_v2_challenger_factory.py",
    "stock_trading_v2_replay_tournament.py", "stock_trading_v2_regret_engine.py",
    "stock_trading_v2_outcome_replay.py", "provenance_contract.py",
    "stock_trading_sourced_metadata.py", "company_logo_resolver.py",
    "stock_trading_session_calendar.py", "nyse_session_calendar.py",
    "stock_trading_v2_replay_capture.py",
)

CONTINUOUS_PROTOCOL = "stock-trading-cash-edge-protocol-v2"


def continuous_cash_protocol(horizon_sessions: int) -> dict[str, Any]:
    """Predeclared forward experiment; changing a constant needs a new version.

    The weekly betting score tests a *clipped conditional-mean* null, not an
    unconditional mean or future profitability guarantee. Portfolio economics
    and risk have their own, unclipped gates. No independence of names, weeks
    or overlapping positions is asserted.
    """
    if horizon_sessions not in {1, 2}:
        raise ValueError("continuous CASH horizon must be 1 or 2 sessions")
    return {
        "schema_version": CONTINUOUS_PROTOCOL,
        "fixed_regular_sessions": 60, "fixed_paired_n": 60 // horizon_sessions,
        "horizon_sessions": horizon_sessions, "maximum_censored": 0,
        "membership": "first_eligible_anchor_freezes_complete_future_tape",
        "outcome_missing": "WAIT_THEN_TERMINAL_CENSOR_NO_REPLACEMENT",
        "enrollment_deadline_hours": 24, "outcome_deadline_days": 180,
        "session_horizon_rule": "first_60_preregistered_calendar_regular_closes",
        "calendar_requirement": "native_preregistered_complete_session_calendar_v1",
        "execution_semantics": "continuous_production_lifecycle_v2",
        "initial_valuation": "shared_initial_mark_to_liquidation_policy_stress_no_historical_fill_claim",
        "close_valuation": "every_open_position_exact_sourced_regular_close_bar_mark_only",
        "maximum_close_receipt_delay_hours": 24,
        "family_id": "stock-trading-v2-cash-edge-lifetime-v1",
        "lifetime_max_tests": 24, "family_alpha": 0.05, "alpha": 0.05 / 24,
        "single_formal_look": True,
        "statistical_unit": "calendar_week_whole_portfolio_net_increment",
        "statistical_test": "bounded_conditional_mean_e_value_v1",
        "conditional_null": "E[clip(weekly_net_delta_percent/1pp,-1,1)|past]<=0",
        "bet_fraction": 0.9, "clip_scale_percent": 1.0,
        "min_calendar_week_groups": 10, "bootstrap_draws": 20000,
        "bootstrap_block_weeks": 2, "bootstrap_role": "DESCRIPTIVE_SENSITIVITY_ONLY",
        "min_unique_symbols": 5, "min_span_days": 60,
        "max_single_positive_share": 0.5,
        "max_drawdown_increase_pct": 0.0, "min_mean_incremental_pct": 0.0,
    }


def parse_dt(value: Any) -> datetime:
    text = str(value or "").replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("timestamp must be timezone aware")
    return dt


def source_group(event: Mapping[str, Any]) -> str:
    return str(((event.get("source") or {}).get("payload_sha256")) or event.get("event_id") or "")


def composite_score(event: Mapping[str, Any]) -> float | None:
    score_state = ((event.get("candidate_state") or {}).get("score_state") or {})
    for key in ("opening_adjusted_score", "score", "composite_score", "legacy_composite_score", "quant_pre_score"):
        value = score_state.get(key)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            return numeric
    return None


def failed_gate_names(event: Mapping[str, Any]) -> list[str]:
    state = event.get("candidate_state") or {}
    path = state.get("decision_path") or {}
    names: list[str] = []
    for gate in path.get("gates") or []:
        if not isinstance(gate, Mapping) or gate.get("passed") is not False:
            continue
        name = str(gate.get("name") or "").strip()
        if name:
            names.append(name)
    if names:
        return names
    blocker = path.get("first_blocking_gate") or state.get("first_blocking_gate")
    if isinstance(blocker, Mapping):
        name = str(blocker.get("name") or "").strip()
        return [name] if name else []
    return [str(blocker)] if blocker else []


def is_single_entry_blocker(event: Mapping[str, Any]) -> bool:
    names = failed_gate_names(event)
    return len(names) == 1 and names[0] in ENTRY_BLOCKERS


def outcome_net_percent(row: Mapping[str, Any]) -> float | None:
    replay = row.get("replay") or {}
    status = replay.get("status")
    if status == "NOT_ACTIVATED":
        return 0.0
    if status != "SETTLED":
        return None
    try:
        value = float(replay.get("net_return_percent"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def entry_thresholds(candidate: Mapping[str, Any]) -> tuple[float, float]:
    replay = candidate.get("replay_contract") or {}
    if replay.get("adapter") != "entry_threshold_v1":
        raise ValueError("unsupported exact replay adapter")
    champion = float(replay.get("champion_threshold"))
    challenger = float(replay.get("challenger_threshold"))
    if not (challenger < champion):
        raise ValueError("entry threshold challenger must be a bounded relaxation")
    spec = candidate.get("deployment_spec") or {}
    values: list[float] = []
    for target, patches in (spec.get("targets") or {}).items():
        if target not in {"gpw_daily_config", "stock_trading_policy"}:
            continue
        for patch in patches or []:
            path = tuple(str(part) for part in (patch.get("path") or []))
            if path in {("minimum_composite_score",), ("markets", "GPW", "minimum_entry_score")}:
                values.append(float(patch.get("value")))
    if len(values) != 2 or any(abs(value - challenger) > 1e-9 for value in values):
        raise ValueError("deployment spec does not match exact replay threshold")
    return champion, challenger


def collect_entry_threshold_samples(
    *,
    candidate: Mapping[str, Any],
    events: Iterable[Mapping[str, Any]],
    admissions: Iterable[Mapping[str, Any]],
    outcomes: Iterable[Mapping[str, Any]],
    after: datetime | None = None,
    before_or_at: datetime | None = None,
) -> list[dict[str, Any]]:
    champion_threshold, challenger_threshold = entry_thresholds(candidate)
    horizon = int(candidate.get("horizon_sessions") or 0)
    event_map: dict[str, dict[str, Any]] = {}
    groups: dict[str, list[str]] = defaultdict(list)
    for raw in events:
        event = dict(raw)
        if str(event.get("market") or "").upper() != "GPW":
            continue
        try:
            decision_dt = parse_dt(event.get("decision_at"))
        except Exception:
            continue
        if after is not None and decision_dt <= after:
            continue
        if before_or_at is not None and decision_dt > before_or_at:
            continue
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        event_map[event_id] = event
        groups[source_group(event)].append(event_id)

    admission_map: dict[str, dict[str, Any]] = {}
    for row in admissions:
        event_id = str(row.get("source_event_id") or "")
        if event_id:
            admission_map[event_id] = dict(row)

    outcome_map: dict[str, dict[str, Any]] = {}
    for row in outcomes:
        try:
            row_horizon = int(row.get("horizon_sessions") or 0)
        except (TypeError, ValueError):
            continue
        if row_horizon == horizon:
            outcome_map[str(row.get("source_event_id") or "")] = dict(row)

    samples: list[dict[str, Any]] = []
    for group, event_ids in groups.items():
        if any(((admission_map.get(event_id, {}).get("champion") or {}).get("action")) == "LONG" for event_id in event_ids):
            continue
        eligible: list[tuple[float, dict[str, Any], float]] = []
        for event_id in event_ids:
            event = event_map[event_id]
            if event.get("selected") is True or not is_single_entry_blocker(event):
                continue
            score = composite_score(event)
            if score is None or not (challenger_threshold <= score < champion_threshold):
                continue
            outcome = outcome_map.get(event_id)
            if not outcome:
                continue
            net = outcome_net_percent(outcome)
            if net is None:
                continue
            eligible.append((score, event, net))
        if not eligible:
            continue
        eligible.sort(key=lambda item: (-item[0], str(item[1].get("symbol") or "")))
        score, event, net = eligible[0]
        samples.append({
            "source_group": group,
            "decision_at": event.get("decision_at"),
            "session_date": event.get("session_date"),
            "symbol": event.get("symbol"),
            "source_event_id": event.get("event_id"),
            "champion_action": "CASH",
            "challenger_action": "LONG",
            "champion_score_threshold": champion_threshold,
            "challenger_score_threshold": challenger_threshold,
            "candidate_score": round(score, 8),
            "champion_net_return_percent": 0.0,
            "challenger_net_return_percent": round(net, 8),
            "incremental_net_return_percent": round(net, 8),
        })
    samples.sort(key=lambda row: (str(row.get("decision_at")), str(row.get("source_group"))))
    return samples


def cash_edge_policy_pair(candidate: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    replay = candidate.get("replay_contract") or {}
    if replay.get("adapter") != "cash_edge_v2":
        raise ValueError("production lifecycle CASH adapter required")
    market = str(replay.get("market") or "")
    baseline = candidate.get("champion_policy_snapshot") or {}
    if market not in {"GPW", "US"} or not baseline:
        raise ValueError("frozen production policy required")
    expected_sha = candidate.get("champion_policy_sha256") or replay.get("policy_sha256")
    if contracts.payload_sha256(baseline) != expected_sha:
        raise ValueError("frozen Champion policy integrity mismatch")
    champion = deepcopy(dict(baseline))
    challenger = deepcopy(champion)
    base = float(((champion.get("markets") or {}).get(market) or {})["minimum_cash_edge_points"])
    value = float(replay.get("challenger_cash_edge_points"))
    spec = candidate.get("deployment_spec") or {}
    expected_targets = {"stock_trading_policy": [{"op": "replace", "path": ["markets", market, "minimum_cash_edge_points"], "value": value}]}
    if (spec.get("targets") != expected_targets or not math.isfinite(base) or not math.isfinite(value)
            or not 1 <= base <= 15 or abs(base - value) != 1 or not 1 <= value <= 15
            or float(replay.get("champion_cash_edge_points")) != base):
        raise ValueError("only one CASH edge mutation of one point allowed")
    challenger["markets"][market]["minimum_cash_edge_points"] = value
    return champion, challenger


def cash_edge_eligible(event: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    """Ex-ante causal attribution; ranking, held names and rotation never alias."""
    state = event.get("candidate_state") or {}
    path = state.get("decision_path") or {}
    context = state.get("decision_context") or {}
    replay = candidate.get("replay_contract") or {}
    if event.get("market") != replay.get("market") or not context:
        return False
    if context.get("policy_sha256") != replay.get("policy_sha256"):
        return False
    if path.get("eligible") is not True or path.get("already_held") or int(path.get("available_slots") or 0) <= 0:
        return False
    # Primary BUY may be withheld by a +1 margin; an isolated CASH margin
    # rejection may become eligible at -1. Hard gates are untouched.
    action = str(path.get("portfolio_action") or "")
    if not (event.get("selected") is True and action == "BUY") and path.get("cash_margin_only_blocker") is not True:
        return False
    score = composite_score(event)
    if score is None:
        return False
    cash = float(path.get("cash_utility", 50.0))
    base = float(replay.get("champion_cash_edge_points"))
    value = float(replay.get("challenger_cash_edge_points"))
    return min(base, value) <= score - cash < max(base, value)


def native_cash_admission_events(candidate: Mapping[str, Any], frames: Iterable[Mapping[str, Any]], *,
                                 as_of: datetime) -> dict[str, dict[str, Any]]:
    """Eligible source decisions actually observed in main's admission stream.

    Research scans without a native handoff receipt are not formal episodes.
    Frame/code hashes and the original policy/source payload must agree before
    a native opportunity can authorize cohort enrollment. No quotes or outcome
    data are needed at this enrollment step.
    """
    try:
        from scripts import stock_trading_v2_production_replay as production_replay
        from scripts import stock_trading_v2_opportunity_experience as experience
        from scripts import stock_trading_v2_admission_ledger as ledger
    except ImportError:
        return {}
    market = str((candidate.get("replay_contract") or {}).get("market") or "")
    policy_sha = candidate.get("champion_policy_sha256")
    code = production_replay.production_code_sha256()
    observed: dict[str, dict[str, Any]] = {}
    for raw in frames:
        frame = dict(raw.get("frame") or raw)
        if frame.get("kind") != "ADMISSION" or production_replay._validate_frame(frame, code):
            continue
        opportunity = (frame.get("opportunities") or {}).get(market)
        if not isinstance(opportunity, Mapping):
            continue
        try:
            if not parse_dt(opportunity["generated_at"]) <= parse_dt(frame["observed_at"]) <= parse_dt(frame["recorded_at"]) <= as_of:
                continue
            context = opportunity.get("decision_context") or {}
            if (frame.get("policy_sha256") != policy_sha
                    or contracts.payload_sha256(frame.get("policy") or {}) != policy_sha
                    or frame.get("policy") != candidate.get("champion_policy_snapshot")
                    or context.get("policy_sha256") != policy_sha
                    or context.get("policy_snapshot") != frame.get("policy")
                    or context.get("champion_manifest") != frame.get("manifest")
                    or context.get("champion_manifest_sha256") != contracts.payload_sha256(frame.get("manifest") or {})):
                continue
            source = context.get("evidence_snapshot")
            if not isinstance(source, Mapping):
                continue
            derived = experience.events_from_snapshots(opportunity, source,
                candidates_per_cycle=max(1, len(opportunity.get("evaluated_candidates") or [])),
                recorded_at=opportunity["generated_at"])
            for event in derived:
                ledger._context(event)
                if not cash_edge_eligible(event, candidate):
                    continue
                event_id = str(event["event_id"])
                previous = observed.get(event_id)
                if previous is not None and contracts.event_without_runtime_fields(previous) != contracts.event_without_runtime_fields(event):
                    raise contracts.ContractError("conflicting native opportunity identity")
                observed[event_id] = event
        except (KeyError, TypeError, ValueError):
            # An incomplete/invalid native frame cannot authorize membership.
            # Main's independent verifier also rejects its use in a proof.
            continue
    return observed


def production_episode_input(event: Mapping[str, Any], frames: Iterable[Mapping[str, Any]], *, horizon_sessions: int) -> dict[str, Any] | None:
    """Select a native tape by session receipts, never by elapsed 24h.

    A close marker is an actual main-writer receipt from the provider's regular
    period metadata. A session is counted only once the receipt was recorded
    after its sourced end. Missing close observations remain PENDING.
    """
    market = str(event["market"])
    start = parse_dt(event["decision_at"])
    source_sha = str((event.get("source") or {}).get("payload_sha256") or "")
    native = sorted((dict(row.get("frame") or row) for row in frames), key=lambda row: str(row.get("observed_at") or ""))
    first_index = None
    for index, frame in enumerate(native):
        try:
            stamp = parse_dt(frame.get("observed_at"))
        except (TypeError, ValueError):
            continue
        opportunity = (frame.get("opportunities") or {}).get(market) or {}
        if stamp >= start and opportunity.get("opportunity_sha256") == source_sha:
            first_index = index
            break
    if first_index is None:
        return None
    selected, closed_sessions = [], set()
    for frame in native[first_index:]:
        stamp = parse_dt(frame.get("observed_at"))
        if stamp > start + timedelta(hours=168):
            break  # A later overlapping episode cannot repair this tape.
        selected.append(frame)
        session = (frame.get("market_sessions") or {}).get(market) or {}
        if session:
            try:
                close = parse_dt(session["regular_end"])
                opening = parse_dt(session["regular_start"])
                receipt = parse_dt(frame.get("recorded_at"))
            except (KeyError, TypeError, ValueError):
                continue
            date = str(session.get("date") or "")
            if (session.get("source") and opening < close <= stamp <= receipt
                    and close > start and date and session.get("state") in {"POST", "POSTPOST", "CLOSED", "REGULAR_CLOSED"}):
                closed_sessions.add(date)
        if len(closed_sessions) >= horizon_sessions:
            first = selected[0]
            return {"initial_state": first.get("initial_state"), "frames": selected,
                    "bridge_config": first.get("bridge_config"), "runtime": first.get("runtime"),
                    "episode_end_at": frame["observed_at"], "horizon_completed_sessions": len(closed_sessions),
                    "source_frame_sha256": first.get("frame_sha256")}
    return None


def collect_cash_edge_samples(*, candidate: Mapping[str, Any], events: Iterable[Mapping[str, Any]],
                              admissions: Iterable[Mapping[str, Any]], outcomes: Iterable[Mapping[str, Any]],
                              cohort: Iterable[Mapping[str, Any]] | None = None,
                              replay_frames: Iterable[Mapping[str, Any]] = (),
                              after: datetime | None = None, before_or_at: datetime | None = None) -> list[dict[str, Any]]:
    """Replay actual production lifecycle; legacy frozen-geometry is never exact.

    Cohort selection is done by the prospective evaluator *without outcomes*.
    This function can also produce descriptive discovery samples, but cannot
    grant a formal PASS on its own.
    """
    if (candidate.get("prospective_protocol") or {}).get("schema_version") == CONTINUOUS_PROTOCOL and cohort is not None:
        return collect_continuous_cash_samples(candidate=candidate, events=events, cohort=cohort,
                                               replay_frames=replay_frames)
    champion, challenger = cash_edge_policy_pair(candidate)
    try:
        from scripts import stock_trading_v2_production_replay as production_replay
    except ImportError:
        return []  # Missing audited runner is a gate, never a proxy fill.
    events_by_id = {str(row.get("event_id")): dict(row) for row in events}
    chosen = list(cohort) if cohort is not None else [
        {"source_event_id": row["event_id"], "decision_at": row.get("decision_at")}
        for row in events_by_id.values() if cash_edge_eligible(row, candidate)
        and (after is None or parse_dt(row.get("decision_at")) > after)
        and (before_or_at is None or parse_dt(row.get("decision_at")) <= before_or_at)
    ]
    artifacts = {}
    for row in outcomes:
        artifact = row.get("production_policy_replay") or (row.get("replay") or {}).get("production_policy_replay")
        if isinstance(artifact, Mapping):
            artifacts[str(row.get("source_event_id") or "")] = artifact
    frames = list(replay_frames)
    samples = []
    for member in chosen:
        event_id = str(member.get("source_event_id") or "")
        event = events_by_id.get(event_id)
        if not event or not cash_edge_eligible(event, candidate):
            continue
        context = (event.get("candidate_state") or {}).get("decision_context") or {}
        artifact = artifacts.get(event_id)
        if artifact is None and frames:
            artifact = production_episode_input(event, frames, horizon_sessions=int(candidate["horizon_sessions"]))
        if not isinstance(artifact, Mapping) or not artifact.get("bridge_config"):
            continue
        if int(artifact.get("horizon_completed_sessions") or 0) != int(candidate["horizon_sessions"]) or not artifact.get("episode_end_at"):
            continue
        try:
            pair = production_replay.compare_policies(
                artifact.get("initial_state") or context.get("portfolio_snapshot") or {}, artifact.get("frames") or [],
                champion_policy=champion, challenger_policy=challenger,
                bridge_config=artifact["bridge_config"],
                runtime=artifact.get("runtime"),
                initial_capital_by_market=artifact.get("initial_capital_by_market"),
                markets=[str(event["market"])],
            )
        except (ValueError, TypeError, KeyError):
            continue
        if pair.get("formal_evidence_eligible") is not True or pair.get("status") != "COMPLETE":
            continue
        market = str(event["market"])
        result = (pair.get("market_results") or {}).get(market) or {}
        if not result:
            continue
        left, right = result.get("champion") or {}, result.get("challenger") or {}
        # Paired complete portfolio NAV includes occupied slots, lifecycle,
        # ratcheted stops, winner extensions and costs, not just candidate R.
        sample = {
            "source_group": source_group(event), "source_event_id": event_id,
            "source_event_sha256": event.get("event_sha256"), "decision_at": event["decision_at"],
            "session_date": event.get("session_date"), "symbol": event["symbol"], "market": market,
            "episode_end_at": artifact.get("episode_end_at"), "block_id": member.get("block_id", source_group(event)),
            "horizon_completed_sessions": artifact.get("horizon_completed_sessions"),
            "source_frame_sha256": artifact.get("source_frame_sha256"),
            "champion_net_return_percent": left.get("net_return_percent"),
            "challenger_net_return_percent": right.get("net_return_percent"),
            "incremental_net_return_percent": result.get("net_delta_percent"),
            "champion_max_drawdown_percent": left.get("max_drawdown_percent"),
            "challenger_max_drawdown_percent": right.get("max_drawdown_percent"),
            "benchmark_return_percent": left.get("benchmark_return_percent"),
            "challenger_top_winner_share": right.get("top_winner_share"),
            "challenger_pnl_excluding_top_winner": right.get("pnl_excluding_top_winner"),
            "production_replay": pair,
            "execution_semantics": "production_lifecycle_v1",
        }
        if all(isinstance(sample[k], (int, float)) and math.isfinite(float(sample[k])) for k in (
            "champion_net_return_percent", "challenger_net_return_percent", "incremental_net_return_percent",
            "champion_max_drawdown_percent", "challenger_max_drawdown_percent")):
            samples.append(sample)
    return sorted(samples, key=lambda row: (str(row["decision_at"]), str(row["source_event_id"])))


def continuous_trial_input(event: Mapping[str, Any], frames: Iterable[Mapping[str, Any]], *,
                           protocol: Mapping[str, Any], as_of: datetime | None = None) -> dict[str, Any]:
    """Freeze one future calendar/tape, never skip an absent expected session.

    The first main frame owns the calendar. Later calendar revisions cannot
    move the endpoint or replace a missing close. A calendar is a scheduled
    coverage contract, not proof that the exchange actually opened: each of
    its dates additionally needs matching native provider close metadata.
    """
    market, start = str(event["market"]), parse_dt(event["decision_at"])
    native = []
    for raw in frames:
        frame = dict(raw.get("frame") or raw)
        try:
            observed, recorded = parse_dt(frame["observed_at"]), parse_dt(frame["recorded_at"])
        except (KeyError, ValueError, TypeError):
            return {"status": "PENDING", "reason": "native_frame_clock_invalid"}
        if as_of is None or (observed <= as_of and recorded <= as_of):
            native.append(frame)
    native.sort(key=lambda row: (parse_dt(row["observed_at"]), parse_dt(row["recorded_at"]), row.get("frame_sha256", "")))
    source_sha = (event.get("source") or {}).get("payload_sha256")
    first_index = next((i for i, row in enumerate(native) if parse_dt(row["observed_at"]) >= start
        and ((row.get("opportunities") or {}).get(market) or {}).get("opportunity_sha256") == source_sha), None)
    if first_index is None:
        return {"status": "PENDING", "reason": "native_anchor_frame_missing"}
    first = native[first_index]
    calendar = (first.get("session_calendar") or {}).get(market)
    try:
        from scripts import stock_trading_portfolio as portfolio
        from zoneinfo import ZoneInfo
        body = dict(calendar or {})
        stored = body.pop("calendar_sha256", None)
        if (not stored or stored != contracts.payload_sha256(body)
                or body.get("schema_version") != "stock-trading-session-calendar-v1"
                or body.get("market") != market or not body.get("source")
                or not body.get("source_url") or not body.get("calendar_id")
                or ZoneInfo(str(body.get("timezone"))) != portfolio.MARKET_TZ[market]
                or not parse_dt(body["observed_at"]) <= parse_dt(body["received_at"]) <= start
                or not parse_dt(body["coverage_start"]) <= start < parse_dt(body["coverage_end"])):
            raise ValueError("calendar not prospectively bound")
        schedule = []
        previous = None
        for session in body["sessions"]:
            opening, close = parse_dt(session["regular_start"]), parse_dt(session["regular_end"])
            if (not opening < close or session["date"] != close.astimezone(portfolio.MARKET_TZ[market]).date().isoformat()
                    or (previous is not None and opening <= previous)
                    or not parse_dt(body["coverage_start"]) <= opening < close <= parse_dt(body["coverage_end"])):
                raise ValueError("calendar schedule invalid")
            previous = close
            if close > start:
                schedule.append(dict(session))
        schedule = schedule[:int(protocol["fixed_regular_sessions"])]
        if len(schedule) != int(protocol["fixed_regular_sessions"]):
            raise ValueError("calendar does not cover fixed endpoint")
    except (KeyError, ValueError, TypeError):
        return {"status": "PENDING", "reason": "preregistered_session_calendar_missing_or_invalid"}
    expected = {row["date"]: row for row in schedule}
    following_opens = {row["date"]: parse_dt(schedule[index + 1]["regular_start"])
                       for index, row in enumerate(schedule[:-1])}
    close_frames, selected = {}, []
    final_close = parse_dt(schedule[-1]["regular_end"])
    for frame in native[first_index:]:
        selected.append(frame)
        session = (frame.get("market_sessions") or {}).get(market) or {}
        if session:
            try:
                close = parse_dt(session["regular_end"])
                opening = parse_dt(session["regular_start"])
                date = str(session["date"])
                stamp, receipt = parse_dt(frame["observed_at"]), parse_dt(frame["recorded_at"])
                if start < close <= final_close:
                    entry = expected.get(date)
                    if (not entry or opening != parse_dt(entry["regular_start"])
                            or close != parse_dt(entry["regular_end"])):
                        return {"status": "PENDING", "reason": "provider_calendar_disagreement"}
                    if (market in (frame.get("close_observations") or {})
                            and session.get("source") and opening < close <= stamp <= receipt
                            and session.get("state") in {"POST", "POSTPOST", "CLOSED", "REGULAR_CLOSED"}):
                        if (receipt > close + timedelta(hours=int(protocol["maximum_close_receipt_delay_hours"]))
                                or (date in following_opens and receipt >= following_opens[date])):
                            return {"status": "PENDING", "reason": "session_close_receipt_after_next_session_or_deadline"}
                        close_frames.setdefault(date, frame)
            except (KeyError, ValueError, TypeError):
                return {"status": "PENDING", "reason": "native_session_metadata_invalid"}
        if all(row["date"] in close_frames for row in schedule):
            # The endpoint is the first receipt of the predeclared 60th close.
            endpoint = close_frames[schedule[-1]["date"]]
            if endpoint is not frame:
                return {"status": "PENDING", "reason": "earlier_session_close_arrived_after_fixed_endpoint"}
            horizon = int(protocol["horizon_sessions"])
            return {"status": "COMPLETE", "initial_state": first.get("initial_state"), "frames": selected,
                    "bridge_config": first.get("bridge_config"), "runtime": first.get("runtime"),
                    "source_frame_sha256": first.get("frame_sha256"), "calendar_sha256": stored,
                    "schedule": schedule, "regular_end": schedule[-1]["regular_end"],
                    "episode_end_at": frame["observed_at"],
                    "boundaries": [{"session": schedule[index], "frame": close_frames[schedule[index]["date"]]}
                                   for index in range(horizon - 1, len(schedule), horizon)]}
        # A later receipt cannot repair an expected missing earlier close.
        if parse_dt(frame["observed_at"]) >= final_close and schedule[-1]["date"] in close_frames:
            return {"status": "PENDING", "reason": "expected_session_close_missing_at_fixed_endpoint"}
    return {"status": "PENDING", "reason": "waiting_for_all_predeclared_session_closes",
            "expected_sessions": len(schedule), "observed_closes": len(close_frames),
            "regular_end": schedule[-1]["regular_end"], "calendar_sha256": stored}


def collect_continuous_cash_samples(*, candidate: Mapping[str, Any], events: Iterable[Mapping[str, Any]],
                                    cohort: Iterable[Mapping[str, Any]],
                                    replay_frames: Iterable[Mapping[str, Any]],
                                    coverage_diagnostics: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One pair of books, one immutable tape, consecutive NAV differences.

    There is exactly one starting canonical book. Later canonical snapshots
    never reset either alternative; stops, costs, runners, cash and runtime
    all remain inside the single production runner invocation.
    """
    from scripts import stock_trading_v2_production_replay as replay
    diagnostics = coverage_diagnostics if coverage_diagnostics is not None else {}
    champion, challenger = cash_edge_policy_pair(candidate)
    members = list(cohort)
    if len(members) != 1:
        diagnostics.update(status="PENDING", reason="awaiting_single_registered_continuous_anchor")
        return []
    member = members[0]
    event = next((dict(row) for row in events if row.get("event_id") == member["source_event_id"]), None)
    if not event or not cash_edge_eligible(event, candidate):
        diagnostics.update(status="PENDING", reason="registered_continuous_anchor_source_missing")
        return []
    protocol = candidate["prospective_protocol"]
    tape = continuous_trial_input(event, replay_frames, protocol=protocol)
    if tape["status"] != "COMPLETE" or not tape.get("bridge_config"):
        diagnostics.update(status="PENDING", reason=tape.get("reason", "native_bridge_config_missing"))
        return []
    market = str(event["market"])
    try:
        pair = replay.compare_policies(tape.get("initial_state") or {}, tape["frames"],
            champion_policy=champion, challenger_policy=challenger,
            bridge_config=tape["bridge_config"], runtime=tape.get("runtime"), markets=[market],
            prospective_initial_basis=True)
    except (ValueError, TypeError, KeyError) as exc:
        diagnostics.update(status="PENDING", reason="continuous_native_replay_invalid", detail=str(exc))
        return []
    if pair.get("formal_evidence_eligible") is not True:
        diagnostics.update(status="PENDING", reason="continuous_portfolio_input_coverage_incomplete",
            champion=(pair.get("champion") or {}).get("diagnostics") or [],
            challenger=(pair.get("challenger") or {}).get("diagnostics") or [])
        return []
    curves = {name: {row["observed_at"]: row for row in pair[name]["curves_by_market"][market]}
              for name in ("champion", "challenger")}
    samples, previous = [], {"champion": 0.0, "challenger": 0.0}
    previous_end = event["decision_at"]
    for index, boundary in enumerate(tape["boundaries"], 1):
        stamp = boundary["frame"]["observed_at"]
        left, right = curves["champion"].get(stamp), curves["challenger"].get(stamp)
        if left is None or right is None:
            diagnostics.update(status="PENDING", reason="continuous_boundary_nav_missing")
            return []
        control = float(left["net_return_percent"]) - previous["champion"]
        alternative = float(right["net_return_percent"]) - previous["challenger"]
        samples.append({"source_group": source_group(event), "source_event_id": event["event_id"],
            "source_event_sha256": event.get("event_sha256"), "decision_at": previous_end,
            "anchor_decision_at": event["decision_at"], "symbol": event["symbol"], "market": market,
            "block_id": f"{member['block_id']}:{index:04d}", "episode_end_at": stamp,
            "sourced_regular_end": boundary["session"]["regular_end"],
            "horizon_completed_sessions": int(protocol["horizon_sessions"]),
            "source_frame_sha256": tape["source_frame_sha256"], "calendar_sha256": tape["calendar_sha256"],
            "champion_net_return_percent": control, "challenger_net_return_percent": alternative,
            "incremental_net_return_percent": alternative - control,
            "benchmark_return_percent": left.get("benchmark_return_percent"),
            "execution_semantics": "continuous_production_lifecycle_v2",
            "production_replay": {"formal_evidence_eligible": True, "input_sha256": pair["input_sha256"]}})
        previous = {"champion": float(left["net_return_percent"]), "challenger": float(right["net_return_percent"])}
        previous_end = stamp
    if samples:
        samples[-1]["continuous_replay"] = pair
    diagnostics.update(status="COMPLETE", paired_windows=len(samples))
    return samples

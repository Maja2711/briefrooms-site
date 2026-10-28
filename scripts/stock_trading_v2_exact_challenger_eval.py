#!/usr/bin/env python3
"""Fresh-holdout evaluator for exact executable Stock Trading v2 Challengers."""
from __future__ import annotations

import argparse
import json
import hashlib
import math
import statistics
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import stock_trading_v2_challenger_eval as evaluator
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_exact_replay as exact
    from scripts import stock_trading_v2_policy_learner as learner
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_challenger_eval as evaluator
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_exact_replay as exact
    import stock_trading_v2_policy_learner as learner


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def _iter(root: Path) -> list[dict[str, Any]]:
    return [_read(path) for path in sorted(root.rglob("*.json"))] if root.exists() else []


def _exact_candidate(challenger: Mapping[str, Any]) -> dict[str, Any] | None:
    production = challenger.get("production_candidate")
    replay = challenger.get("exact_replay")
    if not isinstance(production, Mapping) or not isinstance(replay, Mapping):
        return None
    spec = production.get("deployment_spec")
    if not isinstance(spec, Mapping):
        raise contracts.ContractError("exact Challenger deployment_spec missing")
    result = {
        "horizon_sessions": challenger["horizon_sessions"],
        "deployment_spec": dict(spec),
        "replay_contract": dict(replay),
    }
    if replay.get("adapter") == "cash_edge_v2":
        result["champion_policy_snapshot"] = (challenger.get("champion") or {}).get("policy_snapshot")
        result["champion_policy_sha256"] = (challenger.get("champion") or {}).get("policy_sha256")
        result["prospective_protocol"] = dict(challenger.get("protocol") or {})
    return result


def _immutable(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _read(path) != row:
            raise contracts.ContractError(f"immutable prospective evidence conflict: {path.name}")
        return
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _cash_protocol(challenger: Mapping[str, Any]) -> dict[str, Any]:
    protocol = dict(challenger.get("protocol") or {})
    if contracts.payload_sha256(protocol) != challenger.get("protocol_sha256"):
        raise contracts.ContractError("CASH protocol hash mismatch")
    if protocol.get("schema_version") == exact.CONTINUOUS_PROTOCOL:
        if protocol != exact.continuous_cash_protocol(int(challenger["horizon_sessions"])):
            raise contracts.ContractError("unapproved continuous CASH prospective protocol")
        return protocol
    if (protocol.get("schema_version") != "stock-trading-cash-edge-protocol-v1"
            or int(protocol.get("fixed_paired_n") or 0) < 30
            or int(protocol.get("block_hours") or 0) < 168
            or protocol.get("single_formal_look") is not True
            or protocol.get("lifetime_max_tests") != 24
            or protocol.get("family_id") != "stock-trading-v2-cash-edge-lifetime-v1"
            or not 0 < float(protocol.get("alpha") or 0) <= 0.05 / 24
            or protocol.get("maximum_censored") != 0
            or protocol.get("membership") != "chronological_eligible_episode_before_outcome"
            or protocol.get("outcome_missing") != "WAIT_THEN_TERMINAL_CENSOR_NO_REPLACEMENT"
            or not 0 < int(protocol.get("enrollment_deadline_hours") or 0) <= 24
            or not 0 < int(protocol.get("outcome_deadline_days") or 0) <= 180
            or int(protocol.get("bootstrap_draws") or 0) < 20000
            or int(protocol.get("min_unique_symbols") or 0) < 5
            or int(protocol.get("min_span_days") or 0) < 10
            or not 0 < float(protocol.get("max_single_positive_share") or 0) <= 0.5
            or float(protocol.get("max_drawdown_increase_pct", math.inf)) > 0
            or float(protocol.get("min_mean_incremental_pct", -math.inf)) < 0
            or protocol.get("horizon_sessions") != challenger.get("horizon_sessions")
            or int(protocol.get("horizon_sessions") or 0) not in {1, 2}
            or protocol.get("session_horizon_rule") != "first_sourced_regular_closes_after_decision"
            or protocol.get("execution_semantics") != "production_lifecycle_v1"):
        raise contracts.ContractError("CASH prospective protocol weakens fixed safety limits")
    return protocol


def _formal_admission(row: Mapping[str, Any]) -> bool:
    try:
        from scripts import stock_trading_v2_admission_ledger as ledger
        return ledger.is_formal_observation(row)
    except (ImportError, AttributeError):
        return False


def _trial_receipt(challenger: Mapping[str, Any], receipts: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Read a frozen main-owned receipt; research cannot invent registration."""
    if not receipts:
        return None
    if receipts.get("schema_version") != "stock-trading-component-trials-v1":
        raise contracts.ContractError("production trial receipt schema invalid")
    trials = receipts.get("trials") or {}
    row = trials.get(str(challenger["challenger_id"]))
    if row is None:
        return None
    family = challenger.get("test_family") or {}
    if (not isinstance(row, Mapping) or row.get("challenger_id") != challenger["challenger_id"]
            or row.get("challenger_sha256") != challenger["challenger_sha256"]
            or row.get("family_id") != "stock-trading-v2-cash-edge-lifetime-v1"
            or row.get("test_index") != family.get("test_index")
            or row.get("protocol_sha256") != challenger["protocol_sha256"]
            or row.get("policy_sha256") != (challenger.get("champion") or {}).get("policy_sha256")):
        raise contracts.ContractError("production trial receipt binding mismatch")
    exact.parse_dt(row.get("registered_at"))
    return dict(row)


def freeze_cohort(challenger: Mapping[str, Any], *, events: list[dict[str, Any]], admissions: list[dict[str, Any]],
                  outcomes: list[dict[str, Any]], cohort_root: Path, now: datetime,
                  trial_receipts: Mapping[str, Any] | None = None,
                  replay_frames: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Append members before results. Missing/late members stay in the sample.

    No outcome-ranked filtering, no replacement, and no post-result insertion
    of an earlier decision into an already frozen chronological cohort.
    """
    protocol = _cash_protocol(challenger)
    if protocol["schema_version"] == exact.CONTINUOUS_PROTOCOL:
        return _freeze_continuous_cohort(challenger, events=events, admissions=admissions, outcomes=outcomes,
            cohort_root=cohort_root, now=now, trial_receipts=trial_receipts, replay_frames=replay_frames or [])
    candidate = _exact_candidate(challenger)
    assert candidate is not None
    receipt = _trial_receipt(challenger, trial_receipts)
    contract = {"challenger_id": challenger["challenger_id"], "challenger_sha256": challenger["challenger_sha256"],
                "protocol_sha256": challenger["protocol_sha256"], "created_at": challenger["validation_start_at"]}
    if receipt is None or exact.parse_dt(receipt["registered_at"]) > now:
        # No contract/member write before main reserves the experiment. A
        # missing handoff receipt is waiting, never a failed statistical look.
        cohort = {**contract, "members": [], "required_n": int(protocol["fixed_paired_n"]),
                  "membership_closed": False, "awaiting_production_trial_receipt": True}
        cohort["cohort_sha256"] = contracts.payload_sha256(cohort)
        return cohort
    root = cohort_root / str(challenger["challenger_id"])
    contract["production_trial_receipt"] = receipt
    _immutable(root / "contract.json", contract)
    members = [_read(path) for path in sorted((root / "members").glob("*.json"))] if (root / "members").exists() else []
    events_by_id = {str(row.get("event_id")): row for row in events}
    admissions_by_id = {str(row.get("source_event_id")): row for row in admissions if _formal_admission(row)}
    for index, member in enumerate(members, start=1):
        body = dict(member)
        digest = body.pop("member_sha256", None)
        source = events_by_id.get(str(member.get("source_event_id")))
        observed = admissions_by_id.get(str(member.get("source_event_id")))
        if (member.get("index") != index or digest != contracts.payload_sha256(body)
                or (source is not None and member.get("event_sha256") != contracts.payload_sha256(source))
                or (observed is not None and member.get("admission_sha256") != contracts.payload_sha256(observed))
                or (index > 1 and exact.parse_dt(member["decision_at"]) < exact.parse_dt(members[index - 2]["block_until"]))):
            raise contracts.ContractError("immutable prospective cohort member integrity mismatch")
    outcome_ids = {str(row.get("source_event_id")) for row in outcomes}
    start = max(exact.parse_dt(challenger["validation_start_at"]), exact.parse_dt(receipt["registered_at"]))
    used_groups = {str(row["source_group"]) for row in members}
    blocked_until = max((exact.parse_dt(row["block_until"]) for row in members), default=start)
    native_events = exact.native_cash_admission_events(candidate, replay_frames or [], as_of=now)
    for native in sorted(native_events.values(), key=lambda row: (exact.parse_dt(row["decision_at"]), str(row["event_id"]))):
        if len(members) >= int(protocol["fixed_paired_n"]):
            break
        event_id = str(native["event_id"])
        decision_at = exact.parse_dt(native["decision_at"])
        if decision_at < start or decision_at < blocked_until or decision_at > now:
            continue
        if exact.source_group(native) in used_groups:
            continue
        context = (native.get("candidate_state") or {}).get("decision_context") or {}
        if context.get("champion_manifest") != (challenger.get("champion") or {}).get("manifest_snapshot"):
            continue
        event = events_by_id.get(event_id)
        observed = admissions_by_id.get(event_id)
        if event is None or observed is None or contracts.event_without_runtime_fields(event) != contracts.event_without_runtime_fields(native):
            # Do not replace an earlier main-observed eligible episode with a
            # later convenient research row while its source/admission waits.
            break
        late = (event_id in outcome_ids or now - decision_at > timedelta(hours=int(protocol["enrollment_deadline_hours"])))
        # A native close can reveal the formal horizon before the descriptive
        # next-session outcome writer runs. It cannot become a fresh member.
        completed_tape = exact.production_episode_input(event, replay_frames or [],
            horizon_sessions=int(challenger["horizon_sessions"]))
        if completed_tape is not None:
            terminal = completed_tape["frames"][-1]
            sourced_close = ((terminal.get("market_sessions") or {}).get(event["market"]) or {}).get("regular_end")
            if sourced_close is not None and exact.parse_dt(sourced_close) <= now:
                late = True
        member = {
            "index": len(members) + 1, "source_event_id": event_id, "source_group": exact.source_group(event),
            "event_sha256": contracts.payload_sha256(event), "admission_sha256": contracts.payload_sha256(observed),
            "decision_at": event["decision_at"], "frozen_at": now.isoformat().replace("+00:00", "Z"),
            "block_until": (decision_at + timedelta(hours=int(protocol["block_hours"]))).isoformat().replace("+00:00", "Z"),
            "block_id": f"{event.get('market')}:{event_id}",
            "horizon_sessions": int(challenger["horizon_sessions"]),
            "outcome_deadline_at": (decision_at + timedelta(days=int(protocol["outcome_deadline_days"]))).isoformat().replace("+00:00", "Z"),
            "enrollment_status": "CENSORED_LATE_ENROLLMENT" if late else "FROZEN_BEFORE_OUTCOME",
            "policy_sha256": context["policy_sha256"], "portfolio_sha256": context.get("portfolio_sha256"),
        }
        member["member_sha256"] = contracts.payload_sha256(member)
        _immutable(root / "members" / f"{member['index']:04d}.json", member)
        members.append(member)
        used_groups.add(member["source_group"])
        blocked_until = exact.parse_dt(member["block_until"])
    cohort = {**contract, "members": members, "required_n": int(protocol["fixed_paired_n"]),
              "membership_closed": len(members) == int(protocol["fixed_paired_n"])}
    cohort["cohort_sha256"] = contracts.payload_sha256(cohort)
    if cohort["membership_closed"]:
        _immutable(root / "cohort.json", cohort)
    return cohort


def _freeze_continuous_cohort(challenger: Mapping[str, Any], *, events: list[dict[str, Any]],
                              admissions: list[dict[str, Any]], outcomes: list[dict[str, Any]],
                              cohort_root: Path, now: datetime,
                              trial_receipts: Mapping[str, Any] | None,
                              replay_frames: list[dict[str, Any]]) -> dict[str, Any]:
    """One pre-outcome anchor preregisters every later calendar observation.

    Individual windows need no late research enrollment: their exact future
    membership was fixed by this main-receipted anchor and its native calendar.
    Old v1 member files and terminal reports never pass through this path.
    """
    protocol = _cash_protocol(challenger)
    receipt = _trial_receipt(challenger, trial_receipts)
    contract = {"challenger_id": challenger["challenger_id"], "challenger_sha256": challenger["challenger_sha256"],
                "protocol_sha256": challenger["protocol_sha256"], "created_at": challenger["validation_start_at"]}
    root = cohort_root / str(challenger["challenger_id"])
    if receipt is None or exact.parse_dt(receipt["registered_at"]) > now:
        result = {**contract, "members": [], "required_n": 1, "membership_closed": False,
                  "awaiting_production_trial_receipt": True}
        result["cohort_sha256"] = contracts.payload_sha256(result)
        return result
    contract["production_trial_receipt"] = receipt
    _immutable(root / "contract.json", contract)
    members = [_read(path) for path in sorted((root / "members").glob("*.json"))] if (root / "members").exists() else []
    by_id = {str(row.get("event_id")): row for row in events}
    baselines = {str(row.get("source_event_id")): row for row in admissions if _formal_admission(row)}
    if len(members) > 1:
        raise contracts.ContractError("continuous cohort has more than one immutable anchor")
    if members:
        member = members[0]
        body = dict(member)
        stored = body.pop("member_sha256", None)
        event, admission = by_id.get(member["source_event_id"]), baselines.get(member["source_event_id"])
        if (stored != contracts.payload_sha256(body) or member.get("index") != 1
                or (event is not None and member["event_sha256"] != contracts.payload_sha256(event))
                or (admission is not None and member["admission_sha256"] != contracts.payload_sha256(admission))):
            raise contracts.ContractError("immutable continuous anchor integrity mismatch")
    else:
        candidate = _exact_candidate(challenger)
        start = max(exact.parse_dt(challenger["validation_start_at"]), exact.parse_dt(receipt["registered_at"]))
        native = exact.native_cash_admission_events(candidate, replay_frames, as_of=now)
        eligible = sorted((row for row in native.values() if exact.parse_dt(row["decision_at"]) >= start
                           and ((row.get("candidate_state") or {}).get("decision_context") or {}).get("champion_manifest")
                               == (challenger.get("champion") or {}).get("manifest_snapshot")),
                          key=lambda row: (exact.parse_dt(row["decision_at"]), str(row["event_id"])))
        if eligible:
            first = eligible[0]
            event, admission = by_id.get(first["event_id"]), baselines.get(first["event_id"])
            # An absent/mutated first source blocks the whole future tape.
            if event is not None and admission is not None and contracts.event_without_runtime_fields(event) == contracts.event_without_runtime_fields(first):
                decision = exact.parse_dt(event["decision_at"])
                known_result = exact.production_episode_input(event, replay_frames, horizon_sessions=1)
                late = (now < decision or now - decision > timedelta(hours=int(protocol["enrollment_deadline_hours"]))
                        or event["event_id"] in {row.get("source_event_id") for row in outcomes})
                if known_result is not None:
                    close = ((known_result["frames"][-1].get("market_sessions") or {}).get(event["market"]) or {}).get("regular_end")
                    late = late or (close is not None and exact.parse_dt(close) <= now)
                member = {"index": 1, "source_event_id": event["event_id"], "source_group": exact.source_group(event),
                    "event_sha256": contracts.payload_sha256(event), "admission_sha256": contracts.payload_sha256(admission),
                    "decision_at": event["decision_at"], "frozen_at": now.isoformat().replace("+00:00", "Z"),
                    "block_until": event["decision_at"], "block_id": f"{event['market']}:{event['event_id']}:continuous",
                    "horizon_sessions": int(challenger["horizon_sessions"]),
                    "fixed_regular_sessions": int(protocol["fixed_regular_sessions"]),
                    "outcome_deadline_at": (decision + timedelta(days=int(protocol["outcome_deadline_days"]))).isoformat().replace("+00:00", "Z"),
                    "enrollment_status": "CENSORED_LATE_ENROLLMENT" if late else "FROZEN_BEFORE_OUTCOME",
                    "policy_sha256": (event["candidate_state"]["decision_context"])["policy_sha256"],
                    "portfolio_sha256": (event["candidate_state"]["decision_context"]).get("portfolio_sha256")}
                member["member_sha256"] = contracts.payload_sha256(member)
                _immutable(root / "members" / "0001.json", member)
                members.append(member)
    result = {**contract, "members": members, "required_n": 1, "membership_closed": len(members) == 1}
    result["cohort_sha256"] = contracts.payload_sha256(result)
    if result["membership_closed"]:
        _immutable(root / "cohort.json", result)
    return result


def _code_unchanged(challenger: Mapping[str, Any]) -> bool:
    fingerprints = challenger.get("code_fingerprints") or {}
    return set(exact.CASH_FINGERPRINT_FILES) == set(fingerprints) and all(
        (Path(__file__).parent / name).is_file() and hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() == sha
        for name, sha in fingerprints.items()
    )


def formal_metrics(samples: list[dict[str, Any]], protocol: Mapping[str, Any], *, seed: str) -> dict[str, Any]:
    if protocol.get("schema_version") == exact.CONTINUOUS_PROTOCOL:
        return continuous_formal_metrics(samples, protocol, seed=seed)
    values = [float(row["incremental_net_return_percent"]) for row in samples]
    n = len(values)
    positives = sum(value > 0 for value in values)
    # Each member is a separated prospective episode. In addition to the
    # block bootstrap mean, an exact one-sided paired sign test spends the
    # Bonferroni-adjusted lifetime alpha. Zero deltas count against improvement.
    sign_p = sum(math.comb(n, index) for index in range(positives, n + 1)) / (2 ** n) if n else 1.0
    lower = evaluator._bootstrap_lower_bound(values, confidence=1.0 - float(protocol["alpha"]),
                                              draws=int(protocol["bootstrap_draws"]), seed_text=seed)
    positive_values = [max(0.0, value) for value in values]
    concentration = max(positive_values) / sum(positive_values) if sum(positive_values) else 1.0
    dates = sorted(exact.parse_dt(row["decision_at"]) for row in samples)
    mean = statistics.mean(values) if values else 0.0
    midpoint = max(1, n // 2)
    checks = {
        "fixed_independent_episodes": n == int(protocol["fixed_paired_n"]) and len({row["block_id"] for row in samples}) == n,
        "positive_mean": mean > float(protocol["min_mean_incremental_pct"]),
        "adjusted_bootstrap_floor": lower is not None and lower > 0,
        "family_adjusted_sign_test": sign_p <= float(protocol["alpha"]),
        "unique_symbols": len({row["symbol"] for row in samples}) >= int(protocol["min_unique_symbols"]),
        "span_days": bool(dates) and (dates[-1] - dates[0]).days >= int(protocol["min_span_days"]),
        "concentration": concentration <= float(protocol["max_single_positive_share"]),
        "time_halves": bool(values) and statistics.mean(values[:midpoint]) > 0 and bool(values[midpoint:]) and statistics.mean(values[midpoint:]) > 0,
        "drawdown_non_inferiority": all(float(row["challenger_max_drawdown_percent"]) <= float(row["champion_max_drawdown_percent"]) + float(protocol["max_drawdown_increase_pct"]) for row in samples),
        "benchmark_present": all(row.get("benchmark_return_percent") is not None for row in samples),
        "production_lifecycle_net": all(row.get("execution_semantics") == "production_lifecycle_v1" and (row.get("production_replay") or {}).get("formal_evidence_eligible") is True for row in samples),
    }
    return {"paired_n": n, "required_n": int(protocol["fixed_paired_n"]), "mean_incremental_net_return_percent": mean,
            "bootstrap_lower_bound_percent": lower, "paired_sign_p": sign_p,
            "maximum_single_positive_contribution_share": concentration, "checks": checks, "formal_pass": all(checks.values())}


def continuous_formal_metrics(samples: list[dict[str, Any]], protocol: Mapping[str, Any], *, seed: str) -> dict[str, Any]:
    """One weekly bounded-mean betting test, plus separate economic gates.

    For x_w in [-1,1] and the declared null E[x_w|past] <= 0,
    E[1 + .9*x_w | past] <= 1. Its nonnegative product starts at one;
    Markov/Ville gives type-I probability <= alpha at threshold 1/alpha.
    This is not an iid sign test or a claim about an unconditional mean.
    """
    from scripts import stock_trading_portfolio as portfolio
    pair = (samples[-1].get("continuous_replay") or {}) if samples else {}
    market = str(samples[0]["market"]) if samples else "US"
    timezone_local = portfolio.MARKET_TZ[market]
    def week_key(stamp: str) -> str:
        day = exact.parse_dt(stamp).astimezone(timezone_local).date()
        return (day - timedelta(days=day.weekday())).isoformat()
    weeks: dict[str, float] = {}
    contribution: dict[str, float] = {}
    exposure_weeks: dict[str, list[str]] = {}
    if samples and pair:
        beginning = week_key(samples[0]["anchor_decision_at"])
        ending = week_key(samples[-1]["sourced_regular_end"])
        day, last = datetime.fromisoformat(beginning), datetime.fromisoformat(ending)
        while day <= last:
            weeks[day.date().isoformat()] = 0.0
            day += timedelta(days=7)
        curves = {name: {row["observed_at"]: row for row in pair[name]["curves_by_market"][market]}
                  for name in ("champion", "challenger")}
        previous = 0.0
        for stamp in sorted(set(curves["champion"]) & set(curves["challenger"]), key=exact.parse_dt):
            current = float(curves["challenger"][stamp]["net_return_percent"]) - float(curves["champion"][stamp]["net_return_percent"])
            key = week_key(stamp)
            # Delayed post-close receipts still belong to the sourced final
            # session's week, never a new pseudo-observation after endpoint.
            key = min(key, ending)
            weeks[key] = weeks.get(key, 0.0) + current - previous
            previous = current
        for name, sign in (("champion", -1.0), ("challenger", 1.0)):
            book = pair[name]["final_state"]
            rows = portfolio.open_positions(book, market) + list(portfolio.market_state(book, market).get("closed_positions") or [])
            # Every inherited mark/cost cancels. Only prospective contribution
            # may support a symbol's positive margin-policy benefit.
            details = pair[name].get("period_position_contributions_by_market", {}).get(market)
            if details is None:
                # Runtime versions expose prospective totals in the final
                # metrics, but full attribution requires explicit details.
                details = []
            by_id = {str(row.get("position_id")): row for row in rows}
            for detail in details:
                position = by_id.get(str(detail.get("position_id"))) or {}
                symbol = str(position.get("symbol") or "")
                value = float(detail.get("net_pnl_amount") or 0.0) * sign
                contribution[symbol] = contribution.get(symbol, 0.0) + value
                opened = str(position.get("opened_at") or samples[0]["anchor_decision_at"])
                closed = str(position.get("closed_at") or samples[-1]["sourced_regular_end"])
                exposure_weeks.setdefault(symbol, []).extend(key for key in weeks if week_key(opened) <= key <= week_key(closed))
    values = list(weeks.values())
    scale, fraction = float(protocol["clip_scale_percent"]), float(protocol["bet_fraction"])
    clipped = [max(-1.0, min(1.0, value / scale)) for value in values]
    e_value = math.prod(1.0 + fraction * value for value in clipped)
    positive = sum(max(0.0, value) for value in values)
    week_share = max([max(0.0, value) for value in values], default=0.0) / positive if positive else 1.0
    benefits = [max(0.0, value) for value in contribution.values()]
    symbol_share = max(benefits, default=0.0) / sum(benefits) if sum(benefits) else 1.0
    # Preserve local dependence in a descriptive two-week circular moving
    # block sensitivity. It spends no alpha and cannot grant a formal PASS.
    bootstrap_lower = None
    if values:
        rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest(), 16))
        draws, n, width = [], len(values), int(protocol["bootstrap_block_weeks"])
        for _ in range(int(protocol["bootstrap_draws"])):
            resampled = []
            while len(resampled) < n:
                first = rng.randrange(n)
                resampled.extend(values[(first + offset) % n] for offset in range(width))
            draws.append(statistics.mean(resampled[:n]))
        draws.sort()
        bootstrap_lower = draws[int(0.05 * (len(draws) - 1))]
    result = (pair.get("market_results") or {}).get(market) or {}
    control, alternative = result.get("champion") or {}, result.get("challenger") or {}
    total = float(result.get("net_delta_percent") or 0.0)
    mid = max(1, len(values) // 2)
    checks = {
        "fixed_consecutive_session_windows": len(samples) == int(protocol["fixed_paired_n"]) and len({row["block_id"] for row in samples}) == len(samples),
        "continuous_single_pair": bool(pair) and pair.get("formal_evidence_eligible") is True and len({row["production_replay"]["input_sha256"] for row in samples}) == 1,
        "weekly_conditional_mean_e_value": bool(values) and e_value >= 1.0 / float(protocol["alpha"]),
        "calendar_week_coverage": len(weeks) >= int(protocol["min_calendar_week_groups"]),
        "positive_unclipped_net": total > float(protocol["min_mean_incremental_pct"]),
        "positive_unclipped_time_halves": bool(values) and sum(values[:mid]) > 0 and bool(values[mid:]) and sum(values[mid:]) > 0,
        "unique_differential_symbols": len([value for key, value in contribution.items() if key and abs(value) > 1e-12]) >= int(protocol["min_unique_symbols"]),
        "symbol_concentration": symbol_share <= float(protocol["max_single_positive_share"]),
        "week_concentration": week_share <= float(protocol["max_single_positive_share"]),
        "full_curve_drawdown_non_inferiority": bool(result) and float(alternative.get("max_drawdown_percent", math.inf)) <= float(control.get("max_drawdown_percent", -math.inf)) + float(protocol["max_drawdown_increase_pct"]),
        "benchmark_present": bool(samples) and all(row.get("benchmark_return_percent") is not None for row in samples),
        "span_days": bool(samples) and (exact.parse_dt(samples[-1]["sourced_regular_end"]) - exact.parse_dt(samples[0]["anchor_decision_at"])).days >= int(protocol["min_span_days"]),
        "preregistered_calendar": bool(samples) and len({row.get("calendar_sha256") for row in samples}) == 1 and bool(samples[0].get("calendar_sha256")),
    }
    return {"paired_n": len(samples), "required_n": int(protocol["fixed_paired_n"]),
            "fixed_regular_sessions": int(protocol["fixed_regular_sessions"]),
            "weekly_net_delta_percent": [{"week_start": key, "net_delta_percent": value, "bounded_score": clipped[index]} for index, (key, value) in enumerate(weeks.items())],
            "calendar_week_groups": len(weeks), "e_value": e_value, "e_value_threshold": 1.0 / float(protocol["alpha"]),
            "conditional_null": protocol["conditional_null"], "iid_or_unconditional_mean_claim": False,
            "total_incremental_net_return_percent": total, "symbol_incremental_net_pnl_amount": contribution,
            "maximum_symbol_positive_share": symbol_share, "maximum_week_positive_share": week_share,
            "shared_exposure_week_groups": {key: sorted(set(value)) for key, value in exposure_weeks.items()},
            "descriptive_moving_block_bootstrap": {"role": "DESCRIPTIVE_SENSITIVITY_ONLY", "block_weeks": int(protocol["bootstrap_block_weeks"]), "lower_95_percent_weekly_mean": bootstrap_lower},
            "checks": checks, "formal_pass": all(checks.values())}


def evaluate_cash(challenger: Mapping[str, Any], *, events: list[dict[str, Any]], admissions: list[dict[str, Any]],
                  outcomes: list[dict[str, Any]], cohort_root: Path, now: datetime,
                  replay_frames: list[dict[str, Any]] | None = None,
                  trial_receipts: Mapping[str, Any] | None = None) -> dict[str, Any]:
    learner.validate_challenger(challenger)
    protocol = _cash_protocol(challenger)
    if challenger.get("test_family"):
        learner.validate_test_family(challenger["test_family"], challenger_id=str(challenger["challenger_id"]), protocol_sha256=challenger["protocol_sha256"])
    cohort = freeze_cohort(challenger, events=events, admissions=admissions, outcomes=outcomes,
                           cohort_root=cohort_root, now=now, trial_receipts=trial_receipts, replay_frames=replay_frames)
    candidate = _exact_candidate(challenger)
    assert candidate is not None
    # Future receipt files visible in a copied tree are not available evidence
    # at this evaluation clock. Old v1 replay semantics remain unchanged.
    frames_for_evaluation = replay_frames or []
    continuous = protocol["schema_version"] == exact.CONTINUOUS_PROTOCOL
    if continuous:
        frames_for_evaluation = [row for row in frames_for_evaluation
            if exact.parse_dt((row.get("frame") or row)["observed_at"]) <= now
            and exact.parse_dt((row.get("frame") or row)["recorded_at"]) <= now]
    replay_diagnostics: dict[str, Any] = {}
    if continuous:
        samples = exact.collect_continuous_cash_samples(candidate=candidate, events=events,
            cohort=cohort["members"], replay_frames=frames_for_evaluation, coverage_diagnostics=replay_diagnostics)
    else:
        samples = exact.collect_cash_edge_samples(candidate=candidate, events=events, admissions=admissions, outcomes=outcomes,
                                                 cohort=cohort["members"], replay_frames=frames_for_evaluation)
    # Every identity remains frozen even if its outcome is absent. A timeout
    # terminates the experiment; a later result can never replace that member.
    waiting_receipt = cohort.get("awaiting_production_trial_receipt") is True
    failed = (not waiting_receipt and (not _code_unchanged(challenger) or not challenger.get("test_family")
              or any(row["enrollment_status"] != "FROZEN_BEFORE_OUTCOME" for row in cohort["members"])))
    sample_ids = {row["source_event_id"] for row in samples}
    failed = failed or any(now > exact.parse_dt(row["outcome_deadline_at"]) and row["source_event_id"] not in sample_ids for row in cohort["members"])
    terminal = failed or (cohort["membership_closed"] and len(samples) == int(protocol["fixed_paired_n"]))
    metrics = formal_metrics(samples, protocol, seed=str(challenger["challenger_id"])) if terminal and not failed else {
        "paired_n": len(samples), "required_n": int(protocol["fixed_paired_n"]), "formal_pass": False,
        "frozen_members": len(cohort["members"]), "checks": {"code_and_protocol_intact": not failed},
    }
    if waiting_receipt:
        metrics["awaiting_production_trial_receipt"] = True
    if continuous and not terminal and cohort["members"]:
        metrics["replay_coverage"] = replay_diagnostics
        anchor = next((row for row in events if row["event_id"] == cohort["members"][0]["source_event_id"]), None)
        if anchor is not None:
            coverage = exact.continuous_trial_input(anchor, frames_for_evaluation, protocol=protocol, as_of=now)
            metrics["coverage"] = {key: value for key, value in coverage.items() if key not in {"frames", "boundaries", "initial_state", "bridge_config", "runtime", "schedule"}}
    passed = terminal and not failed and metrics.get("formal_pass") is True
    state = "RESEARCH_PASS_AWAITING_MANUAL_ENABLEMENT" if passed else "FORMAL_HOLDOUT_FAIL" if terminal else "COLLECTING_FRESH_HOLDOUT"
    production = dict(challenger["production_candidate"])
    proof = {
        "schema_version": "stock-trading-cash-edge-proof-v1", "challenger_id": challenger["challenger_id"],
        "challenger_sha256": challenger["challenger_sha256"], "deployment_sha256": production["deployment_sha256"],
        "champion_policy_sha256": (challenger.get("champion") or {}).get("policy_sha256"),
        "protocol": protocol, "protocol_sha256": challenger["protocol_sha256"],
        "cohort": cohort, "cohort_sha256": cohort["cohort_sha256"],
        "code_fingerprints": challenger.get("code_fingerprints") or {}, "family": challenger.get("test_family") or {},
        "source_refs": cohort["members"], "paired_samples": samples, "metrics": metrics, "terminal": terminal,
    }
    proof["proof_sha256"] = contracts.payload_sha256(proof)
    report = {
        "schema_version": evaluator.SCHEMA_VERSION, "challenger_id": challenger["challenger_id"],
        "component": challenger["component"], "horizon_sessions": challenger["horizon_sessions"],
        "validation_start_at": challenger["validation_start_at"], "generated_at": now.isoformat().replace("+00:00", "Z"),
        "state": state, "metrics": metrics, "formal_samples": samples, "formal_proof": proof,
        "evaluated_production_candidate": {key: production[key] for key in ("component", "deployment_id", "base_manifest_revision", "base_component_version", "deployment_sha256")},
        "exact_replay_adapter": "cash_edge_v2",
        "governance": {"production_decision_influence": False, "automatic_policy_writeback": False,
                       "production_promotion_enabled": False, "single_formal_look": True},
    }
    report["evaluated_production_candidate"]["execution_semantics"] = protocol["execution_semantics"]
    report["evaluation_sha256"] = contracts.payload_sha256(report)
    evaluator.validate_evaluation(report)
    return report


def evaluate_one(challenger: Mapping[str, Any], *, events: list[dict[str, Any]], admissions: list[dict[str, Any]], outcomes: list[dict[str, Any]]) -> dict[str, Any] | None:
    learner.validate_challenger(challenger)
    replay_candidate = _exact_candidate(challenger)
    if replay_candidate is None:
        return None
    start = exact.parse_dt(challenger["validation_start_at"])
    samples = exact.collect_entry_threshold_samples(candidate=replay_candidate, events=events, admissions=admissions, outcomes=outcomes, after=start)
    report = evaluator.evaluate_samples(challenger, samples)
    production = dict(challenger["production_candidate"])
    report["evaluated_production_candidate"] = {
        "component": production["component"],
        "deployment_id": production["deployment_id"],
        "base_manifest_revision": production["base_manifest_revision"],
        "base_component_version": production["base_component_version"],
        "deployment_sha256": production["deployment_sha256"],
        "execution_semantics": "exact_shadow_replay",
    }
    report["exact_replay_adapter"] = str((challenger.get("exact_replay") or {}).get("adapter") or "")
    body = dict(report)
    body.pop("evaluation_sha256", None)
    report["evaluation_sha256"] = contracts.payload_sha256(body)
    evaluator.validate_evaluation(report)
    return report


def run(*, challenger_root: Path, experience_root: Path, admission_root: Path, outcome_root: Path, output_root: Path,
        replay_frame_root: Path | None = None, now: datetime | None = None,
        trial_receipts: Mapping[str, Any] | None = None, freeze_cohorts_only: bool = False) -> dict[str, Any]:
    events, admissions, outcomes = _iter(experience_root), _iter(admission_root), _iter(outcome_root)
    output_root.mkdir(parents=True, exist_ok=True)
    exact_count = pass_count = frozen_count = 0
    replay_frames = _iter(replay_frame_root) if replay_frame_root else []
    for path in sorted(challenger_root.glob("*.json")) if challenger_root.exists() else []:
        challenger = _read(path)
        output_path = output_root / path.name
        if output_path.exists():
            previous = _read(output_path)
            evaluator.validate_evaluation(previous)
            if previous.get("state") != "COLLECTING_FRESH_HOLDOUT":
                exact_count += 1
                pass_count += int((previous.get("metrics") or {}).get("formal_pass") is True)
                continue  # Closed verdict and cohort are permanently immutable.
        if (challenger.get("exact_replay") or {}).get("adapter") == "cash_edge_v2":
            if freeze_cohorts_only:
                learner.validate_challenger(challenger)
                cohort = freeze_cohort(challenger, events=events, admissions=admissions, outcomes=outcomes,
                                       cohort_root=output_root / ".cohorts", now=now or datetime.now(timezone.utc),
                                       trial_receipts=trial_receipts, replay_frames=replay_frames)
                frozen_count += len(cohort["members"])
                exact_count += 1
                continue
            report = evaluate_cash(challenger, events=events, admissions=admissions, outcomes=outcomes,
                                   cohort_root=output_root / ".cohorts", now=now or datetime.now(timezone.utc),
                                   replay_frames=replay_frames, trial_receipts=trial_receipts)
        else:
            if freeze_cohorts_only:
                continue
            report = evaluate_one(challenger, events=events, admissions=admissions, outcomes=outcomes)
        if report is None:
            continue
        exact_count += 1
        if (report.get("metrics") or {}).get("formal_pass") is True:
            pass_count += 1
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"schema_version": "stock-trading-v2-exact-eval-run-v1", "exact_challengers": exact_count,
            "formal_passes": pass_count, "frozen_members": frozen_count,
            "freeze_cohorts_only": freeze_cohorts_only, "production_decision_influence": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challengers", type=Path, required=True)
    parser.add_argument("--experience-root", type=Path, required=True)
    parser.add_argument("--admission-root", type=Path, required=True)
    parser.add_argument("--outcome-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--replay-frame-root", type=Path, default=Path(__file__).resolve().parents[1] / "data/investments/stock_trading_v2_production_replay_frames")
    parser.add_argument("--trial-receipts", type=Path, help="Read-only snapshot of main-owned lifetime trial registrations")
    parser.add_argument("--freeze-cohorts-only", action="store_true", help="Freeze new members after admissions, before outcome settlement; never evaluate")
    args = parser.parse_args()
    result = run(challenger_root=args.challengers, experience_root=args.experience_root, admission_root=args.admission_root, outcome_root=args.outcome_root, output_root=args.output_root, replay_frame_root=args.replay_frame_root,
                 trial_receipts=_read(args.trial_receipts) if args.trial_receipts and args.trial_receipts.exists() else None,
                 freeze_cohorts_only=args.freeze_cohorts_only)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

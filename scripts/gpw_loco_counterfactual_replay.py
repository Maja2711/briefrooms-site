#!/usr/bin/env python3
"""PR507 — prospective Leave-One-Component-Out (LOCO) replay for GPW Daily.

The production decision remains authoritative.  This module creates a separate
research-only snapshot shortly after the final GPW decision, verifies that an
independent deterministic replay reproduces the published mandatory selector,
and only then permits component ablations.

Supported v1 components are those whose effect can be recomputed from the
prospectively captured evaluated-candidate state without asking an LLM or using
future information:

- catalyst (the mandatory selector's neutral catalyst baseline),
- relative_momentum,
- volume_liquidity,
- market_context,
- risk_reward,
- historical_expectancy,
- opening_confirmation,
- expected_value.

Hard admission gates are intentionally UNSUPPORTED in v1.  Candidates rejected
before the final evaluated set do not have a complete downstream state, so
pretending to replay liquidity/data/risk removal would be post-outcome
reconstruction.  A future version may capture shadow continuation through those
gates prospectively.

Marginal value follows the project convention:

    MV(component) = Utility(FULL) - Utility(WITHOUT component)

Positive MV means the component helped the realised T+2 economic result;
negative MV means the system would have done better in that local counterfactual
without it.  This is a local structural counterfactual conditional on a
prospectively captured, FULL-replay-equivalent state — not a universal causal
claim and never an automatic production change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import tempfile
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

try:
    from scripts import daily_stock_core as core
    from scripts import gpw_daily_pick as gpw
    from scripts import gpw_expected_value as ev
    from scripts import gpw_mandatory_daily as mandatory
    from scripts import gpw_market_data as market
    from scripts import gpw_opening_confirmation as opening
    from scripts import gpw_provider_v2 as provider
    from scripts import gpw_rejected_candidate_outcomes as outcomes
except ModuleNotFoundError:  # pragma: no cover - direct execution from scripts/
    import daily_stock_core as core
    import gpw_daily_pick as gpw
    import gpw_expected_value as ev
    import gpw_mandatory_daily as mandatory
    import gpw_market_data as market
    import gpw_opening_confirmation as opening
    import gpw_provider_v2 as provider
    import gpw_rejected_candidate_outcomes as outcomes

ROOT = Path(__file__).resolve().parents[1]
STORE_DIR = ROOT / "data/investments/loco/gpw"
SNAPSHOT_DIR = STORE_DIR / "snapshots"
OUTPUT_PATH = STORE_DIR / "leave_one_component_out_replay.json"
SNAPSHOT_SCHEMA = "gpw-loco-prospective-snapshot-v1"
REPLAY_SCHEMA = "gpw-loco-counterfactual-replay-v1"
WARSAW = ZoneInfo("Europe/Warsaw")
MAX_CAPTURE_DELAY_SECONDS = 30 * 60
SCORE_TOLERANCE = 0.011
PRICE_TOLERANCE = 0.021
EPSILON = 1e-9

BASE_COMPONENTS = (
    "catalyst",
    "relative_momentum",
    "volume_liquidity",
    "market_context",
    "risk_reward",
    "historical_expectancy",
)
QUANT_COMPONENTS = (
    "relative_momentum",
    "volume_liquidity",
    "market_context",
    "risk_reward",
    "historical_expectancy",
)
SUPPORTED_COMPONENTS = BASE_COMPONENTS + ("opening_confirmation", "expected_value")
UNSUPPORTED_COMPONENTS = {
    "historical_freshness": "candidate can be stopped before a complete downstream decision state exists",
    "market_coverage": "market-level hard gate changes whether the decision is allowed at all",
    "minimum_liquidity": "liquidity-rejected candidates are not prospectively evaluated through opening and EV",
    "finite_atr_and_price": "invalid geometry has no valid counterfactual trade plan",
    "execution_freshness": "failed execution quotes do not have a valid frozen execution state",
    "bounded_published_risk": "risk-rejected candidates are not prospectively evaluated through the complete final selector",
    "expected_value_history_quality": "EV-unready candidates are not part of the production evaluated set",
    "independent_review": "mandatory final selector v1 does not use the legacy Gemini reviewer as a ranking component",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        temporary = Path(handle.name)
    temporary.replace(path)


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=WARSAW)
    return parsed.astimezone(timezone.utc)


def _close(a: Any, b: Any, tolerance: float) -> bool:
    left, right = _finite(a), _finite(b)
    if left is None or right is None:
        return left is None and right is None
    return abs(left - right) <= tolerance


def _weighted_score(scores: Mapping[str, Any], weights: Mapping[str, Any], *, remove: Optional[str] = None) -> float:
    active = [(name, float(weight)) for name, weight in weights.items() if name != remove]
    denominator = sum(weight for _, weight in active)
    if denominator <= 0.0:
        raise ValueError("LOCO score has no positive remaining weight")
    numerator = 0.0
    for name, weight in active:
        value = _finite(scores.get(name))
        if value is None:
            raise ValueError(f"missing frozen score for {name}")
        numerator += value * weight
    return core.round2(numerator / denominator)


def _quant_weights(weights: Mapping[str, Any]) -> dict[str, float]:
    return {name: float(weights[name]) for name in QUANT_COMPONENTS if name in weights}


def _candidate_sort_key(candidate: Mapping[str, Any]) -> tuple[float, float, float]:
    return (
        float(candidate.get("final_score") or 0.0),
        float(candidate.get("opening_adjusted_score") or 0.0),
        float(candidate.get("quant_pre_score") or 0.0),
    )


def _plan_from_candidate(candidate: Mapping[str, Any], *, remove_component: Optional[str] = None) -> dict[str, Any]:
    snapshot = candidate.get("market_snapshot") if isinstance(candidate.get("market_snapshot"), Mapping) else {}
    reference = _finite(snapshot.get("last"))
    risk_fraction = _finite(candidate.get("risk_percent"))
    if reference is None or reference <= 0.0 or risk_fraction is None or risk_fraction <= 0.0:
        raise ValueError("counterfactual candidate has invalid execution/risk geometry")
    risk_fraction = max(core.GPW_PROFILE.risk_floor_percent, risk_fraction)
    pre_ev_rr = _finite(candidate.get("pre_ev_reward_risk"))
    full_rr = _finite(candidate.get("full_reward_risk"))
    rr = pre_ev_rr if remove_component == "expected_value" else full_rr
    if rr is None or rr <= 0.0:
        raise ValueError("counterfactual candidate has invalid reward/risk")
    stop = reference * (1.0 - risk_fraction)
    target = reference + (reference - stop) * rr
    return {
        "action": "LONG",
        "symbol": candidate.get("symbol"),
        "entry_zone": [core.round2(reference * 0.997), core.round2(reference * 1.006)],
        "stop": core.round2(stop),
        "target": core.round2(target),
        "reward_risk": float(rr),
        "reference_price": core.round2(reference),
        "risk_percent": round(risk_fraction, 6),
        "plan_source": "prospective_loco_replay",
    }


def _candidate_state(
    candidate: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    remove_component: Optional[str],
) -> dict[str, Any]:
    weights = snapshot.get("base_weights") if isinstance(snapshot.get("base_weights"), Mapping) else {}
    base_scores = candidate.get("base_scores") if isinstance(candidate.get("base_scores"), Mapping) else {}
    remove_base = remove_component if remove_component in BASE_COMPONENTS else None
    legacy = _weighted_score(base_scores, weights, remove=remove_base)

    quant_weights = _quant_weights(weights)
    if remove_component in QUANT_COMPONENTS:
        quant_pre = _weighted_score(base_scores, quant_weights, remove=remove_component)
    else:
        quant_pre = _finite(candidate.get("quant_pre_score")) or 0.0

    opening_score = _finite(candidate.get("opening_confirmation_score"))
    if opening_score is None:
        raise ValueError("missing frozen opening confirmation score")
    opening_weight = float(snapshot.get("opening_weight") or 0.0)
    if remove_component == "opening_confirmation":
        opening_adjusted = core.round2(legacy)
    else:
        opening_adjusted = opening.blend(legacy, opening_score, opening_weight)

    ev_score = _finite(candidate.get("expected_value_score"))
    ev_weight = _finite(candidate.get("expected_value_weight")) or 0.0
    if remove_component == "expected_value" or ev_score is None or ev_weight <= 0.0:
        final_score = core.round2(opening_adjusted)
    else:
        final_score = core.round2(opening_adjusted * (1.0 - ev_weight) + ev_score * ev_weight)

    return {
        **deepcopy(dict(candidate)),
        "legacy_composite_score": legacy,
        "quant_pre_score": quant_pre,
        "opening_adjusted_score": opening_adjusted,
        "final_score": final_score,
        "removed_component": remove_component,
    }


def replay_selection(snapshot: Mapping[str, Any], remove_component: Optional[str] = None) -> dict[str, Any]:
    if remove_component is not None and remove_component not in SUPPORTED_COMPONENTS:
        return {
            "status": "UNSUPPORTED",
            "component": remove_component,
            "reason": UNSUPPORTED_COMPONENTS.get(remove_component, "component is not replayable from v1 frozen state"),
        }
    candidates = snapshot.get("candidates") if isinstance(snapshot.get("candidates"), Sequence) else []
    states = [
        _candidate_state(row, snapshot, remove_component=remove_component)
        for row in candidates
        if isinstance(row, Mapping)
    ]
    if not states:
        return {"status": "DATA_GAP", "component": remove_component, "reason": "no evaluated candidates in frozen snapshot"}
    ranked = sorted(states, key=_candidate_sort_key, reverse=True)
    selected = ranked[0]
    plan = _plan_from_candidate(selected, remove_component=remove_component)
    return {
        "status": "OK",
        "component": remove_component,
        "selected_symbol": selected.get("symbol"),
        "selected_quant_rank": selected.get("quant_rank"),
        "legacy_composite_score": selected.get("legacy_composite_score"),
        "opening_adjusted_score": selected.get("opening_adjusted_score"),
        "final_score": selected.get("final_score"),
        "plan": plan,
        "ranked_symbols": [row.get("symbol") for row in ranked],
    }


def _published_plan(selection: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "action": "LONG",
        "symbol": selection.get("symbol"),
        "entry_zone": deepcopy(selection.get("entry_zone")),
        "stop": selection.get("stop"),
        "target": selection.get("target"),
        "reward_risk": selection.get("reward_risk"),
        "reference_price": selection.get("reference_price"),
        "risk_percent": selection.get("risk_percent"),
    }


def _plan_equivalent(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if str(left.get("symbol") or "") != str(right.get("symbol") or ""):
        return False
    if not all(_close(left.get(field), right.get(field), PRICE_TOLERANCE) for field in ("reference_price", "stop", "target")):
        return False
    if not _close(left.get("reward_risk"), right.get("reward_risk"), 1e-6):
        return False
    left_zone, right_zone = left.get("entry_zone") or [], right.get("entry_zone") or []
    if len(left_zone) != 2 or len(right_zone) != 2:
        return False
    return all(_close(left_zone[i], right_zone[i], PRICE_TOLERANCE) for i in range(2))


def build_prospective_snapshot(
    *,
    payload: Mapping[str, Any],
    evaluated_candidates: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    policy: Mapping[str, Any],
    candidate_errors: Mapping[str, Any],
    captured_at: datetime,
    ranking_hash: Optional[str] = None,
) -> dict[str, Any]:
    selection = payload.get("selection") if isinstance(payload.get("selection"), Mapping) else {}
    decision_at = _parse_dt(payload.get("generated_at"))
    if decision_at is None:
        raise ValueError("producer generated_at is invalid")
    capture_utc = captured_at.astimezone(timezone.utc)
    delay = (capture_utc - decision_at).total_seconds()
    rows = [deepcopy(dict(row)) for row in evaluated_candidates]
    base_weights = {key: float(value) for key, value in (config.get("weights") or {}).items()}
    snapshot: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA,
        "market": "gpw",
        "decision_date": payload.get("date"),
        "producer_decision_at": payload.get("generated_at"),
        "captured_at": capture_utc.isoformat().replace("+00:00", "Z"),
        "capture_delay_seconds": round(delay, 3),
        "max_capture_delay_seconds": MAX_CAPTURE_DELAY_SECONDS,
        "selection_mode": selection.get("selection_mode"),
        "producer_selected_symbol": selection.get("symbol"),
        "producer_selected_score": selection.get("score"),
        "producer_plan": _published_plan(selection),
        "base_weights": base_weights,
        "opening_weight": mandatory._opening_weight(dict(policy)),
        "evaluated_candidate_count": len(rows),
        "candidate_errors": deepcopy(dict(candidate_errors)),
        "ranking_sha256": ranking_hash,
        "candidates": rows,
        "supported_components": list(SUPPORTED_COMPONENTS),
        "unsupported_components": deepcopy(UNSUPPORTED_COMPONENTS),
        "governance": {
            "prospective_only": True,
            "historical_backfill": False,
            "observational_only": True,
            "decision_influence": False,
            "ranking_writeback": False,
            "gate_writeback": False,
            "production_trade_writeback": False,
            "automatic_learning_writeback": False,
            "automatic_promotion": False,
            "automatic_component_removal": False,
        },
    }
    if delay < -60 or delay > MAX_CAPTURE_DELAY_SECONDS:
        snapshot["status"] = "INVALID_CAPTURE_WINDOW"
        snapshot["validation"] = {"full_replay_equivalent": False, "reason": "capture_not_close_enough_to_producer_decision"}
    elif selection.get("selection_mode") != "MANDATORY_DAILY_FINAL":
        snapshot["status"] = "UNSUPPORTED_SELECTION_MODE"
        snapshot["validation"] = {"full_replay_equivalent": False, "reason": "v1 supports mandatory final selector only"}
    else:
        full = replay_selection(snapshot, None)
        published_opening = selection.get("opening_adjusted_score")
        published_opening_component = selection.get("opening_confirmation_score")
        published_ev = selection.get("expected_value_score")
        replay_selected = next((row for row in rows if row.get("symbol") == full.get("selected_symbol")), None)
        score_ok = full.get("status") == "OK" and _close(full.get("final_score"), selection.get("score"), SCORE_TOLERANCE)
        opening_ok = full.get("status") == "OK" and _close(full.get("opening_adjusted_score"), published_opening, SCORE_TOLERANCE)
        opening_component_ok = replay_selected is not None and _close(replay_selected.get("opening_confirmation_score"), published_opening_component, SCORE_TOLERANCE)
        ev_ok = replay_selected is not None and _close(replay_selected.get("expected_value_score"), published_ev, SCORE_TOLERANCE)
        symbol_ok = str(full.get("selected_symbol") or "") == str(selection.get("symbol") or "")
        plan_ok = full.get("status") == "OK" and _plan_equivalent(full.get("plan") or {}, _published_plan(selection))
        equivalent = all((score_ok, opening_ok, opening_component_ok, ev_ok, symbol_ok, plan_ok))
        snapshot["status"] = "VALIDATED_EQUIVALENT" if equivalent else "FULL_REPLAY_MISMATCH"
        snapshot["validation"] = {
            "full_replay_equivalent": equivalent,
            "symbol_match": symbol_ok,
            "score_match": score_ok,
            "opening_adjusted_match": opening_ok,
            "opening_component_match": opening_component_ok,
            "expected_value_match": ev_ok,
            "plan_match": plan_ok,
            "full_replay": full,
        }
    snapshot["snapshot_id"] = "gpw-loco-" + _sha({"decision_at": snapshot["producer_decision_at"], "symbol": snapshot["producer_selected_symbol"]})[:20]
    snapshot["snapshot_sha256"] = _sha(snapshot)
    verify_snapshot(snapshot)
    return snapshot


def verify_snapshot(snapshot: Mapping[str, Any]) -> None:
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA:
        raise ValueError("LOCO snapshot schema mismatch")
    body = dict(snapshot)
    stored = str(body.pop("snapshot_sha256", ""))
    if not stored or stored != _sha(body):
        raise ValueError("LOCO snapshot hash mismatch")
    governance = snapshot.get("governance") if isinstance(snapshot.get("governance"), Mapping) else {}
    forbidden = (
        "historical_backfill",
        "decision_influence",
        "ranking_writeback",
        "gate_writeback",
        "production_trade_writeback",
        "automatic_learning_writeback",
        "automatic_promotion",
        "automatic_component_removal",
    )
    if any(governance.get(field) is not False for field in forbidden):
        raise ValueError("LOCO zero-authority/prospective contract violated")
    if governance.get("prospective_only") is not True or governance.get("observational_only") is not True:
        raise ValueError("LOCO snapshot must remain prospective and observational")
    if snapshot.get("status") == "VALIDATED_EQUIVALENT" and not (snapshot.get("validation") or {}).get("full_replay_equivalent"):
        raise ValueError("validated LOCO snapshot lacks full replay equivalence")


def _evaluate_current_state(
    *,
    payload: Mapping[str, Any],
    now: datetime,
    config: Mapping[str, Any],
    policy: Mapping[str, Any],
    cache: Mapping[str, Sequence[Any]],
    opening_fetcher: Callable[..., Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    expected_raw = ((payload.get("data_quality") or {}).get("expected_session") if isinstance(payload.get("data_quality"), Mapping) else None)
    expected = date.fromisoformat(str(expected_raw)) if expected_raw else gpw.previous_session(now.date(), dict(config))
    ranked = mandatory.build_ranked_candidates(dict(config), dict(policy), gpw.all_history(), dict(cache), expected)
    neutral_catalyst = float(policy.get("neutral_catalyst_score", 50.0))
    opening_weight = mandatory._opening_weight(dict(policy))
    top_n = max(1, int(policy.get("opening_confirmation_top_candidates", 8)))
    ev_settings = ev.settings_from(dict(config))
    evaluated: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for candidate in ranked[:top_n]:
        symbol = str(candidate["symbol"])
        try:
            execution_snapshot = dict(opening_fetcher(symbol, now=now))
            execution_gate = mandatory._fresh_execution_snapshot(execution_snapshot, now, config=dict(config), policy=dict(policy))
            confirmation = opening.score(candidate, execution_snapshot)
            base_scores = {**deepcopy(candidate.get("scores") or {}), "catalyst": neutral_catalyst}
            legacy = core.composite_score(candidate, {"catalyst_score": neutral_catalyst}, dict(config))
            opening_adjusted = opening.blend(legacy, float(confirmation["score"]), opening_weight)
            pre_ev_rr = float(candidate.get("reward_risk") or config.get("minimum_reward_risk", 1.5))
            model: dict[str, Any] = {}
            ev_weight = 0.0
            final_score = opening_adjusted
            full_rr = pre_ev_rr
            if ev_settings["enabled"]:
                feature_day = date.fromisoformat(str(candidate["historical_feature_session"]))
                model = ev.estimate(candidate, mandatory._completed_for_ev(dict(cache), symbol, feature_day), dict(config))
                if model.get("status") != "ready":
                    raise ValueError(f"expected value unavailable: {model.get('status')} n={model.get('analogue_count', 0)}")
                final_score, ev_weight = ev.blend_score(opening_adjusted, model, dict(config))
                full_rr = float(model["selected_reward_risk"])
            evaluated.append(
                {
                    "symbol": symbol,
                    "name": candidate.get("name"),
                    "sector": candidate.get("sector"),
                    "quant_rank": candidate.get("quant_rank"),
                    "quant_pre_score": candidate.get("quant_pre_score"),
                    "base_scores": base_scores,
                    "legacy_composite_score": core.round2(legacy),
                    "opening_confirmation_score": confirmation.get("score"),
                    "opening_confirmation": deepcopy(confirmation),
                    "opening_adjusted_score": core.round2(opening_adjusted),
                    "expected_value_score": model.get("score") if model else None,
                    "expected_value_model": deepcopy(model) if model else None,
                    "expected_value_weight": float(ev_weight),
                    "final_score": core.round2(final_score),
                    "pre_ev_reward_risk": pre_ev_rr,
                    "full_reward_risk": full_rr,
                    "risk_percent": candidate.get("risk_percent"),
                    "historical_feature_session": candidate.get("historical_feature_session"),
                    "historical_feature_lag_sessions": candidate.get("historical_feature_lag_sessions"),
                    "execution_data_gate": deepcopy(execution_gate),
                    "market_snapshot": execution_snapshot,
                }
            )
        except Exception as exc:
            errors[symbol] = f"{type(exc).__name__}: {str(exc)[:220]}"
    return evaluated, errors


def capture_current(
    *,
    now: Optional[datetime] = None,
    cache: Optional[Mapping[str, Sequence[Any]]] = None,
    opening_fetcher: Callable[..., Mapping[str, Any]] = market.opening_snapshot,
    snapshot_dir: Path = SNAPSHOT_DIR,
) -> dict[str, Any]:
    now_local = (now or gpw.now_warsaw()).astimezone(WARSAW)
    payload = gpw.load_json(gpw.PUBLIC_PATH, {})
    if not isinstance(payload, Mapping) or payload.get("decision") != "TRANSAKCJA":
        return {"status": "NOT_APPLICABLE", "reason": "current GPW producer has no transaction"}
    decision_at = _parse_dt(payload.get("generated_at"))
    if decision_at is None:
        return {"status": "NOT_APPLICABLE", "reason": "invalid producer timestamp"}
    delay = (now_local.astimezone(timezone.utc) - decision_at).total_seconds()
    if delay < -60 or delay > MAX_CAPTURE_DELAY_SECONDS:
        return {"status": "SKIPPED_LATE_CAPTURE", "capture_delay_seconds": round(delay, 3)}
    selection = payload.get("selection") if isinstance(payload.get("selection"), Mapping) else {}
    if selection.get("selection_mode") != "MANDATORY_DAILY_FINAL":
        return {"status": "UNSUPPORTED_SELECTION_MODE", "selection_mode": selection.get("selection_mode")}

    decision_key = _sha({"decision_at": payload.get("generated_at"), "symbol": selection.get("symbol")})[:16]
    path = snapshot_dir / f"{payload.get('date')}_{decision_key}.json"
    if path.exists():
        existing = gpw.load_json(path)
        if not isinstance(existing, Mapping):
            raise ValueError("existing LOCO snapshot is invalid")
        verify_snapshot(existing)
        return {"status": "EXISTS", "path": str(path.relative_to(ROOT)), "snapshot_status": existing.get("status")}

    config = gpw.load_config()
    policy = mandatory.load_policy()
    market_cache = dict(cache) if cache is not None else provider.prefetch_market(config)
    evaluated, errors = _evaluate_current_state(
        payload=payload,
        now=now_local,
        config=config,
        policy=policy,
        cache=market_cache,
        opening_fetcher=opening_fetcher,
    )
    ranking = gpw.load_json(ROOT / "data/investments/gpw_daily_candidate_ranking.json", {})
    ranking_hash = _sha(ranking) if isinstance(ranking, Mapping) else None
    snapshot = build_prospective_snapshot(
        payload=payload,
        evaluated_candidates=evaluated,
        config=config,
        policy=policy,
        candidate_errors=errors,
        captured_at=now_local,
        ranking_hash=ranking_hash,
    )
    _atomic(path, snapshot)
    return {
        "status": "CAPTURED",
        "path": str(path.relative_to(ROOT)),
        "snapshot_status": snapshot.get("status"),
        "evaluated_candidates": len(evaluated),
    }


def load_snapshots(snapshot_dir: Path = SNAPSHOT_DIR) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(snapshot_dir.glob("????-??-??_*.json")):
        value = gpw.load_json(path)
        if not isinstance(value, Mapping):
            raise ValueError(f"invalid LOCO snapshot: {path}")
        verify_snapshot(value)
        rows.append(dict(value))
    return rows


def _load_outcome_records() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(outcomes.STORE_DIR.glob("????-??-??.json")):
        value = gpw.load_json(path)
        if not isinstance(value, Mapping):
            raise ValueError(f"invalid rejected-candidate outcome record: {path}")
        outcomes.verify_record(value)
        rows.append(dict(value))
    return rows


def _matching_outcome(snapshot: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    for record in records:
        source = record.get("source_snapshot") if isinstance(record.get("source_snapshot"), Mapping) else {}
        if str(record.get("decision_date") or "") != str(snapshot.get("decision_date") or ""):
            continue
        if str(source.get("decision_at") or "") == str(snapshot.get("producer_decision_at") or ""):
            return record
    return None


def _t2_return(row: Mapping[str, Any]) -> Optional[float]:
    return outcomes._complete_return(row, 2)


def _plan_changed(full: Mapping[str, Any], variant: Mapping[str, Any]) -> bool:
    return not _plan_equivalent(full, variant)


def replay_snapshot_outcome(
    snapshot: Mapping[str, Any],
    outcome_record: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    now: datetime,
    intraday_fetcher: Callable[[str], Sequence[Mapping[str, Any]]],
    daily_fetcher: Callable[[str], Sequence[Any]],
) -> dict[str, Any]:
    verify_snapshot(snapshot)
    if snapshot.get("status") != "VALIDATED_EQUIVALENT":
        return {
            "snapshot_id": snapshot.get("snapshot_id"),
            "decision_date": snapshot.get("decision_date"),
            "status": "EXCLUDED_INVALID_SNAPSHOT",
            "reason": snapshot.get("status"),
            "variants": [],
        }
    outcomes.verify_record(outcome_record)
    settlement = outcome_record.get("settlement") if isinstance(outcome_record.get("settlement"), Mapping) else {}
    selected = settlement.get("selected") if isinstance(settlement.get("selected"), Mapping) else {}
    full_return = _t2_return(selected)
    if full_return is None:
        return {
            "snapshot_id": snapshot.get("snapshot_id"),
            "decision_date": snapshot.get("decision_date"),
            "status": "PENDING_OR_DATA_GAP",
            "reason": (settlement.get("summary") or {}).get("reason") if isinstance(settlement.get("summary"), Mapping) else settlement.get("status"),
            "variants": [],
        }
    full_replay = replay_selection(snapshot, None)
    if full_replay.get("status") != "OK" or str(full_replay.get("selected_symbol")) != str(selected.get("symbol")):
        return {
            "snapshot_id": snapshot.get("snapshot_id"),
            "decision_date": snapshot.get("decision_date"),
            "status": "INVALID_FULL_BASELINE",
            "reason": "prospective full replay no longer matches settled producer baseline",
            "variants": [],
        }
    source = outcome_record.get("source_snapshot") if isinstance(outcome_record.get("source_snapshot"), Mapping) else {}
    decision_at = _parse_dt(source.get("decision_at"))
    if decision_at is None:
        raise ValueError("outcome source has invalid decision_at")
    decision_day = date.fromisoformat(str(source.get("decision_date")))
    valid_until = date.fromisoformat(str(source.get("valid_until")))
    full_plan = full_replay.get("plan") if isinstance(full_replay.get("plan"), Mapping) else {}
    variants: list[dict[str, Any]] = []
    for component in SUPPORTED_COMPONENTS:
        variant = replay_selection(snapshot, component)
        if variant.get("status") != "OK":
            variants.append({"component": component, "status": variant.get("status"), "reason": variant.get("reason")})
            continue
        variant_plan = variant.get("plan") if isinstance(variant.get("plan"), Mapping) else {}
        decision_changed = str(variant.get("selected_symbol")) != str(full_replay.get("selected_symbol"))
        plan_changed = _plan_changed(full_plan, variant_plan)
        if not decision_changed and not plan_changed:
            without_return = full_return
            without_status = "RESOLVED_REUSED_FULL_PATH"
            settled_variant = None
        else:
            settled_variant = outcomes._settle_plan(
                plan=variant_plan,
                decision_at=decision_at,
                decision_day=decision_day,
                valid_until=valid_until,
                config=dict(config),
                now=now,
                intraday_fetcher=intraday_fetcher,
                daily_fetcher=daily_fetcher,
            )
            without_return = _t2_return(settled_variant)
            without_status = "RESOLVED" if without_return is not None else "PENDING_OR_DATA_GAP"
        if without_return is None:
            variants.append(
                {
                    "component": component,
                    "status": without_status,
                    "full_selected_symbol": full_replay.get("selected_symbol"),
                    "without_selected_symbol": variant.get("selected_symbol"),
                    "decision_changed": decision_changed,
                    "plan_changed": plan_changed,
                }
            )
            continue
        mv = float(full_return) - float(without_return)
        classification = "BENEFICIAL_COMPONENT" if mv > EPSILON else ("HARMFUL_COMPONENT" if mv < -EPSILON else "NEUTRAL_COMPONENT")
        variants.append(
            {
                "component": component,
                "status": "RESOLVED",
                "classification": classification,
                "full_selected_symbol": full_replay.get("selected_symbol"),
                "without_selected_symbol": variant.get("selected_symbol"),
                "decision_changed": decision_changed,
                "plan_changed": plan_changed,
                "full_t2_net_return_percent": round(float(full_return), 6),
                "without_component_t2_net_return_percent": round(float(without_return), 6),
                "marginal_value_percent": round(mv, 6),
                "full_plan": deepcopy(full_plan),
                "without_component_plan": deepcopy(variant_plan),
                "without_component_final_score": variant.get("final_score"),
                "without_component_settlement": deepcopy(settled_variant) if settled_variant is not None else None,
            }
        )
    return {
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_sha256": snapshot.get("snapshot_sha256"),
        "decision_date": snapshot.get("decision_date"),
        "producer_decision_at": snapshot.get("producer_decision_at"),
        "status": "RESOLVED",
        "full_selected_symbol": full_replay.get("selected_symbol"),
        "full_t2_net_return_percent": round(float(full_return), 6),
        "variants": variants,
    }


def _evidence_status(count: int) -> str:
    if count < 20:
        return "OBSERVING"
    if count < 50:
        return "EARLY_SIGNAL"
    return "EVALUABLE"


def _aggregate(component: str, decisions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows: list[Mapping[str, Any]] = []
    for decision in decisions:
        for variant in decision.get("variants") or []:
            if isinstance(variant, Mapping) and variant.get("component") == component and variant.get("status") == "RESOLVED":
                rows.append(variant)
    values = [float(row.get("marginal_value_percent") or 0.0) for row in rows]
    changed = [row for row in rows if row.get("decision_changed") is True]
    plan_changed = [row for row in rows if row.get("plan_changed") is True]
    beneficial = [row for row in rows if row.get("classification") == "BENEFICIAL_COMPONENT"]
    harmful = [row for row in rows if row.get("classification") == "HARMFUL_COMPONENT"]
    return {
        "component": component,
        "resolved_decision_count": len(rows),
        "decision_change_count": len(changed),
        "decision_change_rate": round(len(changed) / len(rows), 6) if rows else None,
        "plan_change_count": len(plan_changed),
        "beneficial_count": len(beneficial),
        "harmful_count": len(harmful),
        "neutral_count": len(rows) - len(beneficial) - len(harmful),
        "beneficial_rate": round(len(beneficial) / len(rows), 6) if rows else None,
        "harmful_rate": round(len(harmful) / len(rows), 6) if rows else None,
        "marginal_value_percent_sum": round(sum(values), 6),
        "mean_marginal_value_percent": round(statistics.fmean(values), 6) if values else None,
        "median_marginal_value_percent": round(statistics.median(values), 6) if values else None,
        "evidence_status": _evidence_status(len(rows)),
    }


def build_replay_artifact(
    *,
    snapshots: Sequence[Mapping[str, Any]],
    outcome_records: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    now: datetime,
    intraday_fetcher: Callable[[str], Sequence[Mapping[str, Any]]],
    daily_fetcher: Callable[[str], Sequence[Any]],
) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    missing_outcome = 0
    invalid_snapshots = 0
    for snapshot in sorted(snapshots, key=lambda row: str(row.get("producer_decision_at") or "")):
        verify_snapshot(snapshot)
        if snapshot.get("status") != "VALIDATED_EQUIVALENT":
            invalid_snapshots += 1
            decisions.append({
                "snapshot_id": snapshot.get("snapshot_id"),
                "decision_date": snapshot.get("decision_date"),
                "status": "EXCLUDED_INVALID_SNAPSHOT",
                "reason": snapshot.get("status"),
                "variants": [],
            })
            continue
        record = _matching_outcome(snapshot, outcome_records)
        if record is None:
            missing_outcome += 1
            decisions.append({
                "snapshot_id": snapshot.get("snapshot_id"),
                "decision_date": snapshot.get("decision_date"),
                "status": "PENDING_OUTCOME_RECORD",
                "variants": [],
            })
            continue
        decisions.append(
            replay_snapshot_outcome(
                snapshot,
                record,
                config=config,
                now=now,
                intraday_fetcher=intraday_fetcher,
                daily_fetcher=daily_fetcher,
            )
        )
    aggregates = [_aggregate(component, decisions) for component in SUPPORTED_COMPONENTS]
    resolved_decisions = [row for row in decisions if row.get("status") == "RESOLVED"]
    artifact: dict[str, Any] = {
        "schema_version": REPLAY_SCHEMA,
        "generated_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "market": "gpw",
        "method": {
            "name": "leave_one_component_out_counterfactual_replay_v1",
            "marginal_value_definition": "utility_full_minus_utility_without_component",
            "utility": "realised_T_plus_2_net_return_percent",
            "capture": "prospective_validated_equivalent_replay",
            "causal_scope": "local_structural_counterfactual_conditional_on_frozen_validated_equivalent_state",
            "absolute_causal_claim": False,
            "historical_backfill": False,
        },
        "coverage": {
            "snapshot_count": len(snapshots),
            "validated_snapshot_count": sum(1 for row in snapshots if row.get("status") == "VALIDATED_EQUIVALENT"),
            "invalid_snapshot_count": invalid_snapshots,
            "missing_outcome_record_count": missing_outcome,
            "resolved_decision_count": len(resolved_decisions),
            "supported_components": list(SUPPORTED_COMPONENTS),
            "unsupported_components": deepcopy(UNSUPPORTED_COMPONENTS),
        },
        "components": aggregates,
        "decisions": decisions[-120:],
        "governance": {
            "observational_only": True,
            "decision_influence": False,
            "ranking_writeback": False,
            "gate_writeback": False,
            "production_trade_writeback": False,
            "automatic_learning_writeback": False,
            "automatic_promotion": False,
            "automatic_component_removal": False,
            "historical_backfill": False,
        },
    }
    artifact["artifact_sha256"] = _sha(artifact)
    verify_replay_artifact(artifact)
    return artifact


def verify_replay_artifact(artifact: Mapping[str, Any]) -> None:
    if artifact.get("schema_version") != REPLAY_SCHEMA:
        raise ValueError("LOCO replay schema mismatch")
    body = dict(artifact)
    stored = str(body.pop("artifact_sha256", ""))
    if not stored or stored != _sha(body):
        raise ValueError("LOCO replay artifact hash mismatch")
    method = artifact.get("method") if isinstance(artifact.get("method"), Mapping) else {}
    if method.get("marginal_value_definition") != "utility_full_minus_utility_without_component":
        raise ValueError("LOCO marginal-value sign contract changed")
    if method.get("absolute_causal_claim") is not False or method.get("historical_backfill") is not False:
        raise ValueError("LOCO causal/prospective boundary violated")
    governance = artifact.get("governance") if isinstance(artifact.get("governance"), Mapping) else {}
    forbidden = (
        "decision_influence",
        "ranking_writeback",
        "gate_writeback",
        "production_trade_writeback",
        "automatic_learning_writeback",
        "automatic_promotion",
        "automatic_component_removal",
        "historical_backfill",
    )
    if any(governance.get(field) is not False for field in forbidden):
        raise ValueError("LOCO replay zero-authority contract violated")
    for component in artifact.get("components") or []:
        if not isinstance(component, Mapping):
            raise ValueError("invalid LOCO component aggregate")
        if component.get("component") not in SUPPORTED_COMPONENTS:
            raise ValueError("unexpected LOCO component")
    for decision in artifact.get("decisions") or []:
        if not isinstance(decision, Mapping):
            raise ValueError("invalid LOCO decision row")
        for variant in decision.get("variants") or []:
            if not isinstance(variant, Mapping):
                raise ValueError("invalid LOCO variant")
            if variant.get("status") == "RESOLVED":
                full = _finite(variant.get("full_t2_net_return_percent"))
                without = _finite(variant.get("without_component_t2_net_return_percent"))
                marginal = _finite(variant.get("marginal_value_percent"))
                if full is None or without is None or marginal is None or abs(marginal - (full - without)) > 1e-5:
                    raise ValueError("LOCO marginal value arithmetic mismatch")


def build_store(*, now: Optional[datetime] = None, output_path: Path = OUTPUT_PATH) -> dict[str, Any]:
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    snapshots = load_snapshots()
    records = _load_outcome_records()
    config = gpw.load_config()
    intraday_cache: dict[str, list[Mapping[str, Any]]] = {}
    daily_cache: dict[str, list[Any]] = {}

    def intraday_fetch(symbol: str) -> Sequence[Mapping[str, Any]]:
        if symbol not in intraday_cache:
            intraday_cache[symbol] = list(outcomes.selected_monitor.fetch_intraday(symbol))
        return intraday_cache[symbol]

    def daily_fetch(symbol: str) -> Sequence[Any]:
        if symbol not in daily_cache:
            daily_cache[symbol] = list(gpw.fetch_yahoo_bars(symbol, range_value="3mo"))
        return daily_cache[symbol]

    artifact = build_replay_artifact(
        snapshots=snapshots,
        outcome_records=records,
        config=config,
        now=checked_at,
        intraday_fetcher=intraday_fetch,
        daily_fetcher=daily_fetch,
    )
    _atomic(output_path, artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--capture", action="store_true", help="Capture a prospective validated-equivalent final-selector state.")
    mode.add_argument("--build", action="store_true", help="Build/settle LOCO replay over existing prospective snapshots.")
    mode.add_argument("--verify", action="store_true", help="Verify snapshots and current replay artifact.")
    args = parser.parse_args()
    if args.capture:
        print(json.dumps(capture_current(), ensure_ascii=False))
        return 0
    if args.build:
        artifact = build_store()
        print(json.dumps({
            "status": "OK",
            "snapshots": artifact["coverage"]["snapshot_count"],
            "resolved_decisions": artifact["coverage"]["resolved_decision_count"],
            "components": len(artifact["components"]),
        }, ensure_ascii=False))
        return 0
    snapshots = load_snapshots()
    for snapshot in snapshots:
        verify_snapshot(snapshot)
    payload = gpw.load_json(OUTPUT_PATH)
    if not isinstance(payload, Mapping):
        raise ValueError("LOCO replay artifact is missing")
    verify_replay_artifact(payload)
    print(json.dumps({"status": "OK", "snapshots": len(snapshots), "artifact_sha256": payload.get("artifact_sha256")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""PR29.1 — prospective rejected-candidate freeze for GPW and US Daily Stock.

This module runs in the same publisher workflow immediately after the engine has
finalised the current Daily Stock output and before that output is committed.
It never changes ranking, admission, review, sizing or execution.  It only adds
an immutable point-in-time diagnostic field describing candidates that the
engine considered but did not select.

The freeze deliberately does *not* re-run news or LLM analysis.  Later-stage
rejection reasons are taken from the already-produced engine payload; the
market/risk plan is reconstructed prospectively from the same completed-session
rules while the decision is still current.  Once a valid freeze exists for a
specific decision timestamp it is preserved byte-for-byte on later workflow
runs, preventing post-outcome reconstruction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import tempfile
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

try:
    import daily_stock_core as core
except ModuleNotFoundError:  # pragma: no cover
    from scripts import daily_stock_core as core

SCHEMA_VERSION = "daily-stock-rejected-candidate-freeze-v1"
FIELD = "counterfactual_rejected_candidate_freeze"
ELIGIBLE_DECISIONS = {
    "gpw": {"TRANSAKCJA", "BRAK_TRANSAKCJI"},
    "us": {"TRADE", "NO_TRADE"},
}


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        temp = Path(handle.name)
    temp.replace(path)


def _iso_z(value: Optional[datetime] = None) -> str:
    dt = value or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _gate(
    name: str,
    passed: bool,
    *,
    reason: str,
    stage: str,
    hard: bool = True,
    observed: Any = None,
    threshold: Any = None,
) -> dict[str, Any]:
    return {
        "name": str(name),
        "passed": bool(passed),
        "hard": bool(hard),
        "stage": str(stage),
        "reason": str(reason),
        "observed_value": observed,
        "threshold": threshold,
    }


def _first_failed(gates: Sequence[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    for gate in gates:
        if gate.get("passed") is False:
            return dict(gate)
    return None


def _bar_day(bar: Any) -> Optional[date]:
    value = getattr(bar, "day", None)
    return value if isinstance(value, date) else None


def _bar_number(bar: Any, field: str) -> Optional[float]:
    return _finite(getattr(bar, field, None))


def _profile_for(market: str) -> core.QuantProfile:
    if market == "gpw":
        return core.GPW_PROFILE
    if market == "us":
        return core.US_PROFILE
    raise ValueError(f"unsupported market: {market}")


def _diagnostic_market_state(
    company: Mapping[str, Any],
    bars: Sequence[Any],
    expected: date,
    config: Mapping[str, Any],
    profile: core.QuantProfile,
) -> dict[str, Any]:
    completed = [bar for bar in bars if _bar_day(bar) is not None and _bar_day(bar) <= expected]
    if not completed:
        return {
            "market_data_ok": False,
            "reason": "empty_completed_history",
            "gates": [_gate("market_data", False, reason="empty_completed_history", stage="market_data")],
        }
    last_day = _bar_day(completed[-1])
    if last_day != expected:
        return {
            "market_data_ok": False,
            "reason": f"stale_latest_session:{last_day}",
            "gates": [_gate("market_data", False, reason=f"stale_latest_session:{last_day}", stage="market_data")],
        }
    if len(completed) < 60:
        return {
            "market_data_ok": False,
            "reason": f"insufficient_history:{len(completed)}",
            "gates": [_gate("market_data", False, reason=f"insufficient_history:{len(completed)}", stage="market_data")],
        }

    close = _bar_number(completed[-1], "close")
    if close is None or close <= 0:
        return {
            "market_data_ok": False,
            "reason": "invalid_reference_price",
            "gates": [_gate("market_data", False, reason="invalid_reference_price", stage="market_data")],
        }
    turnover_values = []
    for bar in completed[-20:]:
        price = _bar_number(bar, "close")
        volume = _finite(getattr(bar, "volume", None))
        if price is not None and volume is not None:
            turnover_values.append(price * volume)
    turnover = statistics.median(turnover_values) if turnover_values else 0.0
    atr = core.true_range(list(completed))
    if atr <= 0:
        return {
            "market_data_ok": False,
            "reason": "invalid_atr",
            "gates": [_gate("market_data", False, reason="invalid_atr", stage="market_data")],
        }

    risk = max(atr * profile.risk_atr_multiple, close * profile.risk_floor_percent)
    risk_percent = risk / close
    reward_risk = float(profile.reward_risk)
    turnover_threshold = float(config[profile.turnover_config_key])
    max_risk = float(config["maximum_risk_percent"])
    liquidity_pass = turnover >= turnover_threshold
    risk_pass = risk_percent <= max_risk
    gates = [
        _gate("market_data", True, reason="fresh_completed_session", stage="market_data"),
        _gate(
            "liquidity",
            liquidity_pass,
            reason="median_turnover_threshold",
            stage="quant_screen",
            observed=round(turnover, 6),
            threshold=turnover_threshold,
        ),
        _gate(
            "position_risk",
            risk_pass,
            reason="maximum_risk_percent",
            stage="quant_screen",
            observed=round(risk_percent, 8),
            threshold=max_risk,
        ),
    ]
    return {
        "market_data_ok": True,
        "symbol": str(company.get("symbol") or ""),
        "last_session": expected.isoformat(),
        "reference_price": round(float(close), 8),
        "entry_zone": [
            round(float(close) * profile.entry_low_multiple, 8),
            round(float(close) * profile.entry_high_multiple, 8),
        ],
        "stop": round(float(close) - risk, 8),
        "target": round(float(close) + risk * reward_risk, 8),
        "reward_risk": reward_risk,
        "risk_percent": round(risk_percent, 8),
        "atr": round(atr, 8),
        "median_turnover": round(turnover, 6),
        "turnover_threshold": turnover_threshold,
        "gates": gates,
        "quant_screen_passed": liquidity_pass and risk_pass,
        "reason": "quant_screen_passed" if liquidity_pass and risk_pass else "quant_screen_rejected",
    }


def _explicit_rejections(payload: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    quality = payload.get("data_quality") if isinstance(payload.get("data_quality"), Mapping) else {}
    reasons: dict[str, str] = {}
    details: dict[str, dict[str, Any]] = {}
    for symbol, reason in (quality.get("screened_out") or {}).items():
        reasons[str(symbol)] = str(reason)
    for symbol, reason in (quality.get("analysis_rejections") or {}).items():
        reasons[str(symbol)] = str(reason)
    for symbol, reason in (quality.get("provider_failures") or {}).items():
        reasons[str(symbol)] = f"provider_failure:{reason}"
    for symbol, reason in (quality.get("source_errors") or {}).items():
        reasons[str(symbol)] = f"source_error:{reason}"
    rows = quality.get("review_rejections") or []
    if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
        for row in rows:
            if isinstance(row, Mapping) and row.get("symbol"):
                symbol = str(row["symbol"])
                reasons[symbol] = "independent_review"
                details[symbol] = dict(row)
    return reasons, details


def _later_gate_from_reason(
    reason: str,
    *,
    config: Mapping[str, Any],
    review_detail: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    text = str(reason or "")
    if text.startswith("provider_failure:"):
        return _gate("market_data", False, reason=text, stage="market_data")
    if text.startswith("source_error:"):
        return _gate("source_availability", False, reason=text, stage="evidence")
    if text == "missing_analysis":
        return _gate("analysis_available", False, reason=text, stage="evidence")
    if text == "source_gate":
        return _gate("source_evidence", False, reason=text, stage="evidence")
    if text.startswith("score:"):
        observed = _finite(text.split(":", 1)[1])
        return _gate(
            "minimum_composite_score",
            False,
            reason=text,
            stage="scoring",
            observed=observed,
            threshold=_finite(config.get("minimum_composite_score")),
        )
    if text.startswith("reward_risk:") or text == "reward_risk":
        observed = _finite(text.split(":", 1)[1]) if ":" in text else None
        return _gate(
            "reward_risk",
            False,
            reason=text,
            stage="risk",
            observed=observed,
            threshold=_finite(config.get("minimum_reward_risk")),
        )
    if text == "independent_review":
        return _gate(
            "independent_review",
            False,
            reason=str((review_detail or {}).get("reason") or "review_rejected"),
            stage="review",
        )
    if text.startswith("screened_by_"):
        return _gate("quant_screen", False, reason=text, stage="quant_screen")
    return _gate("engine_rejection", False, reason=text or "rejected", stage="decision")


def _source_payload_hash(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop(FIELD, None)
    return _sha(body)


def _validate_freeze(freeze: Mapping[str, Any]) -> None:
    if freeze.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("rejected-candidate freeze schema mismatch")
    body = dict(freeze)
    stored = str(body.pop("freeze_sha256", ""))
    if not stored or stored != _sha(body):
        raise ValueError("rejected-candidate freeze hash mismatch")
    seen: set[str] = set()
    for row in freeze.get("candidates") or []:
        cid = str(row.get("candidate_id") or "")
        if not cid or cid in seen:
            raise ValueError("duplicate/empty rejected candidate id")
        seen.add(cid)
        state_body = dict(row)
        state_hash = str(state_body.pop("state_sha256", ""))
        if not state_hash or state_hash != _sha(state_body):
            raise ValueError(f"candidate state hash mismatch: {cid}")
        if row.get("selected") is not False or str(row.get("action") or "").upper() != "LONG":
            raise ValueError("PR29.1 freeze may contain rejected LONG candidates only")


def _freeze_status_applicable(payload: Mapping[str, Any], market: str) -> bool:
    decision = str(payload.get("decision") or "").upper()
    quality = payload.get("data_quality") if isinstance(payload.get("data_quality"), Mapping) else {}
    return decision in ELIGIBLE_DECISIONS[market] and bool(quality.get("expected_session"))


def build_freeze(
    payload: Mapping[str, Any],
    *,
    market: str,
    config: Mapping[str, Any],
    universe: Sequence[Mapping[str, Any]],
    bar_fetcher: Callable[[str], Sequence[Any]],
    exact_builder: Callable[[Mapping[str, Any], Sequence[Any]], Optional[dict[str, Any]]],
    normalizer: Callable[[list[dict[str, Any]]], None],
    frozen_at: Optional[datetime] = None,
) -> dict[str, Any]:
    if market not in {"gpw", "us"}:
        raise ValueError("market must be gpw or us")
    quality = payload.get("data_quality") if isinstance(payload.get("data_quality"), Mapping) else {}
    expected = date.fromisoformat(str(quality["expected_session"]))
    profile = _profile_for(market)
    explicit, review_details = _explicit_rejections(payload)
    selected = payload.get("selection") if isinstance(payload.get("selection"), Mapping) else {}
    selected_symbol = str(selected.get("symbol") or "")
    decision = str(payload.get("decision") or "").upper()
    reason_text = str(payload.get("reason") or "")
    no_sources_global = (
        "Brak świeżego" in reason_text
        or "No fresh verifiable catalyst" in reason_text
        or "No fresh, verifiable catalyst" in reason_text
    )

    raw_states: dict[str, dict[str, Any]] = {}
    exact_candidates: list[dict[str, Any]] = []
    for company in universe:
        symbol = str(company.get("symbol") or "")
        if not symbol:
            continue
        try:
            bars = list(bar_fetcher(symbol))
            state = _diagnostic_market_state(company, bars, expected, config, profile)
            state["company"] = {
                "symbol": symbol,
                "name": company.get("name"),
                "sector": company.get("sector"),
            }
            raw_states[symbol] = state
            try:
                exact = exact_builder(company, bars)
            except Exception as exc:
                exact = None
                state["exact_builder_error"] = f"{type(exc).__name__}:{str(exc)[:180]}"
            if isinstance(exact, dict):
                exact_candidates.append(exact)
        except Exception as exc:
            raw_states[symbol] = {
                "market_data_ok": False,
                "reason": f"freeze_fetch_failure:{type(exc).__name__}:{str(exc)[:180]}",
                "company": {"symbol": symbol, "name": company.get("name"), "sector": company.get("sector")},
                "gates": [_gate("market_data", False, reason=f"freeze_fetch_failure:{type(exc).__name__}", stage="market_data")],
            }

    if exact_candidates:
        normalizer(exact_candidates)
    exact_by_symbol = {str(row.get("symbol") or ""): row for row in exact_candidates}
    ranked = sorted(exact_candidates, key=lambda row: float(row.get("quant_pre_score") or 0.0), reverse=True)
    rank_by_symbol = {str(row.get("symbol") or ""): index + 1 for index, row in enumerate(ranked)}
    shortlist_limit = int(config.get("top_candidates_for_news") or len(ranked) or 1)

    candidates: list[dict[str, Any]] = []
    for company in universe:
        symbol = str(company.get("symbol") or "")
        if not symbol or symbol == selected_symbol:
            continue
        state = raw_states.get(symbol) or {}
        exact = exact_by_symbol.get(symbol)
        rank = rank_by_symbol.get(symbol)
        gates = [dict(row) for row in (state.get("gates") or [])]
        explicit_reason = explicit.get(symbol)

        if state.get("market_data_ok") is not True:
            pass
        elif exact is None:
            # The diagnostic plan exists, but the engine's own quant builder did
            # not admit this symbol. Preserve the more specific deterministic
            # liquidity/risk failure when available.
            failed = _first_failed(gates)
            if failed is None:
                gates.append(_gate("quant_candidate", False, reason=explicit_reason or "engine_quant_builder_rejected", stage="quant_screen"))
        else:
            gates.append(_gate("quant_candidate", True, reason="engine_quant_candidate_created", stage="quant_screen"))
            in_shortlist = bool(rank is not None and rank <= shortlist_limit)
            gates.append(
                _gate(
                    "shortlist_rank",
                    in_shortlist,
                    reason="within_news_shortlist" if in_shortlist else "ranked_below_news_shortlist",
                    stage="ranking",
                    hard=False,
                    observed=rank,
                    threshold=shortlist_limit,
                )
            )
            if in_shortlist:
                if explicit_reason:
                    gates.append(_later_gate_from_reason(explicit_reason, config=config, review_detail=review_details.get(symbol)))
                elif no_sources_global:
                    gates.append(_gate("source_availability", False, reason="no_fresh_verifiable_catalyst", stage="evidence"))
                else:
                    gates.append(
                        _gate(
                            "final_selection_rank",
                            False,
                            reason="higher_ranked_or_approved_candidate_selected" if selected_symbol else "engine_finished_flat_without_explicit_candidate_gate",
                            stage="decision",
                            hard=False,
                        )
                    )

        # Include every non-selected universe member for which the engine had
        # enough state to describe why it did not become the final LONG. This
        # makes FLAT diagnostics complete while separating operational failures.
        first_block = _first_failed(gates)
        if first_block is None:
            gates.append(_gate("final_selection_rank", False, reason="not_selected", stage="decision", hard=False))
            first_block = _first_failed(gates)

        plan_source = "engine_quant_candidate" if exact is not None else "diagnostic_pre_gate_same_point_in_time_rules"
        risk_plan = None
        if state.get("market_data_ok") is True:
            risk_plan = {
                "reference_price": state.get("reference_price"),
                "entry_zone": state.get("entry_zone"),
                "stop": state.get("stop"),
                "target": state.get("target"),
                "reward_risk": state.get("reward_risk"),
                "risk_percent": state.get("risk_percent"),
                "atr": state.get("atr"),
                "plan_source": plan_source,
                "activation_rule": "diagnostic_reference_plan_only; future settler must respect frozen entry-zone activation",
            }

        composite_score = None
        if explicit_reason and explicit_reason.startswith("score:"):
            composite_score = _finite(explicit_reason.split(":", 1)[1])
        if symbol in review_details:
            composite_score = _finite(review_details[symbol].get("score")) or composite_score

        row: dict[str, Any] = {
            "candidate_id": f"{market}:{payload.get('date')}:{symbol}:LONG",
            "market": market,
            "symbol": symbol,
            "name": company.get("name"),
            "sector": company.get("sector"),
            "action": "LONG",
            "selected": False,
            "decision_at": payload.get("generated_at"),
            "expected_session": expected.isoformat(),
            "first_blocking_gate": first_block,
            "decision_path": {
                "gates": gates,
                "explicit_engine_rejection": explicit_reason,
                "producer_decision": decision,
                "producer_reason": reason_text,
            },
            "score_state": {
                "rank": rank,
                "shortlist_limit": shortlist_limit,
                "quant_pre_score": None if exact is None else _finite(exact.get("quant_pre_score")),
                "composite_score": composite_score,
                "scores": {} if exact is None else deepcopy(exact.get("scores") or {}),
                "returns": {} if exact is None else deepcopy(exact.get("returns") or {}),
            },
            "market_state": {
                "last_session": state.get("last_session"),
                "reference_price": state.get("reference_price"),
                "median_turnover": state.get("median_turnover"),
                "turnover_threshold": state.get("turnover_threshold"),
                "risk_percent": state.get("risk_percent"),
            },
            "risk_plan": risk_plan,
            "settlement_eligibility": {
                "eligible": risk_plan is not None,
                "mode": "risk_plan" if risk_plan is not None else "insufficient_counterfactual_state",
                "reason": "prospectively_frozen_risk_plan" if risk_plan is not None else "market_state_insufficient",
            },
            "governance": {
                "decision_influence": False,
                "ranking_writeback": False,
                "gate_writeback": False,
                "historical_backfill": False,
                "news_or_llm_rerun": False,
            },
        }
        row["state_sha256"] = _sha(row)
        candidates.append(row)

    freeze: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "market": market,
        "date": payload.get("date"),
        "decision_at": payload.get("generated_at"),
        "frozen_at": _iso_z(frozen_at),
        "source_payload_sha256": _source_payload_hash(payload),
        "status": "frozen",
        "selected_symbol": selected_symbol or None,
        "candidate_count": len(candidates),
        "economically_evaluable_count": sum(1 for row in candidates if (row.get("settlement_eligibility") or {}).get("eligible") is True),
        "candidates": candidates,
        "contract": {
            "prospective_only": True,
            "preserve_existing_same_decision": True,
            "news_or_llm_rerun": False,
            "source_engine_writeback": False,
            "decision_influence": False,
            "future_outcome_requires_preexisting_freeze": True,
        },
    }
    freeze["freeze_sha256"] = _sha(freeze)
    return freeze


def apply_freeze(
    payload: dict[str, Any],
    *,
    market: str,
    config: Mapping[str, Any],
    universe: Sequence[Mapping[str, Any]],
    bar_fetcher: Callable[[str], Sequence[Any]],
    exact_builder: Callable[[Mapping[str, Any], Sequence[Any]], Optional[dict[str, Any]]],
    normalizer: Callable[[list[dict[str, Any]]], None],
    frozen_at: Optional[datetime] = None,
) -> tuple[dict[str, Any], bool]:
    existing = payload.get(FIELD)
    if isinstance(existing, Mapping):
        _validate_freeze(existing)
        if str(existing.get("decision_at") or "") == str(payload.get("generated_at") or ""):
            return payload, False
        raise RuntimeError("refusing to replace PR29.1 freeze for a different decision timestamp")

    if not _freeze_status_applicable(payload, market):
        return payload, False

    freeze = build_freeze(
        payload,
        market=market,
        config=config,
        universe=universe,
        bar_fetcher=bar_fetcher,
        exact_builder=exact_builder,
        normalizer=normalizer,
        frozen_at=frozen_at,
    )
    out = dict(payload)
    out[FIELD] = freeze
    return out, True


def _production_context(market: str):
    if market == "gpw":
        try:
            import gpw_daily_pick as engine
            import gpw_market_data as market_data
        except ModuleNotFoundError:  # pragma: no cover
            from scripts import gpw_daily_pick as engine
            from scripts import gpw_market_data as market_data
        config = engine.load_config()
        history = engine.all_history()

        def fetch(symbol: str):
            return market_data.fetch_resilient_bars(symbol)

        def builder(company: Mapping[str, Any], bars: Sequence[Any]):
            expected = date.fromisoformat(str(current_payload_quality["expected_session"]))
            return engine.build_quant_candidate(dict(company), list(bars), expected, config, history)

        return engine, config, fetch, builder, engine.normalize_cross_section

    try:
        import us_daily_stock as engine
    except ModuleNotFoundError:  # pragma: no cover
        from scripts import us_daily_stock as engine
    config = engine.load_config()

    def fetch(symbol: str):
        bars, _meta = engine.fetch_resilient_bars(symbol)
        return bars

    def builder(company: Mapping[str, Any], bars: Sequence[Any]):
        expected = date.fromisoformat(str(current_payload_quality["expected_session"]))
        return engine.build_candidate(dict(company), list(bars), expected, config)

    return engine, config, fetch, builder, engine.normalize_cross_section


# CLI context is intentionally narrow; builder closures read only this current
# payload's expected session and are never reused across decisions.
current_payload_quality: Mapping[str, Any] = {}


def verify_payload(payload: Mapping[str, Any], market: str) -> dict[str, Any]:
    freeze = payload.get(FIELD)
    if not isinstance(freeze, Mapping):
        if _freeze_status_applicable(payload, market):
            raise ValueError("eligible Daily Stock decision is missing PR29.1 freeze")
        return {"ok": True, "status": "not_applicable"}
    _validate_freeze(freeze)
    if freeze.get("market") != market:
        raise ValueError("freeze market mismatch")
    if str(freeze.get("decision_at") or "") != str(payload.get("generated_at") or ""):
        raise ValueError("freeze decision timestamp mismatch")
    return {
        "ok": True,
        "status": freeze.get("status"),
        "candidate_count": freeze.get("candidate_count"),
        "economically_evaluable_count": freeze.get("economically_evaluable_count"),
        "freeze_sha256": freeze.get("freeze_sha256"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["gpw", "us"], required=True)
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    default = Path("data/investments/gpw_daily_pick.json" if args.market == "gpw" else "data/investments/us_daily_stock.json")
    path = args.payload or default
    payload = _read(path)
    if args.verify:
        print(json.dumps(verify_payload(payload, args.market), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    global current_payload_quality
    current_payload_quality = payload.get("data_quality") if isinstance(payload.get("data_quality"), Mapping) else {}
    _engine, config, fetcher, builder, normalizer = _production_context(args.market)
    out, changed = apply_freeze(
        payload,
        market=args.market,
        config=config,
        universe=list(config["universe"]),
        bar_fetcher=fetcher,
        exact_builder=builder,
        normalizer=normalizer,
    )
    if changed:
        _atomic_write(path, out)
    result = verify_payload(out, args.market)
    result["changed"] = changed
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

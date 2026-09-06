#!/usr/bin/env python3
"""Prospective BRACE Portfolio 10K learning loop for PLN FX and option hedging.

The loop is deliberately separate from broker execution. It freezes T0 portfolio,
FX and option-market state, settles only previously frozen counterfactuals, and
may automatically promote a *bounded production policy* when pre-committed
validation gates pass. The learner can never expand the risk envelope.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, stdev
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "portfolio10k"
PORTFOLIO = ROOT / "data" / "investments" / "portfolio_10k.json"
ENVELOPE = DATA / "brace_learning_envelope_v1.json"
PRODUCTION_POLICY = DATA / "brace_production_learning_policy.json"
OPTION_MARKET = DATA / "option_market_snapshot.json"
STATE_ROOT = DATA / "brace_learning_v1"
SNAPSHOT_DIR = STATE_ROOT / "snapshots"
SETTLEMENTS = STATE_ROOT / "counterfactual_settlements.jsonl"
STATE = STATE_ROOT / "learning_state.json"
PROMOTIONS = STATE_ROOT / "promotion_history.jsonl"
PRODUCTION_HEDGE_PLAN = DATA / "production_hedge_plan.json"

SCHEMA = "brace-portfolio-learning-loop-v1"
SNAPSHOT_SCHEMA = "brace-portfolio-learning-snapshot-v1"
SETTLEMENT_SCHEMA = "brace-portfolio-counterfactual-settlement-v1"
PLAN_SCHEMA = "brace-portfolio-production-hedge-plan-v1"
PROMOTION_SCHEMA = "brace-portfolio-auto-promotion-v1"
HORIZONS = (7, 30, 90)
PROMOTION_HORIZON = 30
MAX_SETTLEMENT_LAG_DAYS = 3
Z95 = 1.959963984540054


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            row = json.loads(raw)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _as_date(value: Any) -> date:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def _validate_envelope(envelope: Mapping[str, Any]) -> None:
    if envelope.get("schema_version") != "brace-portfolio-learning-envelope-v1":
        raise ValueError("BRACE learning envelope schema mismatch")
    if envelope.get("learner_may_expand_envelope") is not False:
        raise ValueError("learner_may_expand_envelope must remain false")
    if envelope.get("real_broker_integration") is not False or envelope.get("trade_execution_authority") is not False:
        raise ValueError("BRACE learning loop cannot receive broker/trade authority")
    if envelope.get("historical_backfill") is not False or envelope.get("prospective_only") is not True:
        raise ValueError("BRACE learning must remain prospective-only")
    ratios = list((envelope.get("fx_hedge") or {}).get("allowed_ratios") or [])
    if ratios != sorted(set(ratios)) or not ratios or ratios[0] != 0.0 or ratios[-1] > 1.0:
        raise ValueError("invalid governed FX hedge ratios")
    options = envelope.get("options_hedge") or {}
    if options.get("naked_short_options") is not False:
        raise ValueError("naked short options are prohibited")


def _validate_policy(policy: Mapping[str, Any], envelope: Mapping[str, Any]) -> None:
    if policy.get("schema_version") != "brace-portfolio-production-learning-policy-v1":
        raise ValueError("BRACE production learning policy schema mismatch")
    safety = policy.get("safety") or {}
    if safety.get("learner_may_expand_envelope") is not False or safety.get("real_broker_integration") is not False:
        raise ValueError("production learning policy violates fixed safety boundary")
    allowed_ratios = set((envelope.get("fx_hedge") or {}).get("allowed_ratios") or [])
    for regime in (policy.get("fx_hedge_ratio_by_regime") or {}).values():
        for ratio in (regime or {}).values():
            if float(ratio) not in allowed_ratios:
                raise ValueError("production FX ratio outside fixed envelope")
    allowed_options = {"NONE", *set((envelope.get("options_hedge") or {}).get("allowed_strategies") or [])}
    if any(str(x) not in allowed_options for x in (policy.get("options_strategy_by_regime") or {}).values()):
        raise ValueError("production option strategy outside fixed envelope")


def _fx_exposures(portfolio: Mapping[str, Any], envelope: Mapping[str, Any]) -> dict[str, Any]:
    supported = envelope.get("supported_fx_pairs") or {}
    result: dict[str, Any] = {}
    for currency, pair in supported.items():
        rows = [p for p in (portfolio.get("positions") or []) if p.get("status") == "active" and p.get("currency") == currency]
        notional_pln = sum(max(0.0, _finite(p.get("current_value_pln"))) for p in rows)
        local = sum(max(0.0, _finite(p.get("current_value_pln"))) / max(_finite(p.get("current_fx_to_pln")), 1e-12) for p in rows if _finite(p.get("current_fx_to_pln")) > 0)
        weighted_fx = notional_pln / local if local > 0 else None
        fx_times = sorted(str(p.get("current_fx_updated_at") or "") for p in rows if p.get("current_fx_updated_at"))
        result[currency] = {
            "pair": pair,
            "position_count": len(rows),
            "notional_pln": round(notional_pln, 8),
            "notional_local": round(local, 8),
            "spot": round(weighted_fx, 8) if weighted_fx else None,
            "fx_observed_at": fx_times[-1] if fx_times else None,
            "data_status": "OK" if (notional_pln == 0 or weighted_fx) else "DATA_GAP",
        }
    return result


def _market_regime(option_market: Mapping[str, Any] | None) -> str:
    context = (option_market or {}).get("market_context") or {}
    regime = str(context.get("fx_regime") or "DEFAULT").upper().strip()
    return regime if regime else "DEFAULT"


def _eligible_option_strategies(option_market: Mapping[str, Any] | None, nav: float, envelope: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not option_market:
        return []
    cfg = envelope.get("options_hedge") or {}
    allowed = set(cfg.get("allowed_strategies") or [])
    output: list[dict[str, Any]] = []
    for raw in option_market.get("strategies", []) or []:
        row = dict(raw)
        strategy = str(row.get("strategy_type") or "")
        if strategy not in allowed or row.get("market_data_complete") is not True:
            continue
        dte = int(_finite(row.get("dte"), -1))
        spread = _finite(row.get("relative_bid_ask_spread"), 999.0)
        premium = _finite(row.get("net_premium_pln"), -1.0)
        if not int(cfg.get("minimum_dte", 0)) <= dte <= int(cfg.get("maximum_dte", 9999)):
            continue
        if spread > _finite(cfg.get("maximum_relative_bid_ask_spread"), 0.20):
            continue
        if premium > nav * _finite(cfg.get("maximum_single_premium_fraction_nav"), 0.005):
            continue
        if row.get("contains_naked_short_option") is True:
            continue
        if not row.get("strategy_id") or _finite(row.get("hedged_notional_pln")) <= 0:
            continue
        row["entry_value_pln"] = premium
        output.append(row)
    return sorted(output, key=lambda x: (_finite(x.get("relative_bid_ask_spread"), 999.0), _finite(x.get("net_premium_pln"), 999999.0)))


def _policy_ratio(policy: Mapping[str, Any], regime: str, currency: str) -> float:
    by_regime = policy.get("fx_hedge_ratio_by_regime") or {}
    values = by_regime.get(regime) or by_regime.get("DEFAULT") or {}
    return _finite(values.get(currency), 0.0)


def _policy_option(policy: Mapping[str, Any], regime: str) -> str:
    by_regime = policy.get("options_strategy_by_regime") or {}
    return str(by_regime.get(regime) or by_regime.get("DEFAULT") or "NONE")


def capture(now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    envelope = _read(ENVELOPE, {})
    policy = _read(PRODUCTION_POLICY, {})
    portfolio = _read(PORTFOLIO, {})
    option_market = _read(OPTION_MARKET, None)
    _validate_envelope(envelope)
    _validate_policy(policy, envelope)
    nav = _finite(portfolio.get("total_value_pln"))
    if nav <= 0:
        nav = _finite(portfolio.get("cash_pln")) + sum(_finite(p.get("current_value_pln")) for p in portfolio.get("positions", []) or [])
    if nav <= 0:
        raise ValueError("portfolio NAV is unavailable")
    regime = _market_regime(option_market)
    fx = _fx_exposures(portfolio, envelope)
    options = _eligible_option_strategies(option_market, nav, envelope)
    source = {
        "portfolio_sha256": _sha(portfolio),
        "envelope_sha256": _sha(envelope),
        "production_policy_sha256": _sha(policy),
        "option_market_sha256": _sha(option_market) if option_market else None,
    }
    base = {
        "schema_version": SNAPSHOT_SCHEMA,
        "engine_id": "brace_portfolio_10k",
        "captured_at": now.isoformat(timespec="seconds"),
        "decision_date": now.date().isoformat(),
        "nav_pln": round(nav, 8),
        "market_regime": regime,
        "fx_exposures": fx,
        "option_strategies": options,
        "production_policy_at_t0": policy,
        "source_hashes": source,
        "horizons_days": list(HORIZONS),
        "prospective_only": True,
        "historical_backfill": False,
        "immutable": True,
    }
    base["snapshot_id"] = f"brace10k-{now.date().isoformat()}-{_sha(base)[:16]}"
    base["snapshot_sha256"] = _sha(base)
    path = SNAPSHOT_DIR / f"{now.date().isoformat()}.json"
    if path.exists():
        existing = _read(path, {})
        if existing.get("snapshot_sha256") != base["snapshot_sha256"]:
            return {"status": "ALREADY_CAPTURED_IMMUTABLE", "snapshot_id": existing.get("snapshot_id")}
        return {"status": "ALREADY_CAPTURED_IDENTICAL", "snapshot_id": existing.get("snapshot_id")}
    _write(path, base)
    return {"status": "CAPTURED", "snapshot_id": base["snapshot_id"], "fx_exposures": fx, "eligible_option_strategies": len(options)}


def _current_fx(portfolio: Mapping[str, Any], envelope: Mapping[str, Any]) -> dict[str, Any]:
    return _fx_exposures(portfolio, envelope)


def _option_exit_values(option_market: Mapping[str, Any] | None) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in (option_market or {}).get("strategies", []) or []:
        if row.get("strategy_id") and row.get("market_data_complete") is True and row.get("liquidation_value_pln") is not None:
            result[str(row["strategy_id"])] = _finite(row.get("liquidation_value_pln"))
    return result


def _settlement_key(snapshot_id: str, horizon: int, kind: str, identity: str) -> str:
    return f"{snapshot_id}:{horizon}d:{kind}:{identity}"


def settle(as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or _now().date()
    envelope = _read(ENVELOPE, {})
    _validate_envelope(envelope)
    portfolio = _read(PORTFOLIO, {})
    option_market = _read(OPTION_MARKET, None)
    current_fx = _current_fx(portfolio, envelope)
    option_exit = _option_exit_values(option_market)
    existing = {str(row.get("settlement_id")) for row in _jsonl(SETTLEMENTS)}
    created = 0
    gaps = 0
    cost_rate = _finite((envelope.get("fx_hedge") or {}).get("estimated_round_trip_cost_bps")) / 10000.0
    allowed_ratios = list((envelope.get("fx_hedge") or {}).get("allowed_ratios") or [])

    for path in sorted(SNAPSHOT_DIR.glob("*.json")):
        snap = _read(path, {})
        if snap.get("schema_version") != SNAPSHOT_SCHEMA or snap.get("prospective_only") is not True:
            continue
        signal_date = _as_date(snap.get("captured_at"))
        for horizon in HORIZONS:
            target = signal_date + timedelta(days=horizon)
            if as_of < target:
                continue
            if (as_of - target).days > MAX_SETTLEMENT_LAG_DAYS:
                continue
            nav = max(_finite(snap.get("nav_pln")), 1e-12)
            regime = str(snap.get("market_regime") or "DEFAULT")
            for currency, exposure in (snap.get("fx_exposures") or {}).items():
                if _finite(exposure.get("notional_pln")) <= 0:
                    continue
                start_fx = _finite(exposure.get("spot"))
                end_fx = _finite((current_fx.get(currency) or {}).get("spot"))
                if start_fx <= 0 or end_fx <= 0:
                    gaps += 1
                    continue
                local = _finite(exposure.get("notional_local"))
                for ratio in allowed_ratios:
                    identity = f"{currency}:{ratio:.2f}"
                    sid = _settlement_key(str(snap.get("snapshot_id")), horizon, "FX", identity)
                    if sid in existing:
                        continue
                    gross = local * (start_fx - end_fx) * float(ratio)
                    cost = _finite(exposure.get("notional_pln")) * float(ratio) * cost_rate
                    net = gross - cost
                    row = {
                        "schema_version": SETTLEMENT_SCHEMA,
                        "settlement_id": sid,
                        "snapshot_id": snap.get("snapshot_id"),
                        "signal_date": signal_date.isoformat(),
                        "evaluation_date": as_of.isoformat(),
                        "horizon_days": horizon,
                        "kind": "FX",
                        "currency": currency,
                        "market_regime": regime,
                        "variant": {"hedge_ratio": ratio},
                        "counterfactual_baseline": "NO_FX_HEDGE",
                        "start_fx": start_fx,
                        "end_fx": end_fx,
                        "gross_hedge_pnl_pln": round(gross, 8),
                        "transaction_cost_pln": round(cost, 8),
                        "net_hedge_pnl_pln": round(net, 8),
                        "utility_improvement": round(net / nav, 10),
                        "economically_evaluable": True,
                        "prospective_only": True,
                    }
                    row["row_sha256"] = _sha(row)
                    _append(SETTLEMENTS, row)
                    existing.add(sid)
                    created += 1

            for strategy in snap.get("option_strategies", []) or []:
                strategy_id = str(strategy.get("strategy_id") or "")
                sid = _settlement_key(str(snap.get("snapshot_id")), horizon, "OPTION", strategy_id)
                if not strategy_id or sid in existing:
                    continue
                if strategy_id not in option_exit:
                    gaps += 1
                    continue
                entry = _finite(strategy.get("entry_value_pln"))
                exit_value = option_exit[strategy_id]
                net = exit_value - entry
                row = {
                    "schema_version": SETTLEMENT_SCHEMA,
                    "settlement_id": sid,
                    "snapshot_id": snap.get("snapshot_id"),
                    "signal_date": signal_date.isoformat(),
                    "evaluation_date": as_of.isoformat(),
                    "horizon_days": horizon,
                    "kind": "OPTION",
                    "strategy_id": strategy_id,
                    "strategy_type": strategy.get("strategy_type"),
                    "market_regime": regime,
                    "counterfactual_baseline": "NO_OPTIONS_HEDGE",
                    "entry_value_pln": round(entry, 8),
                    "liquidation_value_pln": round(exit_value, 8),
                    "net_hedge_pnl_pln": round(net, 8),
                    "utility_improvement": round(net / nav, 10),
                    "economically_evaluable": True,
                    "prospective_only": True,
                    "market_prices_required": True,
                }
                row["row_sha256"] = _sha(row)
                _append(SETTLEMENTS, row)
                existing.add(sid)
                created += 1
    return {"status": "SETTLED", "created": created, "data_gaps": gaps}


def _stats(values: Iterable[float]) -> dict[str, Any]:
    rows = list(values)
    if not rows:
        return {"n": 0, "mean": None, "ci_low": None, "ci_high": None}
    avg = mean(rows)
    if len(rows) < 2:
        low = high = avg
    else:
        se = stdev(rows) / math.sqrt(len(rows))
        low, high = avg - Z95 * se, avg + Z95 * se
    return {"n": len(rows), "mean": round(avg, 10), "ci_low": round(low, 10), "ci_high": round(high, 10)}


def build_counterfactual_report() -> dict[str, Any]:
    rows = [r for r in _jsonl(SETTLEMENTS) if r.get("horizon_days") == PROMOTION_HORIZON and r.get("economically_evaluable") is True]
    fx: dict[str, Any] = {}
    options: dict[str, Any] = {}
    for currency in sorted({str(r.get("currency")) for r in rows if r.get("kind") == "FX"}):
        fx[currency] = {}
        currency_rows = [r for r in rows if r.get("kind") == "FX" and r.get("currency") == currency]
        for regime in sorted({str(r.get("market_regime") or "DEFAULT") for r in currency_rows}):
            fx[currency][regime] = {}
            regime_rows = [r for r in currency_rows if str(r.get("market_regime") or "DEFAULT") == regime]
            for ratio in sorted({_finite((r.get("variant") or {}).get("hedge_ratio")) for r in regime_rows}):
                vals = [_finite(r.get("utility_improvement")) for r in regime_rows if _finite((r.get("variant") or {}).get("hedge_ratio")) == ratio]
                fx[currency][regime][f"{ratio:.2f}"] = _stats(vals)
    for strategy in sorted({str(r.get("strategy_type")) for r in rows if r.get("kind") == "OPTION"}):
        options[strategy] = {}
        strategy_rows = [r for r in rows if r.get("kind") == "OPTION" and r.get("strategy_type") == strategy]
        for regime in sorted({str(r.get("market_regime") or "DEFAULT") for r in strategy_rows}):
            options[strategy][regime] = _stats(_finite(r.get("utility_improvement")) for r in strategy_rows if str(r.get("market_regime") or "DEFAULT") == regime)
    report = {
        "schema_version": "brace-portfolio-counterfactual-report-v1",
        "generated_at": _now().isoformat(timespec="seconds"),
        "promotion_horizon_days": PROMOTION_HORIZON,
        "fx": fx,
        "options": options,
        "settlement_count": len(rows),
        "causal_scope": "frozen hedge-policy counterfactuals only; no claim beyond the frozen variants",
    }
    report["evidence_sha256"] = _sha(report)
    return report


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "candidate_signature": None,
        "candidate_evidence_sha256": None,
        "consecutive_confirmations": 0,
        "last_promotion_id": None,
        "last_evidence_sha256": None,
    }


def _candidate(report: Mapping[str, Any], policy: Mapping[str, Any], envelope: Mapping[str, Any]) -> dict[str, Any] | None:
    validation = envelope.get("validation") or {}
    min_mean = _finite(validation.get("minimum_mean_utility_improvement"), 0.001)
    min_low = _finite(validation.get("minimum_ci_lower_bound_utility"), 0.0)
    fx_min = int(validation.get("fx_minimum_resolved_observations", 30))
    option_min = int(validation.get("options_minimum_resolved_observations", 20))
    best: dict[str, Any] | None = None
    for currency, regimes in (report.get("fx") or {}).items():
        for regime, ratios in (regimes or {}).items():
            for ratio_text, stat in (ratios or {}).items():
                ratio = float(ratio_text)
                if ratio == _policy_ratio(policy, regime, currency):
                    continue
                if int(stat.get("n") or 0) < fx_min or stat.get("ci_low") is None:
                    continue
                if _finite(stat.get("mean")) < min_mean or _finite(stat.get("ci_low")) <= min_low:
                    continue
                score = _finite(stat.get("ci_low"))
                row = {"kind": "FX", "currency": currency, "regime": regime, "target_ratio": ratio, "statistics": stat, "score": score}
                if best is None or score > _finite(best.get("score"), -999.0):
                    best = row
    for strategy, regimes in (report.get("options") or {}).items():
        for regime, stat in (regimes or {}).items():
            if strategy == _policy_option(policy, regime):
                continue
            if int(stat.get("n") or 0) < option_min or stat.get("ci_low") is None:
                continue
            if _finite(stat.get("mean")) < min_mean or _finite(stat.get("ci_low")) <= min_low:
                continue
            score = _finite(stat.get("ci_low"))
            row = {"kind": "OPTION", "strategy": strategy, "regime": regime, "statistics": stat, "score": score}
            if best is None or score > _finite(best.get("score"), -999.0):
                best = row
    return best


def _step_ratio(current: float, target: float, envelope: Mapping[str, Any]) -> float:
    limit = _finite((envelope.get("fx_hedge") or {}).get("promotion_step_limit"), 0.25)
    allowed = sorted(float(x) for x in (envelope.get("fx_hedge") or {}).get("allowed_ratios") or [])
    desired = max(current - limit, min(current + limit, target))
    return min(allowed, key=lambda x: abs(x - desired))


def auto_promote() -> dict[str, Any]:
    envelope = _read(ENVELOPE, {})
    policy = _read(PRODUCTION_POLICY, {})
    _validate_envelope(envelope)
    _validate_policy(policy, envelope)
    report = build_counterfactual_report()
    state = _read(STATE, _default_state()) or _default_state()
    candidate = _candidate(report, policy, envelope)
    evidence_hash = str(report.get("evidence_sha256"))
    signature = _sha(candidate) if candidate else None
    confirmations = int(state.get("consecutive_confirmations") or 0)
    if candidate and signature == state.get("candidate_signature") and evidence_hash != state.get("candidate_evidence_sha256"):
        confirmations += 1
    elif candidate:
        confirmations = 1
    else:
        confirmations = 0
    required = int((envelope.get("validation") or {}).get("required_consecutive_confirmations", 2))
    status = "NO_VALIDATED_PROMOTION"
    promotion_id = None
    if candidate and confirmations >= required:
        updated = deepcopy(policy)
        regime = str(candidate.get("regime") or "DEFAULT")
        if candidate["kind"] == "FX":
            currency = str(candidate["currency"])
            current = _policy_ratio(policy, regime, currency)
            new_ratio = _step_ratio(current, float(candidate["target_ratio"]), envelope)
            updated.setdefault("fx_hedge_ratio_by_regime", {}).setdefault(regime, deepcopy((updated.get("fx_hedge_ratio_by_regime") or {}).get("DEFAULT") or {"USD": 0.0, "EUR": 0.0}))[currency] = new_ratio
            change = {"kind": "FX", "currency": currency, "regime": regime, "from": current, "to": new_ratio, "validated_target": candidate["target_ratio"]}
        else:
            old = _policy_option(policy, regime)
            updated.setdefault("options_strategy_by_regime", {})[regime] = str(candidate["strategy"])
            change = {"kind": "OPTION", "regime": regime, "from": old, "to": candidate["strategy"]}
        promotion_id = f"brace10k-promotion-{_sha({'candidate': candidate, 'evidence': evidence_hash})[:20]}"
        updated["policy_version"] = int(policy.get("policy_version") or 0) + 1
        updated["status"] = "AUTO_PROMOTED_BOUNDED_POLICY"
        updated["promotion"] = {
            "automatic": True,
            "manual_approval_required": False,
            "last_promotion_id": promotion_id,
            "evidence_sha256": evidence_hash,
            "validation_epoch_id": f"brace10k-epoch-{evidence_hash[:16]}",
        }
        _validate_policy(updated, envelope)
        _write(PRODUCTION_POLICY, updated)
        event = {
            "schema_version": PROMOTION_SCHEMA,
            "promotion_id": promotion_id,
            "promoted_at": _now().isoformat(timespec="seconds"),
            "engine_id": "brace_portfolio_10k",
            "change": change,
            "candidate": candidate,
            "evidence_sha256": evidence_hash,
            "automatic": True,
            "manual_approval_required": False,
            "bounded_by_envelope_sha256": _sha(envelope),
            "trade_execution_authority": False,
        }
        event["row_sha256"] = _sha(event)
        _append(PROMOTIONS, event)
        confirmations = 0
        status = "AUTO_PROMOTED"
    state.update({
        "schema_version": SCHEMA,
        "generated_at": _now().isoformat(timespec="seconds"),
        "candidate_signature": signature,
        "candidate_evidence_sha256": evidence_hash if candidate else None,
        "consecutive_confirmations": confirmations,
        "required_confirmations": required,
        "last_promotion_id": promotion_id or state.get("last_promotion_id"),
        "last_evidence_sha256": evidence_hash,
        "status": status,
        "candidate": candidate,
        "counterfactual_report": report,
    })
    _write(STATE, state)
    return {"status": status, "promotion_id": promotion_id, "candidate": candidate, "confirmations": confirmations}


def build_production_hedge_plan(now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    envelope = _read(ENVELOPE, {})
    policy = _read(PRODUCTION_POLICY, {})
    portfolio = _read(PORTFOLIO, {})
    option_market = _read(OPTION_MARKET, None)
    _validate_envelope(envelope)
    _validate_policy(policy, envelope)
    nav = _finite(portfolio.get("total_value_pln")) or (_finite(portfolio.get("cash_pln")) + sum(_finite(p.get("current_value_pln")) for p in portfolio.get("positions", []) or []))
    fx = _fx_exposures(portfolio, envelope)
    regime = _market_regime(option_market)
    fx_targets = []
    for currency, exposure in fx.items():
        ratio = _policy_ratio(policy, regime, currency)
        notional = _finite(exposure.get("notional_pln"))
        fx_targets.append({
            "currency": currency,
            "pair": exposure.get("pair"),
            "exposure_pln": round(notional, 8),
            "target_hedge_ratio": ratio,
            "target_hedge_notional_pln": round(notional * ratio, 8),
            "market_spot": exposure.get("spot"),
            "status": "TARGET_ONLY_NO_BROKER_EXECUTION" if notional > 0 else "NO_EXPOSURE",
        })
    desired_option = _policy_option(policy, regime)
    eligible = _eligible_option_strategies(option_market, nav, envelope)
    selected = next((x for x in eligible if x.get("strategy_type") == desired_option), None) if desired_option != "NONE" else None
    option_status = "NONE_BY_PRODUCTION_POLICY" if desired_option == "NONE" else ("ELIGIBLE_MARKET_QUOTE_AVAILABLE" if selected else "OPTIONS_MARKET_DATA_UNAVAILABLE_OR_INELIGIBLE")
    plan = {
        "schema_version": PLAN_SCHEMA,
        "generated_at": now.isoformat(timespec="seconds"),
        "engine_id": "brace_portfolio_10k",
        "base_currency": "PLN",
        "market_regime": regime,
        "production_policy_version": policy.get("policy_version"),
        "production_policy_status": policy.get("status"),
        "portfolio_nav_pln": round(nav, 8),
        "fx_targets": fx_targets,
        "options": {
            "desired_strategy": desired_option,
            "status": option_status,
            "selected_market_strategy": selected,
            "eligible_market_strategy_count": len(eligible),
        },
        "execution": {
            "paper_target_only": True,
            "real_broker_integration": False,
            "trade_execution_authority": False,
        },
        "source_sha256": {
            "portfolio": _sha(portfolio),
            "policy": _sha(policy),
            "envelope": _sha(envelope),
            "option_market": _sha(option_market) if option_market else None,
        },
    }
    plan["content_sha256"] = _sha(plan)
    _write(PRODUCTION_HEDGE_PLAN, plan)
    return plan


def verify() -> dict[str, Any]:
    envelope = _read(ENVELOPE, {})
    policy = _read(PRODUCTION_POLICY, {})
    _validate_envelope(envelope)
    _validate_policy(policy, envelope)
    for path in SNAPSHOT_DIR.glob("*.json"):
        row = _read(path, {})
        stored = row.get("snapshot_sha256")
        body = dict(row)
        body.pop("snapshot_sha256", None)
        if stored != _sha(body):
            raise ValueError(f"snapshot hash mismatch: {path}")
    ids: set[str] = set()
    for row in _jsonl(SETTLEMENTS):
        sid = str(row.get("settlement_id") or "")
        if not sid or sid in ids:
            raise ValueError("duplicate settlement id")
        ids.add(sid)
        body = dict(row)
        stored = body.pop("row_sha256", None)
        if stored != _sha(body):
            raise ValueError(f"settlement hash mismatch: {sid}")
        if row.get("prospective_only") is not True:
            raise ValueError("non-prospective settlement found")
    plan = _read(PRODUCTION_HEDGE_PLAN, None)
    if plan:
        if (plan.get("execution") or {}).get("real_broker_integration") is not False or (plan.get("execution") or {}).get("trade_execution_authority") is not False:
            raise ValueError("production hedge plan gained execution authority")
    return {"ok": True, "snapshots": len(list(SNAPSHOT_DIR.glob('*.json'))), "settlements": len(ids), "policy_version": policy.get("policy_version")}


def run_all() -> dict[str, Any]:
    result = {"capture": capture(), "settle": settle(), "promotion": auto_promote()}
    plan = build_production_hedge_plan()
    result["production_plan"] = {"status": plan["options"]["status"], "policy_version": plan["production_policy_version"]}
    result["verify"] = verify()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--settle", action="store_true")
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--build-production-plan", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--run-all", action="store_true")
    args = parser.parse_args()
    if args.run_all or not any((args.capture, args.settle, args.promote, args.build_production_plan, args.verify)):
        result = run_all()
    else:
        result: dict[str, Any] = {}
        if args.capture:
            result["capture"] = capture()
        if args.settle:
            result["settle"] = settle()
        if args.promote:
            result["promotion"] = auto_promote()
        if args.build_production_plan:
            plan = build_production_hedge_plan()
            result["production_plan"] = {"policy_version": plan["production_policy_version"], "options": plan["options"]["status"]}
        if args.verify:
            result["verify"] = verify()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

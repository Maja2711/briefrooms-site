#!/usr/bin/env python3
"""Prospective Stock Trading V1 Champion vs V2 Challenger promotion bridge.

This module closes the generation-level learning loop for the *entry-decision
source* while preserving the canonical production portfolio/risk kernel.

Formal evidence is frozen only after the market session. V1 and V2 are then
replayed under the same next-session-open, stop-first conservative methodology.
The primary fixed-N test and the confirmation fixed-N holdout are disjoint. A
PASS on both automatically promotes V2 market-by-market. After promotion V1
continues as a shadow parent and fresh paired evidence can automatically roll
V2 back. A V2 definition change invalidates prior authority and fails closed to
V1 until the new definition proves itself prospectively.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean, median
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

try:
    from scripts import statistical_promotion_gate as stats
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_discovery as discovery
    from scripts import stock_trading_v2_opportunity_engine as opportunity
    from scripts import stock_trading_v2_outcome_replay as replay
except ModuleNotFoundError:  # pragma: no cover
    import statistical_promotion_gate as stats
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_discovery as discovery
    import stock_trading_v2_opportunity_engine as opportunity
    import stock_trading_v2_outcome_replay as replay

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data/investments/stock_trading_generation_config.json"
STATE_PATH = ROOT / "data/investments/stock_trading_generation_state.json"
PAIR_ROOT = ROOT / "data/investments/stock_trading_generation_pairs"
OUTCOME_ROOT = ROOT / "data/investments/stock_trading_generation_outcomes"
V1_PATHS = {
    "GPW": ROOT / "data/investments/gpw_daily_pick.json",
    "US": ROOT / "data/investments/us_daily_stock.json",
}
V2_PATHS = {
    "GPW": ROOT / "data/investments/stock_trading_v2_opportunity/gpw.json",
    "US": ROOT / "data/investments/stock_trading_v2_opportunity/us.json",
}
MARKET_TZ = {"GPW": ZoneInfo("Europe/Warsaw"), "US": ZoneInfo("America/New_York")}
FREEZE_AFTER = {"GPW": (17, 10), "US": (16, 10)}
STATE_SCHEMA = "stock-trading-generation-state-v1"
PAIR_SCHEMA = "stock-trading-generation-pair-v1"
OUTCOME_SCHEMA = "stock-trading-generation-paired-outcome-v1"
CONFIG_SCHEMA = "stock-trading-generation-promotion-config-v1"
V1_TRADE_DECISION = {"GPW": "TRANSAKCJA", "US": "TRADE"}
V1_DATA_ERROR = {"GPW": {"AWARIA_DANYCH"}, "US": {"DATA_ERROR", "PENDING"}}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _read(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temp = Path(handle.name)
    temp.replace(path)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        raise ValueError("timestamp missing")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = _read(path)
    if not isinstance(config, dict) or config.get("schema_version") != CONFIG_SCHEMA:
        raise ValueError("generation promotion config schema mismatch")
    if config.get("enabled") is not True:
        raise ValueError("generation promotion must be enabled")
    if config.get("champion_generation") != "v1" or config.get("challenger_generation") != "v2":
        raise ValueError("generation identities mismatch")
    if config.get("promotion_scope") != "entry_decision_source":
        raise ValueError("unsupported generation promotion scope")
    evaluation = config.get("evaluation") or {}
    if int(evaluation.get("primary_fixed_paired_n") or 0) < 20:
        raise ValueError("primary generation fixed-N is too small")
    if int(evaluation.get("holdout_fixed_paired_n") or 0) < int(evaluation["primary_fixed_paired_n"]):
        raise ValueError("generation holdout cannot be smaller than primary")
    if int(evaluation.get("bootstrap_samples") or 0) < 1000:
        raise ValueError("generation bootstrap count too small")
    confidence = float(evaluation.get("confidence_level") or 0.0)
    if not 0.8 <= confidence < 1.0:
        raise ValueError("generation confidence out of bounds")
    production = config.get("production") or {}
    if production.get("automatic_promotion_enabled") is not True:
        raise ValueError("generation automatic promotion must be enabled")
    if production.get("manual_approval_required") is not False:
        raise ValueError("generation promotion must not require manual approval after formal PASS")
    if production.get("automatic_rollback_enabled") is not True:
        raise ValueError("generation automatic rollback must be enabled")
    if production.get("replacement_authority_enabled") is not False:
        raise ValueError("replacement authority must remain separately gated")
    return config


def definition_hash(repo_root: Path = ROOT, config: Mapping[str, Any] | None = None) -> str:
    config = config or load_config(repo_root / "data/investments/stock_trading_generation_config.json")
    parts: list[dict[str, str]] = []
    for relative in config.get("v2_definition_files") or []:
        path = repo_root / str(relative)
        if not path.is_file():
            raise FileNotFoundError(f"V2 definition file missing: {relative}")
        parts.append({"path": str(relative), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    if not parts:
        raise ValueError("V2 definition file set is empty")
    return _sha(parts)


def _state_body(state: Mapping[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(dict(state))
    body.pop("state_sha256", None)
    return body


def validate_state(state: Mapping[str, Any]) -> None:
    if state.get("schema_version") != STATE_SCHEMA:
        raise ValueError("generation state schema mismatch")
    controls = state.get("controls") or {}
    required = {
        "closed_loop_enabled": True,
        "automatic_promotion_enabled": True,
        "automatic_rollback_enabled": True,
        "manual_approval_required": False,
        "shared_canonical_portfolio_risk_kernel": True,
        "replacement_authority_enabled": False,
    }
    for key, expected in required.items():
        if controls.get(key) is not expected:
            raise ValueError(f"generation state invariant failed: {key}")
    markets = state.get("markets") or {}
    if set(markets) != {"GPW", "US"}:
        raise ValueError("generation state markets mismatch")
    for market, row in markets.items():
        if row.get("active_generation") not in {"v1", "v2"}:
            raise ValueError(f"{market} active generation invalid")
        if row.get("parent_generation") != "v1" or row.get("challenger_generation") != "v2":
            raise ValueError(f"{market} generation lineage invalid")
        if row.get("active_generation") == "v2":
            if not row.get("promoted_definition_sha256"):
                raise ValueError(f"{market} V2 active without promoted definition hash")
            if row.get("promoted_definition_sha256") != row.get("v2_definition_sha256"):
                raise ValueError(f"{market} active V2 definition is not the proven definition")
    stored = state.get("state_sha256")
    if stored is not None and str(stored) != _sha(_state_body(state)):
        raise ValueError("generation state hash mismatch")


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    state = _read(path)
    if not isinstance(state, dict):
        raise ValueError("generation state missing")
    validate_state(state)
    return state


def save_state(state: Mapping[str, Any], path: Path = STATE_PATH, *, now: datetime | None = None) -> dict[str, Any]:
    payload = copy.deepcopy(dict(state))
    payload["updated_at"] = _iso(now or datetime.now(timezone.utc))
    payload["state_sha256"] = None
    payload["state_sha256"] = _sha(_state_body(payload))
    validate_state(payload)
    _atomic(path, payload)
    return payload


def _source_hash(payload: Mapping[str, Any], hash_field: str | None = None) -> str:
    if hash_field and payload.get(hash_field):
        return str(payload[hash_field])
    return _sha(payload)


def _risk_plan(reference: Any, stop: Any, target: Any, *, entry_zone: Any = None, skip_above: Any = None,
               reward_risk: Any = None, risk_percent: Any = None) -> dict[str, Any] | None:
    ref, sl, tp = _finite(reference), _finite(stop), _finite(target)
    if ref is None or sl is None or tp is None or not (0 < sl < ref < tp):
        return None
    zone = []
    if isinstance(entry_zone, (list, tuple)):
        zone = [value for value in (_finite(x) for x in entry_zone) if value is not None]
    skip = _finite(skip_above)
    if skip is None and zone:
        skip = max(zone)
    return {
        "reference_price": ref,
        "stop": sl,
        "target": tp,
        "entry_zone": zone,
        "skip_above": skip,
        "reward_risk": _finite(reward_risk),
        "risk_percent": _finite(risk_percent),
    }


def v1_decision(payload: Mapping[str, Any], market: str) -> dict[str, Any]:
    market = market.upper()
    decision = str(payload.get("decision") or "")
    generated = str(payload.get("generated_at") or "")
    if decision in V1_DATA_ERROR[market]:
        return {"action": "INVALID", "reason": decision or "data_error", "generated_at": generated}
    if decision != V1_TRADE_DECISION[market]:
        return {"action": "CASH", "reason": decision or "no_trade", "generated_at": generated, "symbol": None, "risk_plan": None}
    selection = payload.get("selection") if isinstance(payload.get("selection"), Mapping) else {}
    snapshot = selection.get("market_snapshot") if isinstance(selection.get("market_snapshot"), Mapping) else {}
    reference = snapshot.get("last") if snapshot.get("last") is not None else selection.get("reference_price")
    plan = _risk_plan(
        reference,
        selection.get("stop"),
        selection.get("target"),
        entry_zone=selection.get("entry_zone"),
        skip_above=selection.get("skip_above"),
        reward_risk=selection.get("reward_risk"),
        risk_percent=selection.get("risk_percent"),
    )
    symbol = str(selection.get("symbol") or selection.get("ticker") or "").strip()
    if not symbol or plan is None:
        return {"action": "INVALID", "reason": "v1_trade_missing_valid_frozen_plan", "generated_at": generated}
    market_symbol = str(selection.get("market_data_symbol") or symbol).strip()
    if market == "GPW" and "." not in market_symbol:
        market_symbol += ".WA"
    return {
        "action": "LONG",
        "reason": "v1_trade_signal",
        "generated_at": generated,
        "symbol": symbol,
        "market_data_symbol": market_symbol,
        "risk_plan": plan,
    }


def v2_decision(payload: Mapping[str, Any], market: str) -> dict[str, Any]:
    market = market.upper()
    opportunity.validate_opportunity(payload)
    decision = payload.get("decision") or {}
    action = str(decision.get("action") or "")
    generated = str(payload.get("generated_at") or "")
    if action not in {"BUY", "REPLACE"}:
        return {"action": "CASH", "reason": action or "CASH", "generated_at": generated, "symbol": None, "risk_plan": None,
                "opportunity_action": action}
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), Mapping) else {}
    if candidate.get("eligible") is not True:
        return {"action": "INVALID", "reason": "v2_selected_candidate_not_eligible", "generated_at": generated}
    risk = candidate.get("research_risk_plan") if isinstance(candidate.get("research_risk_plan"), Mapping) else {}
    if risk.get("status") != "VALID_RESEARCH_REFERENCE" or risk.get("execution_ready") is not False:
        return {"action": "INVALID", "reason": "v2_research_risk_plan_invalid", "generated_at": generated}
    plan = _risk_plan(
        risk.get("reference_price"), risk.get("stop"), risk.get("target"),
        entry_zone=risk.get("entry_zone"), skip_above=risk.get("skip_above"),
        reward_risk=risk.get("reward_risk"), risk_percent=risk.get("risk_percent"),
    )
    symbol = str(candidate.get("symbol") or "").strip()
    market_symbol = str(candidate.get("market_data_symbol") or symbol).strip()
    if market == "GPW" and "." not in market_symbol:
        market_symbol += ".WA"
    if not symbol or plan is None:
        return {"action": "INVALID", "reason": "v2_selected_candidate_missing_valid_plan", "generated_at": generated}
    return {
        "action": "LONG",
        "reason": "v2_opportunity_signal",
        "generated_at": generated,
        "symbol": symbol,
        "market_data_symbol": market_symbol,
        "risk_plan": plan,
        "opportunity_action": action,
    }


def _source_current(decision: Mapping[str, Any], *, now: datetime, market: str, max_age_minutes: int) -> bool:
    try:
        generated = _parse(decision.get("generated_at"))
    except Exception:
        return False
    local_now = now.astimezone(MARKET_TZ[market])
    local_generated = generated.astimezone(MARKET_TZ[market])
    if local_generated.date() != local_now.date():
        return False
    age = (now.astimezone(timezone.utc) - generated).total_seconds() / 60.0
    return -5.0 <= age <= float(max_age_minutes)


def _after_market_close(now: datetime, market: str) -> bool:
    local = now.astimezone(MARKET_TZ[market])
    hour, minute = FREEZE_AFTER[market]
    return local.weekday() < 5 and (local.hour, local.minute) >= (hour, minute)


def pair_path(root: Path, market: str, session_date: str) -> Path:
    return root / market.lower() / f"{session_date}.json"


def validate_pair(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != PAIR_SCHEMA:
        raise ValueError("generation pair schema mismatch")
    if payload.get("market") not in {"GPW", "US"}:
        raise ValueError("generation pair market invalid")
    if payload.get("governance", {}).get("prospective_only") is not True or payload.get("governance", {}).get("immutable") is not True:
        raise ValueError("generation pair governance invalid")
    body = dict(payload)
    stored = str(body.pop("pair_sha256", ""))
    if not stored or stored != _sha(body):
        raise ValueError("generation pair hash mismatch")


def freeze_pair(
    market: str,
    *,
    now: datetime,
    v1_payload: Mapping[str, Any],
    v2_payload: Mapping[str, Any],
    definition_sha256: str,
    config: Mapping[str, Any],
    root: Path = PAIR_ROOT,
) -> dict[str, Any] | None:
    market = market.upper()
    if not _after_market_close(now, market):
        return None
    local_day = now.astimezone(MARKET_TZ[market]).date().isoformat()
    path = pair_path(root, market, local_day)
    if path.exists():
        existing = _read(path)
        validate_pair(existing)
        return existing
    left = v1_decision(v1_payload, market)
    right = v2_decision(v2_payload, market)
    freeze_cfg = config.get("freeze") or {}
    valid = (
        left.get("action") != "INVALID"
        and right.get("action") != "INVALID"
        and _source_current(left, now=now, market=market, max_age_minutes=int(freeze_cfg.get("v1_max_age_minutes") or 720))
        and _source_current(right, now=now, market=market, max_age_minutes=int(freeze_cfg.get("v2_max_age_minutes") or 240))
    )
    disagreement = (
        left.get("action") != right.get("action")
        or (left.get("action") == "LONG" and right.get("action") == "LONG" and str(left.get("market_data_symbol")) != str(right.get("market_data_symbol")))
    )
    payload: dict[str, Any] = {
        "schema_version": PAIR_SCHEMA,
        "market": market,
        "session_date": local_day,
        "frozen_at": _iso(now),
        "v2_definition_sha256": definition_sha256,
        "v1_source_sha256": _source_hash(v1_payload),
        "v2_source_sha256": _source_hash(v2_payload, "opportunity_sha256"),
        "v1": copy.deepcopy(dict(left)),
        "v2": copy.deepcopy(dict(right)),
        "eligible_for_formal_evidence": bool(valid and disagreement),
        "generation_disagreement": bool(disagreement),
        "governance": {
            "prospective_only": True,
            "immutable": True,
            "no_lookahead": True,
            "production_decision_influence_at_freeze": False,
            "activation_model": freeze_cfg.get("activation_model"),
            "same_bar_policy": freeze_cfg.get("same_bar_policy"),
        },
    }
    payload["pair_sha256"] = _sha(payload)
    validate_pair(payload)
    _atomic(path, payload)
    return payload


def _pseudo_event(pair: Mapping[str, Any], side: str) -> dict[str, Any]:
    row = pair[side]
    return contracts.make_experience_event(
        market=str(pair["market"]),
        symbol=str(row["market_data_symbol"]),
        decision_at=str(pair["frozen_at"]),
        session_date=str(pair["session_date"]),
        selected=True,
        source_engine=f"generation-{side}",
        source_schema_version=PAIR_SCHEMA,
        source_policy_version=str(pair.get("v2_definition_sha256") if side == "v2" else "v1-production-champion"),
        source_payload_sha256=str(pair["pair_sha256"]),
        candidate_state={"risk_plan": copy.deepcopy(row["risk_plan"])},
        recorded_at=str(pair["frozen_at"]),
    )


def _cash_result(horizon: int) -> dict[str, Any]:
    return {
        "status": "SETTLED",
        "reason": "cash_reference",
        "horizon_sessions": horizon,
        "net_return_percent": 0.0,
        "gross_return_percent": 0.0,
        "net_r": 0.0,
        "gross_r": 0.0,
        "exit_reason": "CASH",
    }


def _normalise_replay(result: Mapping[str, Any] | None, horizon: int) -> dict[str, Any] | None:
    if result is None:
        return None
    if result.get("status") == "NOT_ACTIVATED":
        return {
            **copy.deepcopy(dict(result)),
            "effective_status": "CASH_NOT_ACTIVATED",
            "net_return_percent": 0.0,
            "gross_return_percent": 0.0,
            "net_r": 0.0,
            "gross_r": 0.0,
        }
    if result.get("status") == "SETTLED":
        return copy.deepcopy(dict(result))
    if result.get("status") == "UNPLAYABLE":
        return copy.deepcopy(dict(result))
    return {"status": "UNPLAYABLE", "reason": "unexpected_replay_status", "horizon_sessions": horizon}


def outcome_path(root: Path, market: str, session_date: str) -> Path:
    return root / market.lower() / f"{session_date}.json"


def validate_outcome(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != OUTCOME_SCHEMA:
        raise ValueError("generation outcome schema mismatch")
    if payload.get("market") not in {"GPW", "US"}:
        raise ValueError("generation outcome market invalid")
    if payload.get("governance", {}).get("no_lookahead") is not True:
        raise ValueError("generation outcome lookahead invariant failed")
    body = dict(payload)
    stored = str(body.pop("outcome_sha256", ""))
    if not stored or stored != _sha(body):
        raise ValueError("generation outcome hash mismatch")


def settle_pair(
    pair: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    bars_provider: Callable[[str, str], Sequence[discovery.Bar]],
    settled_at: datetime | None = None,
) -> dict[str, Any] | None:
    validate_pair(pair)
    horizon = int((config.get("evaluation") or {}).get("horizon_sessions") or 5)
    cost = float((config.get("cost_stress_percent") or {})[str(pair["market"])])
    results: dict[str, Any] = {}
    for side in ("v1", "v2"):
        row = pair[side]
        action = row.get("action")
        if action == "CASH":
            results[side] = _cash_result(horizon)
            continue
        if action != "LONG":
            results[side] = {"status": "UNPLAYABLE", "reason": "invalid_frozen_action", "horizon_sessions": horizon}
            continue
        symbol = str(row.get("market_data_symbol") or "")
        bars = bars_provider(symbol, str(pair["market"]))
        replay_result = replay.replay_horizon(
            _pseudo_event(pair, side),
            bars,
            horizon_sessions=horizon,
            cost_stress_percent=cost,
            replay_version="generation-promotion-v1",
        )
        normalised = _normalise_replay(replay_result, horizon)
        if normalised is None:
            return None
        results[side] = normalised
    if any(results[side].get("status") == "UNPLAYABLE" for side in ("v1", "v2")):
        formal = False
        delta = None
    else:
        formal = bool(pair.get("eligible_for_formal_evidence"))
        delta = float(results["v2"].get("net_return_percent") or 0.0) - float(results["v1"].get("net_return_percent") or 0.0)
    payload: dict[str, Any] = {
        "schema_version": OUTCOME_SCHEMA,
        "market": pair["market"],
        "session_date": pair["session_date"],
        "settled_at": _iso(settled_at or datetime.now(timezone.utc)),
        "source_pair_sha256": pair["pair_sha256"],
        "v2_definition_sha256": pair["v2_definition_sha256"],
        "horizon_sessions": horizon,
        "formal_evidence_eligible": formal,
        "v1": results["v1"],
        "v2": results["v2"],
        "paired_net_incremental_return_percent": None if delta is None else round(delta, 8),
        "symbols": sorted({str(x) for x in (pair["v1"].get("market_data_symbol"), pair["v2"].get("market_data_symbol")) if x}),
        "governance": {
            "immutable": True,
            "no_lookahead": True,
            "same_frozen_decision_date": True,
            "production_decision_influence": False,
        },
    }
    payload["outcome_sha256"] = _sha(payload)
    validate_outcome(payload)
    return payload


def _default_bars_provider(config: Mapping[str, Any]) -> Callable[[str, str], Sequence[discovery.Bar]]:
    discovery_cfg = _read(ROOT / "data/investments/stock_trading_v2_discovery_config.json", {}) or {}
    network = discovery_cfg.get("network") or {}
    hosts = [str(x) for x in network.get("provider_hosts") or ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]]
    timeout = int(network.get("timeout_seconds") or 15)
    attempts = int(network.get("attempts_per_host") or 2)

    def provider(symbol: str, market: str) -> Sequence[discovery.Bar]:
        bars, _ = discovery.fetch_yahoo_history(
            symbol,
            range_value="6mo",
            timeout=timeout,
            attempts_per_host=attempts,
            hosts=hosts,
        )
        return bars

    return provider


def iter_json(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.json") if path.is_file()) if root.exists() else []


def settle_store(
    *,
    config: Mapping[str, Any],
    pair_root: Path = PAIR_ROOT,
    outcome_root: Path = OUTCOME_ROOT,
    bars_provider: Callable[[str, str], Sequence[discovery.Bar]] | None = None,
) -> dict[str, Any]:
    provider = bars_provider or _default_bars_provider(config)
    written = existing = pending = failed = 0
    failures: list[str] = []
    for path in iter_json(pair_root):
        pair = _read(path)
        validate_pair(pair)
        target = outcome_path(outcome_root, str(pair["market"]), str(pair["session_date"]))
        if target.exists():
            validate_outcome(_read(target))
            existing += 1
            continue
        try:
            outcome_payload = settle_pair(pair, config=config, bars_provider=provider)
            if outcome_payload is None:
                pending += 1
                continue
            _atomic(target, outcome_payload)
            written += 1
        except Exception as exc:
            failed += 1
            failures.append(f"{pair.get('market')}:{pair.get('session_date')}:{type(exc).__name__}:{str(exc)[:180]}")
    return {"written": written, "existing": existing, "pending": pending, "failed": failed, "failures": failures[:20]}


def _eligible_outcomes(
    *,
    market: str,
    definition_sha256: str,
    after: datetime,
    outcome_root: Path = OUTCOME_ROOT,
    pair_root: Path = PAIR_ROOT,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in iter_json(outcome_root / market.lower()):
        outcome = _read(path)
        validate_outcome(outcome)
        if outcome.get("formal_evidence_eligible") is not True:
            continue
        if outcome.get("v2_definition_sha256") != definition_sha256:
            continue
        pair = _read(pair_path(pair_root, market, str(outcome["session_date"])))
        validate_pair(pair)
        if _parse(pair["frozen_at"]) <= after:
            continue
        rows.append({**outcome, "frozen_at": pair["frozen_at"]})
    return sorted(rows, key=lambda row: (str(row.get("frozen_at")), str(row.get("session_date"))))


def paired_metrics(rows: Sequence[Mapping[str, Any]], *, sample_id: str, config: Mapping[str, Any]) -> dict[str, Any]:
    values = [float(row["paired_net_incremental_return_percent"]) for row in rows]
    if not values:
        return {"n": 0}
    evaluation = config.get("evaluation") or {}
    low, high = stats.deterministic_bootstrap_mean_ci(
        values,
        samples=int(evaluation["bootstrap_samples"]),
        confidence=float(evaluation["confidence_level"]),
        seed_text=sample_id,
    )
    dates = [datetime.fromisoformat(str(row["session_date"])).date() for row in rows]
    symbols = {symbol for row in rows for symbol in row.get("symbols") or [] if symbol}
    half = max(1, len(values) // 2)
    positives = [value for value in values if value > 0]
    positive_sum = sum(positives)
    concentration = None if positive_sum <= 0 else max(positives) / positive_sum
    v1 = [float((row.get("v1") or {}).get("net_return_percent") or 0.0) for row in rows]
    v2 = [float((row.get("v2") or {}).get("net_return_percent") or 0.0) for row in rows]
    return {
        "n": len(values),
        "v1_net_mean_return_percent": round(fmean(v1), 8),
        "v2_net_mean_return_percent": round(fmean(v2), 8),
        "paired_net_incremental_mean_percent": round(fmean(values), 8),
        "paired_net_incremental_median_percent": round(median(values), 8),
        "paired_net_positive_rate": round(sum(value > 0 for value in values) / len(values), 6),
        "bootstrap_ci_low_percent": round(low, 8),
        "bootstrap_ci_high_percent": round(high, 8),
        "unique_symbols": len(symbols),
        "span_days": (max(dates) - min(dates)).days if len(dates) > 1 else 0,
        "first_half_net_mean_percent": round(fmean(values[:half]), 8),
        "second_half_net_mean_percent": round(fmean(values[half:]), 8) if values[half:] else round(fmean(values[:half]), 8),
        "largest_positive_contribution_share": None if concentration is None else round(concentration, 8),
        "confidence_level": float(evaluation["confidence_level"]),
        "bootstrap_samples": int(evaluation["bootstrap_samples"]),
    }


def formal_gate(metrics: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[str, list[str]]:
    evaluation = config.get("evaluation") or {}
    reasons: list[str] = []
    if float(metrics.get("paired_net_incremental_mean_percent") or 0.0) < float(evaluation["minimum_net_incremental_return_percent"]):
        reasons.append("incremental_mean_below_minimum")
    if float(metrics.get("paired_net_positive_rate") or 0.0) < float(evaluation["minimum_net_positive_rate"]):
        reasons.append("positive_rate_below_minimum")
    if float(metrics.get("bootstrap_ci_low_percent") or 0.0) <= 0.0:
        reasons.append("bootstrap_lower_bound_not_positive")
    if int(metrics.get("unique_symbols") or 0) < int(evaluation["minimum_unique_symbols"]):
        reasons.append("insufficient_symbol_diversity")
    if int(metrics.get("span_days") or 0) < int(evaluation["minimum_span_days"]):
        reasons.append("validation_span_too_short")
    if evaluation.get("require_positive_first_half") and float(metrics.get("first_half_net_mean_percent") or 0.0) <= 0:
        reasons.append("first_half_not_positive")
    if evaluation.get("require_positive_second_half") and float(metrics.get("second_half_net_mean_percent") or 0.0) <= 0:
        reasons.append("second_half_not_positive")
    concentration = metrics.get("largest_positive_contribution_share")
    if concentration is not None and float(concentration) > float(evaluation["maximum_single_positive_contribution_share"]):
        reasons.append("single_positive_contribution_too_concentrated")
    return ("PASS", []) if not reasons else ("FAIL", reasons)


def rollback_gate(metrics: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[bool, list[str]]:
    rollback = config.get("rollback") or {}
    triggers = {
        "incremental_mean_bad": float(metrics.get("paired_net_incremental_mean_percent") or 0.0) <= float(rollback["maximum_net_incremental_mean_percent"]),
        "positive_rate_bad": float(metrics.get("paired_net_positive_rate") or 1.0) <= float(rollback["maximum_net_positive_rate"]),
        "bootstrap_upper_below_zero": float(metrics.get("bootstrap_ci_high_percent") or 1.0) < 0.0,
        "span_sufficient": int(metrics.get("span_days") or 0) >= int(rollback["minimum_span_days"]),
    }
    required = ["incremental_mean_bad", "positive_rate_bad", "span_sufficient"]
    if rollback.get("require_bootstrap_upper_bound_below_zero"):
        required.append("bootstrap_upper_below_zero")
    failed = [name for name in required if not triggers[name]]
    return not failed, failed


def _sample_payload(status: str, rows: Sequence[Mapping[str, Any]], metrics: Mapping[str, Any], reasons: Sequence[str], *, evaluated_at: datetime) -> dict[str, Any]:
    return {
        "status": status,
        "evaluated_at": _iso(evaluated_at),
        "sample_outcome_sha256": [str(row["outcome_sha256"]) for row in rows],
        "sample_session_dates": [str(row["session_date"]) for row in rows],
        "metrics": copy.deepcopy(dict(metrics)),
        "blocking_reasons": list(reasons),
    }


def reconcile_definition(state: dict[str, Any], current_hash: str, *, now: datetime) -> None:
    for market, row in state["markets"].items():
        previous = row.get("v2_definition_sha256")
        if not previous:
            row["v2_definition_sha256"] = current_hash
            row["validation_start_at"] = _iso(now)
            row["status"] = "COLLECTING_PRIMARY"
            continue
        if previous == current_hash:
            continue
        if row.get("active_generation") == "v2":
            row["active_generation"] = "v1"
            row["revision"] = int(row.get("revision") or 0) + 1
            row["rollback"] = {
                "reason": "v2_definition_changed_fail_closed",
                "at": _iso(now),
                "from_definition_sha256": previous,
                "to_definition_sha256": current_hash,
            }
        row["v2_definition_sha256"] = current_hash
        row["promoted_definition_sha256"] = None
        row["promoted_at"] = None
        row["primary"] = None
        row["holdout"] = None
        row["blocked_until"] = None
        row["validation_start_at"] = _iso(now)
        row["status"] = "COLLECTING_PRIMARY"


def _restart_after_cooldown(row: dict[str, Any], *, now: datetime) -> None:
    blocked = row.get("blocked_until")
    if not blocked or now < _parse(blocked):
        return
    if row.get("active_generation") != "v1":
        return
    row["primary"] = None
    row["holdout"] = None
    row["validation_start_at"] = _iso(now)
    row["blocked_until"] = None
    row["status"] = "COLLECTING_PRIMARY"


def evaluate_state(
    state: dict[str, Any],
    *,
    config: Mapping[str, Any],
    now: datetime,
    outcome_root: Path = OUTCOME_ROOT,
    pair_root: Path = PAIR_ROOT,
) -> dict[str, Any]:
    evaluation = config.get("evaluation") or {}
    primary_n = int(evaluation["primary_fixed_paired_n"])
    holdout_n = int(evaluation["holdout_fixed_paired_n"])
    rollback_n = int((config.get("rollback") or {})["fixed_paired_n"])
    report: dict[str, Any] = {"markets": {}}
    for market, row in state["markets"].items():
        _restart_after_cooldown(row, now=now)
        definition = str(row.get("v2_definition_sha256") or "")
        if not definition or not row.get("validation_start_at"):
            report["markets"][market] = {"status": row.get("status")}
            continue
        if row.get("blocked_until") and now < _parse(row["blocked_until"]):
            report["markets"][market] = {"status": "COOLDOWN", "blocked_until": row["blocked_until"]}
            continue

        if row.get("active_generation") == "v2":
            promoted_at = _parse(row["promoted_at"])
            rows = _eligible_outcomes(market=market, definition_sha256=definition, after=promoted_at,
                                      outcome_root=outcome_root, pair_root=pair_root)
            rollback_state = row.get("rollback") if isinstance(row.get("rollback"), Mapping) else {}
            consumed = set(rollback_state.get("monitored_outcome_sha256") or [])
            fresh = [item for item in rows if item["outcome_sha256"] not in consumed]
            if len(fresh) >= rollback_n:
                sample = fresh[:rollback_n]
                metrics = paired_metrics(sample, sample_id=f"rollback:{market}:{definition}:{row.get('revision')}", config=config)
                should_rollback, missing = rollback_gate(metrics, config)
                monitored = list(consumed) + [item["outcome_sha256"] for item in sample]
                row["rollback"] = {
                    "status": "TRIGGERED" if should_rollback else "MONITORING",
                    "evaluated_at": _iso(now),
                    "metrics": metrics,
                    "missing_trigger_conditions": missing,
                    "monitored_outcome_sha256": monitored,
                }
                if should_rollback:
                    row["active_generation"] = "v1"
                    row["revision"] = int(row.get("revision") or 0) + 1
                    row["status"] = "ROLLED_BACK_TO_V1"
                    row["promoted_definition_sha256"] = None
                    row["promoted_at"] = None
                    row["blocked_until"] = _iso(now + timedelta(days=int((config.get("rollback") or {})["cooldown_days"])))
                    row["validation_start_at"] = row["blocked_until"]
                    row["primary"] = None
                    row["holdout"] = None
            report["markets"][market] = {"status": row.get("status"), "active_generation": row.get("active_generation"), "rollback": row.get("rollback")}
            continue

        start = _parse(row["validation_start_at"])
        if row.get("primary") is None:
            available = _eligible_outcomes(market=market, definition_sha256=definition, after=start,
                                           outcome_root=outcome_root, pair_root=pair_root)
            if len(available) < primary_n:
                row["status"] = "COLLECTING_PRIMARY"
                report["markets"][market] = {"status": row["status"], "observed_n": len(available), "target_n": primary_n}
                continue
            sample = available[:primary_n]
            metrics = paired_metrics(sample, sample_id=f"primary:{market}:{definition}", config=config)
            status, reasons = formal_gate(metrics, config)
            row["primary"] = _sample_payload(status, sample, metrics, reasons, evaluated_at=now)
            if status != "PASS":
                row["status"] = "PRIMARY_REJECTED"
                row["blocked_until"] = _iso(now + timedelta(days=int((config.get("rollback") or {})["cooldown_days"])))
                report["markets"][market] = {"status": row["status"], "primary": row["primary"]}
                continue
            row["status"] = "COLLECTING_FRESH_HOLDOUT"
            row["holdout"] = {"status": "COLLECTING", "start_at": _iso(now), "observed_n": 0, "target_n": holdout_n}

        holdout = row.get("holdout") or {}
        if row.get("primary", {}).get("status") == "PASS" and holdout.get("status") == "COLLECTING":
            holdout_start = _parse(holdout["start_at"])
            available = _eligible_outcomes(market=market, definition_sha256=definition, after=holdout_start,
                                           outcome_root=outcome_root, pair_root=pair_root)
            primary_ids = set(row["primary"].get("sample_outcome_sha256") or [])
            available = [item for item in available if item["outcome_sha256"] not in primary_ids]
            if len(available) < holdout_n:
                holdout["observed_n"] = len(available)
                row["holdout"] = holdout
                row["status"] = "COLLECTING_FRESH_HOLDOUT"
                report["markets"][market] = {"status": row["status"], "observed_n": len(available), "target_n": holdout_n}
                continue
            sample = available[:holdout_n]
            if primary_ids.intersection({item["outcome_sha256"] for item in sample}):
                raise RuntimeError("primary/holdout generation samples overlap")
            metrics = paired_metrics(sample, sample_id=f"holdout:{market}:{definition}", config=config)
            status, reasons = formal_gate(metrics, config)
            row["holdout"] = _sample_payload(status, sample, metrics, reasons, evaluated_at=now)
            row["holdout"]["start_at"] = _iso(holdout_start)
            if status == "PASS":
                row["active_generation"] = "v2"
                row["promoted_definition_sha256"] = definition
                row["promoted_at"] = _iso(now)
                row["revision"] = int(row.get("revision") or 0) + 1
                row["status"] = "ACTIVE_V2_PRODUCTION_ENTRY_SOURCE"
                row["rollback"] = {"status": "MONITORING", "monitored_outcome_sha256": []}
            else:
                row["status"] = "HOLDOUT_REJECTED"
                row["blocked_until"] = _iso(now + timedelta(days=int((config.get("rollback") or {})["cooldown_days"])))
        report["markets"][market] = {"status": row.get("status"), "active_generation": row.get("active_generation"), "primary": row.get("primary"), "holdout": row.get("holdout")}
    return report


def verify_store(pair_root: Path = PAIR_ROOT, outcome_root: Path = OUTCOME_ROOT) -> dict[str, Any]:
    pairs = outcomes = 0
    for path in iter_json(pair_root):
        validate_pair(_read(path)); pairs += 1
    for path in iter_json(outcome_root):
        validate_outcome(_read(path)); outcomes += 1
    return {"pairs": pairs, "outcomes": outcomes, "ok": True}


def run(
    *,
    now: datetime | None = None,
    markets: Sequence[str] = ("GPW", "US"),
    config_path: Path = CONFIG_PATH,
    state_path: Path = STATE_PATH,
    pair_root: Path = PAIR_ROOT,
    outcome_root: Path = OUTCOME_ROOT,
    bars_provider: Callable[[str, str], Sequence[discovery.Bar]] | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    config = load_config(config_path)
    state = load_state(state_path)
    current_definition = definition_hash(ROOT, config)
    reconcile_definition(state, current_definition, now=now)
    frozen: dict[str, Any] = {}
    for market in markets:
        market = market.upper()
        v1_payload = _read(V1_PATHS[market], {})
        v2_payload = _read(V2_PATHS[market], {})
        if not isinstance(v1_payload, Mapping) or not isinstance(v2_payload, Mapping):
            frozen[market] = None
            continue
        try:
            pair = freeze_pair(
                market,
                now=now,
                v1_payload=v1_payload,
                v2_payload=v2_payload,
                definition_sha256=current_definition,
                config=config,
                root=pair_root,
            )
            frozen[market] = pair.get("pair_sha256") if pair else None
        except Exception as exc:
            frozen[market] = f"ERROR:{type(exc).__name__}:{str(exc)[:180]}"
    settlement = settle_store(config=config, pair_root=pair_root, outcome_root=outcome_root, bars_provider=bars_provider)
    evaluation = evaluate_state(state, config=config, now=now, outcome_root=outcome_root, pair_root=pair_root)
    saved = save_state(state, state_path, now=now)
    verify = verify_store(pair_root, outcome_root)
    return {
        "schema_version": "stock-trading-generation-promotion-run-v1",
        "generated_at": _iso(now),
        "v2_definition_sha256": current_definition,
        "frozen": frozen,
        "settlement": settlement,
        "evaluation": evaluation,
        "active_generation": {market: saved["markets"][market]["active_generation"] for market in ("GPW", "US")},
        "manual_approval_required": False,
        "automatic_promotion_enabled": True,
        "automatic_rollback_enabled": True,
        "store": verify,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stock Trading V1 Champion vs V2 Challenger generation promotion")
    parser.add_argument("--mode", choices=["run", "verify", "evaluate", "settle"], default="run")
    parser.add_argument("--market", choices=["all", "GPW", "US"], default="all")
    parser.add_argument("--now")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    parser.add_argument("--pair-root", type=Path, default=PAIR_ROOT)
    parser.add_argument("--outcome-root", type=Path, default=OUTCOME_ROOT)
    args = parser.parse_args()
    now = _parse(args.now) if args.now else datetime.now(timezone.utc)
    config = load_config(args.config)
    if args.mode == "verify":
        state = load_state(args.state)
        current = definition_hash(ROOT, config)
        print(json.dumps({"state": "OK", "active_generation": {m: state["markets"][m]["active_generation"] for m in ("GPW", "US")}, "current_v2_definition_sha256": current, "store": verify_store(args.pair_root, args.outcome_root)}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.mode == "settle":
        print(json.dumps(settle_store(config=config, pair_root=args.pair_root, outcome_root=args.outcome_root), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.mode == "evaluate":
        state = load_state(args.state)
        current = definition_hash(ROOT, config)
        reconcile_definition(state, current, now=now)
        result = evaluate_state(state, config=config, now=now, outcome_root=args.outcome_root, pair_root=args.pair_root)
        save_state(state, args.state, now=now)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    markets = ("GPW", "US") if args.market == "all" else (args.market,)
    print(json.dumps(run(now=now, markets=markets, config_path=args.config, state_path=args.state, pair_root=args.pair_root, outcome_root=args.outcome_root), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

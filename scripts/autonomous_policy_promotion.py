#!/usr/bin/env python3
"""PR35 — Policy Candidate + Autonomous Promotion Gate + Rollback v1.

The first autonomous scope is deliberately narrow: the Daily GPW and Daily US
minimum composite-score threshold.  The system never edits Python code, never
changes arbitrary config keys and never executes trades.  It can promote only
an allowlisted one-point threshold change after prospective train + shadow
validation, and can automatically roll it back after poor live paper outcomes.

State is private/durable (GitHub Actions artifact), not committed to the public
repository.  Production engines read it through ``policy_runtime_overlay``.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Mapping, Optional, Sequence

try:
    from learning_ledger import read_events, verify_chain
    from policy_runtime_overlay import REGISTRY_SCHEMA, registry_hash, validate_registry
except ModuleNotFoundError:  # pragma: no cover
    from scripts.learning_ledger import read_events, verify_chain
    from scripts.policy_runtime_overlay import REGISTRY_SCHEMA, registry_hash, validate_registry

ACTIVATION_FILENAME = "policy_activation.json"
REGISTRY_FILENAME = "policy_registry.json"
AUDIT_FILENAME = "promotion_ledger.jsonl"
SHADOW_FILENAME = "policy_shadow_outcomes.jsonl"
STATUS_FILENAME = "policy_promotion_status.json"

ACTIVATION_SCHEMA = "briefrooms-autonomous-policy-activation-v1"
AUDIT_SCHEMA = "briefrooms-policy-promotion-ledger-v1"
SHADOW_SCHEMA = "briefrooms-policy-shadow-outcome-v1"
STATUS_SCHEMA = "briefrooms-autonomous-policy-status-v1"

TRAIN_MIN_N = 30
VALIDATION_MIN_N = 20
VALIDATION_MAX_N = 50
ROLLBACK_MIN_N = 8
ROLLBACK_EMERGENCY_MIN_N = 3

POLICY_SPECS: dict[str, dict[str, Any]] = {
    "gpw_daily": {
        "config_path": "data/investments/gpw_daily_pick_config.json",
        "parameter": "minimum_composite_score",
        "gate": "minimum_composite_score",
        "lower": 68.0,
        "upper": 76.0,
        "step": 1.0,
        "history_dir": "data/investments/gpw_daily_pick_history",
        "market": "gpw",
    },
    "us_daily": {
        "config_path": "data/investments/us_daily_stock_config.json",
        "parameter": "target_score",
        "gate": "minimum_composite_score",
        "lower": 68.0,
        "upper": 76.0,
        "step": 1.0,
        "history_dir": "data/investments/us_daily_stock_history",
        "market": "us",
    },
}

CONTROLS = {
    "autonomous_promotion_enabled": True,
    "automatic_rollback_enabled": True,
    "code_mutation_enabled": False,
    "arbitrary_parameter_mutation_enabled": False,
    "trade_execution_enabled": False,
    "historical_backfill_enabled": False,
    "same_sample_train_and_validate_enabled": False,
    "multi_parameter_candidate_enabled": False,
    "llm_policy_mutation_enabled": False,
}


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}-{_sha([str(x) for x in parts])[:24]}"


def _parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("timestamp required")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    if path.name == REGISTRY_FILENAME:
        body.pop("registry_sha256", None)
        body["registry_sha256"] = registry_hash(body)
    text = json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        tmp = Path(handle.name)
    tmp.replace(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"non-object row in {path}")
        rows.append(row)
    return rows


def _append_line(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def verify_audit_chain(path: Path) -> dict[str, Any]:
    rows = _read_jsonl(path)
    prev = "GENESIS"
    for index, row in enumerate(rows):
        if row.get("schema_version") != AUDIT_SCHEMA:
            raise ValueError("promotion ledger schema mismatch")
        if row.get("previous_hash") != prev:
            raise ValueError(f"promotion ledger chain break at {index}")
        body = dict(row)
        stored = str(body.pop("event_hash", ""))
        if not stored or stored != _sha(body):
            raise ValueError(f"promotion ledger hash mismatch at {index}")
        prev = stored
    return {"events": len(rows), "head_hash": prev}


def append_audit(path: Path, event_type: str, payload: Mapping[str, Any], occurred_at: str) -> str:
    state = verify_audit_chain(path)
    body = {
        "schema_version": AUDIT_SCHEMA,
        "event_id": _stable_id("polevt", event_type, occurred_at, _sha(payload)),
        "event_type": event_type,
        "occurred_at": _iso(_parse_time(occurred_at)),
        "payload": dict(payload),
        "previous_hash": state["head_hash"],
    }
    body["event_hash"] = _sha(body)
    _append_line(path, body)
    return str(body["event_id"])


def _shadow_hash(row: Mapping[str, Any]) -> str:
    body = dict(row)
    body.pop("row_sha256", None)
    return _sha(body)


def verify_shadow(path: Path) -> dict[str, Any]:
    rows = _read_jsonl(path)
    ids: set[str] = set()
    for index, row in enumerate(rows):
        if row.get("schema_version") != SHADOW_SCHEMA:
            raise ValueError("shadow outcome schema mismatch")
        rid = str(row.get("shadow_outcome_id") or "")
        if not rid or rid in ids:
            raise ValueError("duplicate/empty shadow outcome id")
        ids.add(rid)
        if row.get("row_sha256") != _shadow_hash(row):
            raise ValueError(f"shadow outcome hash mismatch at {index}")
    return {"rows": len(rows)}


def ensure_activation(state_dir: Path, now: datetime) -> dict[str, Any]:
    path = state_dir / ACTIVATION_FILENAME
    existing = _read_json(path)
    if isinstance(existing, dict):
        if existing.get("schema_version") != ACTIVATION_SCHEMA:
            raise ValueError("PR35 activation schema mismatch")
        _parse_time(str(existing.get("activated_at") or ""))
        return existing
    if (state_dir / REGISTRY_FILENAME).exists() or (state_dir / AUDIT_FILENAME).exists():
        raise RuntimeError("PR35 activation missing while policy history exists; FAIL_CLOSED")
    payload = {
        "schema_version": ACTIVATION_SCHEMA,
        "activated_at": _iso(now),
        "prospective_only": True,
        "historical_backfill": False,
        "same_sample_train_and_validate": False,
    }
    _atomic_json(path, payload)
    return payload


def _baseline_entry(engine_id: str, config: Mapping[str, Any]) -> dict[str, Any]:
    version = str(config.get("policy_version") or "")
    return {
        "engine_id": engine_id,
        "status": "ACTIVE",
        "policy_id": f"{engine_id}:baseline:{version}",
        "revision": 0,
        "baseline_policy_version": version,
        "effective_policy_version": version,
        "overrides": {},
        "activated_at": None,
        "source_candidate_id": None,
        "parent": None,
        "blocked_until": None,
    }


def ensure_registry(state_dir: Path, repo_root: Path, now: datetime) -> dict[str, Any]:
    path = state_dir / REGISTRY_FILENAME
    existing = _read_json(path)
    if isinstance(existing, dict):
        validate_registry(existing)
        return existing
    engines: dict[str, Any] = {}
    for engine_id, spec in POLICY_SPECS.items():
        config = _read_json(repo_root / spec["config_path"], {})
        if not isinstance(config, dict) or not config.get("policy_version"):
            raise RuntimeError(f"missing baseline policy config for {engine_id}")
        engines[engine_id] = _baseline_entry(engine_id, config)
    registry = {
        "schema_version": REGISTRY_SCHEMA,
        "updated_at": _iso(now),
        "controls": dict(CONTROLS),
        "engines": engines,
        "candidates": {},
        "rejected_transitions": [],
    }
    _atomic_json(path, registry)
    return _read_json(path)


def _current_value(engine_id: str, registry: Mapping[str, Any], repo_root: Path) -> float:
    spec = POLICY_SPECS[engine_id]
    config = _read_json(repo_root / spec["config_path"], {})
    baseline = float(config[spec["parameter"]])
    state = (registry.get("engines") or {}).get(engine_id) or {}
    return float((state.get("overrides") or {}).get(spec["parameter"], baseline))


def _pr29_snapshots(events: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for event in events:
        if event.get("event_type") != "learning_observation":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        if payload.get("observation_type") != "counterfactual_decision_snapshot":
            continue
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), Mapping) else {}
        sid = str(snapshot.get("snapshot_id") or "")
        if sid:
            out[sid] = snapshot
    return out


def _first_failed_gate(candidate: Mapping[str, Any]) -> Mapping[str, Any] | None:
    meta = candidate.get("metadata") if isinstance(candidate.get("metadata"), Mapping) else {}
    first = meta.get("first_blocking_gate") if isinstance(meta.get("first_blocking_gate"), Mapping) else None
    if first and first.get("passed") is False:
        return first
    for gate in candidate.get("gates") or []:
        if isinstance(gate, Mapping) and gate.get("passed") is False:
            return gate
    return None


def _other_hard_gates_pass(candidate: Mapping[str, Any], ignored_gate: str) -> bool:
    for gate in candidate.get("gates") or []:
        if not isinstance(gate, Mapping):
            continue
        if str(gate.get("name") or "") == ignored_gate:
            continue
        if gate.get("hard", True) and gate.get("passed") is False:
            return False
    return True


def fetch_yahoo_daily(symbol: str, decision_day: date) -> list[Bar]:
    start = int(datetime.combine(decision_day - timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    end = int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp())
    params = urllib.parse.urlencode({"period1": start, "period2": end, "interval": "1d", "events": "history"})
    encoded = urllib.parse.quote(symbol, safe="")
    last_error: Exception | None = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            req = urllib.request.Request(
                f"https://{host}/v8/finance/chart/{encoded}?{params}",
                headers={"User-Agent": "BriefRooms-PR35-Shadow/1.0", "Cache-Control": "no-cache"},
            )
            with urllib.request.urlopen(req, timeout=18) as response:
                payload = json.load(response)
            result = (payload.get("chart", {}).get("result") or [None])[0]
            if not result:
                raise ValueError("empty Yahoo chart")
            stamps = result.get("timestamp") or []
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            rows: list[Bar] = []
            for i, stamp in enumerate(stamps):
                values = [(quote.get(key) or [None] * len(stamps))[i] for key in ("open", "high", "low", "close")]
                if any(value is None for value in values):
                    continue
                rows.append(Bar(datetime.fromtimestamp(int(stamp), timezone.utc).date(), *(float(x) for x in values)))
            return sorted(rows, key=lambda row: row.day)
        except Exception as exc:  # provider failures must not mutate policy
            last_error = exc
    raise RuntimeError(f"Yahoo settlement unavailable for {symbol}: {type(last_error).__name__}")


def settle_long_two_full_sessions(entry: float, stop: float, target: float, bars: Sequence[Bar], decision_day: date) -> dict[str, Any] | None:
    future = [bar for bar in sorted(bars, key=lambda row: row.day) if bar.day > decision_day]
    if len(future) < 2:
        return None
    risk = entry - stop
    if not (risk > 0 and stop < entry < target):
        return None
    used = future[:2]
    exit_price = used[-1].close
    exit_reason = "two_session_horizon"
    conservative = False
    exit_day = used[-1].day
    for bar in used:
        same = bar.low <= stop and bar.high >= target
        if same:
            exit_price, exit_reason, conservative, exit_day = stop, "stop", True, bar.day
            break
        if bar.low <= stop:
            exit_price, exit_reason, exit_day = stop, "stop", bar.day
            break
        if bar.high >= target:
            exit_price, exit_reason, exit_day = target, "target", bar.day
            break
    return_percent = (exit_price / entry - 1.0) * 100.0
    r_multiple = (exit_price - entry) / risk
    return {
        "entry": round(entry, 8),
        "exit_price": round(exit_price, 8),
        "exit_reason": exit_reason,
        "exit_day": exit_day.isoformat(),
        "return_percent": round(return_percent, 8),
        "r_multiple": round(r_multiple, 8),
        "conservative_same_bar": conservative,
        "settlement_rule": "frozen_reference_entry_then_next_two_full_sessions_stop_target_horizon_v1",
    }


def collect_shadow_outcomes(
    state_dir: Path,
    events: Sequence[Mapping[str, Any]],
    activation: Mapping[str, Any],
    *,
    allow_network: bool,
    now: datetime,
) -> dict[str, int]:
    path = state_dir / SHADOW_FILENAME
    verify_shadow(path)
    existing = {str(row["shadow_outcome_id"]) for row in _read_jsonl(path)}
    activated_at = _parse_time(str(activation["activated_at"]))
    stats = {"considered": 0, "settled": 0, "not_due": 0, "provider_skipped": 0}
    if not allow_network:
        return stats
    for snapshot in _pr29_snapshots(events).values():
        engine_id = str(snapshot.get("engine_id") or "")
        if engine_id not in POLICY_SPECS:
            continue
        decision_at = _parse_time(str(snapshot.get("decision_at") or ""))
        if decision_at < activated_at:
            continue
        decision_day = decision_at.date()
        for candidate in snapshot.get("candidates") or []:
            if not isinstance(candidate, Mapping) or bool(candidate.get("selected")):
                continue
            if str(candidate.get("action") or "") != "LONG" or candidate.get("settlement_mode") != "risk_plan":
                continue
            cid = str(candidate.get("candidate_id") or "")
            oid = _stable_id("polshadow", snapshot.get("snapshot_id"), cid)
            if oid in existing:
                continue
            stats["considered"] += 1
            symbol = str(candidate.get("market_symbol") or "")
            entry = _finite(candidate.get("reference_price") if candidate.get("reference_price") is not None else candidate.get("entry"))
            stop, target = _finite(candidate.get("stop")), _finite(candidate.get("target"))
            if not symbol or entry is None or stop is None or target is None:
                continue
            try:
                bars = fetch_yahoo_daily(symbol, decision_day)
            except Exception:
                stats["provider_skipped"] += 1
                continue
            settlement = settle_long_two_full_sessions(entry, stop, target, bars, decision_day)
            if settlement is None:
                stats["not_due"] += 1
                continue
            first = _first_failed_gate(candidate) or {}
            score = _finite(candidate.get("score"))
            threshold = _finite(first.get("threshold"))
            row = {
                "schema_version": SHADOW_SCHEMA,
                "shadow_outcome_id": oid,
                "snapshot_id": snapshot.get("snapshot_id"),
                "candidate_id": cid,
                "engine_id": engine_id,
                "decision_at": _iso(decision_at),
                "settled_at": _iso(now),
                "symbol": symbol,
                "candidate_score": score,
                "first_blocking_gate": str(first.get("name") or "unknown"),
                "source_threshold": threshold,
                "other_hard_gates_passed": _other_hard_gates_pass(candidate, str(first.get("name") or "")),
                **settlement,
            }
            row["row_sha256"] = _shadow_hash(row)
            _append_line(path, row)
            existing.add(oid)
            stats["settled"] += 1
    verify_shadow(path)
    return stats


def _summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "mean_return_percent": None, "positive_rate": None, "mean_r": None, "cumulative_r": None, "max_drawdown_r": None, "first_half_mean_return": None, "second_half_mean_return": None}
    ordered = sorted(rows, key=lambda row: str(row.get("decision_at") or ""))
    returns = [float(row["return_percent"]) for row in ordered]
    r_values = [float(row["r_multiple"]) for row in ordered if _finite(row.get("r_multiple")) is not None]
    cumulative, peak, max_dd = 0.0, 0.0, 0.0
    for value in r_values:
        cumulative += value
        peak = max(peak, cumulative)
        max_dd = min(max_dd, cumulative - peak)
    half = max(1, len(returns) // 2)
    return {
        "n": len(rows),
        "mean_return_percent": round(fmean(returns), 8),
        "positive_rate": round(sum(value > 0 for value in returns) / len(returns), 6),
        "mean_r": None if not r_values else round(fmean(r_values), 8),
        "cumulative_r": None if not r_values else round(sum(r_values), 8),
        "max_drawdown_r": None if not r_values else round(max_dd, 8),
        "first_half_mean_return": round(fmean(returns[:half]), 8),
        "second_half_mean_return": round(fmean(returns[half:]), 8) if returns[half:] else round(fmean(returns[:half]), 8),
    }


def _marginal_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    engine_id: str,
    gate: str,
    current_value: float,
    candidate_value: float,
    after: Optional[datetime] = None,
) -> list[Mapping[str, Any]]:
    selected: list[Mapping[str, Any]] = []
    low, high = min(current_value, candidate_value), max(current_value, candidate_value)
    for row in rows:
        if row.get("engine_id") != engine_id or row.get("first_blocking_gate") != gate:
            continue
        if row.get("other_hard_gates_passed") is not True:
            continue
        decision_at = _parse_time(str(row.get("decision_at") or ""))
        if after is not None and decision_at <= after:
            continue
        score = _finite(row.get("candidate_score"))
        threshold = _finite(row.get("source_threshold"))
        if score is None:
            continue
        if threshold is not None and abs(threshold - current_value) > 1e-6:
            continue
        if low <= score < high:
            selected.append(row)
    return selected


def _transition_recently_blocked(registry: Mapping[str, Any], engine_id: str, parameter: str, old: float, new: float, now: datetime) -> bool:
    for row in registry.get("rejected_transitions") or []:
        if row.get("engine_id") != engine_id or row.get("parameter") != parameter:
            continue
        if abs(float(row.get("from", 1e9)) - old) > 1e-6 or abs(float(row.get("to", 1e9)) - new) > 1e-6:
            continue
        until = row.get("blocked_until")
        if until and _parse_time(str(until)) > now:
            return True
    return False


def create_candidates(registry: dict[str, Any], state_dir: Path, repo_root: Path, now: datetime) -> int:
    rows = _read_jsonl(state_dir / SHADOW_FILENAME)
    created = 0
    for engine_id, spec in POLICY_SPECS.items():
        engine = registry["engines"][engine_id]
        blocked_until = engine.get("blocked_until")
        if blocked_until and _parse_time(str(blocked_until)) > now:
            continue
        if any(c.get("engine_id") == engine_id and c.get("status") == "SHADOW_VALIDATION" for c in registry["candidates"].values()):
            continue
        current = _current_value(engine_id, registry, repo_root)
        proposed = max(float(spec["lower"]), current - float(spec["step"]))
        if proposed >= current - 1e-9:
            continue
        if _transition_recently_blocked(registry, engine_id, spec["parameter"], current, proposed, now):
            continue
        training = _marginal_rows(rows, engine_id=engine_id, gate=spec["gate"], current_value=current, candidate_value=proposed)
        stats = _summary(training)
        if stats["n"] < TRAIN_MIN_N:
            continue
        if float(stats["mean_return_percent"] or 0.0) < 0.15 or float(stats["positive_rate"] or 0.0) < 0.55:
            continue
        if stats["mean_r"] is not None and float(stats["mean_r"]) < 0.10:
            continue
        candidate_id = _stable_id("policycand", engine_id, spec["parameter"], current, proposed, _iso(now))
        candidate = {
            "candidate_id": candidate_id,
            "engine_id": engine_id,
            "parameter": spec["parameter"],
            "gate": spec["gate"],
            "from_value": current,
            "to_value": proposed,
            "created_at": _iso(now),
            "validation_start_at": _iso(now),
            "status": "SHADOW_VALIDATION",
            "training": stats,
            "validation": _summary([]),
            "promotion_gate": {"status": "COLLECTING"},
        }
        registry["candidates"][candidate_id] = candidate
        append_audit(state_dir / AUDIT_FILENAME, "candidate_created", candidate, _iso(now))
        created += 1
    return created


def _promotion_gate(stats: Mapping[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    n = int(stats.get("n") or 0)
    if n < VALIDATION_MIN_N:
        return "COLLECTING", [f"validation_n_below_{VALIDATION_MIN_N}"]
    if float(stats.get("mean_return_percent") or 0.0) < 0.15:
        reasons.append("mean_return_below_0.15pct")
    if float(stats.get("positive_rate") or 0.0) < 0.55:
        reasons.append("positive_rate_below_55pct")
    if stats.get("mean_r") is not None and float(stats["mean_r"]) < 0.10:
        reasons.append("mean_r_below_0.10")
    if stats.get("max_drawdown_r") is not None and float(stats["max_drawdown_r"]) < -3.0:
        reasons.append("shadow_drawdown_below_minus_3R")
    if float(stats.get("first_half_mean_return") or 0.0) <= 0.0:
        reasons.append("first_half_not_positive")
    if float(stats.get("second_half_mean_return") or 0.0) <= 0.0:
        reasons.append("second_half_not_positive")
    if not reasons:
        return "PASS", []
    if n >= VALIDATION_MAX_N:
        return "FAIL", reasons
    return "CONTINUE", reasons


def _policy_snapshot(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "policy_id": state.get("policy_id"),
        "revision": state.get("revision"),
        "baseline_policy_version": state.get("baseline_policy_version"),
        "effective_policy_version": state.get("effective_policy_version"),
        "overrides": copy.deepcopy(state.get("overrides") or {}),
        "activated_at": state.get("activated_at"),
        "source_candidate_id": state.get("source_candidate_id"),
        "parent": copy.deepcopy(state.get("parent")),
    }


def promote_candidate(registry: dict[str, Any], candidate: dict[str, Any], state_dir: Path, now: datetime) -> None:
    engine_id = candidate["engine_id"]
    current = registry["engines"][engine_id]
    parent = _policy_snapshot(current)
    revision = int(current.get("revision") or 0) + 1
    overrides = dict(current.get("overrides") or {})
    overrides[candidate["parameter"]] = candidate["to_value"]
    baseline = str(current["baseline_policy_version"])
    effective = f"{baseline}+auto{revision}"
    new_state = {
        "engine_id": engine_id,
        "status": "ACTIVE",
        "policy_id": _stable_id("policy", engine_id, revision, candidate["candidate_id"]),
        "revision": revision,
        "baseline_policy_version": baseline,
        "effective_policy_version": effective,
        "overrides": overrides,
        "activated_at": _iso(now),
        "source_candidate_id": candidate["candidate_id"],
        "parent": parent,
        "blocked_until": None,
    }
    registry["engines"][engine_id] = new_state
    candidate["status"] = "PROMOTED"
    candidate["promoted_at"] = _iso(now)
    candidate["effective_policy_version"] = effective
    append_audit(state_dir / AUDIT_FILENAME, "policy_promoted", {"candidate": candidate, "new_policy": new_state}, _iso(now))


def update_candidates(registry: dict[str, Any], state_dir: Path, now: datetime) -> tuple[int, int]:
    rows = _read_jsonl(state_dir / SHADOW_FILENAME)
    promoted = rejected = 0
    for candidate in list(registry["candidates"].values()):
        if candidate.get("status") != "SHADOW_VALIDATION":
            continue
        validation = _marginal_rows(
            rows,
            engine_id=candidate["engine_id"],
            gate=candidate["gate"],
            current_value=float(candidate["from_value"]),
            candidate_value=float(candidate["to_value"]),
            after=_parse_time(candidate["validation_start_at"]),
        )
        stats = _summary(validation)
        status, reasons = _promotion_gate(stats)
        candidate["validation"] = stats
        candidate["promotion_gate"] = {"status": status, "blocking_reasons": reasons, "evaluated_at": _iso(now)}
        if status == "PASS":
            promote_candidate(registry, candidate, state_dir, now)
            promoted += 1
        elif status == "FAIL":
            candidate["status"] = "REJECTED"
            candidate["rejected_at"] = _iso(now)
            blocked_until = now + timedelta(days=30)
            registry["rejected_transitions"].append({
                "engine_id": candidate["engine_id"], "parameter": candidate["parameter"],
                "from": candidate["from_value"], "to": candidate["to_value"],
                "reason": "promotion_gate_fail", "blocked_until": _iso(blocked_until),
            })
            append_audit(state_dir / AUDIT_FILENAME, "candidate_rejected", candidate, _iso(now))
            rejected += 1
    return promoted, rejected


def _resolved_history(repo_root: Path, engine_id: str, policy_version: str, activated_at: datetime) -> list[dict[str, Any]]:
    history = repo_root / POLICY_SPECS[engine_id]["history_dir"]
    rows: list[dict[str, Any]] = []
    if not history.exists():
        return rows
    for path in sorted(history.glob("????-??-??.json")):
        payload = _read_json(path)
        if not isinstance(payload, dict) or payload.get("policy_version") != policy_version:
            continue
        outcome = payload.get("outcome") if isinstance(payload.get("outcome"), Mapping) else {}
        if str(outcome.get("status") or "").upper() != "RESOLVED":
            continue
        generated = payload.get("generated_at")
        try:
            if generated and _parse_time(str(generated)) < activated_at:
                continue
        except ValueError:
            continue
        r = _finite(outcome.get("r_multiple"))
        ret = _finite(outcome.get("return_percent"))
        if r is None and ret is None:
            continue
        rows.append({"date": payload.get("date"), "r_multiple": r, "return_percent": ret})
    return rows


def _rollback_trigger(rows: Sequence[Mapping[str, Any]]) -> tuple[bool, dict[str, Any]]:
    r_values = [float(row["r_multiple"]) for row in rows if _finite(row.get("r_multiple")) is not None]
    returns = [float(row["return_percent"]) for row in rows if _finite(row.get("return_percent")) is not None]
    n = len(rows)
    metrics = {
        "n": n,
        "mean_r": None if not r_values else round(fmean(r_values), 8),
        "cumulative_r": None if not r_values else round(sum(r_values), 8),
        "positive_rate": None if not returns else round(sum(x > 0 for x in returns) / len(returns), 6),
        "mean_return_percent": None if not returns else round(fmean(returns), 8),
    }
    emergency = n >= ROLLBACK_EMERGENCY_MIN_N and metrics["cumulative_r"] is not None and float(metrics["cumulative_r"]) <= -3.0
    regular = n >= ROLLBACK_MIN_N and (
        (metrics["cumulative_r"] is not None and float(metrics["cumulative_r"]) <= -2.0)
        or (metrics["mean_r"] is not None and float(metrics["mean_r"]) <= -0.20)
        or (metrics["positive_rate"] is not None and float(metrics["positive_rate"]) <= 0.30)
    )
    return bool(emergency or regular), metrics


def monitor_rollbacks(registry: dict[str, Any], state_dir: Path, repo_root: Path, now: datetime) -> int:
    count = 0
    for engine_id, state in list(registry["engines"].items()):
        if int(state.get("revision") or 0) <= 0 or not state.get("parent") or not state.get("activated_at"):
            continue
        rows = _resolved_history(repo_root, engine_id, str(state["effective_policy_version"]), _parse_time(state["activated_at"]))
        trigger, metrics = _rollback_trigger(rows)
        state["live_monitor"] = {**metrics, "evaluated_at": _iso(now), "rollback_triggered": trigger}
        if not trigger:
            continue
        failed = _policy_snapshot(state)
        parent = copy.deepcopy(state["parent"])
        restored = {
            "engine_id": engine_id,
            "status": "ACTIVE",
            "policy_id": parent["policy_id"],
            "revision": parent["revision"],
            "baseline_policy_version": parent["baseline_policy_version"],
            "effective_policy_version": parent["effective_policy_version"],
            "overrides": parent["overrides"],
            "activated_at": parent.get("activated_at"),
            "source_candidate_id": parent.get("source_candidate_id"),
            "parent": parent.get("parent"),
            "blocked_until": _iso(now + timedelta(days=14)),
            "rollback_from_policy_id": failed["policy_id"],
            "rollback_at": _iso(now),
        }
        registry["engines"][engine_id] = restored
        source_candidate = str(state.get("source_candidate_id") or "")
        if source_candidate in registry["candidates"]:
            registry["candidates"][source_candidate]["status"] = "ROLLED_BACK"
            registry["candidates"][source_candidate]["rolled_back_at"] = _iso(now)
            registry["candidates"][source_candidate]["rollback_metrics"] = metrics
        registry["rejected_transitions"].append({
            "engine_id": engine_id,
            "parameter": POLICY_SPECS[engine_id]["parameter"],
            "from": (failed.get("overrides") or {}).get(POLICY_SPECS[engine_id]["parameter"]),
            "to": (restored.get("overrides") or {}).get(POLICY_SPECS[engine_id]["parameter"]),
            "reason": "automatic_live_rollback",
            "blocked_until": _iso(now + timedelta(days=30)),
        })
        append_audit(state_dir / AUDIT_FILENAME, "policy_rolled_back", {"failed_policy": failed, "restored_policy": restored, "live_metrics": metrics}, _iso(now))
        count += 1
    return count


def verify_state(state_dir: Path) -> dict[str, Any]:
    activation = _read_json(state_dir / ACTIVATION_FILENAME)
    if not isinstance(activation, dict) or activation.get("schema_version") != ACTIVATION_SCHEMA:
        raise ValueError("missing/invalid PR35 activation")
    registry = _read_json(state_dir / REGISTRY_FILENAME)
    if not isinstance(registry, dict):
        raise ValueError("missing PR35 registry")
    validate_registry(registry)
    if registry.get("controls") != CONTROLS:
        raise ValueError("PR35 controls changed")
    for engine_id, state in (registry.get("engines") or {}).items():
        if engine_id not in POLICY_SPECS:
            raise ValueError("unknown policy engine")
        overrides = state.get("overrides") or {}
        if set(overrides) - {POLICY_SPECS[engine_id]["parameter"]}:
            raise ValueError("non-allowlisted override in registry")
    audit = verify_audit_chain(state_dir / AUDIT_FILENAME)
    shadow = verify_shadow(state_dir / SHADOW_FILENAME)
    return {"activation": activation["activated_at"], "audit": audit, "shadow": shadow, "engines": list((registry.get("engines") or {}).keys())}


def run(state_dir: Path, learning_state_dir: Path, repo_root: Path, *, allow_network: bool = True, now: Optional[datetime] = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state_dir.mkdir(parents=True, exist_ok=True)
    activation = ensure_activation(state_dir, now)
    registry = ensure_registry(state_dir, repo_root, now)
    ledger = learning_state_dir / "learning_ledger.jsonl"
    events: list[Mapping[str, Any]] = []
    if ledger.exists():
        verify_chain(ledger)
        events = read_events(ledger)

    shadow_stats = collect_shadow_outcomes(state_dir, events, activation, allow_network=allow_network, now=now)
    rollbacks = monitor_rollbacks(registry, state_dir, repo_root, now)
    promoted, rejected = update_candidates(registry, state_dir, now)
    created = create_candidates(registry, state_dir, repo_root, now)
    registry["updated_at"] = _iso(now)
    _atomic_json(state_dir / REGISTRY_FILENAME, registry)
    registry = _read_json(state_dir / REGISTRY_FILENAME)
    validate_registry(registry)
    verified = verify_state(state_dir)
    status = {
        "schema_version": STATUS_SCHEMA,
        "generated_at": _iso(now),
        "mode": "autonomous_bounded_policy",
        "controls": dict(CONTROLS),
        "shadow_settlement": shadow_stats,
        "candidates_created": created,
        "candidates_promoted": promoted,
        "candidates_rejected": rejected,
        "automatic_rollbacks": rollbacks,
        "active_policies": {engine: {"revision": state.get("revision"), "effective_policy_version": state.get("effective_policy_version"), "overrides": state.get("overrides")} for engine, state in registry["engines"].items()},
        "verification": verified,
    }
    _atomic_json(state_dir / STATUS_FILENAME, status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="BriefRooms PR35 autonomous policy promotion")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--learning-state-dir")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--now")
    args = parser.parse_args()
    state_dir = Path(args.state_dir)
    if args.verify:
        print(json.dumps(verify_state(state_dir), ensure_ascii=False, sort_keys=True))
        return 0
    if not args.learning_state_dir:
        raise SystemExit("--learning-state-dir is required unless --verify")
    now = _parse_time(args.now) if args.now else datetime.now(timezone.utc)
    status = run(state_dir, Path(args.learning_state_dir), Path(args.repo_root), allow_network=not args.no_network, now=now)
    print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

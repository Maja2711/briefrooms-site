"""Immutable, native-time production decision/admission records for TR-03.

This is persistence, not direction, execution or promotion authority. Historical
positions without a manifest stay unclassified; no hindsight reconstruction.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import provenance_contract as provenance

ENGINE_VERSION = "eurusd-daily-spot-v1.9.1"
SCHEMA = "eurusd-production-decision-manifest-v1"
LEDGER_SCHEMA = "eurusd-production-decisions-v1"
CODE_FILES = (
    "daily_eurusd_spot.py", "daily_eurusd_spot_v12.py", "daily_eurusd_spot_v13.py",
    "daily_eurusd_spot_v19.py", "daily_eurusd_bollinger_h1.py", "daily_eurusd_bollinger_d1.py", "daily_eurusd_bollinger_touch.py", "daily_eurusd_belief_decision.py",
    "daily_eurusd_reentry_safety.py", "daily_eurusd_reflection.py", "daily_eurusd_reentry_learning.py",
    "daily_eurusd_lifecycle.py", "daily_eurusd_spot_v14.py",
    "daily_eurusd_production_policy.py", "daily_eurusd_decision_record.py",
    "execution_price_engine.py", "epistemic_consumer_interface.py",
    "daily_eurusd_spot_v15.py", "daily_eurusd_spot_v16.py", "daily_eurusd_spot_v17.py", "daily_eurusd_spot_v18.py",
    "daily_eurusd_fast_lifecycle.py", "daily_eurusd_macro_event_risk.py",
    "daily_eurusd_event_overlay.py", "investments_wes_macro_belief.py",
    "belief_epistemic_state.py",
    "belief_market_data_adapter.py", "daily_engine_contract.py", "provenance_contract.py",
    "investment_event_engine_profiles.py", "investment_event_intelligence.py",
    "statistical_promotion_gate.py", "briefrooms_evolution_contracts.py",
)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("decision timestamps must include timezone")
    return parsed.astimezone(timezone.utc)


def runtime_code_hash() -> str:
    root = Path(__file__).resolve().parent
    return digest({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                   for name in CODE_FILES})


def market_snapshot_hash(snapshot: Any) -> str:
    # Hash the facts consumed for geometry, with original timestamps; no
    # refresh timestamp is substituted for a source observation.
    rows = {}
    for symbol, bars in (getattr(snapshot, "bars", {}) or {}).items():
        rows[str(symbol)] = [{"timestamp": bar.timestamp.isoformat(),
            "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close}
            for bar in bars]
    return digest(rows)


def make_manifest(payload: Mapping[str, Any], *, snapshot_sha256: str) -> dict[str, Any]:
    metadata = payload.get("metadata") or {}
    decision = copy.deepcopy(metadata.get("final_decision") or {})
    envelope = provenance.verify_attached(decision)
    policy = copy.deepcopy(metadata.get("production_policy") or {})
    decision_at = envelope["created_at"]
    execution = copy.deepcopy(metadata.get("execution_price_engine") or {})
    record = {
        "schema_version": SCHEMA,
        "engine_version": ENGINE_VERSION,
        "instrument": "EUR/USD",
        "decision_at": decision_at,
        "native_decision_id": envelope.get("decision_id") or envelope["artifact_id"],
        "policy_version_id": policy.get("version_id"),
        "policy_sha256": policy.get("policy_sha256"),
        "policy": policy,
        "runtime_code_sha256": runtime_code_hash(),
        "market_snapshot_sha256": snapshot_sha256,
        "epistemic_snapshot_sha256": (decision.get("epistemic_consumer") or {}).get("source_sha256"),
        "decision_sha256": digest(decision),
        "final_decision": decision,
        "execution_admission": copy.deepcopy(metadata.get("execution_admission") or {}),
        "execution_price_engine": execution,
        "risk": copy.deepcopy(metadata.get("risk") or {}),
        "execution_geometry": {name: payload.get(name) for name in ("entry", "stop", "target")},
        "prospective": True,
        "historical_backfill": False,
    }
    record["decision_id"] = "eurusd-production-decision:" + digest(record)[:24]
    record["manifest_sha256"] = digest(record)
    validate_manifest(record)
    return record


def validate_manifest(record: Mapping[str, Any]) -> None:
    if record.get("schema_version") != SCHEMA or record.get("instrument") != "EUR/USD":
        raise ValueError("invalid production decision manifest")
    if not record.get("prospective") or record.get("historical_backfill") is not False:
        raise ValueError("production decision must be prospective")
    body = {key: value for key, value in record.items() if key != "manifest_sha256"}
    if digest(body) != record.get("manifest_sha256"):
        raise ValueError("production decision manifest changed after freeze")
    identity = {key: value for key, value in body.items() if key != "decision_id"}
    if record.get("decision_id") != "eurusd-production-decision:" + digest(identity)[:24]:
        raise ValueError("production decision identity mismatch")
    for name in ("policy_version_id", "policy_sha256", "runtime_code_sha256", "market_snapshot_sha256"):
        if not record.get(name):
            raise ValueError(f"manifest lacks {name}")
    _time(record["decision_at"])
    decision = record.get("final_decision") or {}
    native = provenance.verify_attached(decision)
    if native["created_at"] != record["decision_at"]:
        raise ValueError("decision creation time differs from native provenance")
    if record.get("native_decision_id") != (native.get("decision_id") or native["artifact_id"]):
        raise ValueError("native decision identity mismatch")
    policy = record.get("policy") or {}
    if (policy.get("version_id") != record["policy_version_id"]
            or policy.get("policy_sha256") != record["policy_sha256"]):
        raise ValueError("manifest policy identity does not reconcile")
    if record.get("decision_sha256") != digest(decision):
        raise ValueError("final decision hash mismatch")
    execution = record.get("execution_price_engine") or {}
    if execution.get("verified") is True:
        if execution.get("execution_cost_contract") != "DAILY_EURUSD_BID_ASK_V1":
            raise ValueError("verified production admission lacks a versioned cost model")
        fill_at = _time(execution.get("verified_at"))
        if fill_at < _time(record["decision_at"]):
            raise ValueError("fill predates final decision")
        for name in ("entry", "stop", "target"):
            value = (record.get("execution_geometry") or {}).get(name)
            if value is None or not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError("verified admission lacks finite execution geometry")


def empty_ledger() -> dict[str, Any]:
    return {"schema_version": LEDGER_SCHEMA, "decisions": []}


def validate_ledger(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != LEDGER_SCHEMA or not isinstance(payload.get("decisions"), list):
        raise ValueError("invalid production decision ledger")
    seen = set()
    for row in payload["decisions"]:
        validate_manifest(row)
        if row["decision_id"] in seen:
            raise ValueError("duplicate decision identity")
        seen.add(row["decision_id"])


def load_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_ledger()
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_ledger(payload)
    return payload


def append_manifest(ledger: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    validate_manifest(record)
    result = copy.deepcopy(dict(ledger))
    for old in result["decisions"]:
        if old["decision_id"] == record["decision_id"]:
            if old != record:
                raise ValueError("cannot overwrite a frozen decision")
            return result
    result["decisions"].append(copy.deepcopy(dict(record)))
    return result


def validate_ledger_transition(previous: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    validate_ledger(previous)
    validate_ledger(current)
    old = previous["decisions"]
    if current["decisions"][:len(old)] != old:
        raise ValueError("frozen production decisions are append-only")


def save_ledger(path: Path, ledger: Mapping[str, Any]) -> None:
    validate_ledger(ledger)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(ledger, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def production_measurements(history: Mapping[str, Any]) -> dict[str, Any]:
    """All real manifest-bound outcomes by actually consumed policy version.

    These descriptive production results are separate from the purged paired
    component test; unmatched versions/market periods are not a causal A/B test.
    Legacy trades stay in canonical history and receive no fabricated cohort.
    """
    versions: dict[str, dict[str, Any]] = {}
    excluded = 0
    for trade in history.get("trades") or []:
        manifest = trade.get("decision_manifest")
        if not isinstance(manifest, Mapping):
            excluded += 1
            continue
        net_r = validate_trade_binding(trade)
        row = versions.setdefault(manifest["policy_version_id"], {
            "policy_sha256": manifest["policy_sha256"], "results": [], "trade_ids": []})
        row["results"].append(net_r)
        row["trade_ids"].append(trade["trade_id"])
    result = {}
    for version_id, row in versions.items():
        values = row.pop("results")
        gains = sum(x for x in values if x > 0)
        losses = -sum(x for x in values if x < 0)
        equity = peak = drawdown = 0.0
        for value in values:
            equity += value
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        result[version_id] = {**row, "closed_trades": len(values),
            "wins": sum(x > 0 for x in values), "losses": sum(x < 0 for x in values),
            "sum_net_r": sum(values), "mean_net_r": sum(values) / len(values),
            "profit_factor_r": gains / losses if losses else None,
            "max_drawdown_r": drawdown}
    return {"basis": "CANONICAL_ACTUAL_PAPER_COST_MODEL", "by_policy_version": result,
        "legacy_without_manifest": excluded, "unmatched_version_comparison_is_causal": False}


def validate_trade_binding(trade: Mapping[str, Any]) -> float:
    manifest = trade["decision_manifest"]
    validate_manifest(manifest)
    geometry = manifest["execution_geometry"]
    for field in ("entry", "stop", "target"):
        if abs(float(trade[field]) - float(geometry[field])) > 1e-9:
            raise ValueError("closed trade differs from frozen admission geometry")
    direction = manifest["final_decision"]["direction"]
    if trade["direction"] != direction or trade["engine_version"] != manifest["engine_version"]:
        raise ValueError("closed trade decision semantics mismatch")
    execution = manifest["execution_price_engine"]
    if ((manifest.get("policy") or {}).get("allowed") is not True
            or (manifest.get("execution_admission") or {}).get("allowed") is not True
            or execution.get("verified") is not True or execution.get("status") != "VERIFIED_FILL"):
        raise ValueError("canonical outcome lacks an admitted, verified decision")
    if _time(trade["opened_at"]) != _time(execution["verified_at"]):
        raise ValueError("canonical entry time differs from verified fill")
    if _time(trade["closed_at"]) < _time(execution["verified_at"]):
        raise ValueError("trade outcome predates verified admission")
    entry, stop, exit_price = float(geometry["entry"]), float(geometry["stop"]), float(trade["exit_price"])
    if not math.isfinite(exit_price) or exit_price <= 0 or entry == stop:
        raise ValueError("invalid price/risk in trade measurement")
    return (1 if direction == "LONG" else -1) * (exit_price - entry) / abs(entry - stop)

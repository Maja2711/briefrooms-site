#!/usr/bin/env python3
"""One fail-closed publication gate for every Daily EUR/USD writer and retry.

This observer has no direction, execution or promotion authority. Closed trades
are append-only, and a new policy cannot reinterpret an existing position.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

try:
    from daily_engine_contract import validate_daily_engine_output
    import audit_provenance
except ImportError:
    from scripts.daily_engine_contract import validate_daily_engine_output
    from scripts import audit_provenance

ROOT = Path(__file__).resolve().parents[1]
SPOT = "data/investments/eurusd_daily_spot.json"
HISTORY = "data/investments/eurusd_daily_history.json"
POLICY = "data/investments/eurusd_production_policy_state.json"
DECISIONS = "data/investments/eurusd_production_decisions.json"
EVENTS = "data/notifications/trading-events.json"
ENGINE_VERSION = "eurusd-daily-spot-v1.9.1"
TERMINAL = {"CLOSED_SL", "CLOSED_TP", "CLOSED_TIME", "CLOSED_DYNAMIC", "CLOSED_EVENT"}
LEGACY_VETOES = {
    "score_below_adaptive_long_threshold", "score_above_adaptive_short_threshold",
    "confidence_below_minimum", "signal_not_persistent", "entry_overextended_vs_ema20",
    "latest_30m_bar_is_shock_bar", "post_stop_cooldown", "daily_entry_limit_reached",
}
FROZEN_ENTRY_FIELDS = (
    "trade_id", "direction", "opened_at", "entry", "stop", "target",
    "entry_score", "entry_confidence", "entry_components", "entry_weights",
    "entry_thesis", "execution_price_engine", "contextual_entry_policy_at_entry",
    "decision_manifest",
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def validate_payload(payload: Mapping[str, Any], *, lifecycle_only: bool = False) -> list[str]:
    """Preserve the same production contract on initial generation and retries."""
    errors: list[str] = []
    try:
        validate_daily_engine_output(payload)
    except (ValueError, TypeError, KeyError) as exc:
        errors.append(f"invalid Daily output: {exc}")
    if payload.get("instrument") != "EUR/USD":
        errors.append("Daily persistence is restricted to EUR/USD")
    allowed_versions = {ENGINE_VERSION, "eurusd-daily-spot-v1.9.0"} if lifecycle_only else {ENGINE_VERSION}
    if payload.get("engine_version") not in allowed_versions:
        errors.append("unexpected EUR/USD engine version")
    metadata = _mapping(payload.get("metadata"))
    candidate = _mapping(metadata.get("candidate"))
    leaked = LEGACY_VETOES.intersection(candidate.get("gate_reasons") or [])
    if leaked:
        errors.append(f"legacy admission veto leaked into v1.9 runtime: {sorted(leaked)}")
    for field in ("dynamic_exit", "same_thesis_reentry_guard"):
        if _mapping(metadata.get(field)).get("enabled") is not True:
            errors.append(f"v1.9 output missing enabled {field} metadata")

    position = _mapping(metadata.get("position"))
    terminal = str(payload.get("status") or "").upper() in TERMINAL
    contextual = _mapping(metadata.get("contextual_entry_policy"))
    if not contextual and not position and not terminal:
        errors.append("v1.9 flat/new-entry output missing contextual entry policy metadata")
    if contextual:
        if contextual.get("direction_mutation_allowed") is not False:
            errors.append("contextual entry policy must not own market direction")
        if contextual.get("decision_influence") is not False:
            errors.append("legacy contextual learner influences v1.9 production")
    pipeline = _mapping(metadata.get("belief_pipeline"))
    if pipeline.get("architecture") != "BELIEF_FIRST_V1" or pipeline.get("final_decision_after_belief") is not True:
        errors.append("v1.9 output missing Belief-first pipeline contract")

    authority = _mapping(metadata.get("direction_authority"))
    for key, expected in (
        ("owner", "NATIVE_DAILY_EURUSD_BELIEF_FIRST_DECISION_ENGINE"),
        ("source", "NATIVE_BELIEF_FIRST"), ("epistemic_input", "BELIEF_CORE_CF07"),
    ):
        if authority.get(key) != expected:
            errors.append(f"unexpected v1.9 direction authority {key}")
    final = _mapping(metadata.get("final_decision"))
    if final.get("decision_source") != "NATIVE_BELIEF_FIRST":
        errors.append("v1.9 final decision source is not NATIVE_BELIEF_FIRST")
    if final.get("epistemic_source") != "BELIEF_CORE_CF07":
        errors.append("v1.9 final decision lost Belief/CF-07 epistemic source")
    if final.get("legacy_raw_score_direction_authority") is not False:
        errors.append("legacy raw score direction authority leaked into v1.9")
    consumer = _mapping(final.get("epistemic_consumer"))
    # Existing-position risk and terminal closes must remain available even
    # when upstream Epistemic data cannot qualify a new decision.
    if not position and not terminal and consumer.get("available") is not True:
        errors.append("DAILY_EURUSD_EPISTEMIC_UNAVAILABLE_NO_PUBLICATION: " + str(consumer.get("reason") or "unknown"))
    shadow = _mapping(metadata.get("shadow_boundary"))
    for name in ("A_B_C", "FSE", "EURUSD_X"):
        row = _mapping(shadow.get(name))
        for field in ("production_direction_authority", "production_timing_authority"):
            if row.get(field) is not False:
                errors.append(f"{name} regained {field}")
    return errors


def _trade_map(history: Any, label: str, errors: list[str]) -> dict[str, Mapping[str, Any]]:
    if not isinstance(history, Mapping) or history.get("schema_version") != "eurusd-daily-history-v1" or not isinstance(history.get("trades"), list):
        errors.append(f"{label}: invalid EURUSD history")
        return {}
    records: dict[str, Mapping[str, Any]] = {}
    for row in history["trades"]:
        identity = str(_mapping(row).get("trade_id") or "")
        if not identity or not isinstance(row, Mapping):
            errors.append(f"{label}: closed trade without trade_id")
        elif identity in records:
            errors.append(f"{label}: duplicate closed trade {identity}")
        else:
            records[identity] = row
    return records


def validate_history(history: Any, baseline: Any = None) -> list[str]:
    errors: list[str] = []
    current = _trade_map(history, "current history", errors)
    if baseline is None:
        return errors
    previous = _trade_map(baseline, "baseline history", errors)
    for identity, record in previous.items():
        if identity not in current:
            errors.append(f"immutable closed trade removed: {identity}")
        elif current[identity] != record:
            errors.append(f"immutable closed trade rewritten: {identity}")
    return errors


def validate_frozen_position(payload: Any, history: Any, baseline_payload: Any) -> list[str]:
    previous = _mapping(_mapping(_mapping(baseline_payload).get("metadata")).get("position"))
    if str(previous.get("status") or "").upper() != "OPEN":
        return []
    identity = previous.get("trade_id")
    current = _mapping(_mapping(_mapping(payload).get("metadata")).get("position"))
    if current.get("trade_id") != identity:
        current = next((row for row in (_mapping(history).get("trades") or []) if _mapping(row).get("trade_id") == identity), {})
    if not current:
        return [f"persisted OPEN position disappeared without settlement: {identity}"]
    return [f"frozen entry {key} rewritten for {identity}" for key in FROZEN_ENTRY_FIELDS if key in previous and current.get(key) != previous[key]]


def validate_notification_coverage(payload: Any, events: Any) -> list[str]:
    position = _mapping(_mapping(_mapping(payload).get("metadata")).get("position"))
    if str(position.get("status") or "").upper() != "OPEN":
        return []
    identity = str(position.get("trade_id") or "")
    expected = hashlib.sha256(f"daily|OPEN|{identity}".encode()).hexdigest()[:24]
    if any(_mapping(row).get("event_id") == expected for row in _mapping(events).get("events", [])):
        return []
    return [f"OPEN position has no notification event: {identity} / {expected}"]


def _read(root: Path, path: str, errors: list[str], *, optional: bool = False) -> Any:
    file = root / path
    if optional and not file.exists():
        return None
    try:
        value = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("expected JSON object")
        return value
    except (OSError, ValueError) as exc:
        errors.append(f"{path}: {exc}")
        return None


def _baseline(root: Path, ref: str | None, path: str, errors: list[str]) -> Any:
    if ref is None:
        return None
    result = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=root, capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        # An absent file is legal at prospective policy/ledger activation;
        # a missing baseline commit is never treated as empty history.
        exists = subprocess.run(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=root, capture_output=True, text=True)
        if exists.returncode:
            errors.append(f"invalid baseline ref: {ref}")
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        errors.append(f"invalid baseline JSON: {path}")
        return None


def _module(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return importlib.import_module(f"scripts.{name}")


def validate_production_feedback(
    payload: Any, history: Any, policy: Any, decisions: Any,
    before_policy: Any = None, before_decisions: Any = None,
) -> list[str]:
    """Use each owner module's contract; this gate never reconstructs a policy."""
    errors: list[str] = []
    if policy is not None:
        try:
            module = _module("daily_eurusd_production_policy")
            module.validate_state(policy)
            module.validate_history_bindings(policy, history)
            if before_policy is not None:
                module.validate_transition(before_policy, policy)
        except (ImportError, ValueError, TypeError, KeyError) as exc:
            errors.append(f"production policy: {exc}")
    if decisions is not None:
        try:
            module = _module("daily_eurusd_decision_record")
            module.validate_ledger(decisions)
            if before_decisions is not None:
                module.validate_ledger_transition(before_decisions, decisions)
            ledger = {row["decision_id"]: row for row in decisions["decisions"]}
            metadata = _mapping(_mapping(payload).get("metadata"))
            records = [metadata, _mapping(metadata.get("position")), *(_mapping(history).get("trades") or [])]
            for record in records:
                manifest = _mapping(record).get("decision_manifest")
                if manifest is None:
                    continue
                module.validate_manifest(manifest)
                if ledger.get(manifest["decision_id"]) != manifest:
                    errors.append(f"frozen decision missing or mismatched in ledger: {manifest['decision_id']}")
            if policy is None:
                errors.append("decision ledger requires its production policy registry")
            else:
                versions = policy.get("versions") or {}
                for manifest in decisions["decisions"]:
                    version = versions.get(manifest["policy_version_id"])
                    context = manifest.get("policy") or {}
                    if not version:
                        errors.append(f"manifest policy version missing: {manifest['policy_version_id']}")
                        continue
                    if (version.get("payload_sha256") != manifest["policy_sha256"]
                            or context.get("minimum_score_distance") != _mapping(version.get("policy")).get("minimum_score_distance")):
                        errors.append(f"manifest policy differs from registered version: {manifest['decision_id']}")
                    _module("daily_eurusd_production_policy").validate_reentry_manifest(policy, manifest)
                    final = manifest["final_decision"]
                    expected = _module("daily_eurusd_production_policy")._allows(version["policy"], float(final["score"]), str(final["direction"]))
                    if context.get("allowed") is not expected:
                        errors.append(f"manifest vote differs from registered policy: {manifest['decision_id']}")
                all_cases = [*(policy.get("cases") or {}).values(),
                             *((policy.get("reentry_learning") or {}).get("cases") or {}).values()]
                for case in all_cases:
                    manifest = _mapping(case.get("frozen")).get("manifest")
                    if not isinstance(manifest, Mapping) or ledger.get(manifest.get("decision_id")) != manifest:
                        errors.append("policy case has no identical frozen decision in ledger")
        except (ImportError, ValueError, TypeError, KeyError) as exc:
            errors.append(f"production decisions: {exc}")
    for trade in (_mapping(history).get("trades") or []):
        if _mapping(trade).get("decision_manifest") is not None:
            try:
                _module("daily_eurusd_decision_record").validate_trade_binding(trade)
            except (ImportError, ValueError, TypeError, KeyError) as exc:
                errors.append(f"manifested closed trade: {exc}")
    return errors


def _raw(root: Path, path: str) -> bytes | None:
    file = root / path
    return file.read_bytes() if file.exists() else None


def _baseline_raw(root: Path, ref: str | None, path: str) -> bytes | None:
    if ref is None:
        return None
    result = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=root, capture_output=True)
    return result.stdout if result.returncode == 0 else None


def validate_state(root: Path = ROOT, baseline_ref: str | None = None, lifecycle_only: bool = False) -> list[str]:
    errors: list[str] = []
    payload = _read(root, SPOT, errors)
    history = _read(root, HISTORY, errors)
    metadata = _mapping(_mapping(payload).get("metadata"))
    degraded = _mapping(metadata.get("production_policy_loop")).get("status") == "DEGRADED_EXIT_ONLY"
    exit_only = lifecycle_only or degraded
    if payload is not None:
        errors.extend(validate_payload(payload, lifecycle_only=exit_only))
    baseline_history = _baseline(root, baseline_ref, HISTORY, errors)
    errors.extend(validate_history(history, baseline_history))
    baseline_payload = _baseline(root, baseline_ref, SPOT, errors)
    errors.extend(validate_frozen_position(payload, history, baseline_payload))
    if exit_only:
        prior = _mapping(_mapping(_mapping(baseline_payload).get("metadata")).get("position"))
        current = _mapping(_mapping(_mapping(payload).get("metadata")).get("position"))
        if baseline_ref is None:
            errors.append("exit-only validation requires a baseline ref")
        if str(prior.get("status") or "").upper() != "OPEN" or not prior.get("trade_id"):
            errors.append("exit-only publication requires a persisted baseline OPEN position")
        if degraded and (_mapping(metadata.get("production_policy_loop")).get("entry_authority") is not False
                         or _mapping(metadata.get("production_policy_loop")).get("policy_changed") is not False):
            errors.append("degraded lifecycle cannot gain entry/policy authority")
        if current and current.get("trade_id") != prior.get("trade_id"):
            errors.append("exit-only writer created a new position")
        previous_ids = {str(_mapping(row).get("trade_id")) for row in (_mapping(baseline_history).get("trades") or [])}
        for row in (_mapping(history).get("trades") or []):
            identity = _mapping(row).get("trade_id")
            if identity not in previous_ids and identity != prior.get("trade_id"):
                errors.append(f"exit-only writer settled an unrelated position: {identity}")
    events = _read(root, EVENTS, errors)
    errors.extend(validate_notification_coverage(payload, events))
    if payload is not None:
        try:
            provenance = audit_provenance.audit_paths([root / SPOT], "daily", audit_provenance.NATIVE_ACTIVATION_AT)
            errors.extend(f"native provenance: {row}" for row in provenance.get("errors", []))
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f"invalid native provenance: {exc}")
    feedback: dict[str, Any] = {}
    previous_feedback: dict[str, Any] = {}
    for path in (POLICY, DECISIONS):
        if exit_only:
            if _raw(root, path) != _baseline_raw(root, baseline_ref, path):
                errors.append(f"exit-only writer mutated production policy/decisions: {path}")
            continue
        current = _read(root, path, errors, optional=True)
        before = _baseline(root, baseline_ref, path, errors)
        feedback[path] = current
        previous_feedback[path] = before
        if before is not None and current is None:
            errors.append(f"production state removed: {path}")
    if exit_only:
        # Unchanged corrupt/missing governance must not disable the existing
        # position's risk exit. No ledger/policy validation or reset is allowed.
        for trade in (_mapping(history).get("trades") or []):
            if _mapping(trade).get("decision_manifest") is not None:
                try:
                    _module("daily_eurusd_decision_record").validate_trade_binding(trade)
                except (ImportError, ValueError, TypeError, KeyError) as exc:
                    errors.append(f"manifested closed trade: {exc}")
    else:
        if (feedback[POLICY] is None) != (feedback[DECISIONS] is None):
            errors.append("production policy and decision ledger must share one initialized epoch")
        if feedback[POLICY] is None and (metadata.get("production_policy_loop") or any(_mapping(t).get("decision_manifest") for t in (_mapping(history).get("trades") or []))):
            errors.append("production feedback epoch missing; silent reset forbidden")
        errors.extend(validate_production_feedback(
            payload, history, feedback[POLICY], feedback[DECISIONS],
            previous_feedback[POLICY], previous_feedback[DECISIONS],
        ))
        manifest = metadata.get("decision_manifest")
        if manifest is not None:
            try:
                if manifest.get("runtime_code_sha256") != _module("daily_eurusd_decision_record").runtime_code_hash():
                    errors.append("runtime source changed after decision computation; recompute required")
            except (ImportError, ValueError, TypeError) as exc:
                errors.append(f"runtime source binding: {exc}")
    position = _mapping(_mapping(_mapping(payload).get("metadata")).get("position"))
    prior_position = _mapping(_mapping(_mapping(baseline_payload).get("metadata")).get("position"))
    if not exit_only and position and position.get("trade_id") != prior_position.get("trade_id"):
        if not position.get("decision_manifest") or feedback[POLICY] is None or feedback[DECISIONS] is None:
            errors.append("new OPEN position requires a frozen production decision, policy and ledger")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default=None)
    parser.add_argument("--lifecycle-only", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    errors = validate_state(args.root, args.baseline_ref, args.lifecycle_only)
    print(json.dumps({"status": "failed" if errors else "passed", "errors": errors}, ensure_ascii=False))
    if not errors:
        print("EURUSD_V19_BELIEF_FIRST_OK SHARED_PRE_PERSIST_VALIDATOR")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

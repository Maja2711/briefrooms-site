#!/usr/bin/env python3
"""Bridge Daily Stock MISS evidence into the existing PR35/PR36 research path.

This is intentionally *not* a second policy-promotion engine.  It can emit a
challenger intent only for a parameter already allowlisted by PR35, and only
while the current PR35/PR36 production freeze is active.  It never mutates the
runtime registry, production configs or positions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import autonomous_policy_promotion as pr35
    from scripts import autonomous_policy_promotion_v2 as pr35v2
    from scripts import daily_stock_miss_engine as miss
except ModuleNotFoundError:  # pragma: no cover
    import autonomous_policy_promotion as pr35
    import autonomous_policy_promotion_v2 as pr35v2
    import daily_stock_miss_engine as miss

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MISS_REPORT = ROOT / "data/investments/daily_stock_miss_learning_report.json"
DEFAULT_OUTPUT = ROOT / "data/investments/daily_stock_challenger_intents.json"
PROMOTION_CONFIG = ROOT / "data/investments/statistical_promotion_gate_v2_config.json"
SCHEMA_VERSION = "daily-stock-challenger-intents-v2"

MARKET_ENGINE = {"gpw": "gpw_daily", "us": "us_daily"}
# Only this rejection gate is currently represented by an allowlisted PR35
# production parameter.  Ranking/shortlist findings remain hypotheses.
GATE_TO_PR35 = {"minimum_composite_score": "minimum_composite_score"}


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        tmp = Path(handle.name)
    tmp.replace(path)


def assert_production_freeze(*, repo_root: Path = ROOT, state_dir: Path | None = None) -> dict[str, Any]:
    config_path = repo_root / "data/investments/statistical_promotion_gate_v2_config.json"
    config = _read_json(config_path)
    if config.get("production_promotion_enabled") is not False:
        raise RuntimeError("FAIL_CLOSED: PR36 production promotion freeze is not active")
    if int(config.get("promotion_methodology_version") or 0) != pr35v2.PROMOTION_METHODOLOGY_VERSION:
        raise RuntimeError("FAIL_CLOSED: unexpected promotion methodology")
    state = {
        "promotion_methodology_version": pr35v2.PROMOTION_METHODOLOGY_VERSION,
        "promotion_mode": pr35v2.FREEZE_MODE,
        "production_promotion_enabled": False,
        "runtime_registry_checked": False,
    }
    if state_dir is not None and (state_dir / pr35.REGISTRY_FILENAME).exists():
        registry = _read_json(state_dir / pr35.REGISTRY_FILENAME)
        pr35.validate_registry(registry)
        governance = registry.get("governance") if isinstance(registry.get("governance"), Mapping) else {}
        if governance.get("production_promotion_enabled") is not False:
            raise RuntimeError("FAIL_CLOSED: runtime registry allows production promotion")
        if governance.get("production_materialization_enabled") is not False:
            raise RuntimeError("FAIL_CLOSED: runtime registry allows production materialization")
        if governance.get("promotion_mode") != pr35v2.FREEZE_MODE:
            raise RuntimeError("FAIL_CLOSED: runtime registry is not in PR35/PR36 freeze mode")
        state["runtime_registry_checked"] = True
    return state


def _current_value(engine_id: str, repo_root: Path) -> tuple[str, float]:
    spec = pr35.POLICY_SPECS[engine_id]
    config = _read_json(repo_root / str(spec["config_path"]))
    parameter = str(spec["parameter"])
    if parameter not in config:
        raise RuntimeError(f"baseline parameter missing for {engine_id}: {parameter}")
    return parameter, float(config[parameter])


def build_intents(
    report: Mapping[str, Any],
    *,
    repo_root: Path = ROOT,
    state_dir: Path | None = None,
    minimum_misses: int = 3,
    minimum_mean_regret_pp: float = 0.50,
    generated_at: str | None = None,
) -> dict[str, Any]:
    miss.validate_report(report)
    freeze = assert_production_freeze(repo_root=repo_root, state_dir=state_dir)
    intents: list[dict[str, Any]] = []
    hypothesis_only: list[dict[str, Any]] = []

    for pattern in report.get("patterns") or []:
        if not isinstance(pattern, Mapping) or pattern.get("learnable") is not True:
            continue
        market = str(pattern.get("market") or "").lower()
        gate = str(pattern.get("gate") or "")
        evidence = {
            "pattern_id": pattern.get("pattern_id"),
            "observations": int(pattern.get("observations") or 0),
            "misses": int(pattern.get("misses") or 0),
            "miss_rate": float(pattern.get("miss_rate") or 0.0),
            "mean_candidate_minus_selected_percent": float(pattern.get("mean_candidate_minus_selected_percent") or 0.0),
            "unique_symbols": int(pattern.get("unique_symbols") or 0),
            "evidence_observation_ids": list(pattern.get("evidence_observation_ids") or []),
        }
        engine_id = MARKET_ENGINE.get(market)
        if not engine_id or gate not in GATE_TO_PR35:
            hypothesis_only.append({
                "hypothesis_id": f"misshyp-{_sha([market, gate, pattern.get('pattern_id')])[:20]}",
                "market": market,
                "gate": gate,
                "reason": "pattern_has_no_allowlisted_PR35_parameter_mapping",
                "evidence": evidence,
                "production_authority": False,
            })
            continue
        if evidence["misses"] < int(minimum_misses) or evidence["mean_candidate_minus_selected_percent"] < float(minimum_mean_regret_pp):
            hypothesis_only.append({
                "hypothesis_id": f"misshyp-{_sha([market, gate, pattern.get('pattern_id')])[:20]}",
                "market": market,
                "gate": gate,
                "reason": "insufficient_pattern_support_for_challenger_intent",
                "evidence": evidence,
                "production_authority": False,
            })
            continue

        spec = pr35.POLICY_SPECS[engine_id]
        parameter, current = _current_value(engine_id, repo_root)
        proposed = max(float(spec["lower"]), current - float(spec["step"]))
        if proposed >= current:
            hypothesis_only.append({
                "hypothesis_id": f"misshyp-{_sha([market, gate, current])[:20]}",
                "market": market,
                "gate": gate,
                "reason": "allowlisted_parameter_already_at_lower_bound",
                "evidence": evidence,
                "production_authority": False,
            })
            continue
        intent = {
            "intent_id": f"misschall-{_sha([engine_id, parameter, current, proposed, pattern.get('pattern_id')])[:24]}",
            "engine_id": engine_id,
            "market": market,
            "gate": gate,
            "parameter": parameter,
            "from_value": current,
            "to_value": proposed,
            "step": float(spec["step"]),
            "evidence": evidence,
            "route": {
                "candidate_engine": "autonomous_policy_promotion_v2.PR35",
                "confirmation_gate": "statistical_promotion_gate_v2.PR36",
                "validation_fixed_n": pr35v2.VALIDATION_FIXED_N,
                "production_promotion_enabled": False,
                "automatic_registry_mutation": False,
            },
            "status": "RESEARCH_INTENT_ONLY",
            "production_authority": False,
        }
        intents.append(intent)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_report_sha256": report.get("report_sha256"),
        "challenger_intents": intents,
        "hypothesis_only": hypothesis_only,
        "freeze": freeze,
        "governance": {
            "second_promotion_engine_created": False,
            "production_decision_influence": False,
            "production_policy_writeback": False,
            "production_materialization": False,
            "runtime_registry_writeback": False,
            "automatic_promotion": False,
            "trade_execution": False,
            "existing_PR35_PR36_is_only_promotion_route": True,
        },
    }
    body = dict(payload)
    body.pop("payload_sha256", None)
    payload["payload_sha256"] = _sha(body)
    return payload


def validate_intents(payload: Mapping[str, Any], *, repo_root: Path = ROOT) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Daily Stock challenger intent schema mismatch")
    body = dict(payload)
    stored = str(body.pop("payload_sha256", ""))
    if not stored or stored != _sha(body):
        raise ValueError("Daily Stock challenger intent hash mismatch")
    governance = payload.get("governance") if isinstance(payload.get("governance"), Mapping) else {}
    false_fields = (
        "second_promotion_engine_created",
        "production_decision_influence",
        "production_policy_writeback",
        "production_materialization",
        "runtime_registry_writeback",
        "automatic_promotion",
        "trade_execution",
    )
    if any(governance.get(field) is not False for field in false_fields):
        raise ValueError("challenger adapter zero-authority contract violated")
    if governance.get("existing_PR35_PR36_is_only_promotion_route") is not True:
        raise ValueError("challenger adapter attempted to bypass PR35/PR36")
    assert_production_freeze(repo_root=repo_root)
    for intent in payload.get("challenger_intents") or []:
        engine_id = str(intent.get("engine_id") or "")
        if engine_id not in pr35.POLICY_SPECS:
            raise ValueError("intent references non-allowlisted engine")
        spec = pr35.POLICY_SPECS[engine_id]
        if str(intent.get("parameter")) != str(spec["parameter"]):
            raise ValueError("intent references non-allowlisted parameter")
        current = float(intent.get("from_value"))
        proposed = float(intent.get("to_value"))
        if abs((current - proposed) - float(spec["step"])) > 1e-9 and proposed != float(spec["lower"]):
            raise ValueError("challenger intent exceeds PR35 one-step transition")
        if proposed < float(spec["lower"]) or proposed > float(spec["upper"]):
            raise ValueError("challenger intent outside PR35 bounds")
        route = intent.get("route") if isinstance(intent.get("route"), Mapping) else {}
        if route.get("production_promotion_enabled") is not False or route.get("automatic_registry_mutation") is not False:
            raise ValueError("challenger intent can mutate production/registry")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build zero-authority challenger intents from Daily Stock MISS evidence")
    parser.add_argument("--report", type=Path, default=DEFAULT_MISS_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        validate_intents(_read_json(args.output))
        print(json.dumps({"status": "OK"}, sort_keys=True))
        return 0
    report = _read_json(args.report)
    payload = build_intents(report, state_dir=args.state_dir)
    validate_intents(payload)
    _atomic_json(args.output, payload)
    print(json.dumps({"status": "BUILT", "challenger_intents": len(payload["challenger_intents"]), "hypothesis_only": len(payload["hypothesis_only"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

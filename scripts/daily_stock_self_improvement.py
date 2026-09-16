#!/usr/bin/env python3
"""Governed Daily Stock MISS -> hypothesis -> PR35/PR36 research orchestrator.

The name "self improvement" describes the learning loop, not autonomous
production mutation.  This program materializes observational MISS evidence,
research-only challenger intents and registry-ready hypothesis proposals.  The
existing PR35/PR36 pipeline remains the *only* route toward policy promotion and
is currently frozen from production by design.
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
    from scripts import daily_stock_champion_challenger as champion
    from scripts import daily_stock_miss_engine as miss
    from scripts import lesson_hypothesis_registry as hypothesis_registry
except ModuleNotFoundError:  # pragma: no cover
    import daily_stock_champion_challenger as champion
    import daily_stock_miss_engine as miss
    import lesson_hypothesis_registry as hypothesis_registry

ROOT = Path(__file__).resolve().parents[1]
MISS_REPORT = ROOT / "data/investments/daily_stock_miss_learning_report.json"
CHALLENGER_INTENTS = ROOT / "data/investments/daily_stock_challenger_intents.json"
HYPOTHESIS_PROPOSALS = ROOT / "data/investments/daily_stock_miss_hypothesis_proposals.json"
CANONICAL_HYPOTHESIS_REGISTRY = ROOT / "data/investments/lesson_hypothesis_registry_v1.json"
PROPOSAL_SCHEMA = "daily-stock-miss-hypothesis-proposals-v2"
RUN_SCHEMA = "daily-stock-self-improvement-run-v2"


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        tmp = Path(handle.name)
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _assert_hypothesis_registry_zero_authority(repo_root: Path = ROOT) -> dict[str, Any]:
    path = repo_root / "data/investments/lesson_hypothesis_registry_v1.json"
    registry = hypothesis_registry.load_registry(path)
    governance = registry.get("governance") if isinstance(registry.get("governance"), Mapping) else {}
    for key in (
        "automatic_promotion",
        "learning_ledger_writeback",
        "production_policy_writeback",
        "production_ranking_writeback",
        "production_sizing_writeback",
        "trade_execution",
    ):
        if governance.get(key) is not False:
            raise RuntimeError(f"FAIL_CLOSED: hypothesis registry authority changed: {key}")
    return {"registry_sha256": registry.get("registry_sha256"), "production_authority": False}


def build_hypothesis_proposals(
    report: Mapping[str, Any],
    intents: Mapping[str, Any],
    *,
    generated_at: str | None = None,
    repo_root: Path = ROOT,
) -> dict[str, Any]:
    miss.validate_report(report)
    champion.validate_intents(intents, repo_root=repo_root)
    registry_state = _assert_hypothesis_registry_zero_authority(repo_root)
    intents_by_pattern = {
        str((row.get("evidence") or {}).get("pattern_id")): row
        for row in intents.get("challenger_intents") or []
        if isinstance(row, Mapping)
    }
    hypothesis_only_by_pattern = {
        str((row.get("evidence") or {}).get("pattern_id")): row
        for row in intents.get("hypothesis_only") or []
        if isinstance(row, Mapping)
    }
    proposals: list[dict[str, Any]] = []
    for pattern in report.get("patterns") or []:
        if not isinstance(pattern, Mapping) or pattern.get("learnable") is not True or int(pattern.get("misses") or 0) <= 0:
            continue
        pattern_id = str(pattern.get("pattern_id") or "")
        intent = intents_by_pattern.get(pattern_id)
        hypothesis_only = hypothesis_only_by_pattern.get(pattern_id)
        market = str(pattern.get("market") or "").lower()
        gate = str(pattern.get("gate") or "")
        if intent:
            claim = (
                f"{market.upper()} Daily legal candidates rejected only by {gate} may have positive "
                "prospective incremental expectancy under the existing PR35/PR36 validation path."
            )
            experiment_spec = {
                "engine_id": intent.get("engine_id"),
                "gate": intent.get("gate"),
                "parameter": intent.get("parameter"),
                "from_value": intent.get("from_value"),
                "to_value": intent.get("to_value"),
                "stage": "PR35",
                "promotion_methodology_version": 2,
                "validation_target_n": 30,
                "sample_unit": "prospective_marginal_shadow_outcomes",
            }
            route = "PR35_PR36_ALLOWLISTED_CHALLENGER"
        else:
            claim = (
                f"{market.upper()} Daily legal candidates rejected at soft gate {gate} show a recurring "
                "actionable MISS pattern that should be tested prospectively before any policy change."
            )
            experiment_spec = None
            route = "HYPOTHESIS_ONLY_NO_PRODUCTION_PARAMETER_MAPPING"
        proposal = {
            "proposal_id": f"missproposal-{_sha([pattern_id, market, gate])[:24]}",
            "status": "DISCOVERED_RESEARCH_ONLY",
            "claim": claim,
            "market": market,
            "gate": gate,
            "route": route,
            "experiment_spec": experiment_spec,
            "evidence": {
                "source_pattern_id": pattern_id,
                "observations": pattern.get("observations"),
                "misses": pattern.get("misses"),
                "avoided_losses": pattern.get("avoided_losses"),
                "miss_rate": pattern.get("miss_rate"),
                "mean_candidate_minus_selected_percent": pattern.get("mean_candidate_minus_selected_percent"),
                "unique_symbols": pattern.get("unique_symbols"),
                "observation_ids": list(pattern.get("evidence_observation_ids") or []),
            },
            "challenger_intent_id": intent.get("intent_id") if intent else None,
            "hypothesis_only_reason": hypothesis_only.get("reason") if hypothesis_only else None,
            "automatic_registry_write": False,
            "production_authority": False,
        }
        proposals.append(proposal)
    payload = {
        "schema_version": PROPOSAL_SCHEMA,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_report_sha256": report.get("report_sha256"),
        "source_challenger_payload_sha256": intents.get("payload_sha256"),
        "canonical_hypothesis_registry": registry_state,
        "proposals": proposals,
        "governance": {
            "registry_ready_not_registry_mutation": True,
            "automatic_registry_write": False,
            "production_policy_writeback": False,
            "production_ranking_writeback": False,
            "automatic_promotion": False,
            "trade_execution": False,
            "hard_gate_weakening_allowed": False,
            "prospective_validation_required": True,
        },
    }
    body = dict(payload)
    body.pop("payload_sha256", None)
    payload["payload_sha256"] = _sha(body)
    return payload


def validate_proposals(payload: Mapping[str, Any], *, repo_root: Path = ROOT) -> None:
    if payload.get("schema_version") != PROPOSAL_SCHEMA:
        raise ValueError("MISS hypothesis proposal schema mismatch")
    body = dict(payload)
    stored = str(body.pop("payload_sha256", ""))
    if not stored or stored != _sha(body):
        raise ValueError("MISS hypothesis proposal hash mismatch")
    governance = payload.get("governance") if isinstance(payload.get("governance"), Mapping) else {}
    if governance.get("registry_ready_not_registry_mutation") is not True or governance.get("prospective_validation_required") is not True:
        raise ValueError("MISS hypothesis proposal research boundary missing")
    for key in (
        "automatic_registry_write",
        "production_policy_writeback",
        "production_ranking_writeback",
        "automatic_promotion",
        "trade_execution",
        "hard_gate_weakening_allowed",
    ):
        if governance.get(key) is not False:
            raise ValueError(f"MISS hypothesis proposal has forbidden authority: {key}")
    _assert_hypothesis_registry_zero_authority(repo_root)
    for proposal in payload.get("proposals") or []:
        if proposal.get("production_authority") is not False or proposal.get("automatic_registry_write") is not False:
            raise ValueError("proposal may not have production/registry write authority")


def run(
    *,
    horizon_sessions: int = miss.DEFAULT_HORIZON,
    min_regret_pp: float = miss.DEFAULT_MIN_REGRET_PP,
    state_dir: Path | None = None,
    repo_root: Path = ROOT,
) -> dict[str, Any]:
    freeze = champion.assert_production_freeze(repo_root=repo_root, state_dir=state_dir)
    _assert_hypothesis_registry_zero_authority(repo_root)
    report = miss.build_from_repo(horizon_sessions=horizon_sessions, min_regret_pp=min_regret_pp)
    miss.validate_report(report)
    intents = champion.build_intents(report, repo_root=repo_root, state_dir=state_dir)
    champion.validate_intents(intents, repo_root=repo_root)
    proposals = build_hypothesis_proposals(report, intents, repo_root=repo_root)
    validate_proposals(proposals, repo_root=repo_root)
    _atomic_json(MISS_REPORT, report)
    _atomic_json(CHALLENGER_INTENTS, intents)
    _atomic_json(HYPOTHESIS_PROPOSALS, proposals)
    return {
        "schema_version": RUN_SCHEMA,
        "status": "COMPLETE_RESEARCH_ONLY",
        "records_seen": report.get("records_seen"),
        "misses": report.get("misses"),
        "patterns": len(report.get("patterns") or []),
        "challenger_intents": len(intents.get("challenger_intents") or []),
        "hypothesis_proposals": len(proposals.get("proposals") or []),
        "production_promotion_enabled": freeze.get("production_promotion_enabled"),
        "production_mutation": False,
    }


def verify(*, repo_root: Path = ROOT) -> dict[str, Any]:
    report = _read_json(MISS_REPORT)
    intents = _read_json(CHALLENGER_INTENTS)
    proposals = _read_json(HYPOTHESIS_PROPOSALS)
    miss.validate_report(report)
    champion.validate_intents(intents, repo_root=repo_root)
    validate_proposals(proposals, repo_root=repo_root)
    if intents.get("source_report_sha256") != report.get("report_sha256"):
        raise ValueError("challenger intents are not bound to current MISS report")
    if proposals.get("source_report_sha256") != report.get("report_sha256"):
        raise ValueError("hypothesis proposals are not bound to current MISS report")
    if proposals.get("source_challenger_payload_sha256") != intents.get("payload_sha256"):
        raise ValueError("hypothesis proposals are not bound to challenger intents")
    freeze = champion.assert_production_freeze(repo_root=repo_root)
    return {
        "status": "OK",
        "misses": report.get("misses"),
        "challenger_intents": len(intents.get("challenger_intents") or []),
        "hypothesis_proposals": len(proposals.get("proposals") or []),
        "production_promotion_enabled": freeze.get("production_promotion_enabled"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Close the Daily Stock MISS learning loop without production mutation")
    parser.add_argument("--horizon", type=int, default=miss.DEFAULT_HORIZON)
    parser.add_argument("--min-regret-pp", type=float, default=miss.DEFAULT_MIN_REGRET_PP)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = verify() if args.verify else run(horizon_sessions=args.horizon, min_regret_pp=args.min_regret_pp, state_dir=args.state_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fail-closed exact-evidence wrapper for component Champion promotion.

A formal research PASS is not enough. Before the production promotion engine can
see a candidate, this gate requires the evaluation to cryptographically bind the
exact production deployment that was evaluated in shadow/replay. This prevents
"tested A, deployed B" failures and keeps legacy semantic experiments out of
production.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import stock_trading_component_promotion as promotion
    from scripts import stock_trading_component_router as router
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_component_promotion as promotion
    import stock_trading_component_router as router

REQUIRED_CANDIDATE_FIELDS = (
    "component",
    "deployment_id",
    "base_manifest_revision",
    "base_component_version",
    "deployment_sha256",
)
EXACT_SEMANTICS = "exact_shadow_replay"


class EvidenceBindingError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise EvidenceBindingError(f"{path} must contain a JSON object")
    return payload


def deployment_sha256(spec: Mapping[str, Any]) -> str:
    raw = json.dumps(
        dict(spec),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _candidate(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EvidenceBindingError(f"{label} missing")
    result = dict(value)
    for key in REQUIRED_CANDIDATE_FIELDS:
        raw = result.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            raise EvidenceBindingError(f"{label} missing {key}")
    try:
        revision = int(result["base_manifest_revision"])
    except (TypeError, ValueError) as exc:
        raise EvidenceBindingError(f"{label} invalid base_manifest_revision") from exc
    if revision < 1:
        raise EvidenceBindingError(f"{label} invalid base_manifest_revision")
    result["base_manifest_revision"] = revision
    return result


def validate_exact_binding(
    challenger: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    candidate = _candidate(challenger.get("production_candidate"), label="production_candidate")
    evaluated = _candidate(
        evaluation.get("evaluated_production_candidate"),
        label="evaluated_production_candidate",
    )
    if evaluated.get("execution_semantics") != EXACT_SEMANTICS:
        raise EvidenceBindingError("evaluation did not use exact_shadow_replay semantics")
    for key in REQUIRED_CANDIDATE_FIELDS:
        if str(candidate[key]) != str(evaluated[key]):
            raise EvidenceBindingError(f"evaluated candidate mismatch: {key}")

    deployment_id = str(candidate["deployment_id"])
    deployment = (registry.get("deployments") or {}).get(deployment_id)
    if not isinstance(deployment, Mapping):
        raise EvidenceBindingError("deployment is not production-approved")
    router.validate_deployment(deployment_id, deployment)
    actual_sha = deployment_sha256(deployment)
    if candidate["deployment_sha256"] != actual_sha:
        raise EvidenceBindingError("challenger deployment hash differs from production registry")
    if evaluated["deployment_sha256"] != actual_sha:
        raise EvidenceBindingError("evaluation deployment hash differs from production registry")
    if str(candidate["component"]) != str(deployment.get("component")):
        raise EvidenceBindingError("candidate component differs from deployment component")
    return candidate


def stage_bound_evidence(
    *,
    challengers: Path,
    evaluations: Path,
    registry_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    registry = router.load_registry(registry_path)
    out_challengers = output_root / "challengers"
    out_evaluations = output_root / "evaluations"
    out_challengers.mkdir(parents=True, exist_ok=True)
    out_evaluations.mkdir(parents=True, exist_ok=True)
    accepted = 0
    rejected: list[dict[str, str]] = []
    for evaluation_path in sorted(evaluations.glob("*.json")) if evaluations.exists() else []:
        evaluation = _read(evaluation_path)
        challenger_id = str(evaluation.get("challenger_id") or "")
        challenger_path = challengers / f"{challenger_id}.json"
        if not challenger_path.exists():
            rejected.append({"challenger_id": challenger_id, "reason": "challenger_missing"})
            continue
        challenger = _read(challenger_path)
        try:
            promotion.validate_research_pair(challenger, evaluation)
            validate_exact_binding(challenger, evaluation, registry)
        except (promotion.PromotionError, EvidenceBindingError, router.RouterError) as exc:
            rejected.append({"challenger_id": challenger_id, "reason": str(exc)})
            continue
        (out_challengers / challenger_path.name).write_text(
            json.dumps(challenger, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (out_evaluations / evaluation_path.name).write_text(
            json.dumps(evaluation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        accepted += 1
    return {
        "accepted": accepted,
        "rejected": rejected,
        "challengers": out_challengers,
        "evaluations": out_evaluations,
    }


def run_bound(
    *,
    challengers: Path,
    evaluations: Path,
    registry_path: Path = router.REGISTRY_PATH,
    **kwargs: Any,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="stock-trading-bound-promotion-") as tmp:
        staged = stage_bound_evidence(
            challengers=challengers,
            evaluations=evaluations,
            registry_path=registry_path,
            output_root=Path(tmp),
        )
        if staged["accepted"] == 0:
            return {
                "status": "NO_PROMOTION",
                "reason": "no_exactly_bound_candidate_passed_gate",
                "binding_rejections": staged["rejected"],
            }
        result = promotion.run(
            challengers=staged["challengers"],
            evaluations=staged["evaluations"],
            registry_path=registry_path,
            **kwargs,
        )
        result["binding_rejections"] = staged["rejected"]
        result["exact_binding_gate"] = True
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challengers", type=Path, required=True)
    parser.add_argument("--evaluations", type=Path, required=True)
    parser.add_argument("--registry", type=Path, default=router.REGISTRY_PATH)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--gpw-config", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    kwargs: dict[str, Any] = {"dry_run": args.dry_run}
    if args.manifest is not None:
        kwargs["manifest_path"] = args.manifest
    if args.history is not None:
        kwargs["history_dir"] = args.history
    if args.audit is not None:
        kwargs["audit_path"] = args.audit
    if args.gpw_config is not None:
        kwargs["gpw_config_path"] = args.gpw_config
    if args.policy is not None:
        kwargs["policy_path"] = args.policy
    result = run_bound(
        challengers=args.challengers,
        evaluations=args.evaluations,
        registry_path=args.registry,
        **kwargs,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

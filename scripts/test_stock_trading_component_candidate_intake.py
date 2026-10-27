#!/usr/bin/env python3
"""Bounded CASH intake unit tests with only independent proof mocked.

The unmocked evidence suite verifies full prospective native replay. These
fixtures exercise single-path CASH bounds, two-horizon identity, current
Champion checks, hashes and registry persistence using isolated temporary files.
"""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts import stock_trading_component_bound_promotion as bound
from scripts import stock_trading_component_candidate_intake as intake
from scripts import stock_trading_component_champion as champion
from scripts import stock_trading_component_promotion as promotion
from scripts import stock_trading_component_router as router
from scripts import stock_trading_component_evidence as evidence
from scripts import stock_trading_v2_challenger_factory as factory
from scripts import stock_trading_v2_replay_tournament as tournament


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _policy() -> dict:
    return {
        "policy_version": "intake-unit-v2",
        "champion_engine": "v2",
        "cash_utility": 50,
        "forced_trade_allowed": False,
        "markets": {
            "GPW": {
                "minimum_entry_score": 0,
                "minimum_cash_edge_points": 5,
                "max_open_positions": 3,
                "minimum_reward_risk": 1.5,
                "maximum_risk_percent": 0.07,
            },
            "US": {
                "minimum_entry_score": 0,
                "minimum_cash_edge_points": 5,
                "max_open_positions": 3,
                "minimum_reward_risk": 1.5,
                "maximum_risk_percent": 0.07,
            },
        },
    }


def _gpw() -> dict:
    return {"minimum_composite_score": 0}


def _registry() -> dict:
    return {
        "schema_version": router.REGISTRY_SCHEMA,
        "governance": {
            "research_may_reference_only": True,
            "production_owns_executable_artifacts": True,
            "arbitrary_code_loading_forbidden": True,
            "one_component_per_promotion": True,
        },
        "deployments": {},
    }


def _spec(cash_edge: int) -> dict:
    return {
        "component": "entry",
        "version": f"cash-edge-gpw-{cash_edge}-r1",
        "type": "config_patch",
        "targets": {
            "stock_trading_policy": [
                {"op": "replace", "path": ["markets", "GPW", "minimum_cash_edge_points"], "value": cash_edge},
            ],
        },
    }


def _pair(name: str, *, cash_edge: int, horizon: int, revision: int = 1, tamper_hash: bool = False) -> tuple[dict, dict, str]:
    spec = _spec(cash_edge)
    deployment_sha = bound.deployment_sha256(spec)
    deployment_id = f"stdep-entry-{deployment_sha[:20]}"
    base = factory._cash_edge_candidates(hypothesis={"component": "cash_admission_margin", "market": "GPW", "horizon_sessions": 1},
        manifest=champion.bootstrap_manifest(created_at="2026-09-17T00:00:00Z"), policy=_policy())[0]
    base.update(deployment_id=deployment_id, base_manifest_revision=revision, horizon_sessions=horizon,
        deployment_sha256=("0" * 64) if tamper_hash else deployment_sha, deployment_spec=spec)
    base["replay_contract"]["challenger_cash_edge_points"] = cash_edge
    base["candidate_sha256"] = promotion._hash_without(base, "candidate_sha256")
    challenger = tournament.make_challenger(base, "intake-unit-discovery", {}, start_at="2026-09-17T00:00:00Z")
    challenger["challenger_id"] = name
    challenger["challenger_sha256"] = promotion._hash_without(challenger, "challenger_sha256")
    candidate = challenger["production_candidate"]
    evaluation = {
        "schema_version": "stock-trading-v2-challenger-evaluation-v1",
        "challenger_id": name,
        "component": "cash_admission_margin",
        "horizon_sessions": horizon,
        "state": "RESEARCH_PASS_EXACT_HOLDOUT",
        "metrics": {"formal_pass": True},
        "evaluated_production_candidate": {
            "component": candidate["component"],
            "deployment_id": candidate["deployment_id"],
            "base_manifest_revision": candidate["base_manifest_revision"],
            "base_component_version": candidate["base_component_version"],
            "deployment_sha256": candidate["deployment_sha256"],
            "execution_semantics": bound.EXACT_SEMANTICS,
        },
    }
    evaluation["evaluation_sha256"] = promotion._hash_without(evaluation, "evaluation_sha256")
    return challenger, evaluation, deployment_id


@patch.object(evidence, "validate_proof", return_value={"verified": True})
def main(proof_mock) -> int:
    with tempfile.TemporaryDirectory(prefix="component-intake-test-") as tmp:
        root = Path(tmp)
        challengers = root / "challengers"
        evaluations = root / "evaluations"
        challengers.mkdir()
        evaluations.mkdir()
        registry_path = root / "registry.json"
        manifest_path = root / "manifest.json"
        gpw_path = root / "gpw.json"
        policy_path = root / "policy.json"
        approved_path = root / "approved.json"
        trials_path = root / "trials.json"
        evidence.initialize_trials(trials_path)

        base_manifest = champion.bootstrap_manifest(created_at="2026-09-17T00:00:00Z")
        _write(manifest_path, base_manifest)
        _write(registry_path, _registry())
        _write(gpw_path, _gpw())
        _write(policy_path, _policy())

        fixtures: list[tuple[str, dict, dict]] = []
        valid_ids: list[str] = []
        for horizon in (1, 2):
            ch, ev, deployment_id = _pair(f"valid-h{horizon}", cash_edge=4, horizon=horizon)
            fixtures.append((f"valid-h{horizon}", ch, ev))
            valid_ids.append(deployment_id)
        assert valid_ids[0] == valid_ids[1]
        valid_id = valid_ids[0]

        # A short-only PASS is deliberately insufficient: the exact same
        # deployment must also PASS the other production holding horizon.
        short_only_ch, short_only_ev, short_only_id = _pair("short-only", cash_edge=6, horizon=1)
        fixtures.append(("short-only", short_only_ch, short_only_ev))

        too_large_ids: list[str] = []
        for horizon in (1, 2):
            ch, ev, deployment_id = _pair(f"too-large-h{horizon}", cash_edge=3, horizon=horizon)
            fixtures.append((f"too-large-h{horizon}", ch, ev))
            too_large_ids.append(deployment_id)
        too_large_id = too_large_ids[0]

        tampered_ch, tampered_ev, tampered_id = _pair("tampered", cash_edge=2, horizon=1, tamper_hash=True)
        stale_ch, stale_ev, stale_id = _pair("stale", cash_edge=2, horizon=2, revision=2)
        long_ch, long_ev, long_id = _pair("long-horizon", cash_edge=2, horizon=20)
        fixtures.extend([
            ("tampered", tampered_ch, tampered_ev),
            ("stale", stale_ch, stale_ev),
            ("long-horizon", long_ch, long_ev),
        ])

        for index, (name, ch, ev) in enumerate(fixtures, 1):
            family = {"family_id": evidence.FAMILY_ID, "test_index": index, "challenger_id": ch["challenger_id"],
                "protocol_sha256": ch["protocol_sha256"], "reserved_at": ch["created_at"], "lifetime_max_tests": 24, "alpha": 0.05 / 24}
            family["reservation_sha256"] = promotion._hash_without(family, "reservation_sha256")
            ch["test_family"] = family
            ch["challenger_sha256"] = promotion._hash_without(ch, "challenger_sha256")
            _write(challengers / f"{name}.json", ch)
            _write(evaluations / f"{name}.json", ev)

        result = intake.approve_proposals(
            challengers=challengers,
            evaluations=evaluations,
            registry_path=registry_path,
            manifest_path=manifest_path,
            gpw_config_path=gpw_path,
            policy_path=policy_path,
            output_path=approved_path,
            source_root=root / "source", frames_root=root / "frames", trials_path=trials_path,
        )
        assert result["accepted"] == [valid_id]
        reason_text = "\n".join(row["reason"] for row in result["rejected"])
        assert "maximum per-promotion delta" in reason_text
        assert "SHA" in reason_text
        assert "stale" in reason_text.lower()
        assert "outside the 1-2 session production mandate" in reason_text
        assert "missing exact fresh-holdout PASS" in reason_text

        approved = router.load_registry(approved_path)
        assert set(approved["deployments"]) == {valid_id}
        for rejected_id in (short_only_id, too_large_id, tampered_id, stale_id, long_id):
            assert rejected_id not in approved["deployments"]

        promoted = champion.build_promoted_manifest(
            base_manifest,
            component="entry",
            version=approved["deployments"][valid_id]["version"],
            deployment_id=valid_id,
            challenger_id="valid-h1",
            evidence_sha256="a" * 64,
            promotion_id="synthetic-promotion",
            expected_revision=1,
            promoted_at="2026-09-17T01:00:00Z",
        )
        _write(manifest_path, promoted)
        persist = intake.persist_active(
            approved_registry_path=approved_path,
            registry_path=registry_path,
            manifest_path=manifest_path,
        )
        assert persist["persisted"] == [valid_id]
        production_registry = router.load_registry(registry_path)
        assert set(production_registry["deployments"]) == {valid_id}
        assert proof_mock.call_count == len(fixtures)
        assert approved["deployments"][valid_id]["targets"] == {
            "stock_trading_policy": [{"op": "replace", "path": ["markets", "GPW", "minimum_cash_edge_points"], "value": 4}]}

    print("COMPONENT_CANDIDATE_INTAKE_TESTS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

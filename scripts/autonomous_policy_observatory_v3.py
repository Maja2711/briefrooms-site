#!/usr/bin/env python3
"""Production-aware observatory for the autonomous PR35/PR36 closed loop.

The existing observatory remains the canonical research view.  This wrapper
adds the separately governed production actuator state, so a successfully
materialized Challenger is shown as the active Champion even though the PR35 /
PR36 research registry intentionally keeps production authority frozen.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    import autonomous_policy_closed_loop as closed
    import autonomous_policy_observatory as base
except ModuleNotFoundError:  # pragma: no cover
    from scripts import autonomous_policy_closed_loop as closed
    from scripts import autonomous_policy_observatory as base


def _authorized(state: Mapping[str, Any], engine: Mapping[str, Any]) -> bool:
    if int(engine.get("revision") or 0) == 0:
        return True
    candidate_id = str(engine.get("source_candidate_id") or "")
    outcome = (state.get("candidate_outcomes") or {}).get(candidate_id)
    return isinstance(outcome, Mapping) and outcome.get("status") == "PROMOTED"


def build(state_dir: Path, repo_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    public, private = base.build(state_dir, repo_root)
    production = closed.load_state(repo_root, create=False, now=datetime.now(timezone.utc))
    safety = public.get("safety") if isinstance(public.get("safety"), dict) else {}
    research_flag = bool(safety.get("production_promotion_enabled", False))

    if production is None:
        safety.update({
            "research_gate_production_promotion_enabled": research_flag,
            "closed_loop_production_enabled": True,
            "automatic_materialization": True,
            "production_promotion_enabled": True,
            "promotion_mode": "PR35_PR36_THEN_AUTONOMOUS_CLOSED_LOOP",
        })
        public["safety"] = safety
        public["state_digest"] = base._digest(public)
        private["production_state"] = None
        private["public"] = public
        return public, private

    closed.verify_state(repo_root, production)
    production_engines = production.get("engines") if isinstance(production.get("engines"), Mapping) else {}
    for row in public.get("engines") or []:
        if not isinstance(row, dict):
            continue
        engine_id = str(row.get("engine") or "")
        engine = production_engines.get(engine_id)
        if not isinstance(engine, Mapping):
            continue
        row["active"] = {
            "policy_version": engine.get("effective_policy_version"),
            "revision": int(engine.get("revision") or 0),
            "parameter": engine.get("parameter"),
            "value": engine.get("value"),
            "baseline": int(engine.get("revision") or 0) == 0,
            "statistically_authorized": _authorized(production, engine),
            "source_candidate_id": engine.get("source_candidate_id"),
            "activated_at": engine.get("activated_at"),
            "source": "autonomous_closed_loop_production_state",
        }
        row["blocked_until"] = engine.get("blocked_until")
        if engine.get("live_monitor") is not None:
            row["live_monitor"] = engine.get("live_monitor")

    safety.update({
        "research_gate_production_promotion_enabled": research_flag,
        "closed_loop_production_enabled": True,
        "automatic_materialization": True,
        "automatic_rollback": True,
        "production_promotion_enabled": True,
        "promotion_mode": "PR35_PR36_THEN_AUTONOMOUS_CLOSED_LOOP",
        "production_authority_layer": "autonomous_policy_closed_loop",
    })
    public["safety"] = safety
    public["state_digest"] = base._digest(public)
    private["production_state"] = production
    private["public"] = public
    return public, private


def publish(state_dir: Path, repo_root: Path) -> dict[str, Any]:
    public, private = build(state_dir, repo_root)
    target = repo_root / base.PUBLIC_PATH
    existing = base._read_json(target, {})
    changed = not isinstance(existing, Mapping) or existing.get("state_digest") != public.get("state_digest")
    if changed:
        base._write(target, public)
    base._write(state_dir / base.PRIVATE_PATH, private)
    return {
        "changed": changed,
        "public_path": base.PUBLIC_PATH,
        "state_digest": public["state_digest"],
        "engines": len(public.get("engines") or []),
        "closed_loop_production_enabled": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Production-aware autonomous policy observatory")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    state_dir, repo_root = Path(args.state_dir), Path(args.repo_root)
    if args.verify:
        public, _ = build(state_dir, repo_root)
        result = {
            "ok": True,
            "state_digest": public["state_digest"],
            "engines": len(public.get("engines") or []),
            "closed_loop_production_enabled": public["safety"]["closed_loop_production_enabled"],
        }
    else:
        result = publish(state_dir, repo_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

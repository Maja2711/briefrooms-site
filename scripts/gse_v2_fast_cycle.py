#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gse_v2_learning_loop as loop

UTC = timezone.utc
SCHEMA_VERSION = "gse-v2-fast-cycle-v1"


def _required_json(path: Path, label: str) -> dict[str, Any]:
    payload = loop.read_json(path, {})
    if not payload:
        raise ValueError(f"{label} is unavailable: {path}")
    return payload


def run_fast_cycle(
    state_dir: Path,
    catalog_path: Path,
    *,
    now: datetime | None = None,
    market: Any = None,
) -> dict[str, Any]:
    """Run the inexpensive prospective part of GSE v2.

    Heavy historical walk-forward and policy-grid research are deliberately reused
    from the last deep-research cycle. Every successful call still processes new
    frozen forecasts/verifications, recalibrates prospective quality, recomputes
    readiness, persists state and appends a hash-chained Learning Ledger record.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    market = market or loop.HistoricalMarketClient()

    catalog = _required_json(catalog_path, "GSE historical catalogue")
    if not catalog.get("events"):
        raise ValueError("GSE historical catalogue contains no events")

    enriched_path = state_dir / "gse_v2_enriched_library.json"
    historical_path = state_dir / "gse_v2_historical_walkforward.json"
    policy_path = state_dir / "gse_v2_policy_proposal.json"

    enriched = _required_json(enriched_path, "GSE v2 enriched historical library")
    historical_report = _required_json(historical_path, "GSE v2 historical walk-forward report")
    policy_proposal = _required_json(policy_path, "GSE v2 policy proposal")

    if not enriched.get("responses"):
        raise ValueError("GSE v2 enriched historical library has no responses")

    current_catalog_sha = loop.canonical_sha256(loop._catalog_projection(catalog))
    historical_refresh_required = enriched.get("catalog_sha256") != current_catalog_sha

    candidates_added = loop.generate_candidates(state_dir, enriched, market, now=now)
    verifications_added = loop.verify_candidates(state_dir, now=now)
    calibration = loop.build_calibration(state_dir)
    loop.write_json(state_dir / "gse_v2_regime_calibration.json", calibration)

    gate = loop.readiness(historical_report, calibration)
    state = {
        "schema_version": loop.SCHEMA_VERSION,
        "cycle_schema_version": SCHEMA_VERSION,
        "mode": loop.MODE,
        "updated_at": loop.iso_z(now),
        "cycle_mode": "fast_prospective",
        "active_research_policy": {
            "version": loop.RESEARCH_POLICY_VERSION,
            **{key: float(value) for key, value in loop.ACTIVE_RESEARCH_POLICY.items()},
        },
        "policy_proposal": policy_proposal,
        "historical": {
            "evaluable_walkforward_n": historical_report.get("evaluable_predictions"),
            "overall": historical_report.get("overall"),
            "reused_from_deep_research": True,
            "refresh_required": historical_refresh_required,
        },
        "prospective": calibration.get("overall"),
        "readiness": gate,
        "controls": {
            "automatic_tuning_enabled": False,
            "policy_proposal_auto_apply_enabled": False,
            "automatic_promotion_enabled": False,
            "decision_engine_connected": False,
            "belief_core_connected": False,
            "trade_execution_enabled": False,
            "policy_output_enabled": False,
            "v1_forecast_modified": False,
        },
        "last_cycle": {
            "cycle_mode": "fast_prospective",
            "candidates_added": candidates_added,
            "verifications_added": verifications_added,
            "enriched_library_refreshed_at": enriched.get("built_at"),
            "historical_refresh_required": historical_refresh_required,
        },
    }
    loop.write_json(state_dir / "gse_v2_learning_state.json", state)
    loop.append_learning_ledger(
        state_dir,
        now=now,
        enriched_library=enriched,
        historical_report=historical_report,
        policy_proposal=policy_proposal,
        calibration=calibration,
        candidates_added=candidates_added,
        verifications_added=verifications_added,
    )
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fast prospective GSE v2 learning cycle")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--catalog", required=True)
    args = parser.parse_args()
    state = run_fast_cycle(Path(args.state_dir), Path(args.catalog))
    print(
        json.dumps(
            {
                "mode": state["mode"],
                "cycle_mode": state["cycle_mode"],
                "readiness": state["readiness"],
                "last_cycle": state["last_cycle"],
                "prospective": state["prospective"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import investments_weekly_ma_structure as ma
import investments_weekly_v4 as v4

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "data/investments/multi_instrument_exposure_policy.json"
OUT = ROOT / "data/investments/eurusd_ma_structure_validation.json"


def main() -> None:
    policy = v4.read(POLICY, {})
    now = datetime.now().astimezone()
    context = ma.context("eurusd", now, policy)
    candidates = {
        "synthetic_long": {"direction": "long", "conviction": 5.0},
        "synthetic_short": {"direction": "short", "conviction": 5.0},
    }
    bounded = ma.apply_to_candidates("eurusd", candidates, {"data_quality": "passed", "score": 999.0, "score_cap": 4.0}, policy)
    max_adjust = float((policy.get("eurusd_ma_structure") or {}).get("candidate_alignment_bonus_max") or 3.0)
    checks = {
        "policy_enabled": bool((policy.get("eurusd_ma_structure") or {}).get("enabled")),
        "live_context_available": context.get("data_quality") == "passed",
        "defense_in_depth_clamp_long": abs(float(bounded["synthetic_long"].get("ma_structure_adjustment") or 0.0) - max_adjust) < 1e-9,
        "defense_in_depth_clamp_short": abs(float(bounded["synthetic_short"].get("ma_structure_adjustment") or 0.0) + max_adjust) < 1e-9,
        "no_standalone_trigger": (policy.get("eurusd_ma_structure") or {}).get("rule", "").find("never a standalone trade trigger") >= 0,
    }
    payload = {
        "status": "passed" if all(checks.values()) else "failed",
        "validated_at": now.isoformat(timespec="seconds"),
        "github_sha": os.environ.get("GITHUB_SHA"),
        "policy_version": policy.get("policy_version"),
        "checks": checks,
        "live_context": context,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    if payload["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "gse-v2-learning-review-status-v1"
UTC = timezone.utc


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False)
    tmp = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _run_metadata() -> dict[str, Any]:
    repo = os.getenv("GITHUB_REPOSITORY") or None
    run_id = os.getenv("GITHUB_RUN_ID") or None
    server = (os.getenv("GITHUB_SERVER_URL") or "https://github.com").rstrip("/")
    return {
        "repository": repo,
        "run_id": run_id,
        "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT") or None,
        "event": os.getenv("GITHUB_EVENT_NAME") or None,
        "sha": os.getenv("GITHUB_SHA") or None,
        "run_url": f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else None,
    }


def build_status(state: Mapping[str, Any], proposal: Mapping[str, Any], calibration: Mapping[str, Any], previous: Mapping[str, Any] | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    previous = dict(previous or {})
    policy_status = str(proposal.get("status") or "unknown")
    readiness = dict(state.get("readiness") or {})
    readiness_status = str(readiness.get("status") or "unknown")

    shadow_review = policy_status == "eligible_for_human_shadow_review"
    promotion_review = readiness_status == "eligible_for_human_promotion_review"
    review_required = shadow_review or promotion_review
    if promotion_review:
        status = "human_promotion_review_required"
    elif shadow_review:
        status = "human_shadow_review_required"
    else:
        status = readiness_status if readiness_status != "unknown" else "shadow_learning"

    prev_status = str(previous.get("status") or "none")
    prev_review = bool(previous.get("review_required"))
    if review_required and not prev_review:
        transition = "entered_human_review"
    elif not review_required and prev_review:
        transition = "left_human_review"
    elif status != prev_status:
        transition = "status_changed"
    else:
        transition = "unchanged"

    prospective = dict(state.get("prospective") or {})
    paired_n = int(prospective.get("paired_n") or 0)
    delta_brier = prospective.get("delta_brier_v2_minus_v1")
    delta_log = prospective.get("delta_log_loss_v2_minus_v1")
    bias = prospective.get("calibration_bias_v2_regime")
    quality_flags = {
        "prospective_v2_worse_brier": paired_n >= 10 and delta_brier is not None and float(delta_brier) > 0,
        "prospective_v2_worse_log_loss": paired_n >= 10 and delta_log is not None and float(delta_log) > 0,
        "prospective_calibration_bias_above_0_10": paired_n >= 10 and bias is not None and abs(float(bias)) > 0.10,
        "review_candidate_available": review_required,
    }

    candidate = proposal.get("candidate") if isinstance(proposal.get("candidate"), Mapping) else None
    payload = {
        "schema_version": SCHEMA_VERSION,
        "published_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": status,
        "previous_status": prev_status,
        "transition": transition,
        "review_required": review_required,
        "review_kind": "promotion" if promotion_review else "shadow_policy" if shadow_review else None,
        "policy_proposal": {
            "status": policy_status,
            "candidate": dict(candidate) if candidate else None,
            "holdout_delta_brier_candidate_minus_active": proposal.get("holdout_delta_brier_candidate_minus_active"),
            "train_cluster_n": proposal.get("train_cluster_n"),
            "holdout_cluster_n": proposal.get("holdout_cluster_n"),
            "automatically_applied": bool(proposal.get("automatically_applied", False)),
            "active_policy_unchanged": bool(proposal.get("active_policy_unchanged", True)),
        },
        "readiness": readiness,
        "historical": state.get("historical") or {},
        "prospective": prospective,
        "quality_flags": quality_flags,
        "controls": state.get("controls") or {},
        "calibration_overall": calibration.get("overall") or {},
        "run": _run_metadata(),
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish compact auditable GSE v2 learning/review status")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--previous", default="")
    args = parser.parse_args()
    root = Path(args.state_dir)
    state = _read_json(root / "gse_v2_learning_state.json")
    proposal = _read_json(root / "gse_v2_policy_proposal.json")
    calibration = _read_json(root / "gse_v2_regime_calibration.json")
    if not state or not proposal:
        raise SystemExit("GSE v2 learning state or policy proposal is unavailable")
    previous = _read_json(Path(args.previous)) if args.previous else {}
    payload = build_status(state, proposal, calibration, previous)
    _write_json_atomic(Path(args.output), payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

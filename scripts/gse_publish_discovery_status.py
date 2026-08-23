from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "gse-historical-discovery-public-status-v1"
DEFAULT_TARGET = 100
UTC = timezone.utc


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _run_metadata() -> dict[str, Any]:
    run_id = os.getenv("GITHUB_RUN_ID") or None
    repository = os.getenv("GITHUB_REPOSITORY") or None
    server = (os.getenv("GITHUB_SERVER_URL") or "https://github.com").rstrip("/")
    return {
        "repository": repository,
        "event": os.getenv("GITHUB_EVENT_NAME") or None,
        "run_id": run_id,
        "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT") or None,
        "sha": os.getenv("GITHUB_SHA") or None,
        "ref": os.getenv("GITHUB_REF") or None,
        "run_url": f"{server}/{repository}/actions/runs/{run_id}" if repository and run_id else None,
    }


def build_status(state: Mapping[str, Any] | None, *, discovery_exit_code: int = 0, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    state = dict(state or {})
    target = int(state.get("target_verified_cluster_n") or DEFAULT_TARGET)
    verified = state.get("effective_verified_cluster_n")
    verified_n = int(verified) if verified is not None else None
    target_met = bool(state.get("target_met")) if verified_n is not None else False

    if discovery_exit_code != 0:
        status = "discovery_error"
    elif verified_n is None:
        status = "state_unavailable"
    elif target_met:
        status = "target_met"
    else:
        status = "below_target"

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "published_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": status,
        "mode": state.get("mode") or "shadow",
        "target_verified_clusters": target,
        "verified_clusters": verified_n,
        "target_met": target_met,
        "machine_verified_documents": state.get("machine_verified_document_n"),
        "auto_clusters": state.get("auto_cluster_n"),
        "quality_balance_gate": state.get("quality_balance_gate") or None,
        "by_scenario": state.get("by_scenario") or {},
        "by_source": state.get("by_source") or {},
        "full_backfill_run": state.get("full_backfill_run"),
        "last_full_backfill_at": state.get("last_full_backfill_at"),
        "pipeline_version": state.get("pipeline_version"),
        "discovery_exit_code": int(discovery_exit_code),
        "controls": state.get("controls") or {
            "market_outcomes_used_for_event_selection": False,
            "automatic_tuning_enabled": False,
            "decision_engine_connected": False,
            "belief_core_connected": False,
            "trade_execution_enabled": False,
            "policy_output_enabled": False,
        },
        "run": _run_metadata(),
        "source_state_sha256": _canonical_sha256(state) if state else None,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a compact auditable GSE historical discovery status")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--discovery-exit-code", type=int, default=0)
    args = parser.parse_args()

    state_path = Path(args.state_dir) / "gse_historical_discovery_state.json"
    state = _read_json(state_path)
    status = build_status(state, discovery_exit_code=args.discovery_exit_code)
    _write_json_atomic(Path(args.output), status)
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

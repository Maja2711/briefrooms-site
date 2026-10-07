#!/usr/bin/env python3
"""One-way metadata-only migration to P1 WES v5 history seals."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

try:
    from scripts import wes_v5_history_seal as seal
except ModuleNotFoundError:  # direct scripts/ execution
    import wes_v5_history_seal as seal

ROOT = Path(__file__).resolve().parents[1]
WEEKLY = ROOT / "data" / "investments" / "weekly"


def read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def migrate() -> dict[str, Any]:
    seal.verify_manifest()
    rows = []
    changed_files = 0
    for path in sorted(WEEKLY.glob("*.json")):
        week = read(path)
        if not week or not seal.is_wes_v5_week(week):
            continue

        before = seal.sha256(seal.economic_payload(week))
        before_serialized = seal.canonical_json(week)
        seal.seal_forecast(week)

        leg_count = 0
        for item in week.get("instruments", []):
            if not isinstance(item, dict):
                continue
            legs = item.get("position_legs") if isinstance(item.get("position_legs"), list) else []
            for leg in legs:
                if not isinstance(leg, dict):
                    continue
                seal.seal_closed_leg(str(week.get("week_id") or path.stem), leg)
                leg_count += 1

        after = seal.sha256(seal.economic_payload(week))
        if before != after:
            raise seal.WesHistorySealError(
                f"P1 seal migration changed economic payload: {path.name}"
            )

        after_serialized = seal.canonical_json(week)
        changed = before_serialized != after_serialized
        if changed:
            atomic_write(path, week)
            changed_files += 1
        rows.append({
            "week_id": week.get("week_id") or path.stem,
            "changed": changed,
            "economic_payload_hash_before": before,
            "economic_payload_hash_after": after,
            "position_legs_sealed": leg_count,
            "forecast_payload_hash": (week.get("frozen_forecast_seal") or {}).get("payload_hash"),
        })

    manifest = seal.verify_manifest()
    return {
        "schema_version": "briefrooms-wes-v5-history-seal-migration-v1",
        "status": "SEALED",
        "weeks": rows,
        "changed_files": changed_files,
        "manifest_records": len(manifest.get("records") or []),
        "manifest_head_record_hash": manifest.get("head_record_hash"),
    }


def check() -> dict[str, Any]:
    manifest = seal.verify_manifest()
    issues = []
    checked = 0
    for path in sorted(WEEKLY.glob("*.json")):
        week = read(path)
        if not week or not seal.is_wes_v5_week(week):
            continue
        checked += 1
        for issue in seal.week_seal_violations(week, manifest):
            issues.append({"week_id": week.get("week_id") or path.stem, **issue})
    if issues:
        raise seal.WesHistorySealError("WES v5 P1 seal check failed: " + json.dumps(issues, ensure_ascii=False))
    return {
        "schema_version": "briefrooms-wes-v5-history-seal-migration-v1",
        "status": "PASS",
        "checked_weeks": checked,
        "manifest_records": len(manifest.get("records") or []),
        "manifest_head_record_hash": manifest.get("head_record_hash"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = check() if args.check else migrate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

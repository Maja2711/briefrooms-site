#!/usr/bin/env python3
"""Versioned component-level Champion manifest for BriefRooms Stock Trading.

The manifest is the single production authority for which implementation of each
Stock Trading component is active. Promotion mutates exactly one component,
creates an immutable snapshot of the prior revision and records an audit event.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "data/investments/stock_trading_champion_manifest.json"
HISTORY_DIR = ROOT / "data/investments/stock_trading_champion_history"
AUDIT_PATH = ROOT / "data/investments/stock_trading_component_promotion_audit.jsonl"
SCHEMA_VERSION = "stock-trading-champion-manifest-v1"
COMPONENTS = ("universe", "ranking", "meta_label", "entry", "risk", "portfolio", "exit", "regime")


class ChampionError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ChampionError(f"Cannot read Champion manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ChampionError("Champion manifest must be a JSON object")
    return payload


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        temp = Path(handle.name)
    os.replace(temp, path)


def _append_audit(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def validate_manifest(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ChampionError("Champion manifest schema mismatch")
    revision = payload.get("revision")
    if not isinstance(revision, int) or revision < 1:
        raise ChampionError("Champion manifest revision must be a positive integer")
    if payload.get("status") != "ACTIVE":
        raise ChampionError("Champion manifest must be ACTIVE")
    components = payload.get("components")
    if not isinstance(components, Mapping) or set(components) != set(COMPONENTS):
        raise ChampionError("Champion manifest must define the complete component set")
    for component in COMPONENTS:
        spec = components.get(component)
        if not isinstance(spec, Mapping):
            raise ChampionError(f"Champion component {component} is invalid")
        version = str(spec.get("version") or "").strip()
        if not version:
            raise ChampionError(f"Champion component {component} has no version")
        deployment_id = spec.get("deployment_id")
        source = str(spec.get("source") or "")
        if source == "legacy":
            if deployment_id not in (None, ""):
                raise ChampionError(f"Legacy component {component} cannot have deployment_id")
        else:
            if not str(deployment_id or "").strip():
                raise ChampionError(f"Promoted component {component} requires deployment_id")


def bootstrap_manifest(*, created_at: str | None = None) -> dict[str, Any]:
    stamp = created_at or utc_now()
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "revision": 1,
        "status": "ACTIVE",
        "previous_revision": None,
        "updated_at": stamp,
        "promotion_id": None,
        "components": {
            component: {
                "version": "v1",
                "source": "legacy",
                "deployment_id": None,
                "challenger_id": None,
                "evidence_sha256": None,
                "promoted_at": None,
            }
            for component in COMPONENTS
        },
    }
    validate_manifest(payload)
    return payload


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    payload = _read_json(path)
    validate_manifest(payload)
    return payload


def save_manifest(payload: Mapping[str, Any], path: Path = MANIFEST_PATH) -> None:
    validate_manifest(payload)
    _atomic_json(path, payload)


def snapshot_manifest(payload: Mapping[str, Any], history_dir: Path = HISTORY_DIR) -> Path:
    validate_manifest(payload)
    revision = int(payload["revision"])
    path = history_dir / f"champion-r{revision:06d}.json"
    if path.exists():
        existing = _read_json(path)
        if existing != dict(payload):
            raise ChampionError(f"Champion history collision at revision {revision}")
        return path
    _atomic_json(path, payload)
    return path


def build_promoted_manifest(
    current: Mapping[str, Any],
    *,
    component: str,
    version: str,
    deployment_id: str,
    challenger_id: str,
    evidence_sha256: str,
    promotion_id: str,
    expected_revision: int | None = None,
    promoted_at: str | None = None,
) -> dict[str, Any]:
    validate_manifest(current)
    if component not in COMPONENTS:
        raise ChampionError(f"Unknown Stock Trading component: {component}")
    if expected_revision is not None and int(current["revision"]) != int(expected_revision):
        raise ChampionError(
            f"Champion revision race: expected {expected_revision}, current {current['revision']}"
        )
    for label, value in {
        "version": version,
        "deployment_id": deployment_id,
        "challenger_id": challenger_id,
        "evidence_sha256": evidence_sha256,
        "promotion_id": promotion_id,
    }.items():
        if not str(value or "").strip():
            raise ChampionError(f"Promotion missing {label}")

    stamp = promoted_at or utc_now()
    next_manifest = deepcopy(dict(current))
    old_revision = int(current["revision"])
    next_manifest["revision"] = old_revision + 1
    next_manifest["previous_revision"] = old_revision
    next_manifest["updated_at"] = stamp
    next_manifest["promotion_id"] = promotion_id
    next_manifest["components"][component] = {
        "version": version,
        "source": "component_promotion",
        "deployment_id": deployment_id,
        "challenger_id": challenger_id,
        "evidence_sha256": evidence_sha256,
        "promoted_at": stamp,
    }
    validate_manifest(next_manifest)
    return next_manifest


def promote_component(
    *,
    component: str,
    version: str,
    deployment_id: str,
    challenger_id: str,
    evidence_sha256: str,
    promotion_id: str,
    manifest_path: Path = MANIFEST_PATH,
    history_dir: Path = HISTORY_DIR,
    audit_path: Path = AUDIT_PATH,
    expected_revision: int | None = None,
    promoted_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = load_manifest(manifest_path)
    snapshot_manifest(current, history_dir)
    next_manifest = build_promoted_manifest(
        current,
        component=component,
        version=version,
        deployment_id=deployment_id,
        challenger_id=challenger_id,
        evidence_sha256=evidence_sha256,
        promotion_id=promotion_id,
        expected_revision=expected_revision,
        promoted_at=promoted_at,
    )
    save_manifest(next_manifest, manifest_path)
    _append_audit(
        audit_path,
        {
            "event": "COMPONENT_PROMOTED",
            "at": next_manifest["updated_at"],
            "promotion_id": promotion_id,
            "component": component,
            "from_revision": current["revision"],
            "to_revision": next_manifest["revision"],
            "old_component": current["components"][component],
            "new_component": next_manifest["components"][component],
        },
    )
    return current, next_manifest


def restore_manifest(
    prior: Mapping[str, Any],
    *,
    failed_revision: int,
    reason: str,
    manifest_path: Path = MANIFEST_PATH,
    audit_path: Path = AUDIT_PATH,
) -> dict[str, Any]:
    validate_manifest(prior)
    restored = deepcopy(dict(prior))
    save_manifest(restored, manifest_path)
    _append_audit(
        audit_path,
        {
            "event": "AUTO_ROLLBACK",
            "at": utc_now(),
            "failed_revision": failed_revision,
            "restored_revision": restored["revision"],
            "reason": reason,
        },
    )
    return restored


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--bootstrap", action="store_true")
    args = parser.parse_args()
    if args.bootstrap:
        if args.manifest.exists():
            raise SystemExit("Champion manifest already exists")
        save_manifest(bootstrap_manifest(), args.manifest)
    manifest = load_manifest(args.manifest)
    if args.verify or args.bootstrap:
        print(json.dumps({"ok": True, "revision": manifest["revision"], "components": manifest["components"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

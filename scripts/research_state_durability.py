#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

SCHEMA_VERSION = "research-state-durability-v1"
MANIFEST_FILENAME = "RESEARCH_STATE_DURABILITY_MANIFEST.json"
HISTORY_FILENAME = "RESEARCH_STATE_CHECKPOINT_HISTORY.jsonl"
CHECKPOINT_ARTIFACT_PREFIX = "research-state-durability-"
UTC = timezone.utc


@dataclass(frozen=True)
class LayerSpec:
    layer_id: str
    producer_workflow: str
    primary_artifact: str
    archive_filename: str
    required_files: tuple[str, ...]


LAYERS: Dict[str, LayerSpec] = {
    "pr10_broad_market": LayerSpec(
        "pr10_broad_market",
        "BRACE Broad-Market Belief Shadow",
        "brace-broad-market-belief-state",
        "brace-broad-market-belief-state.tgz",
        ("layer_state.json", "state.json", "BRACE_BROAD_MARKET_BELIEF_REPORT.json"),
    ),
    "pr11_sector_factor": LayerSpec(
        "pr11_sector_factor",
        "BRACE Sector-Factor Belief Shadow",
        "brace-sector-factor-belief-state",
        "brace-sector-factor-belief-state.tgz",
        ("pr11_state.json", "belief_core/state.json", "BRACE_SECTOR_FACTOR_BELIEF_REPORT.json"),
    ),
    "pr12_company_entity": LayerSpec(
        "pr12_company_entity",
        "BRACE Company-Entity Framework Shadow",
        "brace-company-entity-framework-state",
        "brace-company-entity-framework-state.tgz",
        ("ENTITY_ACTIVATION_STATE.json", "BRACE_COMPANY_ENTITY_FRAMEWORK_REPORT.json"),
    ),
    "pr13_primary_source": LayerSpec(
        "pr13_primary_source",
        "BRACE Entity Primary-Source Evidence Shadow",
        "brace-entity-primary-source-evidence-state",
        "brace-entity-primary-source-evidence-state.tgz",
        ("ENTITY_PRIMARY_SOURCE_EVIDENCE_STATE.json", "BRACE_ENTITY_PRIMARY_SOURCE_EVIDENCE_REPORT.json"),
    ),
    "pr14_interpretation": LayerSpec(
        "pr14_interpretation",
        "BRACE Entity Evidence Interpretation Shadow",
        "brace-entity-evidence-interpretation-state",
        "brace-entity-evidence-interpretation-state.tgz",
        ("ENTITY_EVIDENCE_INTERPRETATION_STATE.json", "BRACE_ENTITY_EVIDENCE_INTERPRETATION_REPORT.json"),
    ),
    "pr15_belief_forecast": LayerSpec(
        "pr15_belief_forecast",
        "BRACE Entity Belief State-Forecast Shadow",
        "brace-entity-belief-state-forecast-state",
        "brace-entity-belief-state-forecast-state.tgz",
        (
            "ENTITY_BELIEF_FORECAST_RUNTIME_STATE.json",
            "belief_core/state.json",
            "BRACE_ENTITY_BELIEF_STATE_FORECAST_REPORT.json",
        ),
    ),
    "pr16_calibration": LayerSpec(
        "pr16_calibration",
        "BRACE Entity Calibration Diagnostics Shadow",
        "brace-entity-calibration-diagnostics-state",
        "brace-entity-calibration-diagnostics-state.tgz",
        ("ENTITY_CALIBRATION_DIAGNOSTICS_STATE.json", "BRACE_ENTITY_CALIBRATION_DIAGNOSTICS_REPORT.json"),
    ),
    "pr16_1_world_state": LayerSpec(
        "pr16_1_world_state",
        "Investment Semantics World State Shadow",
        "investment-semantics-world-state",
        "investment-semantics-world-state.tgz",
        ("INVESTMENT_WORLD_STATE_RUNTIME_STATE.json", "INVESTMENT_SEMANTICS_WORLD_STATE_REPORT.json"),
    ),
    "pr17_entity_bridge": LayerSpec(
        "pr17_entity_bridge",
        "BRACE Entity Belief Prospective Shadow Bridge",
        "brace-entity-belief-shadow-bridge-state",
        "brace-entity-belief-shadow-bridge-state.tgz",
        ("BRACE_ENTITY_BELIEF_SHADOW_BRIDGE_STATE.json", "BRACE_ENTITY_BELIEF_WITH_WITHOUT_REPORT.json"),
    ),
    "pr19_epistemic_graph": LayerSpec(
        "pr19_epistemic_graph",
        "Belief Epistemic Causal Graph Shadow",
        "belief-epistemic-causal-graph-state",
        "belief-epistemic-causal-graph-state.tgz",
        ("BELIEF_EPISTEMIC_CAUSAL_GRAPH_STATE.json", "BELIEF_EPISTEMIC_CAUSAL_GRAPH_REPORT.json"),
    ),
}


def _spec(layer_id: str) -> LayerSpec:
    try:
        return LAYERS[layer_id]
    except KeyError as exc:
        raise ValueError(f"unknown research-state layer: {layer_id}") from exc


def checkpoint_artifact_name(layer_id: str) -> str:
    _spec(layer_id)
    return CHECKPOINT_ARTIFACT_PREFIX + layer_id


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _canonical_sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _payload_files(state_dir: Path) -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    for path in sorted(p for p in state_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(state_dir).as_posix()
        if rel in {MANIFEST_FILENAME, HISTORY_FILENAME}:
            continue
        rows.append({"path": rel, "sha256": _file_sha(path), "size_bytes": path.stat().st_size})
    return rows


def _assert_required(state_dir: Path, spec: LayerSpec) -> None:
    missing = [rel for rel in spec.required_files if not (state_dir / rel).is_file()]
    if missing:
        raise RuntimeError(f"{spec.layer_id}: missing required state files: {', '.join(missing)}")


def _read_manifest(state_dir: Path) -> Optional[Dict[str, Any]]:
    path = state_dir / MANIFEST_FILENAME
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("durability manifest must be a JSON object")
    return payload


def _history_rows(state_dir: Path) -> list[Dict[str, Any]]:
    path = state_dir / HISTORY_FILENAME
    if not path.exists():
        return []
    rows: list[Dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not row.get("checkpoint_id"):
            raise RuntimeError(f"invalid durability history row at line {lineno}")
        rows.append(row)
    ids = [str(x["checkpoint_id"]) for x in rows]
    if len(ids) != len(set(ids)):
        raise RuntimeError("durability history contains duplicate checkpoint ids")
    return rows


def _compact_manifest(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    payload = manifest.get("payload") or {}
    return {
        "checkpoint_id": manifest.get("checkpoint_id"),
        "checkpoint_created_at": manifest.get("checkpoint_created_at"),
        "layer_id": manifest.get("layer_id"),
        "parent_checkpoint_id": manifest.get("parent_checkpoint_id"),
        "producer_workflow": manifest.get("producer_workflow"),
        "producer_run_id": manifest.get("producer_run_id"),
        "producer_head_sha": manifest.get("producer_head_sha"),
        "payload_aggregate_sha256": payload.get("aggregate_sha256"),
    }


def _verify_manifest_self(layer_id: str, manifest: Mapping[str, Any], history: Sequence[Mapping[str, Any]]) -> None:
    spec = _spec(layer_id)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError(f"{layer_id}: unsupported durability schema")
    if manifest.get("layer_id") != layer_id:
        raise RuntimeError(f"{layer_id}: manifest layer mismatch")
    if manifest.get("producer_workflow") != spec.producer_workflow:
        raise RuntimeError(f"{layer_id}: producer workflow mismatch")
    checkpoint_id = str(manifest.get("checkpoint_id") or "")
    body = dict(manifest)
    body.pop("checkpoint_id", None)
    if checkpoint_id != "rsd1-" + _canonical_sha(body)[:32]:
        raise RuntimeError(f"{layer_id}: manifest checkpoint hash mismatch")
    _parse_time(str(manifest.get("checkpoint_created_at")))
    parent = manifest.get("parent_checkpoint_id")
    if parent and str(parent) not in {str(x["checkpoint_id"]) for x in history}:
        raise RuntimeError(f"{layer_id}: manifest parent checkpoint missing from local history")


def seal_state(
    layer_id: str,
    state_dir: Path,
    *,
    now: Optional[datetime] = None,
    producer_run_id: Optional[str] = None,
    producer_attempt: Optional[str] = None,
    producer_head_sha: Optional[str] = None,
    migration_from_legacy: bool = False,
) -> Dict[str, Any]:
    spec = _spec(layer_id)
    state_dir = state_dir.resolve()
    _assert_required(state_dir, spec)
    previous = _read_manifest(state_dir)
    history = _history_rows(state_dir)
    history_ids = {str(x["checkpoint_id"]) for x in history}
    if previous:
        _verify_manifest_self(layer_id, previous, history)
        prev_id = str(previous["checkpoint_id"])
        if prev_id not in history_ids:
            with (state_dir / HISTORY_FILENAME).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(_compact_manifest(previous), ensure_ascii=False, sort_keys=True) + "\n")
        parent_checkpoint_id: Optional[str] = prev_id
        legacy_parent = False
    else:
        parent_checkpoint_id = None
        legacy_parent = bool(migration_from_legacy)

    files = _payload_files(state_dir)
    body: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "layer_id": layer_id,
        "checkpoint_created_at": _iso_z(now or datetime.now(UTC)),
        "parent_checkpoint_id": parent_checkpoint_id,
        "legacy_parent_without_manifest": legacy_parent,
        "producer_workflow": spec.producer_workflow,
        "producer_run_id": str(producer_run_id or os.environ.get("GITHUB_RUN_ID") or "local"),
        "producer_attempt": str(producer_attempt or os.environ.get("GITHUB_RUN_ATTEMPT") or "1"),
        "producer_head_sha": str(producer_head_sha or os.environ.get("GITHUB_SHA") or "local"),
        "payload": {
            "files": files,
            "file_count": len(files),
            "aggregate_sha256": _canonical_sha(files),
        },
        "durability_contract": {
            "primary_storage": "github_actions_artifact",
            "secondary_storage": "independent_refresh_artifact",
            "missing_prior_state_policy": "FAIL_CLOSED",
            "silent_first_run_reset": False,
            "storage_refresh_mutates_research_state": False,
            "public_repository_state_persistence": False,
        },
    }
    body["checkpoint_id"] = "rsd1-" + _canonical_sha(body)[:32]
    (state_dir / MANIFEST_FILENAME).write_text(
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    verify_state(layer_id, state_dir, allow_legacy=False)
    return body


def verify_state(layer_id: str, state_dir: Path, *, allow_legacy: bool = False) -> Dict[str, Any]:
    spec = _spec(layer_id)
    state_dir = state_dir.resolve()
    _assert_required(state_dir, spec)
    manifest = _read_manifest(state_dir)
    if manifest is None:
        if allow_legacy:
            return {"status": "legacy_unsealed", "layer_id": layer_id}
        raise RuntimeError(f"{layer_id}: durability manifest is missing")
    history = _history_rows(state_dir)
    _verify_manifest_self(layer_id, manifest, history)
    payload = manifest.get("payload") or {}
    current_files = _payload_files(state_dir)
    if payload.get("files") != current_files:
        raise RuntimeError(f"{layer_id}: payload hash/size manifest mismatch")
    if payload.get("file_count") != len(current_files):
        raise RuntimeError(f"{layer_id}: payload file_count mismatch")
    if payload.get("aggregate_sha256") != _canonical_sha(current_files):
        raise RuntimeError(f"{layer_id}: payload aggregate hash mismatch")
    checkpoint_id = str(manifest.get("checkpoint_id") or "")
    if not checkpoint_id.startswith("rsd1-"):
        raise RuntimeError(f"{layer_id}: invalid checkpoint id")
    return {"status": "sealed", "layer_id": layer_id, "checkpoint_id": checkpoint_id}


def _api_json(url: str, token: str) -> Any:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def _download(url: str, token: str, destination: Path) -> None:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=90) as response, destination.open("wb") as fh:
        shutil.copyfileobj(response, fh)


def _safe_extract_tar(tar_path: Path, destination: Path) -> None:
    destination = destination.resolve()
    with tarfile.open(tar_path, "r:gz") as tf:
        for member in tf.getmembers():
            target = (destination / member.name).resolve()
            if target != destination and destination not in target.parents:
                raise RuntimeError("unsafe path in state tar archive")
        tf.extractall(destination)


def _extract_artifact_zip(zip_path: Path, archive_filename: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.infolist():
                target = (root / member.filename).resolve()
                if target != root and root not in target.parents:
                    raise RuntimeError("unsafe path in artifact zip")
            zf.extractall(root)
        archives = list(root.rglob(archive_filename))
        if len(archives) != 1:
            raise RuntimeError(f"artifact must contain exactly one {archive_filename}")
        _safe_extract_tar(archives[0], destination)


def _successful_main_candidate(
    repository: str,
    token: str,
    artifact: Mapping[str, Any],
    expected_workflow: str,
) -> bool:
    wr = artifact.get("workflow_run") or {}
    if str(wr.get("head_branch") or "") != "main" or not wr.get("id"):
        return False
    run = _api_json(f"https://api.github.com/repos/{repository}/actions/runs/{wr['id']}", token)
    return (
        str(run.get("name") or "") == expected_workflow
        and str(run.get("head_branch") or "") == "main"
        and str(run.get("conclusion") or "") == "success"
    )


def _artifact_candidates(repository: str, token: str, artifact_name: str) -> list[Mapping[str, Any]]:
    from urllib.parse import quote
    url = f"https://api.github.com/repos/{repository}/actions/artifacts?name={quote(artifact_name)}&per_page=100"
    payload = _api_json(url, token)
    rows = [x for x in payload.get("artifacts", []) if not x.get("expired")]
    rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return rows


def _clear_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _try_restore_from_artifact_name(
    *,
    repository: str,
    token: str,
    layer_id: str,
    artifact_name: str,
    expected_workflow: str,
    state_dir: Path,
    allow_legacy: bool,
    max_candidates: int = 12,
) -> Optional[Dict[str, Any]]:
    spec = _spec(layer_id)
    for artifact in _artifact_candidates(repository, token, artifact_name)[:max_candidates]:
        try:
            if not _successful_main_candidate(repository, token, artifact, expected_workflow):
                continue
            with tempfile.TemporaryDirectory() as tmp:
                zip_path = Path(tmp) / "artifact.zip"
                extract_dir = Path(tmp) / "state"
                _download(str(artifact["archive_download_url"]), token, zip_path)
                _extract_artifact_zip(zip_path, spec.archive_filename, extract_dir)
                verification = verify_state(layer_id, extract_dir, allow_legacy=allow_legacy)
                _clear_directory(state_dir)
                shutil.copytree(extract_dir, state_dir, dirs_exist_ok=True)
                wr = artifact.get("workflow_run") or {}
                return {
                    "source": "primary" if artifact_name == spec.primary_artifact else "durability_checkpoint",
                    "artifact_id": artifact.get("id"),
                    "artifact_name": artifact_name,
                    "producer_run_id": wr.get("id"),
                    "producer_head_sha": wr.get("head_sha"),
                    **verification,
                }
        except Exception as exc:
            print(f"research-state restore skipped artifact {artifact.get('id')}: {exc}", file=sys.stderr)
    return None


def restore_state(
    layer_id: str,
    state_dir: Path,
    *,
    repository: str,
    token: str,
    optional: bool = False,
    primary_only: bool = False,
) -> Dict[str, Any]:
    spec = _spec(layer_id)
    state_dir.mkdir(parents=True, exist_ok=True)
    result = _try_restore_from_artifact_name(
        repository=repository,
        token=token,
        layer_id=layer_id,
        artifact_name=spec.primary_artifact,
        expected_workflow=spec.producer_workflow,
        state_dir=state_dir,
        allow_legacy=True,
    )
    if result:
        return result
    if not primary_only:
        result = _try_restore_from_artifact_name(
            repository=repository,
            token=token,
            layer_id=layer_id,
            artifact_name=checkpoint_artifact_name(layer_id),
            expected_workflow="Research State Durability Heartbeat",
            state_dir=state_dir,
            allow_legacy=False,
        )
        if result:
            return result
    if optional:
        return {"source": "missing_optional", "status": "missing_optional", "layer_id": layer_id}
    raise RuntimeError(
        f"{layer_id}: no valid production artifact or durability checkpoint; "
        "FAIL_CLOSED to prevent silent research-state reset"
    )


def pack_state(layer_id: str, state_dir: Path, output: Path) -> None:
    verify_state(layer_id, state_dir, allow_legacy=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as tf:
        for path in sorted(state_dir.rglob("*")):
            if path.is_file():
                tf.add(path, arcname=path.relative_to(state_dir).as_posix())


def refresh_checkpoint(
    layer_id: str,
    state_dir: Path,
    output: Path,
    *,
    repository: str,
    token: str,
) -> Dict[str, Any]:
    result = restore_state(
        layer_id,
        state_dir,
        repository=repository,
        token=token,
        optional=False,
        primary_only=True,
    )
    if result.get("status") == "legacy_unsealed":
        seal_state(
            layer_id,
            state_dir,
            producer_run_id=str(result.get("producer_run_id") or "legacy"),
            producer_head_sha=str(result.get("producer_head_sha") or "legacy"),
            migration_from_legacy=True,
        )
    else:
        verify_state(layer_id, state_dir, allow_legacy=False)
    pack_state(layer_id, state_dir, output)
    manifest = _read_manifest(state_dir) or {}
    return {
        "layer_id": layer_id,
        "checkpoint_id": manifest.get("checkpoint_id"),
        "source_artifact_id": result.get("artifact_id"),
        "output": str(output),
        "research_state_mutated": False,
    }


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing environment variable {name}")
    return value


def _cmd_registry(args: argparse.Namespace) -> int:
    specs = [_spec(args.layer)] if args.layer else list(LAYERS.values())
    payload = [
        {
            "layer_id": spec.layer_id,
            "producer_workflow": spec.producer_workflow,
            "primary_artifact": spec.primary_artifact,
            "checkpoint_artifact": checkpoint_artifact_name(spec.layer_id),
            "archive_filename": spec.archive_filename,
            "required_files": list(spec.required_files),
        }
        for spec in specs
    ]
    print(json.dumps(payload[0] if args.layer else payload, indent=2, sort_keys=True))
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    result = restore_state(
        args.layer,
        Path(args.state_dir),
        repository=args.repository or _env("GITHUB_REPOSITORY"),
        token=args.token or _env("GH_TOKEN"),
        optional=args.optional,
        primary_only=args.primary_only,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_seal(args: argparse.Namespace) -> int:
    manifest = seal_state(
        args.layer,
        Path(args.state_dir),
        producer_run_id=args.producer_run_id,
        producer_attempt=args.producer_attempt,
        producer_head_sha=args.producer_head_sha,
        migration_from_legacy=args.migration_from_legacy,
    )
    print(json.dumps({"layer_id": args.layer, "checkpoint_id": manifest["checkpoint_id"]}, indent=2))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    print(json.dumps(
        verify_state(args.layer, Path(args.state_dir), allow_legacy=args.allow_legacy),
        indent=2,
        sort_keys=True,
    ))
    return 0


def _cmd_refresh(args: argparse.Namespace) -> int:
    print(json.dumps(
        refresh_checkpoint(
            args.layer,
            Path(args.state_dir),
            Path(args.output),
            repository=args.repository or _env("GITHUB_REPOSITORY"),
            token=args.token or _env("GH_TOKEN"),
        ),
        indent=2,
        sort_keys=True,
    ))
    return 0


def _cmd_pack(args: argparse.Namespace) -> int:
    pack_state(args.layer, Path(args.state_dir), Path(args.output))
    print(json.dumps({"layer_id": args.layer, "output": str(args.output)}, indent=2))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Research-state integrity, restore and refresh durability contract")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("registry")
    p.add_argument("--layer", choices=sorted(LAYERS))
    p.set_defaults(func=_cmd_registry)

    p = sub.add_parser("restore")
    p.add_argument("--layer", required=True, choices=sorted(LAYERS))
    p.add_argument("--state-dir", required=True)
    p.add_argument("--repository")
    p.add_argument("--token")
    p.add_argument("--optional", action="store_true")
    p.add_argument("--primary-only", action="store_true")
    p.set_defaults(func=_cmd_restore)

    p = sub.add_parser("seal")
    p.add_argument("--layer", required=True, choices=sorted(LAYERS))
    p.add_argument("--state-dir", required=True)
    p.add_argument("--migration-from-legacy", action="store_true")
    p.add_argument("--producer-run-id")
    p.add_argument("--producer-attempt")
    p.add_argument("--producer-head-sha")
    p.set_defaults(func=_cmd_seal)

    p = sub.add_parser("verify")
    p.add_argument("--layer", required=True, choices=sorted(LAYERS))
    p.add_argument("--state-dir", required=True)
    p.add_argument("--allow-legacy", action="store_true")
    p.set_defaults(func=_cmd_verify)

    p = sub.add_parser("refresh")
    p.add_argument("--layer", required=True, choices=sorted(LAYERS))
    p.add_argument("--state-dir", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--repository")
    p.add_argument("--token")
    p.set_defaults(func=_cmd_refresh)

    p = sub.add_parser("pack")
    p.add_argument("--layer", required=True, choices=sorted(LAYERS))
    p.add_argument("--state-dir", required=True)
    p.add_argument("--output", required=True)
    p.set_defaults(func=_cmd_pack)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"RESEARCH_STATE_DURABILITY_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

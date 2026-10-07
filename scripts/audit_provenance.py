#!/usr/bin/env python3
"""Global fail-closed auditor for BriefRooms Provenance Contract v1.

The gate is prospective. Historical artifacts created before native provenance
activation remain readable and are not backfilled. Every governed artifact at
or after the activation boundary must carry a valid native provenance envelope.

This auditor has no decision, scoring, sizing, execution or promotion authority.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    import provenance_contract as pc
except ImportError:
    from scripts import provenance_contract as pc

UTC = timezone.utc
NATIVE_ACTIVATION_AT = "2026-10-07T21:35:18Z"


@dataclass
class GovernedArtifact:
    profile: str
    locator: str
    payload: Mapping[str, Any]
    effective_at: str | None


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_json_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        return []
    return sorted(p for p in path.rglob("*.json") if p.is_file())


def _artifact(profile: str, locator: str, payload: Any, effective_at: Any) -> GovernedArtifact | None:
    if not isinstance(payload, Mapping):
        return None
    return GovernedArtifact(profile, locator, payload, str(effective_at) if effective_at else None)


def _collect_daily(root: Any, locator: str) -> list[GovernedArtifact]:
    if not isinstance(root, Mapping):
        return []
    final = ((root.get("metadata") or {}).get("final_decision")
             if isinstance(root.get("metadata"), Mapping) else None)
    row = _artifact("daily", locator + "#metadata.final_decision", final, root.get("timestamp"))
    return [row] if row else []


def _collect_stock(root: Any, locator: str) -> list[GovernedArtifact]:
    out: list[GovernedArtifact] = []
    if not isinstance(root, Mapping):
        return out
    for i, run in enumerate(root.get("audit") or []):
        if not isinstance(run, Mapping):
            continue
        run_at = run.get("run_at")
        for j, action in enumerate(run.get("actions") or []):
            row = _artifact("stock", f"{locator}#audit[{i}].actions[{j}]", action, run_at)
            if row:
                out.append(row)
    return out


def _collect_shadow(root: Any, locator: str) -> list[GovernedArtifact]:
    out: list[GovernedArtifact] = []
    if not isinstance(root, Mapping):
        return out
    generated_at = root.get("generated_at")
    for i, row in enumerate(root.get("engines") or []):
        item = _artifact("shadow", f"{locator}#engines[{i}]", row, generated_at)
        if item:
            out.append(item)
    return out


def _collect_brace(root: Any, locator: str) -> list[GovernedArtifact]:
    if not isinstance(root, Mapping):
        return []
    row = _artifact("brace", locator, root, root.get("generated_at"))
    return [row] if row else []


def _collect_belief_l3a(root: Any, locator: str) -> list[GovernedArtifact]:
    out: list[GovernedArtifact] = []
    if not isinstance(root, Mapping):
        return out
    groups = [
        ("evidence", "observed_at"),
        ("beliefs", "last_updated"),
        ("forecasts", "forecast_at"),
        ("verifications", "verified_at"),
        ("records", "attempted_at"),
    ]
    for key, time_key in groups:
        rows = root.get(key)
        if not isinstance(rows, list):
            continue
        for i, row in enumerate(rows):
            item = _artifact("belief_l3a", f"{locator}#{key}[{i}]", row, row.get(time_key) if isinstance(row, Mapping) else None)
            if item:
                out.append(item)
    return out


def _collect_wes_week(root: Any, locator: str) -> list[GovernedArtifact]:
    out: list[GovernedArtifact] = []
    if not isinstance(root, Mapping) or not root.get("week_id"):
        return out

    forecast = root.get("frozen_forecast")
    forecast_seal = root.get("frozen_forecast_seal") if isinstance(root.get("frozen_forecast_seal"), Mapping) else {}
    row = _artifact(
        "wes",
        locator + "#frozen_forecast",
        forecast,
        forecast_seal.get("sealed_at") or root.get("forecast_locked_at") or root.get("forecast_created_at"),
    )
    if row:
        out.append(row)

    for i, instrument in enumerate(root.get("instruments") or []):
        if not isinstance(instrument, Mapping):
            continue
        pending = instrument.get("pending_entry_decision")
        item = _artifact(
            "wes",
            f"{locator}#instruments[{i}].pending_entry_decision",
            pending,
            pending.get("decided_at") if isinstance(pending, Mapping) else None,
        )
        if item:
            out.append(item)

        for j, leg in enumerate(instrument.get("position_legs") or []):
            if not isinstance(leg, Mapping):
                continue
            leg_seal = leg.get("position_leg_seal") if isinstance(leg.get("position_leg_seal"), Mapping) else {}
            leg_at = leg_seal.get("sealed_at") or leg.get("archived_at") or leg.get("exit_captured_at")
            item = _artifact("wes", f"{locator}#instruments[{i}].position_legs[{j}]", leg, leg_at)
            if item:
                out.append(item)
            settlement = leg.get("settlement")
            settlement_seal = leg.get("settlement_seal") if isinstance(leg.get("settlement_seal"), Mapping) else {}
            settlement_at = settlement_seal.get("sealed_at") or leg.get("exit_captured_at")
            item = _artifact(
                "wes",
                f"{locator}#instruments[{i}].position_legs[{j}].settlement",
                settlement,
                settlement_at,
            )
            if item:
                out.append(item)
    return out


def _profile_auto(path: Path, root: Any) -> str:
    name = path.name
    text = str(path).replace("\\", "/")
    if name == "eurusd_daily_spot.json":
        return "daily"
    if name == "stock_trading_v2_production_state.json":
        return "stock"
    if name == "shadow_engines_public.json":
        return "shadow"
    if "weekly/" in text and isinstance(root, Mapping) and root.get("week_id"):
        return "wes"
    if isinstance(root, Mapping):
        if root.get("schema_version") == 2 and any(k in root for k in ("evidence", "beliefs", "forecasts", "verifications")):
            return "belief_l3a"
        if root.get("schema_version") == "briefrooms-l3a-experience-state-v1" or isinstance(root.get("records"), list):
            if "L3A" in name.upper() or "experience" in name.lower():
                return "belief_l3a"
        if root.get("mode") == "research_shadow" and root.get("report_version") is not None:
            return "brace"
    return "unknown"


def collect_file(path: Path, profile: str) -> list[GovernedArtifact]:
    root = _read_json(path)
    selected = _profile_auto(path, root) if profile == "auto" else profile
    locator = str(path)
    if selected == "daily":
        return _collect_daily(root, locator)
    if selected == "stock":
        return _collect_stock(root, locator)
    if selected == "shadow":
        return _collect_shadow(root, locator)
    if selected == "brace":
        return _collect_brace(root, locator)
    if selected == "belief_l3a":
        return _collect_belief_l3a(root, locator)
    if selected == "wes":
        return _collect_wes_week(root, locator)
    return []


def _authority_errors(record: GovernedArtifact, env: Mapping[str, Any]) -> list[str]:
    p = record.payload
    authority = str(env.get("authority") or "")
    artifact_type = str(env.get("artifact_type") or "")
    engine_id = str(env.get("engine_id") or "")
    errors: list[str] = []

    if record.profile == "daily":
        if engine_id != "daily_eurusd" or authority != "decision" or artifact_type != "decision":
            errors.append("daily authority/engine/artifact_type mismatch")
        if not env.get("decision_id"):
            errors.append("daily decision_id missing")
    elif record.profile == "stock":
        if engine_id != "stock_trading_v2":
            errors.append("stock engine_id mismatch")
        expected = "execution" if str(p.get("action") or "") in {"open", "close"} else "decision"
        if authority != expected:
            errors.append(f"stock authority mismatch: expected {expected}")
    elif record.profile == "shadow":
        if not engine_id.startswith("shadow:") or authority != "shadow":
            errors.append("shadow authority/engine mismatch")
        domain = env.get("domain_provenance") or {}
        if domain.get("production_authority") is not False:
            errors.append("shadow artifact must explicitly have production_authority=false")
    elif record.profile == "brace":
        if engine_id != "brace" or authority != "report":
            errors.append("BRACE authority/engine mismatch")
        if p.get("active_decision_influence") is not False:
            errors.append("BRACE framework report must remain zero-influence")
    elif record.profile == "belief_l3a":
        if engine_id not in {"belief_core", "l3a"}:
            errors.append("Belief/L3-A engine_id mismatch")
        expected_by_type = {
            "evidence": "evidence",
            "belief_state": "belief",
            "frozen_forecast": "forecast",
            "verification": "verification",
            "l3a_research_attempt": "research",
            "l3a_experience_settlement": "verification",
        }
        expected = expected_by_type.get(artifact_type)
        if expected and authority != expected:
            errors.append(f"Belief/L3-A authority mismatch: expected {expected}")
    elif record.profile == "wes":
        if engine_id != "wes":
            errors.append("WES engine_id mismatch")
        expected_by_type = {
            "wes_frozen_decision": "decision",
            "frozen_forecast": "forecast",
            "position_leg": "execution",
            "settlement": "settlement",
        }
        expected = expected_by_type.get(artifact_type)
        if expected and authority != expected:
            errors.append(f"WES authority mismatch: expected {expected}")

    return errors


def _temporal_errors(record: GovernedArtifact, env: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    created = _parse_dt(env.get("created_at"))
    if created is None:
        return errors

    effective = _parse_dt(record.effective_at)
    if effective is not None and created > effective + timedelta(seconds=5):
        # Shadow observatory provenance deliberately preserves source run time,
        # so created_at <= snapshot generation is valid; never the reverse.
        errors.append("provenance created_at is after artifact effective_at")

    if record.profile == "daily":
        for row in record.payload.get("used_beliefs") or []:
            if not isinstance(row, Mapping):
                continue
            observed = _parse_dt(row.get("observed_at"))
            if observed is not None and observed > created + timedelta(seconds=1):
                errors.append(f"future belief evidence used by daily decision: {row.get('belief_id')}")
    return errors


def audit_records(records: Sequence[GovernedArtifact], *, activation_at: str = NATIVE_ACTIVATION_AT) -> dict[str, Any]:
    activation = _parse_dt(activation_at)
    if activation is None:
        raise ValueError("activation_at must be timezone-aware ISO-8601")

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    validated = 0
    grandfathered = 0
    id_index: dict[str, tuple[str, str, str]] = {}
    edges: dict[str, set[str]] = {}

    for record in records:
        effective = _parse_dt(record.effective_at)
        required = effective is not None and effective >= activation
        env = record.payload.get("provenance") if isinstance(record.payload, Mapping) else None

        if not isinstance(env, Mapping):
            if required:
                errors.append({
                    "locator": record.locator,
                    "profile": record.profile,
                    "error": "missing_native_provenance",
                    "effective_at": record.effective_at,
                })
            else:
                grandfathered += 1
            continue

        try:
            pc.validate_envelope(env, source_payload=record.payload)
        except Exception as exc:
            errors.append({
                "locator": record.locator,
                "profile": record.profile,
                "error": "invalid_provenance_envelope",
                "detail": str(exc),
            })
            continue

        validated += 1
        domain = env.get("domain_provenance") if isinstance(env.get("domain_provenance"), Mapping) else {}
        if required and domain.get("native_write_time") is not True:
            errors.append({
                "locator": record.locator,
                "profile": record.profile,
                "error": "native_write_time_not_asserted",
            })
        if required and env.get("prospective") is not True:
            errors.append({
                "locator": record.locator,
                "profile": record.profile,
                "error": "post_activation_artifact_not_prospective",
            })

        for detail in _authority_errors(record, env):
            errors.append({
                "locator": record.locator,
                "profile": record.profile,
                "error": "authority_boundary_violation",
                "detail": detail,
            })
        for detail in _temporal_errors(record, env):
            errors.append({
                "locator": record.locator,
                "profile": record.profile,
                "error": "temporal_lineage_violation",
                "detail": detail,
            })

        artifact_id = str(env.get("artifact_id") or "")
        digest = str(env.get("payload_hash") or "")
        if artifact_id:
            prior = id_index.get(artifact_id)
            current = (digest, record.profile, record.locator)
            if prior and prior[0] != digest:
                errors.append({
                    "locator": record.locator,
                    "profile": record.profile,
                    "error": "artifact_id_payload_hash_collision",
                    "artifact_id": artifact_id,
                    "other_locator": prior[2],
                })
            else:
                id_index.setdefault(artifact_id, current)
            edges.setdefault(artifact_id, set()).update(str(x) for x in (env.get("parent_artifact_ids") or []) if x)

    # Local graph integrity: parents outside the audited scope are allowed because
    # evidence/private-state lineage may live in a separate protected artifact.
    local_ids = set(id_index)
    local_edges = {node: {p for p in parents if p in local_ids} for node, parents in edges.items()}

    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(node: str, trail: list[str]) -> None:
        if node in visited:
            return
        if node in visiting:
            cycle = trail[trail.index(node):] + [node] if node in trail else trail + [node]
            errors.append({
                "locator": id_index.get(node, ("", "", node))[2],
                "profile": id_index.get(node, ("", "unknown", ""))[1],
                "error": "provenance_parent_cycle",
                "cycle": cycle,
            })
            return
        visiting.add(node)
        trail.append(node)
        for parent in sorted(local_edges.get(node, ())):
            walk(parent, trail)
        trail.pop()
        visiting.remove(node)
        visited.add(node)

    for artifact_id in sorted(local_ids):
        walk(artifact_id, [])

    unresolved = sum(
        1
        for parents in edges.values()
        for parent in parents
        if parent not in local_ids
    )
    if unresolved:
        warnings.append({
            "warning": "external_parent_refs_not_resolved_in_this_audit_scope",
            "count": unresolved,
        })

    return {
        "schema_version": "briefrooms-provenance-global-audit-v1",
        "activation_at": activation_at,
        "status": "failed" if errors else "passed",
        "records_checked": len(records),
        "validated_provenance": validated,
        "grandfathered_legacy": grandfathered,
        "local_artifact_ids": len(local_ids),
        "errors": errors,
        "warnings": warnings,
    }


def audit_paths(paths: Sequence[Path], profile: str, activation_at: str) -> dict[str, Any]:
    records: list[GovernedArtifact] = []
    files: list[str] = []
    for raw in paths:
        for path in _iter_json_files(raw):
            selected = profile
            if profile == "repo":
                selected = "auto"
            rows = collect_file(path, selected)
            if rows:
                files.append(str(path))
                records.extend(rows)
    result = audit_records(records, activation_at=activation_at)
    result["files"] = sorted(set(files))
    return result


def default_repo_paths(root: Path) -> list[Path]:
    paths = [
        root / "data/investments/eurusd_daily_spot.json",
        root / "data/investments/stock_trading_v2_production_state.json",
        root / "data/investments/shadow_engines_public.json",
        root / "data/investments/weekly",
    ]
    return [p for p in paths if p.exists()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed BriefRooms Provenance Contract v1 auditor")
    parser.add_argument(
        "--profile",
        choices=["repo", "auto", "daily", "stock", "shadow", "brace", "belief_l3a", "wes"],
        default="repo",
    )
    parser.add_argument("--file", action="append", dest="files", default=[])
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--activation-at", default=NATIVE_ACTIVATION_AT)
    parser.add_argument("--report")
    args = parser.parse_args(argv)

    root = Path(args.repo_root)
    paths = [Path(x) for x in args.files] if args.files else default_repo_paths(root)
    result = audit_paths(paths, args.profile, args.activation_at)

    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

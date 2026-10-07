#!/usr/bin/env python3
"""BriefRooms Hypothesis Shadow Engine 2.0.

Seven research producers -> immutable hypothesis -> frozen prospective boundary ->
forward evidence -> single fixed-N verdict -> derived LESSON.

Producers:
- EURUSD X
- Stock Trading v2
- BRACE-SPX
- WES
- GSE
- Strategy Research
- FSE — Fractal Structure Engine

This engine is intentionally authority-free. It cannot execute trades, mutate
source models, promote candidates or write production policy/configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA = "briefrooms-hypothesis-shadow-engine-v2"
REGISTRY_FILE = "hse2_experiments.json"
EPOCHS_FILE = "hse2_validation_epochs.jsonl"
EVIDENCE_FILE = "hse2_evidence.jsonl"
RESULTS_FILE = "hse2_results.jsonl"
LESSONS_FILE = "hse2_lessons.jsonl"
PUBLIC_SCHEMA = "briefrooms-hse2-public-v1"

ZERO_AUTHORITY = {
    "production_policy_writeback": False,
    "production_ranking_writeback": False,
    "production_sizing_writeback": False,
    "source_model_writeback": False,
    "trade_execution": False,
    "automatic_promotion": False,
    "arbitrary_code_execution": False,
}

SOURCE_PATHS = {
    "EURUSD_X": "data/investments/eurusd_x_public_pl.json",
    "STOCK_TRADING_V2": "data/investments/daily_stock_challenger_intents.json",
    "BRACE_SPX": "data/public/brace_spx_platform_public.json",
    "WES": "data/investments/wes_incremental_alpha_report.json",
    "GSE": "data/gse/gse_v2_learning_review_status.json",
    "STRATEGY_RESEARCH": "data/investments/research_lab_report.json",
    "FSE": "data/investments/fse_public.json",
}
FSE_SUPPLEMENTAL_PATH = "data/investments/fse_v2_public.json"
FSE_PHASE_CONTRACT_PATH = SOURCE_PATHS["FSE"] + " + " + FSE_SUPPLEMENTAL_PATH


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_sha(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{sha(value)[:24]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        row = json.loads(raw)
        if not isinstance(row, dict):
            raise ValueError(f"non-object JSONL row {line_no}: {path}")
        rows.append(row)
    return rows


def append_chain(path: Path, schema_version: str, payload: Mapping[str, Any], id_key: str) -> dict[str, Any]:
    rows = read_jsonl(path)
    previous = rows[-1]["event_hash"] if rows else "GENESIS"
    body = dict(payload)
    body["schema_version"] = schema_version
    body["previous_hash"] = previous
    if not body.get(id_key):
        body[id_key] = stable_id(id_key.replace("_id", ""), body)
    body["event_hash"] = sha(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical(body) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    verify_chain(path, schema_version, id_key)
    return body


def verify_chain(path: Path, schema_version: str, id_key: str) -> dict[str, Any]:
    rows = read_jsonl(path)
    previous = "GENESIS"
    ids: set[str] = set()
    for index, row in enumerate(rows):
        if row.get("schema_version") != schema_version:
            raise ValueError(f"schema mismatch at {path}:{index}")
        event_id = str(row.get(id_key) or "")
        if not event_id or event_id in ids:
            raise ValueError(f"duplicate/empty {id_key} at {path}:{index}")
        if row.get("previous_hash") != previous:
            raise ValueError(f"hash-chain break at {path}:{index}")
        body = dict(row)
        stored = body.pop("event_hash", None)
        if stored != sha(body):
            raise ValueError(f"event hash mismatch at {path}:{index}")
        ids.add(event_id)
        previous = str(stored)
    return {"ok": True, "events": len(rows), "head_hash": previous}


def proposal(
    *,
    source_engine: str,
    proposal_key: str,
    claim: str,
    champion: str,
    challenger: str,
    metric_name: str,
    target_n: int,
    success_mean_edge: float,
    reject_mean_edge: float,
    counter: Any,
    total: Any,
    source_at: Any,
    source_path: str,
    source_sha256: str | None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    n = int(counter or 0)
    total_value = finite(total)
    detail_map = dict(details or {})
    methodology_version = str(detail_map.get("methodology_version") or "").strip() or None
    return {
        "source_engine": source_engine,
        "proposal_key": proposal_key,
        "claim": claim,
        "champion": champion,
        "challenger": challenger,
        "metric_name": metric_name,
        "metric_direction": "HIGHER_IS_BETTER",
        "target_n": int(target_n),
        "success_mean_edge": float(success_mean_edge),
        "reject_mean_edge": float(reject_mean_edge),
        "measurement": {"counter": n, "total": total_value},
        "source_at": source_at,
        "source_path": source_path,
        "source_sha256": source_sha256,
        "methodology_version": methodology_version,
        "details": detail_map,
    }


def proposals_eurusd_x(data: Mapping[str, Any], source_path: str, source_sha256: str | None) -> list[dict[str, Any]]:
    champion = str(data.get("champion_setup_id") or "")
    challenger = str(data.get("active_challenger_id") or "")
    if not champion or not challenger or champion == challenger:
        return []
    setups = {str(x.get("setup_id")): x for x in data.get("setups", []) if isinstance(x, Mapping)}
    c0, c1 = setups.get(champion, {}), setups.get(challenger, {})
    n0, n1 = int(c0.get("n") or 0), int(c1.get("n") or 0)
    m0, m1 = finite(c0.get("mean_signed_return_bps")), finite(c1.get("mean_signed_return_bps"))
    common_n = min(n0, n1, int(data.get("resolved_4h") or max(n0, n1)))
    total = None if m0 is None or m1 is None else common_n * (m1 - m0)
    return [proposal(
        source_engine="EURUSD_X",
        proposal_key=f"{champion}__vs__{challenger}",
        claim=f"EURUSD X challenger {challenger} has higher prospective signed return than champion {champion}.",
        champion=champion,
        challenger=challenger,
        metric_name="challenger_minus_champion_signed_return_bps",
        target_n=20,
        success_mean_edge=0.25,
        reject_mean_edge=-0.25,
        counter=common_n,
        total=total,
        source_at=data.get("generated_at"),
        source_path=source_path,
        source_sha256=source_sha256,
        details={"horizon_hours": (data.get("calibration_policy") or {}).get("primary_horizon_hours")},
    )]


def proposals_stock_v2(data: Mapping[str, Any], source_path: str, source_sha256: str | None) -> list[dict[str, Any]]:
    out = []
    rows = data.get("hypothesis_only") if isinstance(data.get("hypothesis_only"), list) else []
    for row in rows[:6]:
        if not isinstance(row, Mapping):
            continue
        evidence = row.get("evidence") if isinstance(row.get("evidence"), Mapping) else {}
        n = int(evidence.get("observations") or 0)
        mean = finite(evidence.get("mean_candidate_minus_selected_percent"))
        hid = str(row.get("hypothesis_id") or "")
        if not hid:
            continue
        out.append(proposal(
            source_engine="STOCK_TRADING_V2",
            proposal_key=hid,
            claim=f"Stock Trading v2 hypothesis {hid} continues to add positive candidate-minus-selected return prospectively.",
            champion="Stock Trading v2 selected policy",
            challenger=f"{row.get('market','?')}:{row.get('gate','hypothesis')}",
            metric_name="candidate_minus_selected_percent",
            target_n=30,
            success_mean_edge=0.10,
            reject_mean_edge=-0.05,
            counter=n,
            total=None if mean is None else n * mean,
            source_at=data.get("generated_at"),
            source_path=source_path,
            source_sha256=source_sha256,
            details={"market": row.get("market"), "gate": row.get("gate"), "pattern_id": evidence.get("pattern_id")},
        ))
    return out


def proposals_brace(data: Mapping[str, Any], source_path: str, source_sha256: str | None) -> list[dict[str, Any]]:
    adaptive = data.get("adaptive_research") if isinstance(data.get("adaptive_research"), Mapping) else {}
    out = []
    for row in adaptive.get("challengers", []) if isinstance(adaptive.get("challengers"), list) else []:
        if not isinstance(row, Mapping) or str(row.get("status") or "").upper() not in {"ACTIVE_RESEARCH", "VALIDATING"}:
            continue
        cid = str(row.get("candidate_id") or "")
        if not cid:
            continue
        n = int(row.get("prospective_n") or 0)
        cumulative = finite(row.get("cumulative_return"))
        out.append(proposal(
            source_engine="BRACE_SPX",
            proposal_key=cid,
            claim=f"BRACE-SPX challenger {cid} has positive forward shadow return after HSE2 freeze.",
            champion=str((data.get("frozen_track") or {}).get("generation_id") or "BRACE Frozen"),
            challenger=cid,
            metric_name="prospective_return_percent",
            target_n=20,
            success_mean_edge=0.01,
            reject_mean_edge=-0.01,
            counter=n,
            total=None if cumulative is None else cumulative * 100.0,
            source_at=data.get("generated_at"),
            source_path=source_path,
            source_sha256=source_sha256,
            details={"rule": row.get("rule"), "origin": row.get("origin")},
        ))
    return out[:6]


def proposals_wes(data: Mapping[str, Any], source_path: str, source_sha256: str | None) -> list[dict[str, Any]]:
    overall = data.get("overall") if isinstance(data.get("overall"), Mapping) else {}
    n = int(overall.get("resolved_pairs") or 0)
    mean = finite(overall.get("mean_incremental_alpha_percent"))
    return [proposal(
        source_engine="WES",
        proposal_key="wes-vs-frozen-v5-incremental-alpha",
        claim="WES produces positive prospective incremental alpha versus the prospectively frozen pre-WES V5 risk plan.",
        champion="Frozen V5 baseline",
        challenger="WES current",
        metric_name="incremental_alpha_percent",
        target_n=12,
        success_mean_edge=0.05,
        reject_mean_edge=-0.05,
        counter=n,
        total=None if mean is None else n * mean,
        source_at=data.get("generated_at") or data.get("updated_at"),
        source_path=source_path,
        source_sha256=source_sha256,
        details={"historical_backfill_allowed": data.get("historical_backfill_allowed")},
    )]


def proposals_gse(data: Mapping[str, Any], source_path: str, source_sha256: str | None) -> list[dict[str, Any]]:
    prospective = data.get("prospective") if isinstance(data.get("prospective"), Mapping) else {}
    n = int(prospective.get("paired_n") or 0)
    delta = finite(prospective.get("delta_brier_v2_minus_v1"))
    return [proposal(
        source_engine="GSE",
        proposal_key="gse-v2-regime-vs-v1-brier",
        claim="GSE v2 regime-aware forecasts retain lower prospective Brier score than GSE v1 after HSE2 freeze.",
        champion="GSE v1",
        challenger="GSE v2 regime-aware",
        metric_name="brier_improvement_v1_minus_v2",
        target_n=30,
        success_mean_edge=0.005,
        reject_mean_edge=-0.005,
        counter=n,
        total=None if delta is None else n * (-delta),
        source_at=data.get("published_at"),
        source_path=source_path,
        source_sha256=source_sha256,
        details={"review_status": data.get("status")},
    )]


def proposals_strategy(data: Mapping[str, Any], source_path: str, source_sha256: str | None) -> list[dict[str, Any]]:
    out = []
    for row in data.get("top_candidates", []) if isinstance(data.get("top_candidates"), list) else []:
        if not isinstance(row, Mapping):
            continue
        status = str(row.get("status") or "").lower()
        shadow = row.get("shadow_metrics") if isinstance(row.get("shadow_metrics"), Mapping) else {}
        n = int(shadow.get("count") or 0)
        if "prospective" not in status and "promotion" not in status and n <= 0:
            continue
        cid = str(row.get("candidate_id") or "")
        if not cid:
            continue
        mean = finite(shadow.get("mean_net_percent"))
        spec = row.get("spec") if isinstance(row.get("spec"), Mapping) else {}
        label = " · ".join(str(spec.get(k) or "") for k in ("instrument_id", "side", "timeframe", "rule") if spec.get(k))
        out.append(proposal(
            source_engine="STRATEGY_RESEARCH",
            proposal_key=cid,
            claim=f"Strategy Research candidate {cid} ({label}) has positive prospective shadow net return after HSE2 freeze.",
            champion="no-strategy / current research baseline",
            challenger=cid,
            metric_name="prospective_shadow_mean_net_percent",
            target_n=20,
            success_mean_edge=0.05,
            reject_mean_edge=-0.05,
            counter=n,
            total=None if mean is None else n * mean,
            source_at=data.get("generated_at"),
            source_path=source_path,
            source_sha256=source_sha256,
            details={"spec": dict(spec), "source_status": row.get("status")},
        ))
    return out[:8]


def proposals_fse(data: Mapping[str, Any], source_path: str, source_sha256: str | None) -> list[dict[str, Any]]:
    """Translate FSE's frozen prospective measurements into HSE2 hypotheses.

    FSE owns neither the verdict nor promotion. HSE2 freezes the current
    counters/totals and only credits evidence that arrives after that boundary.
    """
    out = []
    rows = data.get("hse_measurements") if isinstance(data.get("hse_measurements"), list) else []
    for row in rows[:12]:
        if not isinstance(row, Mapping):
            continue
        key = str(row.get("proposal_key") or "")
        if not key:
            continue
        details = row.get("details") if isinstance(row.get("details"), Mapping) else {}
        methodology_version = str(
            details.get("methodology_version")
            or data.get("methodology_version")
            or data.get("schema_version")
            or ""
        ).strip() or None
        out.append(proposal(
            source_engine="FSE",
            proposal_key=key,
            claim=str(row.get("claim") or f"FSE hypothesis {key} retains positive prospective edge."),
            champion=str(row.get("champion") or "50/50 baseline"),
            challenger=str(row.get("challenger") or "FSE"),
            metric_name=str(row.get("metric_name") or "prospective_edge"),
            target_n=int(row.get("target_n") or 40),
            success_mean_edge=float(row.get("success_mean_edge") or 0.0025),
            reject_mean_edge=float(row.get("reject_mean_edge") or -0.0025),
            counter=int(row.get("counter") or 0),
            total=finite(row.get("total")),
            source_at=data.get("generated_at"),
            source_path=source_path,
            source_sha256=source_sha256,
            details={
                **dict(details),
                "methodology_version": methodology_version,
                "fse_module_id": data.get("module_id"),
                "fse_mode": data.get("mode"),
                "fse_components": data.get("components"),
                "production_impact": data.get("production_impact"),
            },
        ))
    return out


ADAPTERS = {
    "EURUSD_X": proposals_eurusd_x,
    "STOCK_TRADING_V2": proposals_stock_v2,
    "BRACE_SPX": proposals_brace,
    "WES": proposals_wes,
    "GSE": proposals_gse,
    "STRATEGY_RESEARCH": proposals_strategy,
    "FSE": proposals_fse,
}


def collect_proposals(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    proposals = []
    source_status: dict[str, Any] = {}
    for source_engine, rel in SOURCE_PATHS.items():
        path = root / rel
        data = load_json(path)
        digest = file_sha(path)
        adapter = ADAPTERS[source_engine]

        if source_engine == "FSE":
            supplemental_path = root / FSE_SUPPLEMENTAL_PATH
            supplemental = load_json(supplemental_path)
            supplemental_digest = file_sha(supplemental_path)

            # Preserve the original FSE core contract path so existing HSE2
            # experiment IDs, freeze boundaries and prospective N remain stable.
            core_rows = adapter(data, rel, digest) if data else []

            # FSE-PHASE was first frozen with the combined dependency path.
            # Keep that contract path stable as well: the phase challenger
            # depends on its own state plus the frozen FSE v2 base probability.
            phase_rows = adapter(
                supplemental,
                FSE_PHASE_CONTRACT_PATH,
                supplemental_digest,
            ) if supplemental else []

            rows = [*core_rows, *phase_rows]
            combined_digest = sha({"core": digest, "phase": supplemental_digest})
            proposals.extend(rows)
            source_status[source_engine] = {
                "path": rel,
                "supplemental_path": FSE_SUPPLEMENTAL_PATH,
                "available": bool(data or supplemental),
                "proposal_count": len(rows),
                "source_sha256": combined_digest,
                "core_sha256": digest,
                "phase_sha256": supplemental_digest,
            }
            continue

        rows = adapter(data, rel, digest) if data else []
        proposals.extend(rows)
        source_status[source_engine] = {
            "path": rel,
            "available": bool(data),
            "proposal_count": len(rows),
            "source_sha256": digest,
        }
    return proposals, source_status


def methodology_version_for(p: Mapping[str, Any]) -> str | None:
    value = p.get("methodology_version")
    details = p.get("details")
    if not value and isinstance(details, Mapping):
        value = details.get("methodology_version")
    text = str(value or "").strip()
    return text or None


def legacy_contract_for(p: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_engine": p["source_engine"],
        "proposal_key": p["proposal_key"],
        "claim": p["claim"],
        "champion": p["champion"],
        "challenger": p["challenger"],
        "metric_name": p["metric_name"],
        "metric_direction": p["metric_direction"],
        "target_n": p["target_n"],
        "success_mean_edge": p["success_mean_edge"],
        "reject_mean_edge": p["reject_mean_edge"],
        "source_path": p["source_path"],
    }


def contract_for(p: Mapping[str, Any]) -> dict[str, Any]:
    contract = legacy_contract_for(p)
    methodology_version = methodology_version_for(p)
    if methodology_version:
        contract["methodology_version"] = methodology_version
    return contract


def load_registry(state_dir: Path) -> dict[str, Any]:
    path = state_dir / REGISTRY_FILE
    if not path.exists():
        return {"schema_version": SCHEMA, "experiments": [], "authority": dict(ZERO_AUTHORITY)}
    payload = load_json(path)
    if payload.get("schema_version") != SCHEMA:
        raise ValueError("HSE2 registry schema mismatch")
    if payload.get("authority") != ZERO_AUTHORITY:
        raise ValueError("HSE2 authority mismatch")
    return payload


def save_registry(state_dir: Path, registry: Mapping[str, Any]) -> None:
    path = state_dir / REGISTRY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(registry), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def experiment_id(p: Mapping[str, Any]) -> str:
    return stable_id("hse2exp", contract_for(p))


def freeze_new_experiment(state_dir: Path, p: Mapping[str, Any], at: str) -> dict[str, Any]:
    contract = contract_for(p)
    eid = experiment_id(p)
    epoch = append_chain(
        state_dir / EPOCHS_FILE,
        "briefrooms-hse2-validation-epoch-v1",
        {
            "epoch_id": stable_id("hse2epoch", {"experiment_id": eid, "frozen_at": at, "contract_sha256": sha(contract)}),
            "experiment_id": eid,
            "source_engine": p["source_engine"],
            "frozen_at": at,
            "contract_sha256": sha(contract),
            "source_snapshot_sha256": p.get("source_sha256"),
            "methodology_version": methodology_version_for(p),
            "identity_schema": "methodology-bound-v2",
            "prospective_only": True,
            "historical_backfill": False,
            "eligible_strictly_after_freeze": True,
            "baseline_counter": int((p.get("measurement") or {}).get("counter") or 0),
            "baseline_total": finite((p.get("measurement") or {}).get("total")),
            "authority": dict(ZERO_AUTHORITY),
        },
        "epoch_id",
    )
    measurement = p.get("measurement") or {}
    return {
        "experiment_id": eid,
        "epoch_id": epoch["epoch_id"],
        "status": "RUNNING_SHADOW",
        "source_active": True,
        "source_engine": p["source_engine"],
        "proposal_key": p["proposal_key"],
        "claim": p["claim"],
        "champion": p["champion"],
        "challenger": p["challenger"],
        "metric_name": p["metric_name"],
        "target_n": int(p["target_n"]),
        "success_mean_edge": float(p["success_mean_edge"]),
        "reject_mean_edge": float(p["reject_mean_edge"]),
        "contract_sha256": sha(contract),
        "frozen_at": at,
        "source_path": p["source_path"],
        "source_snapshot_sha256": p.get("source_sha256"),
        "methodology_version": methodology_version_for(p),
        "methodology_contract_sha256": sha(contract),
        "identity_schema": "methodology-bound-v2",
        "last_counter": int(measurement.get("counter") or 0),
        "last_total": finite(measurement.get("total")),
        "prospective_n": 0,
        "last_evidence_at": None,
        "result_id": None,
        "details": dict(p.get("details") or {}),
        "authority": dict(ZERO_AUTHORITY),
    }


def supersede_fse_routing_duplicates(registry: dict[str, Any]) -> int:
    """Keep the audit trail while disabling the one-cycle FSE routing duplicates.

    The first FSE-PHASE integration temporarily used the combined core+phase
    source_path for all FSE proposals. Because source_path is part of the
    immutable experiment contract, that created duplicate core experiments.
    We never delete epochs/evidence. Instead, if a canonical core experiment
    with the same proposal_key exists, the combined-path duplicate is marked
    administrative_superseded and excluded from formal evaluation/public views.
    """
    experiments = [x for x in registry.get("experiments", []) if isinstance(x, dict)]
    canonical_core = {
        str(x.get("proposal_key")): x
        for x in experiments
        if x.get("source_engine") == "FSE"
        and x.get("source_path") == SOURCE_PATHS["FSE"]
        and str(x.get("proposal_key") or "").startswith(("fse-fractal-memory-", "fse-structural-risk-"))
    }
    changed = 0
    for exp in experiments:
        key = str(exp.get("proposal_key") or "")
        if (
            exp.get("source_engine") == "FSE"
            and exp.get("source_path") == FSE_PHASE_CONTRACT_PATH
            and key in canonical_core
            and exp.get("experiment_id") != canonical_core[key].get("experiment_id")
        ):
            if not exp.get("administrative_superseded"):
                changed += 1
            exp["administrative_superseded"] = True
            exp["source_active"] = False
            exp["superseded_by_experiment_id"] = canonical_core[key].get("experiment_id")
            exp["superseded_reason"] = "fse_phase_routing_path_correction_2026_10_06"
    return changed


def register_and_collect(state_dir: Path, proposals: list[dict[str, Any]], at: str) -> dict[str, Any]:
    registry = load_registry(state_dir)
    supersede_fse_routing_duplicates(registry)
    by_id = {str(x.get("experiment_id")): x for x in registry.get("experiments", []) if isinstance(x, dict)}
    seen = set()
    new_experiments = 0
    evidence_added_n = 0

    for p in proposals:
        requested_eid = experiment_id(p)
        legacy_eid = stable_id("hse2exp", legacy_contract_for(p))
        methodology_version = methodology_version_for(p)
        exp = by_id.get(requested_eid)

        # Migration bridge: preserve already-frozen legacy experiment IDs and
        # their accumulated prospective N, but seal them to the methodology
        # observed at migration. A later methodology version must create a
        # fresh methodology-bound experiment and T0 boundary.
        if exp is None and methodology_version and legacy_eid != requested_eid:
            legacy_exp = by_id.get(legacy_eid)
            if legacy_exp is not None:
                legacy_details = legacy_exp.get("details")
                frozen_methodology = str(
                    legacy_exp.get("methodology_version")
                    or (legacy_details.get("methodology_version") if isinstance(legacy_details, Mapping) else None)
                    or ""
                ).strip() or None
                if frozen_methodology in (None, methodology_version):
                    exp = legacy_exp
                    if frozen_methodology is None:
                        exp["methodology_version"] = methodology_version
                        exp["methodology_contract_sha256"] = sha(contract_for(p))
                        exp["identity_schema"] = "legacy-id-methodology-sealed-v2"
                        exp["methodology_identity_migrated_at"] = at

        if exp is None:
            exp = freeze_new_experiment(state_dir, p, at)
            registry.setdefault("experiments", []).append(exp)
            by_id[str(exp["experiment_id"])] = exp
            new_experiments += 1
            seen.add(str(exp["experiment_id"]))
            continue

        eid = str(exp["experiment_id"])
        seen.add(eid)
        current_contract_sha = sha(contract_for(p))
        legacy_contract_sha = sha(legacy_contract_for(p))
        if exp.get("contract_sha256") == current_contract_sha:
            pass
        elif exp.get("contract_sha256") == legacy_contract_sha:
            frozen_methodology = str(exp.get("methodology_version") or "").strip() or None
            if methodology_version and frozen_methodology != methodology_version:
                raise RuntimeError(f"immutable HSE2 methodology changed inside legacy epoch: {eid}")
            if methodology_version:
                exp["methodology_contract_sha256"] = current_contract_sha
                exp.setdefault("identity_schema", "legacy-id-methodology-sealed-v2")
        else:
            raise RuntimeError(f"immutable HSE2 contract changed: {eid}")
        exp["source_active"] = True
        if exp.get("status") != "RUNNING_SHADOW":
            continue
        measurement = p.get("measurement") or {}
        current_n = int(measurement.get("counter") or 0)
        current_total = finite(measurement.get("total"))
        last_n = int(exp.get("last_counter") or 0)
        last_total = finite(exp.get("last_total"))
        if current_n < last_n:
            # Source reset/revision: never backfill or reinterpret an existing epoch.
            exp["source_reset_detected"] = True
            continue
        if current_n == last_n:
            continue
        if current_total is None or last_total is None:
            # We know samples advanced but cannot reconstruct their prospective edge safely.
            exp["unmeasurable_advance_n"] = int(exp.get("unmeasurable_advance_n") or 0) + (current_n - last_n)
            exp["last_counter"] = current_n
            exp["last_total"] = current_total
            continue
        delta_n = current_n - last_n
        delta_total = current_total - last_total
        mean_edge = delta_total / delta_n
        evidence = append_chain(
            state_dir / EVIDENCE_FILE,
            "briefrooms-hse2-evidence-v1",
            {
                "evidence_id": stable_id("hse2ev", {"experiment_id": eid, "from_n": last_n, "to_n": current_n, "source_sha": p.get("source_sha256")}),
                "experiment_id": eid,
                "epoch_id": exp["epoch_id"],
                "source_engine": exp["source_engine"],
                "observed_at": at,
                "source_at": p.get("source_at"),
                "source_snapshot_sha256": p.get("source_sha256"),
                "methodology_version": methodology_version,
                "from_counter": last_n,
                "to_counter": current_n,
                "n": delta_n,
                "mean_edge": mean_edge,
                "metric_name": exp["metric_name"],
                "granularity": "source_native_block",
                "prospective": True,
                "authority": dict(ZERO_AUTHORITY),
            },
            "evidence_id",
        )
        exp["last_counter"] = current_n
        exp["last_total"] = current_total
        exp["prospective_n"] = int(exp.get("prospective_n") or 0) + int(evidence["n"])
        exp["last_evidence_at"] = at
        evidence_added_n += delta_n

    for exp in registry.get("experiments", []):
        if isinstance(exp, dict) and exp.get("experiment_id") not in seen:
            exp["source_active"] = False

    registry["updated_at"] = at
    registry["summary"] = {
        "total": len(registry.get("experiments", [])),
        "running_shadow": sum(x.get("status") == "RUNNING_SHADOW" for x in registry.get("experiments", []) if isinstance(x, Mapping)),
        "new_experiments": new_experiments,
        "evidence_added_n": evidence_added_n,
    }
    save_registry(state_dir, registry)
    return registry


def weighted_sample(events: Iterable[Mapping[str, Any]], target_n: int) -> tuple[float | None, int, int]:
    remaining = target_n
    total = 0.0
    used = 0
    blocks = 0
    for row in events:
        n = int(row.get("n") or 0)
        value = finite(row.get("mean_edge"))
        if n <= 0 or value is None:
            continue
        take = min(remaining, n)
        total += take * value
        used += take
        remaining -= take
        blocks += 1
        if remaining <= 0:
            break
    return (None if used == 0 else total / used, used, blocks)


def evaluate(state_dir: Path, registry: dict[str, Any], at: str) -> dict[str, Any]:
    evidence = read_jsonl(state_dir / EVIDENCE_FILE)
    results = read_jsonl(state_dir / RESULTS_FILE)
    result_ids_by_experiment = {str(x.get("experiment_id")): x for x in results}
    created = 0
    lessons_created = 0

    for exp in registry.get("experiments", []):
        if (
            not isinstance(exp, dict)
            or exp.get("status") != "RUNNING_SHADOW"
            or exp.get("administrative_superseded") is True
        ):
            continue
        target_n = int(exp.get("target_n") or 0)
        rows = [x for x in evidence if x.get("experiment_id") == exp.get("experiment_id")]
        rows.sort(key=lambda x: (str(x.get("observed_at") or ""), str(x.get("evidence_id") or "")))
        sample_mean, sample_n, blocks = weighted_sample(rows, target_n)
        exp["prospective_n"] = sum(int(x.get("n") or 0) for x in rows)
        if sample_n < target_n or sample_mean is None:
            continue
        if exp["experiment_id"] in result_ids_by_experiment:
            raise RuntimeError("RUNNING experiment already has formal result")
        if sample_mean >= float(exp["success_mean_edge"]):
            verdict = "SUPPORTED"
            reason = "preregistered_mean_edge_success_threshold_passed"
        elif sample_mean <= float(exp["reject_mean_edge"]):
            verdict = "REJECTED"
            reason = "preregistered_mean_edge_falsification_threshold_passed"
        else:
            verdict = "INCONCLUSIVE"
            reason = "fixed_n_completed_inside_inconclusive_band"
        result = append_chain(
            state_dir / RESULTS_FILE,
            "briefrooms-hse2-result-v1",
            {
                "result_id": stable_id("hse2res", {"experiment_id": exp["experiment_id"], "epoch_id": exp["epoch_id"], "target_n": target_n}),
                "experiment_id": exp["experiment_id"],
                "epoch_id": exp["epoch_id"],
                "source_engine": exp["source_engine"],
                "claim": exp["claim"],
                "champion": exp["champion"],
                "challenger": exp["challenger"],
                "metric_name": exp["metric_name"],
                "sample_n": target_n,
                "evidence_blocks": blocks,
                "mean_edge": sample_mean,
                "success_mean_edge": exp["success_mean_edge"],
                "reject_mean_edge": exp["reject_mean_edge"],
                "verdict": verdict,
                "reason": reason,
                "completed_at": at,
                "formal_evaluation_number": 1,
                "authority": dict(ZERO_AUTHORITY),
            },
            "result_id",
        )
        lesson = append_chain(
            state_dir / LESSONS_FILE,
            "briefrooms-hse2-lesson-v1",
            {
                "lesson_id": stable_id("hse2lesson", {"result_id": result["result_id"], "verdict": verdict}),
                "source_result_id": result["result_id"],
                "source_experiment_id": exp["experiment_id"],
                "source_engine": exp["source_engine"],
                "derived_at": at,
                "verdict": verdict,
                "statement": f"{exp['source_engine']} hypothesis finished {verdict} at fixed N={target_n}; mean forward edge={sample_mean:.8f} ({exp['metric_name']}).",
                "hypothesis_input_ready": True,
                "production_authority": False,
                "authority": dict(ZERO_AUTHORITY),
            },
            "lesson_id",
        )
        exp["status"] = verdict
        exp["completed_at"] = at
        exp["result_id"] = result["result_id"]
        exp["lesson_id"] = lesson["lesson_id"]
        created += 1
        lessons_created += 1

    registry["updated_at"] = at
    registry["summary"] = {
        "total": len(registry.get("experiments", [])),
        "running_shadow": sum(x.get("status") == "RUNNING_SHADOW" for x in registry.get("experiments", []) if isinstance(x, Mapping)),
        "supported": sum(x.get("status") == "SUPPORTED" for x in registry.get("experiments", []) if isinstance(x, Mapping)),
        "rejected": sum(x.get("status") == "REJECTED" for x in registry.get("experiments", []) if isinstance(x, Mapping)),
        "inconclusive": sum(x.get("status") == "INCONCLUSIVE" for x in registry.get("experiments", []) if isinstance(x, Mapping)),
        "results_created_this_cycle": created,
        "lessons_created_this_cycle": lessons_created,
    }
    save_registry(state_dir, registry)
    return registry


def verify(state_dir: Path) -> dict[str, Any]:
    registry = load_registry(state_dir)
    if registry.get("authority") != ZERO_AUTHORITY:
        raise ValueError("registry authority violated")
    seen = set()
    for exp in registry.get("experiments", []):
        eid = str(exp.get("experiment_id") or "")
        if not eid or eid in seen:
            raise ValueError("duplicate/empty experiment id")
        seen.add(eid)
        if exp.get("authority") != ZERO_AUTHORITY:
            raise ValueError(f"experiment authority violated: {eid}")
        if exp.get("status") not in {"RUNNING_SHADOW", "SUPPORTED", "REJECTED", "INCONCLUSIVE"}:
            raise ValueError(f"invalid experiment status: {eid}")
    return {
        "ok": True,
        "experiments": len(seen),
        "epochs": verify_chain(state_dir / EPOCHS_FILE, "briefrooms-hse2-validation-epoch-v1", "epoch_id"),
        "evidence": verify_chain(state_dir / EVIDENCE_FILE, "briefrooms-hse2-evidence-v1", "evidence_id"),
        "results": verify_chain(state_dir / RESULTS_FILE, "briefrooms-hse2-result-v1", "result_id"),
        "lessons": verify_chain(state_dir / LESSONS_FILE, "briefrooms-hse2-lesson-v1", "lesson_id"),
        "zero_authority": True,
    }


def public_projection(registry: Mapping[str, Any], source_status: Mapping[str, Any], state_dir: Path, at: str) -> dict[str, Any]:
    results = read_jsonl(state_dir / RESULTS_FILE)
    lessons = read_jsonl(state_dir / LESSONS_FILE)
    experiments = []
    superseded = [
        exp for exp in registry.get("experiments", [])
        if isinstance(exp, Mapping) and exp.get("administrative_superseded") is True
    ]
    for exp in registry.get("experiments", []):
        if not isinstance(exp, Mapping) or exp.get("administrative_superseded") is True:
            continue
        experiments.append({
            key: exp.get(key)
            for key in (
                "experiment_id", "status", "source_active", "source_engine", "claim",
                "champion", "challenger", "metric_name", "target_n", "prospective_n",
                "frozen_at", "last_evidence_at", "completed_at", "result_id", "lesson_id"
            )
        })
    sources = {}
    for source in SOURCE_PATHS:
        rows = [x for x in experiments if x.get("source_engine") == source]
        sources[source] = {
            **dict(source_status.get(source) or {}),
            "experiments_total": len(rows),
            "running_shadow": sum(x.get("status") == "RUNNING_SHADOW" for x in rows),
            "terminal": sum(x.get("status") in {"SUPPORTED", "REJECTED", "INCONCLUSIVE"} for x in rows),
        }
    summary = {
        "sources_configured": len(SOURCE_PATHS),
        "sources_available": sum(bool(x.get("available")) for x in source_status.values()),
        "experiments_total": len(experiments),
        "running_shadow": sum(x.get("status") == "RUNNING_SHADOW" for x in experiments),
        "supported": sum(x.get("status") == "SUPPORTED" for x in experiments),
        "rejected": sum(x.get("status") == "REJECTED" for x in experiments),
        "inconclusive": sum(x.get("status") == "INCONCLUSIVE" for x in experiments),
        "prospective_evidence_n": sum(int(x.get("prospective_n") or 0) for x in experiments),
        "lessons_total": len(lessons),
        "administrative_superseded_experiments": len(superseded),
    }
    return {
        "schema_version": PUBLIC_SCHEMA,
        "engine": "Hypothesis Shadow Engine 2.0",
        "mode": "SHADOW_ONLY",
        "generated_at": at,
        "pipeline": "SOURCE_HYPOTHESIS -> FREEZE -> FORWARD_EVIDENCE -> SUPPORTED/REJECTED/INCONCLUSIVE -> LESSON",
        "source_engines": list(SOURCE_PATHS),
        "summary": summary,
        "sources": sources,
        "experiments": experiments,
        "administrative_corrections": {
            "superseded_experiments": len(superseded),
            "reason": "FSE contract-routing correction; durable epochs/evidence retained, superseded contracts excluded from formal evaluation",
        },
        "recent_results": results[-12:],
        "recent_lessons": lessons[-12:],
        "authority": dict(ZERO_AUTHORITY),
        "production_impact": False,
    }


def run_cycle(root: Path, state_dir: Path, public_path: Path, at: str | None = None) -> dict[str, Any]:
    at = at or now_iso()
    state_dir.mkdir(parents=True, exist_ok=True)
    proposals, source_status = collect_proposals(root)
    registry = register_and_collect(state_dir, proposals, at)
    registry = evaluate(state_dir, registry, at)
    verify(state_dir)
    public = public_projection(registry, source_status, state_dir, at)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.write_text(json.dumps(public, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return public


def main() -> int:
    parser = argparse.ArgumentParser(description="Run BriefRooms Hypothesis Shadow Engine 2.0")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--public", type=Path, default=Path("data/investments/hypothesis_shadow_engine_v2_public.json"))
    parser.add_argument("--now")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify(args.state_dir), ensure_ascii=False, sort_keys=True))
        return 0
    public_path = args.public if args.public.is_absolute() else args.root / args.public
    out = run_cycle(args.root, args.state_dir, public_path, args.now)
    print(json.dumps(out["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

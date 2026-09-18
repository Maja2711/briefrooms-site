#!/usr/bin/env python3
"""Trigger-directed lazy deep research for Stock Trading v2.

The Market Relationship / Trigger Engine owns only scarce research attention.
This module performs actual authority-weighted company research for at most two
symbols emitted by deep_belief_queue while remaining shadow-only.

V1 intentionally uses the existing Stock Trading v2 Deep Evidence backend. It
is a Deep BELIEF proxy experiment, not a full Belief Core invocation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts import briefrooms_market_relationship_trigger as relationship
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_deep_evidence as deep
    from scripts import stock_trading_v2_discovery as discovery
except ModuleNotFoundError:  # pragma: no cover
    import briefrooms_market_relationship_trigger as relationship
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_deep_evidence as deep
    import stock_trading_v2_discovery as discovery

ROOT = Path(__file__).resolve().parents[1]
TRIGGER_PATH = ROOT / "data/investments/market_relationship_trigger/us.json"
FRONTIER_PATH = ROOT / "data/investments/stock_trading_v2_frontier/us.json"
OUTPUT_PATH = ROOT / "data/investments/market_relationship_deep_belief/us.json"
HISTORY_ROOT = ROOT / "data/investments/market_relationship_deep_belief_history"

SCHEMA_VERSION = "briefrooms-trigger-deep-belief-shadow-v1"
BACKEND = "STOCK_TRADING_V2_DEEP_EVIDENCE"
BELIEF_MODE = "DEEP_BELIEF_PROXY_NOT_FULL_BELIEF_CORE"


class TriggerDeepBeliefError(RuntimeError):
    pass


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def snapshot_id(
    trigger_snapshot_sha256: str,
    *,
    trigger_config_version: str,
    evidence_config_version: str,
) -> str:
    raw = (
        f"{trigger_snapshot_sha256}|{SCHEMA_VERSION}|{BACKEND}|"
        f"{trigger_config_version}|{evidence_config_version}"
    ).encode("utf-8")
    return "tdb-" + hashlib.sha256(raw).hexdigest()[:24]


def _candidate_pool(frontier: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    pool: dict[str, dict[str, Any]] = {}
    for rows in (frontier.get("candidates") or [], frontier.get("relationship_pool") or []):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            symbol = str(row.get("symbol") or "").upper()
            if symbol:
                pool[symbol] = dict(row)
    return pool


def validate_inputs(
    trigger_snapshot: Mapping[str, Any],
    frontier: Mapping[str, Any],
    trigger_config: Mapping[str, Any],
) -> None:
    relationship.validate_snapshot(trigger_snapshot, trigger_config)
    discovery.validate_frontier(frontier)
    if str(frontier.get("market") or "").upper() != "US":
        raise contracts.ContractError("trigger deep belief v1 supports US only")
    if trigger_snapshot.get("source_frontier_sha256") != frontier.get("frontier_sha256"):
        raise contracts.ContractError("trigger deep belief frontier lineage mismatch")
    maximum = int(
        (((trigger_config.get("markets") or {}).get("US") or {}).get("maximum_deep_belief_slots"))
        or 2
    )
    queue = trigger_snapshot.get("deep_belief_queue") or []
    if len(queue) > maximum:
        raise contracts.ContractError("trigger deep belief queue exceeds configured budget")
    known = {
        str((row or {}).get("symbol") or "").upper()
        for row in trigger_snapshot.get("candidates") or []
    }
    seen: set[str] = set()
    for row in queue:
        symbol = str((row or {}).get("symbol") or "").upper()
        if not symbol or symbol in seen or symbol not in known:
            raise contracts.ContractError("trigger deep belief queue symbol invalid")
        seen.add(symbol)


def collect_targeted_evidence(
    candidates: Sequence[Mapping[str, Any]],
    *,
    evidence_config: Mapping[str, Any],
) -> dict[str, tuple[list[dict[str, Any]], dict[str, Any]]]:
    if len(candidates) > 2:
        raise contracts.ContractError("targeted deep research exceeded max-two budget")
    if not candidates:
        return {}

    try:
        ticker_map = deep.fetch_sec_ticker_map(config=evidence_config)
        ticker_error = None
    except Exception as exc:
        ticker_map = {}
        ticker_error = f"{type(exc).__name__}: {exc}"[:500]

    now = datetime.now(timezone.utc)
    workers = min(
        max(1, int((evidence_config.get("network") or {}).get("workers") or 2)),
        len(candidates),
    )
    result: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = {}

    def one(candidate: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        symbol = str(candidate.get("symbol") or "").upper()
        rows, meta = deep.fetch_us_evidence(
            candidate,
            now=now,
            config=evidence_config,
            ticker_map=ticker_map,
        )
        if ticker_error:
            meta = deepcopy(dict(meta))
            meta["primary"] = {
                "provider": "SEC_EDGAR",
                "ok": False,
                "reason": ticker_error,
            }
        return symbol, rows, meta

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, candidate) for candidate in candidates]
        for future in as_completed(futures):
            symbol, rows, meta = future.result()
            result[symbol] = (rows, meta)
    return result


def build_snapshot(
    trigger_snapshot: Mapping[str, Any],
    frontier: Mapping[str, Any],
    evidence_by_symbol: Mapping[str, tuple[Sequence[Mapping[str, Any]], Mapping[str, Any]]],
    *,
    trigger_config: Mapping[str, Any],
    evidence_config: Mapping[str, Any],
    generated_at: str | None = None,
) -> dict[str, Any]:
    validate_inputs(trigger_snapshot, frontier, trigger_config)
    pool = _candidate_pool(frontier)
    queue = [dict(row) for row in trigger_snapshot.get("deep_belief_queue") or []]
    if len(queue) > 2:
        raise contracts.ContractError("trigger deep belief exceeded hard max-two budget")

    rows: list[dict[str, Any]] = []
    for selection_rank, queue_row in enumerate(queue, start=1):
        symbol = str(queue_row.get("symbol") or "").upper()
        candidate = pool.get(symbol)
        if not candidate:
            raise contracts.ContractError(f"trigger deep belief candidate missing from frontier pool: {symbol}")
        raw_evidence, provider_meta = evidence_by_symbol.get(
            symbol,
            ([], {"primary": {"ok": False}, "secondary": {"ok": False}}),
        )
        research = deep.build_candidate_evidence(
            candidate,
            raw_evidence,
            provider_meta,
            market="US",
            config=evidence_config,
        )
        research["selection_rank"] = selection_rank
        research["trigger_attention_score"] = queue_row.get("attention_score")
        research["trigger_type"] = queue_row.get("trigger_type")
        research["strongest_event_id"] = queue_row.get("strongest_event_id")
        rows.append(research)

    deltas = [
        abs(float(row.get("deep_opportunity_score") or 0.0) - float(row.get("opportunity_score") or 0.0))
        for row in rows
    ]
    reference_slots = int(evidence_config.get("frontier_candidates_to_enrich") or 10)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id(
            str(trigger_snapshot.get("snapshot_sha256") or ""),
            trigger_config_version=str(trigger_config.get("version") or "unknown"),
            evidence_config_version=str(evidence_config.get("version") or "unknown"),
        ),
        "market": "US",
        "generated_at": generated_at or _iso_now(),
        "mode": "shadow_trigger_directed_lazy_research",
        "research_backend": BACKEND,
        "belief_mode": BELIEF_MODE,
        "trigger_config_version": trigger_config.get("version"),
        "evidence_config_version": evidence_config.get("version"),
        "full_belief_core_invocation": False,
        "source_trigger_snapshot_sha256": trigger_snapshot.get("snapshot_sha256"),
        "source_frontier_sha256": frontier.get("frontier_sha256"),
        "target_count": len(rows),
        "targets": rows,
        "research_economics": {
            "broad_deep_evidence_reference_slots": reference_slots,
            "trigger_directed_slots": len(rows),
            "theoretical_slot_reduction_fraction": round(
                max(0.0, 1.0 - len(rows) / max(1, reference_slots)),
                6,
            ),
            "realized_champion_compute_reduction": False,
            "reason": "broad_top10_arm_remains_champion_until_prospective_promotion",
        },
        "research_yield": {
            "complete_targets": sum(1 for row in rows if row.get("evidence_status") == "COMPLETE"),
            "degraded_targets": sum(1 for row in rows if row.get("evidence_status") == "DEGRADED"),
            "data_error_targets": sum(1 for row in rows if row.get("evidence_status") == "DATA_ERROR"),
            "primary_evidence_items": sum(
                int((row.get("evidence_metrics") or {}).get("primary_count") or 0)
                for row in rows
            ),
            "secondary_evidence_items": sum(
                int((row.get("evidence_metrics") or {}).get("secondary_count") or 0)
                for row in rows
            ),
            "material_event_targets": sum(
                1
                for row in rows
                if (row.get("evidence_metrics") or {}).get("material_event_present") is True
            ),
            "mean_absolute_score_update": round(statistics.mean(deltas), 6) if deltas else 0.0,
        },
        "learning_contract": {
            "future_trigger_outcomes_required": True,
            "compare_information_yield_per_research_unit": True,
            "with_without_required_before_replacing_broad_arm": True,
            "separate_holdout_required": True,
            "no_hindsight_mutation": True,
        },
        "governance": {
            "production_decision_influence": False,
            "trade_execution": False,
            "automatic_portfolio_admission": False,
            "automatic_policy_writeback": False,
            "automatic_promotion": False,
        },
    }
    payload["snapshot_sha256"] = contracts.payload_sha256(payload)
    validate_snapshot(payload, trigger_config=trigger_config, evidence_config=evidence_config)
    return payload


def validate_snapshot(
    payload: Mapping[str, Any],
    *,
    trigger_config: Mapping[str, Any],
    evidence_config: Mapping[str, Any],
) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("market") != "US":
        raise contracts.ContractError("trigger deep belief snapshot schema/market mismatch")
    if payload.get("research_backend") != BACKEND:
        raise contracts.ContractError("trigger deep belief backend mismatch")
    if payload.get("belief_mode") != BELIEF_MODE:
        raise contracts.ContractError("trigger deep belief mode mismatch")
    if payload.get("full_belief_core_invocation") is not False:
        raise contracts.ContractError("trigger deep belief cannot impersonate full Belief Core")

    maximum = int(
        (((trigger_config.get("markets") or {}).get("US") or {}).get("maximum_deep_belief_slots"))
        or 2
    )
    rows = payload.get("targets")
    if not isinstance(rows, list) or int(payload.get("target_count", -1)) != len(rows):
        raise contracts.ContractError("trigger deep belief target count mismatch")
    if len(rows) > maximum:
        raise contracts.ContractError("trigger deep belief output exceeded max target budget")

    seen: set[str] = set()
    for rank, row in enumerate(rows, start=1):
        symbol = str((row or {}).get("symbol") or "")
        if not symbol or symbol in seen:
            raise contracts.ContractError("trigger deep belief duplicate/missing symbol")
        seen.add(symbol)
        if int((row or {}).get("selection_rank") or 0) != rank:
            raise contracts.ContractError("trigger deep belief selection rank mismatch")
        if (row or {}).get("evidence_status") not in {"COMPLETE", "DEGRADED", "DATA_ERROR"}:
            raise contracts.ContractError("trigger deep belief evidence status invalid")
        if ((row or {}).get("admission") or {}).get("production_decision_influence") is not False:
            raise contracts.ContractError("trigger deep belief research escaped shadow governance")

    reference_slots = int(evidence_config.get("frontier_candidates_to_enrich") or 10)
    if int((payload.get("research_economics") or {}).get("broad_deep_evidence_reference_slots") or 0) != reference_slots:
        raise contracts.ContractError("trigger deep belief research economics reference mismatch")

    governance = payload.get("governance") or {}
    for key in (
        "production_decision_influence",
        "trade_execution",
        "automatic_portfolio_admission",
        "automatic_policy_writeback",
        "automatic_promotion",
    ):
        if governance.get(key) is not False:
            raise contracts.ContractError(f"trigger deep belief governance violation: {key}")

    body = dict(payload)
    stored = str(body.pop("snapshot_sha256", ""))
    if not stored or stored != contracts.payload_sha256(body):
        raise contracts.ContractError("trigger deep belief snapshot hash mismatch")


def history_path(root: Path, payload: Mapping[str, Any]) -> Path:
    day = str(payload.get("generated_at") or "")[:10] or "unknown"
    return root / "us" / day / f"{payload.get('snapshot_id')}.json"


def persist_snapshot(root: Path, payload: Mapping[str, Any]) -> bool:
    path = history_path(root, payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = _read_json(path)
        if not isinstance(existing, Mapping):
            raise contracts.ContractError("existing trigger deep belief history unreadable")
        if dict(existing) != dict(payload):
            raise contracts.ContractError("trigger deep belief immutable history conflict")
        return False
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        temp = Path(handle.name)
    temp.replace(path)
    return True


def find_existing(
    root: Path,
    trigger_snapshot_sha256: str,
    *,
    trigger_config_version: str,
    evidence_config_version: str,
) -> dict[str, Any] | None:
    sid = snapshot_id(
        trigger_snapshot_sha256,
        trigger_config_version=trigger_config_version,
        evidence_config_version=evidence_config_version,
    )
    if not root.exists():
        return None
    matches = list(root.rglob(f"{sid}.json"))
    if not matches:
        return None
    payload = _read_json(matches[0])
    return dict(payload) if isinstance(payload, Mapping) else None


def run(
    *,
    trigger_path: Path = TRIGGER_PATH,
    frontier_path: Path = FRONTIER_PATH,
    output_path: Path = OUTPUT_PATH,
    history_root: Path = HISTORY_ROOT,
    trigger_config_path: Path = relationship.CONFIG_PATH,
    evidence_config_path: Path = deep.CONFIG_PATH,
) -> dict[str, Any]:
    trigger_config = relationship.load_config(trigger_config_path)
    evidence_config = deep.load_config(evidence_config_path)
    trigger_snapshot = _read_json(trigger_path)
    frontier = _read_json(frontier_path)
    if not isinstance(trigger_snapshot, Mapping) or not isinstance(frontier, Mapping):
        raise TriggerDeepBeliefError("trigger snapshot or frontier unavailable")
    validate_inputs(trigger_snapshot, frontier, trigger_config)

    existing = find_existing(
        history_root,
        str(trigger_snapshot.get("snapshot_sha256") or ""),
        trigger_config_version=str(trigger_config.get("version") or "unknown"),
        evidence_config_version=str(evidence_config.get("version") or "unknown"),
    )
    if existing is not None:
        validate_snapshot(existing, trigger_config=trigger_config, evidence_config=evidence_config)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return dict(existing)

    pool = _candidate_pool(frontier)
    selected: list[dict[str, Any]] = []
    for row in trigger_snapshot.get("deep_belief_queue") or []:
        symbol = str((row or {}).get("symbol") or "").upper()
        candidate = pool.get(symbol)
        if candidate:
            selected.append(candidate)
    if len(selected) > 2:
        raise contracts.ContractError("trigger deep belief selected more than two candidates")

    evidence = collect_targeted_evidence(selected, evidence_config=evidence_config)
    payload = build_snapshot(
        trigger_snapshot,
        frontier,
        evidence,
        trigger_config=trigger_config,
        evidence_config=evidence_config,
    )
    persist_snapshot(history_root, payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trigger", type=Path, default=TRIGGER_PATH)
    parser.add_argument("--frontier", type=Path, default=FRONTIER_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--history-root", type=Path, default=HISTORY_ROOT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    if args.verify:
        trigger_config = relationship.load_config()
        evidence_config = deep.load_config()
        payload = _read_json(args.output)
        if not isinstance(payload, Mapping):
            raise SystemExit("trigger deep belief output unavailable")
        validate_snapshot(payload, trigger_config=trigger_config, evidence_config=evidence_config)
        print("TRIGGER_DEEP_BELIEF_OK", payload.get("target_count"), payload.get("snapshot_id"))
        return 0

    payload = run(
        trigger_path=args.trigger,
        frontier_path=args.frontier,
        output_path=args.output,
        history_root=args.history_root,
    )
    print(json.dumps({
        "snapshot_id": payload.get("snapshot_id"),
        "targets": payload.get("target_count"),
        "belief_mode": payload.get("belief_mode"),
        "production_decision_influence": False,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

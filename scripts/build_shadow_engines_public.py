#!/usr/bin/env python3
"""Build the public BriefRooms Shadow Engines observatory.

This is a read-only inventory. It never changes model policy, promotion state,
sizing or execution. Workflow health comes from GitHub Actions; observation and
Champion/Challenger metadata comes from existing public/research projections.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "briefrooms-shadow-engines-public-v1"

SPECS = [
    {
        "id": "belief-core",
        "name": "Belief Core Live Shadow",
        "workflows": ["belief-core-shadow-live.yml"],
        "source": "data/investments/decision_lab_public.json",
        "domain": "/pl/inwestycje/decision-lab.html",
        "domain_label": "BriefRooms LAB",
        "max_idle_hours": 3,
    },
    {
        "id": "eurusd-abc",
        "name": "EURUSD A/B/C Live Shadow",
        "workflows": ["daily-eurusd-abc-live-shadow.yml"],
        "source": "data/investments/eurusd_abc_public_pl.json",
        "domain": "/pl/inwestycje/daily-trading.html",
        "domain_label": "Daily Trading",
        "max_idle_hours": 4,
    },
    {
        "id": "eurusd-x",
        "name": "EURUSD X Adaptive Shadow",
        "workflows": ["daily-eurusd-x-shadow.yml"],
        "source": "data/investments/eurusd_x_public_pl.json",
        "domain": "/pl/inwestycje/daily-trading.html",
        "domain_label": "Daily Trading",
        "max_idle_hours": 4,
    },
    {
        "id": "stock-v1",
        "name": "Stock Trading v1 Shadow Challenger",
        "workflows": ["stock-trading-v1-shadow.yml"],
        "source": "data/investments/stock_trading_v1_shadow_portfolio.json",
        "domain": "/pl/inwestycje/stock-trading.html",
        "domain_label": "Stock Trading",
        "max_idle_hours": 2,
    },
    {
        "id": "brace-g6",
        "name": "BRACE-SPX G6 + Adaptive Shadow",
        "workflows": ["brace-spx-generation6.yml"],
        "source": "data/public/brace_spx_platform_public.json",
        "domain": "/pl/inwestycje/brace-spx-lab.html",
        "domain_label": "BRACE-SPX",
        "max_idle_hours": 48,
    },
    {
        "id": "brace-architecture-2",
        "name": "BRACE-SPX Architecture 2 Shadow",
        "workflows": ["brace-spx-architecture-v2-shadow.yml"],
        "source": None,
        "domain": "/pl/inwestycje/brace-spx-lab.html",
        "domain_label": "BRACE-SPX",
        "max_idle_hours": 48,
    },
    {
        "id": "gse-v2",
        "name": "GSE v2 Shadow Learning",
        "workflows": ["gse-hourly-cycle-v2.yml", "gse-v2-learning-shadow.yml"],
        "source": "data/gse/gse_v2_lab_public.json",
        "domain": "/pl/geopolityka.html",
        "domain_label": "Geopolityka",
        "max_idle_hours": 4,
    },
    {
        "id": "wes",
        "name": "WES Incremental Shadow Learning",
        "workflows": ["investments-wes.yml"],
        "source": "data/investments/wes_incremental_alpha_report.json",
        "domain": "/pl/inwestycje/pozycje-tygodniowe.html",
        "domain_label": "Weekly Trading",
        "max_idle_hours": 72,
    },
    {
        "id": "hypothesis-shadow",
        "name": "Hypothesis Shadow Experiments",
        "workflows": ["hypothesis-shadow-experiments.yml"],
        "source": "data/investments/lesson_hypothesis_registry_v1.json",
        "domain": "/pl/inwestycje/decision-lab.html",
        "domain_label": "BriefRooms LAB",
        "max_idle_hours": 48,
    },
    {
        "id": "deepbook",
        "name": "DeepBook Predict Shadow",
        "workflows": ["deepbook-predict-shadow.yml"],
        "source": "data/investments/deepbook_predict_shadow.json",
        "domain": "/pl/inwestycje/decision-lab.html",
        "domain_label": "BriefRooms LAB",
        "max_idle_hours": 1,
    },
]

# These contain "shadow" in the workflow path but are infrastructure/validation,
# not separate user-facing model engines.
IGNORED_SHADOW_WORKFLOWS = {
    "belief-wes-assets-daily-shadow-validation.yml",
    "brace-entity-belief-shadow-bridge.yml",
    "brace-entity-belief-shadow-bridge-validation.yml",
    "stock-trading-v2-shadow-ingest.yml",
    "shadow-alpha-experience-store.yml",
    "shadow-engines-observatory.yml",
}


def load(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def latest_workflow(spec: Mapping[str, Any], statuses: Mapping[str, Any]) -> dict[str, Any]:
    rows = []
    for name in spec["workflows"]:
        row = statuses.get(name)
        if isinstance(row, Mapping) and row:
            rows.append(dict(row, workflow=name))
    rows.sort(key=lambda row: str(row.get("updatedAt") or row.get("createdAt") or ""), reverse=True)
    return rows[0] if rows else {}


def metadata(engine_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
    if engine_id == "belief-core":
        forecasts = data.get("forecasts") if isinstance(data.get("forecasts"), list) else []
        v3 = data.get("belief_core_v3_candidates") if isinstance(data.get("belief_core_v3_candidates"), Mapping) else {}
        candidates = v3.get("candidates") if isinstance(v3.get("candidates"), list) else []
        return {
            "observations": len(forecasts),
            "observation_label": "forecasty",
            "champion": data.get("model") or "Belief Core v2",
            "challenger": f"v3 candidates · {len(candidates)}" if candidates else None,
            "data_at": data.get("generated_at"),
        }
    if engine_id == "eurusd-abc":
        sample = data.get("sample") if isinstance(data.get("sample"), Mapping) else {}
        return {
            "observations": sample.get("captures"),
            "observation_label": "capture",
            "champion": "A/B/C parallel",
            "challenger": None,
            "data_at": data.get("generated_at"),
        }
    if engine_id == "eurusd-x":
        sample = data.get("sample") if isinstance(data.get("sample"), Mapping) else {}
        adaptive = data.get("adaptive_layer") if isinstance(data.get("adaptive_layer"), Mapping) else {}
        champion = adaptive.get("champion") if isinstance(adaptive.get("champion"), Mapping) else {}
        challenger = adaptive.get("challenger") if isinstance(adaptive.get("challenger"), Mapping) else {}
        return {
            "observations": sample.get("captures"),
            "observation_label": "capture",
            "champion": champion.get("version") or "X",
            "challenger": challenger.get("version"),
            "data_at": data.get("generated_at"),
        }
    if engine_id == "stock-v1":
        markets = data.get("markets") if isinstance(data.get("markets"), Mapping) else {}
        observations = 0
        for market in markets.values():
            if isinstance(market, Mapping):
                observations += len(market.get("open_positions") or []) + len(market.get("closed_positions") or [])
        return {
            "observations": observations,
            "observation_label": "pozycje",
            "champion": "Stock Trading v2",
            "challenger": "v1",
            "data_at": data.get("updated_at"),
        }
    if engine_id == "brace-g6":
        frozen = data.get("frozen_track") if isinstance(data.get("frozen_track"), Mapping) else {}
        adaptive = data.get("adaptive_research") if isinstance(data.get("adaptive_research"), Mapping) else {}
        best = adaptive.get("best_challenger")
        active = int(adaptive.get("active_challengers") or 0)
        return {
            "observations": frozen.get("observations_collected"),
            "observation_label": "shadow sessions",
            "champion": frozen.get("generation_id") or "G6 Frozen",
            "challenger": best or (f"{active} aktywne" if active else None),
            "data_at": data.get("generated_at"),
        }
    if engine_id == "brace-architecture-2":
        return {
            "observations": None,
            "observation_label": "research-branch state",
            "champion": "Architecture 2",
            "challenger": None,
            "data_at": None,
        }
    if engine_id == "gse-v2":
        best = data.get("best_horizon") if isinstance(data.get("best_horizon"), Mapping) else {}
        challenger = data.get("challenger") if isinstance(data.get("challenger"), Mapping) else {}
        activity = data.get("activity") if isinstance(data.get("activity"), Mapping) else {}
        return {
            "observations": best.get("n"),
            "observation_label": "evaluated events",
            "champion": "GSE v2 active",
            "challenger": challenger.get("status"),
            "data_at": activity.get("projection_generated_at") or activity.get("last_learning_at"),
        }
    if engine_id == "wes":
        overall = data.get("overall") if isinstance(data.get("overall"), Mapping) else {}
        sample = data.get("sample") if isinstance(data.get("sample"), Mapping) else {}
        n = overall.get("resolved_pairs")
        if n is None:
            n = sample.get("resolved_pairs")
        return {
            "observations": n,
            "observation_label": "resolved pairs",
            "champion": "WES current",
            "challenger": "Frozen V5 baseline",
            "data_at": data.get("generated_at") or data.get("updated_at"),
        }
    if engine_id == "hypothesis-shadow":
        hypotheses = data.get("hypotheses") if isinstance(data.get("hypotheses"), list) else []
        ready = [x for x in hypotheses if isinstance(x, Mapping) and x.get("status") == "READY_FOR_SHADOW"]
        target = sum(int(((x.get("experiment_spec") or {}).get("validation_target_n") or 0)) for x in ready)
        return {
            "observations": 0,
            "observation_label": f"formal evidence · target {target}",
            "champion": "current thresholds",
            "challenger": f"{len(ready)} hipotezy" if ready else None,
            "data_at": None,
        }
    if engine_id == "deepbook":
        snapshots = data.get("snapshots") if isinstance(data.get("snapshots"), list) else []
        return {
            "observations": len(snapshots),
            "observation_label": "snapshots",
            "champion": "—",
            "challenger": "DeepBook Predict",
            "data_at": data.get("updated_at") or data.get("generated_at"),
        }
    return {"observations": None, "observation_label": "obserwacje", "champion": None, "challenger": None, "data_at": None}


def status_for(latest: Mapping[str, Any], meta: Mapping[str, Any], max_idle_hours: float, now: datetime) -> tuple[str, str]:
    if not latest:
        return "IDLE", "brak zarejestrowanego runu workflow"
    status = str(latest.get("status") or "").lower()
    conclusion = str(latest.get("conclusion") or "").lower()
    if status and status != "completed":
        return "RUNNING", "workflow jest w trakcie"
    if conclusion and conclusion not in {"success", "skipped", "neutral"}:
        return "ERROR", f"ostatni workflow zakończył się: {conclusion}"
    last = parse_time(latest.get("updatedAt") or latest.get("createdAt"))
    if last is not None:
        age = (now - last).total_seconds() / 3600
        if age > max_idle_hours:
            return "IDLE", f"ostatni poprawny run ma {age:.1f}h"
    obs = meta.get("observations")
    if obs is not None and int(obs or 0) <= 0:
        return "NO DATA", "workflow działa, ale nie ma jeszcze obserwacji"
    return "RUNNING", "ostatni workflow poprawny"


def coverage(root: Path) -> dict[str, Any]:
    workflows_dir = root / ".github" / "workflows"
    discovered = {
        p.name for p in workflows_dir.glob("*shadow*.yml")
        if "validation" not in p.name
    }
    mapped = {wf for spec in SPECS for wf in spec["workflows"]}
    unmapped = sorted(discovered - mapped - IGNORED_SHADOW_WORKFLOWS)
    return {
        "shadow_workflows_discovered": sorted(discovered),
        "mapped_workflows": sorted(discovered & mapped),
        "ignored_infrastructure": sorted(discovered & IGNORED_SHADOW_WORKFLOWS),
        "unmapped_shadow_workflows": unmapped,
        "complete": not unmapped,
    }


def build(root: Path, workflow_status: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    engines = []
    for spec in SPECS:
        source = root / spec["source"] if spec.get("source") else None
        data = load(source)
        meta = metadata(spec["id"], data)
        latest = latest_workflow(spec, workflow_status)
        state, reason = status_for(latest, meta, float(spec["max_idle_hours"]), now)
        engines.append({
            "id": spec["id"],
            "name": spec["name"],
            "status": state,
            "status_reason": reason,
            "workflow": latest.get("workflow") or spec["workflows"][0],
            "last_run_at": latest.get("updatedAt") or latest.get("createdAt"),
            "last_run_id": latest.get("databaseId"),
            "last_conclusion": latest.get("conclusion"),
            "observations": meta.get("observations"),
            "observation_label": meta.get("observation_label"),
            "champion": meta.get("champion"),
            "challenger": meta.get("challenger"),
            "data_at": meta.get("data_at"),
            "domain": spec["domain"],
            "domain_label": spec["domain_label"],
            "source": spec.get("source"),
        })
    counts = {state: sum(row["status"] == state for row in engines) for state in ("RUNNING", "IDLE", "ERROR", "NO DATA")}
    return {
        "schema_version": SCHEMA,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "read_only": True,
        "production_authority": False,
        "summary": {"total": len(engines), **counts},
        "coverage": coverage(root),
        "engines": engines,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--workflow-status", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/investments/shadow_engines_public.json"))
    parser.add_argument("--list-workflows", action="store_true")
    args = parser.parse_args()
    if args.list_workflows:
        for name in sorted({wf for spec in SPECS for wf in spec["workflows"]}):
            print(name)
        return 0
    statuses = load(args.workflow_status) if args.workflow_status else {}
    payload = build(args.root, statuses)
    output = args.output if args.output.is_absolute() else args.root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    if not payload["coverage"]["complete"]:
        raise SystemExit("unmapped logical shadow workflows: " + ", ".join(payload["coverage"]["unmapped_shadow_workflows"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Opportunity-regret and gate-attribution engine for Stock Trading v2.

The engine compares the frozen Champion action with prospectively frozen
candidate alternatives on the same source decision set. It measures both false
positives (taking a losing trade versus CASH) and false negatives (rejecting a
better legal setup). Results can create Challenger hypotheses, never direct
production mutations.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from scripts import stock_trading_v2_admission_ledger as admission
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_experience_store as experience
    from scripts import stock_trading_v2_outcome_replay as outcomes
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_admission_ledger as admission
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_experience_store as experience
    import stock_trading_v2_outcome_replay as outcomes

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data/investments/stock_trading_v2_learning_config.json"
REPORT_PATH = ROOT / "data/investments/stock_trading_v2_learning/regret_report.json"
SCHEMA_VERSION = "stock-trading-v2-regret-report-v1"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def _iter_json(root: Path) -> Iterable[dict[str, Any]]:
    if not root.exists():
        return []
    return (_read_json(path) for path in sorted(root.rglob("*.json")))


def _blocker(event: Mapping[str, Any]) -> str:
    state = event.get("candidate_state") or {}
    path = state.get("decision_path") or {}
    value = path.get("first_blocking_gate") or state.get("first_blocking_gate")
    if isinstance(value, Mapping):
        # Rejected-candidate freezes store the canonical blocker as a structured
        # gate object. Attribution must use its stable gate name, never the
        # string representation of the whole dictionary.
        name = str(value.get("name") or "").strip()
        if name:
            return name
        reason = str(value.get("reason") or "").strip()
        if reason:
            return reason
    if value:
        return str(value)
    return "selected_candidate" if event.get("selected") else "unspecified_rejection"


def _source_group(event: Mapping[str, Any]) -> str:
    source = event.get("source") or {}
    return str(source.get("payload_sha256") or event.get("event_id"))


def _learning_net_r(outcome: Mapping[str, Any]) -> float | None:
    replay = outcome.get("replay") or {}
    if replay.get("status") == "SETTLED":
        try:
            return float(replay.get("net_r"))
        except (TypeError, ValueError):
            return None
    if replay.get("status") == "NOT_ACTIVATED":
        return 0.0
    return None


def _admission_maps(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _iter_json(root):
        admission.validate_observation(row)
        result[str(row["source_event_id"])] = row
    return result


def build_report(
    events: Iterable[Mapping[str, Any]],
    admission_rows: Iterable[Mapping[str, Any]],
    outcome_rows: Iterable[Mapping[str, Any]],
    config: Mapping[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    events_by_id: dict[str, dict[str, Any]] = {}
    groups: dict[str, list[str]] = defaultdict(list)
    for raw in events:
        event = dict(raw)
        contracts.validate_experience_event(event)
        event_id = str(event["event_id"])
        events_by_id[event_id] = event
        groups[_source_group(event)].append(event_id)

    admissions: dict[str, dict[str, Any]] = {}
    for raw in admission_rows:
        row = dict(raw)
        admission.validate_observation(row)
        admissions[str(row["source_event_id"])] = row

    settled: dict[tuple[str, int], dict[str, Any]] = {}
    for raw in outcome_rows:
        row = dict(raw)
        outcomes.validate_outcome(row)
        settled[(str(row["source_event_id"]), int(row["horizon_sessions"]))] = row

    horizon_reports: dict[str, Any] = {}
    attribution_samples: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for horizon in [int(value) for value in config.get("horizons_sessions") or []]:
        comparisons: list[dict[str, Any]] = []
        for group_key, event_ids in groups.items():
            comparable: list[tuple[str, float, dict[str, Any]]] = []
            for event_id in event_ids:
                row = settled.get((event_id, horizon))
                if row is None:
                    continue
                net_r = _learning_net_r(row)
                if net_r is None:
                    continue
                comparable.append((event_id, net_r, row))
            if len(comparable) < int((config.get("regret") or {}).get("minimum_comparable_candidates") or 2):
                continue

            champion_event: str | None = None
            champion_r = 0.0
            champion_reason = "cash_no_admitted_candidate"
            for event_id, net_r, _ in comparable:
                observed = admissions.get(event_id)
                champion = observed.get("champion") if isinstance(observed, Mapping) else None
                if isinstance(champion, Mapping) and champion.get("action") == "LONG":
                    champion_event = event_id
                    champion_r = net_r
                    champion_reason = str(champion.get("reason") or "long")
                    break

            best_event, best_r, _ = max(comparable, key=lambda item: item[1])
            if best_r < 0:
                best_event = "CASH"
                best_r = 0.0
            rejected = [item for item in comparable if item[0] != champion_event]
            best_rejected_event: str | None = None
            best_rejected_r = 0.0
            if rejected:
                best_rejected_event, best_rejected_r, _ = max(rejected, key=lambda item: item[1])
                if best_rejected_r < 0:
                    best_rejected_event, best_rejected_r = None, 0.0

            regret = max(0.0, best_r - champion_r)
            false_positive_cost = max(0.0, -champion_r) if champion_event else 0.0
            false_negative_cost = max(0.0, best_rejected_r - champion_r)
            comparison = {
                "source_group": group_key,
                "horizon_sessions": horizon,
                "candidate_count": len(comparable),
                "champion_event_id": champion_event,
                "champion_net_r": round(champion_r, 8),
                "champion_reason": champion_reason,
                "best_legal_event_id": best_event,
                "best_legal_net_r": round(best_r, 8),
                "best_rejected_event_id": best_rejected_event,
                "best_rejected_net_r": round(best_rejected_r, 8),
                "opportunity_regret_r": round(regret, 8),
                "false_positive_cost_r": round(false_positive_cost, 8),
                "false_negative_cost_r": round(false_negative_cost, 8),
            }
            comparisons.append(comparison)

            for event_id, net_r, _ in comparable:
                if event_id == champion_event:
                    continue
                event = events_by_id[event_id]
                blocker = _blocker(event)
                attribution_samples[(horizon, blocker)].append(
                    {
                        "event_id": event_id,
                        "symbol": event["symbol"],
                        "session_date": event["session_date"],
                        "incremental_r_vs_champion": net_r - champion_r,
                        "candidate_net_r": net_r,
                        "champion_net_r": champion_r,
                    }
                )

        regrets = [float(row["opportunity_regret_r"]) for row in comparisons]
        false_pos = [float(row["false_positive_cost_r"]) for row in comparisons]
        false_neg = [float(row["false_negative_cost_r"]) for row in comparisons]
        horizon_reports[str(horizon)] = {
            "comparison_groups": len(comparisons),
            "mean_opportunity_regret_r": round(statistics.mean(regrets), 8) if regrets else None,
            "total_opportunity_regret_r": round(sum(regrets), 8),
            "mean_false_positive_cost_r": round(statistics.mean(false_pos), 8) if false_pos else None,
            "mean_false_negative_cost_r": round(statistics.mean(false_neg), 8) if false_neg else None,
            "comparisons": comparisons,
        }

    attribution: dict[str, Any] = {}
    hypotheses: list[dict[str, Any]] = []
    learning_cfg = config.get("learning") or {}
    learnable = {str(value) for value in learning_cfg.get("learnable_components") or []}
    minimum_hypothesis = int(learning_cfg.get("minimum_observations_for_hypothesis") or 12)
    minimum_challenger = int(learning_cfg.get("minimum_observations_for_challenger") or 30)
    minimum_symbols = int(learning_cfg.get("minimum_unique_symbols") or 5)

    for (horizon, blocker), samples in sorted(attribution_samples.items()):
        values = [float(row["incremental_r_vs_champion"]) for row in samples]
        symbols = {str(row["symbol"]) for row in samples}
        dates = sorted({str(row["session_date"]) for row in samples})
        positive_rate = sum(value > 0 for value in values) / len(values) if values else 0.0
        key = f"{horizon}:{blocker}"
        attribution[key] = {
            "horizon_sessions": horizon,
            "blocking_component": blocker,
            "observations": len(samples),
            "unique_symbols": len(symbols),
            "span": [dates[0], dates[-1]] if dates else None,
            "mean_incremental_r_vs_champion": round(statistics.mean(values), 8) if values else None,
            "median_incremental_r_vs_champion": round(statistics.median(values), 8) if values else None,
            "positive_incremental_rate": round(positive_rate, 8),
            "total_positive_missed_r": round(sum(max(0.0, value) for value in values), 8),
        }
        normalized = blocker
        if blocker.startswith("producer_rejected:"):
            normalized = blocker.split(":", 1)[1]
        if (
            normalized in learnable
            and len(samples) >= minimum_hypothesis
            and len(symbols) >= minimum_symbols
            and statistics.mean(values) > 0
            and positive_rate >= 0.55
        ):
            status = "ELIGIBLE_FOR_CHALLENGER_HOLDOUT" if len(samples) >= minimum_challenger else "HYPOTHESIS_ONLY"
            hypotheses.append(
                {
                    "component": normalized,
                    "horizon_sessions": horizon,
                    "status": status,
                    "evidence": deepcopy(attribution[key]),
                    "suggested_experiment": _experiment_for_component(normalized),
                    "automatic_production_change": False,
                }
            )

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or contracts.iso_utc(),
        "mode": "shadow_closed_loop",
        "horizons": horizon_reports,
        "gate_attribution": attribution,
        "challenger_hypotheses": hypotheses,
        "governance": {
            "production_decision_influence": False,
            "automatic_policy_writeback": False,
            "champion_challenger_required": True,
            "single_trade_mutation_forbidden": True,
        },
    }
    body = dict(payload)
    payload["report_sha256"] = contracts.payload_sha256(body)
    validate_report(payload)
    return payload


def _experiment_for_component(component: str) -> dict[str, Any]:
    if component == "non_positive_conservative_expected_value":
        return {
            "type": "uncertainty_aware_ev_challenger",
            "change": "test soft/uncertainty-band treatment instead of binary conservative-EV sign veto",
            "safety_gates_unchanged": True,
        }
    if component == "entry_score_below_threshold":
        return {
            "type": "conditional_score_challenger",
            "change": "test context-conditioned admission threshold without changing hard risk/data gates",
            "safety_gates_unchanged": True,
        }
    return {
        "type": "component_weight_or_margin_challenger",
        "change": f"test isolated bounded change to {component}",
        "safety_gates_unchanged": True,
    }


def validate_report(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise contracts.ContractError("regret report schema mismatch")
    governance = payload.get("governance") or {}
    if governance.get("production_decision_influence") is not False or governance.get("automatic_policy_writeback") is not False:
        raise contracts.ContractError("regret report escaped shadow governance")
    body = dict(payload)
    stored = str(body.pop("report_sha256", ""))
    if not stored or stored != contracts.payload_sha256(body):
        raise contracts.ContractError("regret report hash mismatch")


def run(
    *,
    experience_root: Path = experience.DEFAULT_STORE_ROOT,
    admission_root: Path = admission.DEFAULT_ROOT,
    outcome_root: Path = outcomes.DEFAULT_ROOT,
    config_path: Path = CONFIG_PATH,
) -> dict[str, Any]:
    config = outcomes.load_config(config_path)
    return build_report(
        list(_iter_json(experience_root)),
        list(_iter_json(admission_root)),
        list(_iter_json(outcome_root)),
        config,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experience-root", type=Path, default=experience.DEFAULT_STORE_ROOT)
    parser.add_argument("--admission-root", type=Path, default=admission.DEFAULT_ROOT)
    parser.add_argument("--outcome-root", type=Path, default=outcomes.DEFAULT_ROOT)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    payload = run(
        experience_root=args.experience_root,
        admission_root=args.admission_root,
        outcome_root=args.outcome_root,
        config_path=args.config,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "horizons": {key: value["comparison_groups"] for key, value in payload["horizons"].items()},
                "gate_attribution_rows": len(payload["gate_attribution"]),
                "challenger_hypotheses": len(payload["challenger_hypotheses"]),
                "production_decision_influence": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

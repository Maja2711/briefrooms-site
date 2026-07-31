#!/usr/bin/env python3
"""Sanitized public BRACE control snapshot and bilingual weekly reports."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from brace_portfolio_config import EngineConfig, public_policy
from brace_portfolio_data import ENGINE_DATA_ROOT, canonical_sha256, write_json_atomic

PUBLIC_PATH = ENGINE_DATA_ROOT / "public" / "brace_engine_public.json"
REPORT_PL_PATH = ENGINE_DATA_ROOT / "reports" / "weekly-latest-pl.md"
REPORT_EN_PATH = ENGINE_DATA_ROOT / "reports" / "weekly-latest-en.md"


def _methodology(registry: Mapping[str, Any], methodology_id: Any) -> Dict[str, Any]:
    for item in registry.get("methodologies", []) or []:
        if item.get("methodology_id") == methodology_id:
            return dict(item)
    return {}


def _flatten_conditions(gate: Mapping[str, Any]) -> list[Dict[str, Any]]:
    rows = []
    for group_name, group in (gate.get("conditions") or {}).items():
        if not isinstance(group, Mapping):
            continue
        for condition, passed in group.items():
            rows.append(
                {
                    "group": group_name,
                    "condition": condition,
                    "passed": bool(passed),
                }
            )
    return rows


def _target_status(analysis: Mapping[str, Any], config: EngineConfig) -> str:
    expected = float(analysis.get("expected_annual_return") or 0.0)
    required_risk = float(analysis.get("required_risk_to_target") or 0.0)
    probability = float(analysis.get("probability_of_reaching_target") or 0.0)
    if required_risk > config.max_expected_drawdown:
        return "TARGET_REQUIRES_EXCESSIVE_RISK"
    if (
        expected < config.target_annual_return
        or probability < config.target_probability_floor
    ):
        return "TARGET_NOT_CURRENTLY_JUSTIFIED"
    return "TARGET_CURRENTLY_JUSTIFIED_WITHIN_MODEL"


def build_public_snapshot(
    registry: Mapping[str, Any],
    analysis: Mapping[str, Any],
    pending: Mapping[str, Any],
    shadow: Mapping[str, Any],
    promotion_history: Mapping[str, Any],
    operational: Mapping[str, Any],
    config: EngineConfig,
    generated_at: Optional[datetime] = None,
    paper_portfolio_available: bool = False,
) -> Dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    controller = str(registry.get("controller_state") or "ACTIVE_BASELINE")
    baseline = _methodology(registry, registry.get("baseline_methodology_id"))
    challenger = _methodology(registry, registry.get("challenger_methodology_id"))
    champion = _methodology(registry, registry.get("champion_methodology_id"))
    gate = (challenger.get("validation_results") or {}).get(
        "latest_gate_evaluation"
    ) or {}
    condition_rows = _flatten_conditions(gate)
    passed = sum(1 for item in condition_rows if item["passed"])
    portfolio_path = "/data/investments/portfolio_10k.json"
    if (
        controller in {"PROBATIONARY_CONTROL", "ACTIVE_PAPER_CONTROL"}
        and paper_portfolio_available
    ):
        portfolio_path = "/data/portfolio10k/paper_portfolio.json"
    decisions = []
    for item in pending.get("decisions", []) or []:
        decisions.append(
            {
                key: item.get(key)
                for key in (
                    "decision_id",
                    "generated_at",
                    "action",
                    "instrument",
                    "replacement_instrument",
                    "expected_benefit",
                    "expected_risk",
                    "confidence",
                    "rationale_pl",
                    "rationale_en",
                    "status",
                )
            }
        )
    candidates = []
    for item in analysis.get("candidates", []) or []:
        candidates.append(
            {
                key: item.get(key)
                for key in (
                    "instrument_id",
                    "broker_symbol",
                    "label",
                    "final_score",
                    "risk_adjusted_score",
                    "confidence_score",
                    "expected_return_base",
                    "expected_drawdown",
                    "eligible_for_rotation",
                    "exclusion_reasons",
                )
            }
        )
    shadow_stats = dict(shadow.get("statistics") or shadow)
    history = [
        {
            key: item.get(key)
            for key in (
                "promotion_id",
                "previous_status",
                "new_status",
                "evaluated_at",
                "all_conditions_passed",
                "reason",
            )
        }
        for item in (promotion_history.get("records", []) or [])[-12:]
    ]
    fallback_reason = None
    if controller in {"SAFE_MODE", "DEGRADED", "SUSPENDED", "FALLBACK_BASELINE"}:
        fallback_reason = (
            gate.get("reason")
            or ", ".join(operational.get("safe_mode_reasons") or [])
            or "Safety controls stopped BRACE decisions."
        )
    snapshot = {
        "schema_version": "1.0.0",
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "methodology_version": challenger.get("version"),
        "data_freshness": operational.get("data_freshness", "unknown"),
        "source_metadata": {
            "publisher": "brace_portfolio_publish.py",
            "paper_only": True,
            "real_broker_connected": False,
        },
        "controller_status": controller,
        "display_status": (
            "ACTIVE_BASELINE + BRACE_SHADOW"
            if controller == "ACTIVE_BASELINE" and challenger.get("status") == "SHADOW"
            else controller
        ),
        "portfolio_data_path": portfolio_path,
        "champion": {
            "methodology_id": champion.get("methodology_id"),
            "version": champion.get("version"),
            "status": champion.get("status"),
        },
        "baseline": {
            "methodology_id": baseline.get("methodology_id"),
            "version": baseline.get("version"),
            "status": baseline.get("status"),
        },
        "challenger": {
            "methodology_id": challenger.get("methodology_id"),
            "version": challenger.get("version"),
            "status": challenger.get("status"),
        },
        "promotion_progress": {
            "passed": passed,
            "total": len(condition_rows),
            "percentage": round(100 * passed / len(condition_rows), 1)
            if condition_rows
            else 0.0,
            "conditions": condition_rows,
            "remaining": [
                item["condition"] for item in condition_rows if not item["passed"]
            ],
            "reason": gate.get("reason"),
        },
        "shadow": {
            "calendar_days": shadow_stats.get("calendar_days", 0),
            "decisions": shadow_stats.get("decisions", 0),
            "completed_trades": shadow_stats.get("completed_trades", 0),
            "shadow_return": shadow_stats.get("shadow_return"),
            "baseline_return": shadow_stats.get("baseline_return"),
            "shadow_risk_adjusted_return": shadow_stats.get(
                "shadow_risk_adjusted_return"
            ),
            "baseline_risk_adjusted_return": shadow_stats.get(
                "baseline_risk_adjusted_return"
            ),
        },
        "risk": {
            "status": analysis.get("risk_status", "MONITORED"),
            "expected_drawdown": analysis.get("expected_drawdown"),
            "current_drawdown": analysis.get("current_drawdown"),
            "annual_turnover": analysis.get("annual_turnover"),
            "safe_mode": bool(operational.get("safe_mode")),
            "safe_mode_reasons": list(operational.get("safe_mode_reasons") or []),
        },
        "target": {
            "target_annual_return": config.target_annual_return,
            "expected_annual_return": analysis.get("expected_annual_return"),
            "realized_annualized_return": analysis.get("realized_annualized_return"),
            "probability_of_reaching_target": analysis.get(
                "probability_of_reaching_target"
            ),
            "target_shortfall": analysis.get("target_shortfall"),
            "required_risk_to_target": analysis.get("required_risk_to_target"),
            "status": _target_status(analysis, config),
            "guaranteed": False,
        },
        "last_incremental_learning": analysis.get("last_incremental_learning"),
        "last_research_run": analysis.get("last_research_run"),
        "next_scheduled_analysis": analysis.get("next_scheduled_analysis"),
        "fallback_reason": fallback_reason,
        "promotion_history": history,
        "candidates": candidates[:10],
        "pending_decisions": decisions[:10],
        "policy": public_policy(config),
        "disclaimer_pl": (
            "To autonomiczny eksperyment analityczny i portfel modelowy. "
            "Nie jest to rachunek maklerski ani rekomendacja inwestycyjna."
        ),
        "disclaimer_en": (
            "This is an autonomous analytical experiment and a model portfolio. "
            "It is not a brokerage account or investment advice."
        ),
    }
    snapshot["content_sha256"] = canonical_sha256(
        {key: value for key, value in snapshot.items() if key != "content_sha256"}
    )
    return snapshot


def _remaining(snapshot: Mapping[str, Any]) -> str:
    rows = (snapshot.get("promotion_progress") or {}).get("remaining") or []
    return ", ".join(str(item).replace("_", " ") for item in rows) or "none"


def weekly_report(snapshot: Mapping[str, Any], lang: str) -> str:
    pl = lang == "pl"
    title = "# Tygodniowy raport BRACE Portfolio Engine" if pl else "# BRACE Portfolio Engine weekly report"
    labels = (
        {
            "status": "Aktualny status",
            "champion": "Champion",
            "challenger": "Challenger",
            "progress": "Postep awansu",
            "remaining": "Warunki pozostale do awansu",
            "risk": "Stan ryzyka",
            "target": "Cel 10% rocznie",
            "decisions": "Decyzje oczekujace",
            "note": "Uwaga",
        }
        if pl
        else {
            "status": "Current status",
            "champion": "Champion",
            "challenger": "Challenger",
            "progress": "Promotion progress",
            "remaining": "Remaining promotion gates",
            "risk": "Risk state",
            "target": "10% annual target",
            "decisions": "Pending decisions",
            "note": "Note",
        }
    )
    champion = snapshot.get("champion") or {}
    challenger = snapshot.get("challenger") or {}
    progress = snapshot.get("promotion_progress") or {}
    risk = snapshot.get("risk") or {}
    target = snapshot.get("target") or {}
    return "\n".join(
        [
            title,
            "",
            f"- **{labels['status']}:** {snapshot.get('display_status')}",
            f"- **{labels['champion']}:** {champion.get('methodology_id')} {champion.get('version')}",
            f"- **{labels['challenger']}:** {challenger.get('methodology_id')} {challenger.get('version')} ({challenger.get('status')})",
            f"- **{labels['progress']}:** {progress.get('passed', 0)}/{progress.get('total', 0)} ({progress.get('percentage', 0)}%)",
            f"- **{labels['remaining']}:** {_remaining(snapshot)}",
            f"- **{labels['risk']}:** {risk.get('status')} / safe mode: {risk.get('safe_mode')}",
            f"- **{labels['target']}:** {target.get('status')}",
            f"- **{labels['decisions']}:** {len(snapshot.get('pending_decisions') or [])}",
            "",
            f"**{labels['note']}:** "
            + (
                "BRACE dziala wylacznie na portfelu modelowym; nie laczy sie z brokerem."
                if pl
                else "BRACE operates only on a model portfolio and does not connect to a broker."
            ),
            "",
        ]
    )


def publish(
    snapshot: Mapping[str, Any],
    public_path: Path = PUBLIC_PATH,
    report_pl_path: Path = REPORT_PL_PATH,
    report_en_path: Path = REPORT_EN_PATH,
) -> None:
    write_json_atomic(public_path, snapshot)
    for path, language in ((report_pl_path, "pl"), (report_en_path, "en")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(weekly_report(snapshot, language), encoding="utf-8", newline="\n")

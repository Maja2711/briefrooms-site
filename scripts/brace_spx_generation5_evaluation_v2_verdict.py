#!/usr/bin/env python3
"""Apply the unchanged Generation 5 gate to Evaluation Protocol v2 evidence."""
from __future__ import annotations

from typing import Any, Mapping

import brace_spx_generation5_verdict as original_gate


def evaluate(
    report: Mapping[str, Any],
    audit: Mapping[str, Any],
    original_manifest: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> dict[str, Any]:
    verdict = original_gate.evaluate(report, audit, original_manifest, ledger)
    verdict["schema_version"] = "2.0.0"
    verdict["evaluation_protocol_version"] = 2
    verdict["evaluation_engine_signature"] = report.get("evaluation_engine_signature")
    verdict["candidate_parameters_changed"] = False
    verdict["gate_thresholds_changed"] = False
    verdict["family_pbo"] = dict((report.get("family_multiple_testing") or {}).get("pbo") or {})
    verdict["rank_stability"] = dict(report.get("rank_stability") or {})
    verdict["selected_stability"] = dict(report.get("selected_stability") or {})
    verdict["bootstrap_uncertainty"] = dict(report.get("bootstrap_uncertainty") or {})
    verdict["v1_comparison"] = dict(report.get("comparison_to_evaluation_v1") or {})
    verdict["policy"]["evaluation_protocol_repair_only"] = True
    verdict["policy"]["generation5_v1_evidence_preserved"] = True
    return verdict

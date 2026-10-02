#!/usr/bin/env python3
"""Collect primary-source news/macro observations and feed Belief Core in shadow mode."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from belief_core import BeliefCore, iso_z, parse_time
from belief_core_live import (
    AUTOMATIC_TUNING_ENABLED,
    BELIEFS,
    MODE,
    POLICY_OUTPUT_ENABLED,
    TRADE_EXECUTION_ENABLED,
    append_observations,
    load_scheduler,
    save_scheduler,
)
from belief_llm_interpreter import GeminiEvidenceInterpreter
from belief_macro_calendar_adapter import MacroEventCalendarAdapter
from belief_macro_data_adapter import MacroDataAdapter
from belief_macro_expectations_adapter import MacroExpectationsAdapter
from belief_news_event_adapter import NewsEventAdapter

EVENT_SEEN_LIMIT = 4000
MACRO_LLM_SEEN_LIMIT = 1000
MACRO_LLM_METRICS = {
    "total_nonfarm_payroll_level",
    "unemployment_rate",
    "cpi_index_sa",
}


def _primary_news_ids(observations) -> list[str]:
    return [
        row.observation_id
        for row in observations
        if row.adapter == "news_event" and row.metric == "primary_event_document"
    ]


def _macro_llm_key(observation) -> str:
    metadata = observation.metadata if isinstance(observation.metadata, dict) else dict(observation.metadata or {})
    period = str(metadata.get("data_period") or "")
    return f"{observation.adapter}:{observation.metric}:{period}:{observation.source_ref}"


def _macro_llm_candidates(observations):
    return [
        row
        for row in observations
        if row.status == "ok"
        and (
            (
                row.adapter == "macro_data"
                and row.source_type == "primary"
                and row.metric in MACRO_LLM_METRICS
            )
            or (
                row.adapter == "macro_expectations"
                and row.metric == "macro_expectation_distribution"
            )
        )
    ]


def run_external_cycle(
    state_dir: Path,
    now: datetime,
    *,
    news_adapter: NewsEventAdapter,
    macro_adapter: MacroEventCalendarAdapter,
    macro_data_adapter: Optional[MacroDataAdapter] = None,
    macro_expectations_adapter: Optional[MacroExpectationsAdapter] = None,
) -> Dict[str, Any]:
    if TRADE_EXECUTION_ENABLED or POLICY_OUTPUT_ENABLED or AUTOMATIC_TUNING_ENABLED or MODE != "shadow":
        raise RuntimeError("Belief Core external adapter safety invariant violated")

    state_dir.mkdir(parents=True, exist_ok=True)
    core = BeliefCore(state_dir)
    core.register_beliefs(BELIEFS)
    scheduler = load_scheduler(state_dir)

    seen = list(scheduler.get("processed_event_observation_ids") or [])
    seen_set = set(str(value) for value in seen)

    news_result = news_adapter.run(now, seen_primary_observation_ids=tuple(seen_set))
    macro_result = macro_adapter.run(now)
    macro_data_result = (
        macro_data_adapter.run(now)
        if macro_data_adapter is not None
        else None
    )
    macro_expectations_result = (
        macro_expectations_adapter.run(now)
        if macro_expectations_adapter is not None
        else None
    )

    all_observations = list(news_result.observations) + list(macro_result.observations)
    all_evidence = list(news_result.evidence) + list(macro_result.evidence)
    if macro_data_result is not None:
        all_observations.extend(macro_data_result.observations)
        all_evidence.extend(macro_data_result.evidence)
    if macro_expectations_result is not None:
        all_observations.extend(macro_expectations_result.observations)
        all_evidence.extend(macro_expectations_result.evidence)

    # Interpret selected official BLS macro observations and sourced expectation
    # bundles once per data period/event.
    # This gives EUR/USD macro beliefs a sourced LLM interpretation path without
    # allowing Gemini to invent consensus/actual values.
    interpreter = news_adapter.interpreter
    macro_seen = list(scheduler.get("processed_macro_llm_keys") or [])
    macro_seen_set = set(str(value) for value in macro_seen)
    macro_llm_attempted = 0
    macro_llm_evidence = 0
    macro_llm_source_observations = []
    if macro_data_result is not None:
        macro_llm_source_observations.extend(macro_data_result.observations)
    if macro_expectations_result is not None:
        macro_llm_source_observations.extend(macro_expectations_result.observations)
    if interpreter is not None and interpreter.available and macro_llm_source_observations:
        for observation in _macro_llm_candidates(macro_llm_source_observations):
            key = _macro_llm_key(observation)
            if key in macro_seen_set:
                continue
            macro_llm_attempted += 1
            result = interpreter.interpret(observation)
            if result is not None:
                all_observations.append(result.observation)
                all_evidence.append(result.evidence)
                macro_llm_evidence += 1
            macro_seen.append(key)
            macro_seen_set.add(key)
        macro_seen = macro_seen[-MACRO_LLM_SEEN_LIMIT:]
    scheduler["processed_macro_llm_keys"] = macro_seen

    written = append_observations(state_dir, all_observations)
    if all_evidence:
        core.ingest(all_evidence)
        core.recompute(now)

    # Mark a source document as processed only after an actual LLM-capable run.
    # A valid `none` classification is still a completed interpretation and must
    # not consume Gemini quota again on every hourly retry. If Gemini is down,
    # leave the document unprocessed so a later run can classify it.
    if interpreter is not None and interpreter.available:
        for observation_id in _primary_news_ids(news_result.observations):
            if observation_id not in seen_set:
                seen.append(observation_id)
                seen_set.add(observation_id)
        seen = seen[-EVENT_SEEN_LIMIT:]

    scheduler["processed_event_observation_ids"] = seen
    scheduler["schema_version"] = 2
    scheduler["last_external_run_at"] = iso_z(now)
    primary_source_counts: Dict[str, int] = {}
    for observation in news_result.observations:
        if observation.metric != "primary_event_document":
            continue
        source_class = str(observation.metadata.get("primary_source_class") or observation.metadata.get("category_hint") or "other")
        primary_source_counts[source_class] = primary_source_counts.get(source_class, 0) + 1
    primary_source_status = (
        news_adapter.primary_source_status()
        if hasattr(news_adapter, "primary_source_status")
        else {}
    )
    scheduler["last_external_status"] = {
        "mode": MODE,
        "news_observations": len(news_result.observations),
        "news_evidence": len(news_result.evidence),
        "macro_observations": len(macro_result.observations),
        "macro_evidence": len(macro_result.evidence),
        "macro_data_observations": len(macro_data_result.observations) if macro_data_result else 0,
        "macro_data_evidence": len(macro_data_result.evidence) if macro_data_result else 0,
        "macro_expectations_observations": len(macro_expectations_result.observations) if macro_expectations_result else 0,
        "macro_expectations_evidence": len(macro_expectations_result.evidence) if macro_expectations_result else 0,
        "observations_written": written,
        "evidence_ingested": len(all_evidence),
        "llm_available": bool(interpreter and interpreter.available),
        "llm_model": interpreter.model if interpreter else "",
        "macro_llm_attempted": macro_llm_attempted,
        "macro_llm_evidence": macro_llm_evidence,
        "processed_macro_llm_keys": len(macro_seen),
        "processed_event_observation_ids": len(seen),
        "primary_source_counts": primary_source_counts,
        "primary_source_status": primary_source_status,
    }

    save_scheduler(state_dir, scheduler)
    core.save()
    core.write_dashboard(now)
    return scheduler["last_external_status"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run News/Event + Macro Calendar + Primary Macro Data Belief Core shadow adapters"
    )
    parser.add_argument(
        "--state-dir",
        default=os.environ.get("BELIEF_CORE_STATE_DIR", ".belief_runtime/core"),
    )
    parser.add_argument("--now", help="ISO timestamp override")
    parser.add_argument("--disable-sec", action="store_true")
    parser.add_argument("--disable-company-primary", action="store_true")
    args = parser.parse_args()

    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    interpreter = GeminiEvidenceInterpreter()
    news = NewsEventAdapter(
        interpreter=interpreter,
        enable_sec=False if args.disable_sec else None,
        enable_company_primary=False if args.disable_company_primary else None,
    )
    macro = MacroEventCalendarAdapter()
    macro_data = MacroDataAdapter()
    macro_expectations = MacroExpectationsAdapter()
    status = run_external_cycle(
        Path(args.state_dir),
        now,
        news_adapter=news,
        macro_adapter=macro,
        macro_data_adapter=macro_data,
        macro_expectations_adapter=macro_expectations,
    )
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

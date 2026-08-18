#!/usr/bin/env python3
"""Collect primary-source news/macro observations and feed Belief Core in shadow mode."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

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
from belief_news_event_adapter import NewsEventAdapter

EVENT_SEEN_LIMIT = 4000


def _primary_news_ids(observations) -> list[str]:
    return [
        row.observation_id
        for row in observations
        if row.adapter == "news_event" and row.metric == "primary_event_document"
    ]


def run_external_cycle(
    state_dir: Path,
    now: datetime,
    *,
    news_adapter: NewsEventAdapter,
    macro_adapter: MacroEventCalendarAdapter,
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

    all_observations = list(news_result.observations) + list(macro_result.observations)
    all_evidence = list(news_result.evidence) + list(macro_result.evidence)

    written = append_observations(state_dir, all_observations)
    if all_evidence:
        core.ingest(all_evidence)
        core.recompute(now)

    # Mark a source document as processed only after an actual LLM-capable run.
    # A valid `none` classification is still a completed interpretation and must
    # not consume Gemini quota again on every hourly retry. If Gemini is down,
    # leave the document unprocessed so a later run can classify it.
    interpreter = news_adapter.interpreter
    if interpreter is not None and interpreter.available:
        for observation_id in _primary_news_ids(news_result.observations):
            if observation_id not in seen_set:
                seen.append(observation_id)
                seen_set.add(observation_id)
        seen = seen[-EVENT_SEEN_LIMIT:]

    scheduler["processed_event_observation_ids"] = seen
    scheduler["schema_version"] = 2
    scheduler["last_external_run_at"] = iso_z(now)
    scheduler["last_external_status"] = {
        "mode": MODE,
        "news_observations": len(news_result.observations),
        "news_evidence": len(news_result.evidence),
        "macro_observations": len(macro_result.observations),
        "macro_evidence": len(macro_result.evidence),
        "observations_written": written,
        "evidence_ingested": len(all_evidence),
        "llm_available": bool(interpreter and interpreter.available),
        "llm_model": interpreter.model if interpreter else "",
        "processed_event_observation_ids": len(seen),
    }

    save_scheduler(state_dir, scheduler)
    core.save()
    core.write_dashboard(now)
    return scheduler["last_external_status"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run News/Event + Macro Calendar Belief Core shadow adapters"
    )
    parser.add_argument(
        "--state-dir",
        default=os.environ.get("BELIEF_CORE_STATE_DIR", ".belief_runtime/core"),
    )
    parser.add_argument("--now", help="ISO timestamp override")
    parser.add_argument("--disable-sec", action="store_true")
    args = parser.parse_args()

    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    interpreter = GeminiEvidenceInterpreter()
    news = NewsEventAdapter(
        interpreter=interpreter,
        enable_sec=False if args.disable_sec else None,
    )
    macro = MacroEventCalendarAdapter()
    status = run_external_cycle(
        Path(args.state_dir),
        now,
        news_adapter=news,
        macro_adapter=macro,
    )
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

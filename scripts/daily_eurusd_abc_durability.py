#!/usr/bin/env python3
"""PR20 durability entrypoint for Daily EUR/USD A/B/C live-shadow state.

This wrapper reuses the PR19.2 research_state_durability implementation and
registers the PR20 A/B/C layer without changing the canonical registry used by
older research layers. The shared A/B/C learning state/report are sealed in the
same artifact so learning memory cannot drift away from the decisions/outcomes
that produced it.
"""
from __future__ import annotations

from scripts import research_state_durability as rsd

LAYER_ID = "pr20_eurusd_abc"
PRODUCER_WORKFLOW = "Daily EURUSD A-B-C Live Shadow"
PRIMARY_ARTIFACT = "daily-eurusd-abc-live-shadow-state"
ARCHIVE_FILENAME = "daily-eurusd-abc-live-shadow-state.tgz"


def register_layer() -> None:
    rsd.LAYERS[LAYER_ID] = rsd.LayerSpec(
        LAYER_ID,
        PRODUCER_WORKFLOW,
        PRIMARY_ARTIFACT,
        ARCHIVE_FILENAME,
        (
            "EURUSD_DAILY_ABC_STATE.json",
            "EURUSD_DAILY_ABC_REPORT.json",
            "EURUSD_DAILY_ABC_LEARNING.json",
            "EURUSD_DAILY_ABC_LEARNING_REPORT.json",
        ),
    )


register_layer()


def main() -> int:
    return rsd.main()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Forward-only outcome settler for the Daily EUR/USD A/B/C live-shadow experiment.

The settler never creates a new capture and never changes a frozen A/B/C decision.
It only appends matured forward outcomes to existing captures, rebuilds the private
research report, and leaves files untouched when no horizon has matured yet.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from belief_market_data_adapter import YahooChartClient
import daily_eurusd_experiment_v12 as abc

EURUSD = "EURUSD=X"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _frozen_identity(state: Mapping[str, Any]) -> tuple[tuple[str, str, str, str], ...]:
    rows: list[tuple[str, str, str, str]] = []
    for capture in state.get("captures") or []:
        rows.append((
            str(capture.get("capture_id") or ""),
            str(capture.get("decision_sha256") or ""),
            str(capture.get("market_observed_at") or ""),
            str(capture.get("engine_version") or ""),
        ))
    return tuple(rows)


def _resolved_outcomes(state: Mapping[str, Any]) -> int:
    count = 0
    for capture in state.get("captures") or []:
        for horizon in (capture.get("horizons") or {}).values():
            if isinstance(horizon, Mapping) and horizon.get("outcome") is not None:
                count += 1
    return count


def settle_state(
    state_dir: Path,
    *,
    client: YahooChartClient | None = None,
) -> tuple[bool, int]:
    state_path = state_dir / "EURUSD_DAILY_ABC_STATE.json"
    report_path = state_dir / "EURUSD_DAILY_ABC_REPORT.json"
    state = _load(state_path)
    abc.validate_state(state)

    before_identity = _frozen_identity(state)
    before_resolved = _resolved_outcomes(state)

    client = client or YahooChartClient(timeout=15)
    rows_30m = sorted(client.bars(EURUSD, "10d", "30m"), key=lambda bar: bar.timestamp)
    if len(rows_30m) < 60:
        raise ValueError("EUR/USD 30-minute market data unavailable or insufficient for outcome settlement")

    settled = abc.resolve_outcomes(state, rows_30m)
    abc.validate_state(settled)

    after_identity = _frozen_identity(settled)
    if after_identity != before_identity:
        raise ValueError("PR23 settler attempted to mutate frozen capture identity")

    after_resolved = _resolved_outcomes(settled)
    resolved_delta = after_resolved - before_resolved
    if resolved_delta < 0:
        raise ValueError("PR23 settler cannot remove previously resolved outcomes")
    if resolved_delta == 0:
        # resolve_outcomes updates bookkeeping timestamps even when nothing matured.
        # Do not persist that no-op so we avoid unnecessary artifacts/public commits.
        return False, 0

    report = abc.build_report(settled)
    state_path.write_text(json.dumps(settled, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    abc.validate_files(state_dir)
    return True, resolved_delta


def main() -> int:
    parser = argparse.ArgumentParser(description="Settle matured Daily EUR/USD A/B/C outcomes without creating captures")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    state_dir = Path(args.state_dir)

    if args.validate:
        abc.validate_files(state_dir)
        state = _load(state_dir / "EURUSD_DAILY_ABC_STATE.json")
        print("EURUSD_ABC_SETTLER_OK", len(state.get("captures") or []), _resolved_outcomes(state))
        return 0

    changed, resolved_delta = settle_state(state_dir)
    print(f"ABC_STATE_CHANGED={'true' if changed else 'false'}")
    print(f"ABC_RESOLVED_DELTA={resolved_delta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

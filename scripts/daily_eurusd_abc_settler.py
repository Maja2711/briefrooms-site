#!/usr/bin/env python3
"""Forward-only settler for Daily EUR/USD A/B/C research state.

PR23 point-forward outcomes remain intact. PR24 additionally updates the virtual
trade path of prospective v1.3 captures from 1-minute EUR/USD OHLC. The settler
never creates captures, never changes frozen A/B/C decisions or trade plans, and
leaves private files byte-identical when neither layer changes.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from belief_market_data_adapter import YahooChartClient
import daily_eurusd_experiment_v13 as abc

EURUSD = "EURUSD=X"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _frozen_identity(state: Mapping[str, Any]) -> tuple[tuple[str, str, str, str, str], ...]:
    rows: list[tuple[str, str, str, str, str]] = []
    for capture in state.get("captures") or []:
        rows.append((
            str(capture.get("capture_id") or ""),
            str(capture.get("decision_sha256") or ""),
            str(capture.get("market_observed_at") or ""),
            str(capture.get("engine_version") or ""),
            str(capture.get("trade_plan_sha256") or ""),
        ))
    return tuple(rows)


def _resolved_outcomes(state: Mapping[str, Any]) -> int:
    return sum(
        1
        for capture in state.get("captures") or []
        for horizon in (capture.get("horizons") or {}).values()
        if isinstance(horizon, Mapping) and horizon.get("outcome") is not None
    )


def _open_v13_trade_paths(state: Mapping[str, Any]) -> bool:
    for capture in state.get("captures") or []:
        if str(capture.get("engine_version")) != abc.ENGINE_VERSION:
            continue
        for row in ((capture.get("trade_path") or {}).get("arms") or {}).values():
            if isinstance(row, Mapping) and row.get("status") == "OPEN":
                return True
    return False


def settle_state(
    state_dir: Path,
    *,
    client: YahooChartClient | None = None,
    as_of: datetime | None = None,
) -> tuple[bool, int]:
    state_path = state_dir / "EURUSD_DAILY_ABC_STATE.json"
    report_path = state_dir / "EURUSD_DAILY_ABC_REPORT.json"
    state = _load(state_path)
    abc.validate_state(state)

    before_identity = _frozen_identity(state)
    before_resolved = _resolved_outcomes(state)
    before_trade_digest = abc.trade_path_digest(state)

    client = client or YahooChartClient(timeout=15)
    rows_30m = sorted(client.bars(EURUSD, "10d", "30m"), key=lambda bar: bar.timestamp)
    if len(rows_30m) < 60:
        raise ValueError("EUR/USD 30-minute market data unavailable or insufficient for outcome settlement")

    settled = abc.resolve_outcomes(state, rows_30m)

    trade_path_checked = False
    if _open_v13_trade_paths(settled):
        rows_1m = sorted(client.bars(EURUSD, "5d", "1m"), key=lambda bar: bar.timestamp)
        if not rows_1m:
            raise ValueError("EUR/USD 1-minute market data unavailable for PR24 trade-path settlement")
        settled = abc.update_trade_paths(
            settled,
            rows_1m,
            as_of=(as_of or datetime.now(timezone.utc)),
        )
        trade_path_checked = True

    abc.validate_state(settled)
    after_identity = _frozen_identity(settled)
    if after_identity != before_identity:
        raise ValueError("settler attempted to mutate frozen capture or trade-plan identity")

    after_resolved = _resolved_outcomes(settled)
    resolved_delta = after_resolved - before_resolved
    if resolved_delta < 0:
        raise ValueError("settler cannot remove previously resolved point outcomes")

    trade_changed = abc.trade_path_digest(settled) != before_trade_digest
    if resolved_delta == 0 and not trade_changed:
        return False, 0

    report = abc.build_report(settled)
    state_path.write_text(json.dumps(settled, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    abc.validate_files(state_dir)
    return True, resolved_delta


def main() -> int:
    parser = argparse.ArgumentParser(description="Settle Daily EUR/USD A/B/C point outcomes and PR24 virtual trade paths")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    state_dir = Path(args.state_dir)

    if args.validate:
        abc.validate_files(state_dir)
        state = _load(state_dir / "EURUSD_DAILY_ABC_STATE.json")
        print("EURUSD_ABC_SETTLER_OK", len(state.get("captures") or []), _resolved_outcomes(state))
        return 0

    before = _load(state_dir / "EURUSD_DAILY_ABC_STATE.json")
    before_trade = abc.trade_path_digest(before)
    changed, resolved_delta = settle_state(state_dir)
    after = _load(state_dir / "EURUSD_DAILY_ABC_STATE.json") if changed else before
    trade_changed = abc.trade_path_digest(after) != before_trade
    print(f"ABC_STATE_CHANGED={'true' if changed else 'false'}")
    print(f"ABC_RESOLVED_DELTA={resolved_delta}")
    print(f"ABC_TRADE_PATH_CHANGED={'true' if trade_changed else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

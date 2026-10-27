#!/usr/bin/env python3
"""Read-only provider close receipts for main-owned native replay evidence.

Only benchmark/provider metadata is fetched. Existing production snapshots are
read verbatim; no admission, lifecycle, normalization or settlement runs here.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts import stock_trading_component_champion as champion
    from scripts import stock_trading_portfolio as portfolio
    from scripts import stock_trading_v2_production_bridge as bridge
    from scripts import stock_trading_v2_production_replay as replay
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_component_champion as champion
    import stock_trading_portfolio as portfolio
    import stock_trading_v2_production_bridge as bridge
    import stock_trading_v2_production_replay as replay

FRAME_ROOT = replay.FRAME_ROOT


def _raw_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("production snapshot must be a JSON object")
    return payload


def capture_close(
    root: Path = FRAME_ROOT, now: datetime | None = None, *,
    policy_path: Path | None = None, state_path: Path | None = None,
    runtime_path: Path | None = None, manifest_path: Path | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Append actual POST/CLOSED receipts; absence is pending, never a calendar.

    Optional snapshot paths support isolated validation. Runtime callers share
    the stock writer workflow lock so the read-only snapshot set is coherent.
    """
    real_clock = now is None
    started = now or datetime.now(timezone.utc)
    if started.tzinfo is None:
        raise ValueError("session-close collection requires timezone-aware time")
    snapshots = {
        "policy": _raw_snapshot(policy_path or portfolio.POLICY_PATH),
        "initial_state": _raw_snapshot(state_path or bridge.PORTFOLIO_PATH),
        "runtime": _raw_snapshot(runtime_path or bridge.STATE_PATH),
        "manifest": _raw_snapshot(manifest_path or champion.MANIFEST_PATH),
        "bridge_config": _raw_snapshot(config_path or bridge.CONFIG_PATH),
    }
    benchmarks, sessions = bridge._capture_benchmarks(started)
    collected = datetime.now(timezone.utc) if real_clock else started
    valid_benchmarks, valid_sessions, diagnostics = {}, {}, []
    for market, session in sessions.items():
        benchmark = benchmarks.get(market) or {}
        try:
            opening, closing = replay._time(session["regular_start"]), replay._time(session["regular_end"])
            received = replay._time(session["received_at"])
            observed = replay._time(benchmark["observed_at"])
            benchmark_received = replay._time(benchmark["received_at"])
            price = portfolio._float(benchmark.get("price"))
            eligible = (market in portfolio.MARKET_TZ and session.get("state") in {"POST", "CLOSED"}
                and session.get("closed") is True and bool(session.get("source")) and bool(session.get("provider"))
                and bool(benchmark.get("provider")) and price is not None and price > 0
                and opening < closing <= received <= collected and observed <= benchmark_received <= collected
                and closing <= benchmark_received
                and session.get("date") == opening.astimezone(portfolio.MARKET_TZ[market]).date().isoformat())
        except (ValueError, KeyError, TypeError):
            eligible = False
        if not eligible:
            diagnostics.append({"market": market, "reason": "sourced_closed_session_receipt_unavailable"})
            continue
        valid_benchmarks[market] = benchmark
        valid_sessions[market] = session
    if not valid_sessions:
        return {"status": "PENDING", "reason": "sourced_closed_session_receipt_unavailable", "diagnostics": diagnostics, "production_mutated": False}
    receipt = replay.capture_frame(root, kind="SESSION_CLOSE", observed_at=collected, recorded_at=collected,
        decision_started_at=started, benchmark=valid_benchmarks, market_sessions=valid_sessions, **snapshots)
    return {"status": "CAPTURED", "frame_id": receipt["frame_id"], "path": receipt["path"],
            "markets": sorted(valid_sessions), "diagnostics": diagnostics, "production_mutated": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=FRAME_ROOT)
    args = parser.parse_args()
    print(json.dumps(capture_close(root=args.root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

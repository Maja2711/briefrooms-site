#!/usr/bin/env python3
"""Read-only provider close receipts for main-owned native replay evidence.

Benchmarks, provider session metadata and raw close bars are fetched. Existing
snapshots are read verbatim; no admission, lifecycle or settlement runs here.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import stock_trading_portfolio as portfolio
    from scripts import stock_trading_v2_production_bridge as bridge
    from scripts import stock_trading_v2_production_replay as replay
    from scripts import stock_trading_session_calendar as calendar
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_portfolio as portfolio
    import stock_trading_v2_production_bridge as bridge
    import stock_trading_v2_production_replay as replay
    import stock_trading_session_calendar as calendar

FRAME_ROOT = replay.FRAME_ROOT
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "data/investments/stock_trading_champion_manifest.json"


def _raw_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("production snapshot must be a JSON object")
    return payload


def close_observation(symbol: str, market: str, session: Mapping[str, Any], *,
                      now: datetime, collection_clock: Any = None) -> dict[str, Any]:
    """Read native formed 5m bars for one sourced regular close, without trades."""
    opening, closing = replay._time(session["regular_start"]), replay._time(session["regular_end"])
    if (market not in portfolio.MARKET_TZ or session.get("closed") is not True
            or session.get("state") not in {"POST", "CLOSED"} or not session.get("source")
            or not session.get("provider") or not opening < closing <= now):
        raise ValueError("sourced close bounds unavailable")
    chart = portfolio._chart(symbol, interval="5m", range_value="1d")
    received = collection_clock() if collection_clock is not None else now
    meta = chart.get("meta") or {}
    if (meta.get("symbol") != symbol or meta.get("currency") != portfolio.POSITION_CURRENCY[market]
            or (market == "GPW") != symbol.endswith(".WA")):
        raise ValueError("close chart issuer identity mismatch")
    quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    bars = []
    for index, value in enumerate(chart.get("timestamp") or []):
        try:
            start = datetime.fromtimestamp(int(value), timezone.utc)
            end = start + timedelta(minutes=5)
            high, low, close = (portfolio._float((quote.get(key) or [])[index]) for key in ("high", "low", "close"))
        except (IndexError, TypeError, ValueError, OverflowError, OSError):
            continue
        if not opening <= start < end <= closing <= received:
            continue
        if any(number is None or number <= 0 for number in (high, low, close)) or not low <= close <= high:
            continue
        bars.append({"start": start.isoformat(), "end": end.isoformat(), "interval_seconds": 300,
                     "high": high, "low": low, "close": close, "observed_at": received.isoformat(), "provider": "Yahoo"})
    bars.sort(key=lambda bar: bar["start"])
    return {"symbol": symbol, "market": market, "currency": meta["currency"], "provider": "Yahoo Finance chart",
            "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/" + symbol,
            "received_at": received.isoformat(), "source_bars": bars}


def capture_close(
    root: Path = FRAME_ROOT, now: datetime | None = None, *,
    policy_path: Path | None = None, state_path: Path | None = None,
    runtime_path: Path | None = None, manifest_path: Path | None = None,
    config_path: Path | None = None,
    watch_path: Path | None = None, observation_fetcher: Any = None,
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
        "manifest": _raw_snapshot(manifest_path or MANIFEST_PATH),
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
                and session.get("date") == collected.astimezone(portfolio.MARKET_TZ[market]).date().isoformat()
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
    close_observations = {}
    for market, session in valid_sessions.items():
        # Presence of even an empty market mapping designates an actual close
        # collection attempt. Incidental after-hours admission metadata does not.
        observed = close_observations[market] = {}
        positions = (((snapshots["initial_state"].get("markets") or {}).get(market) or {}).get("open_positions") or [])
        symbols = {str(position.get("symbol") or "") for position in positions
                   if isinstance(position, Mapping) and str(position.get("status") or "OPEN").upper() == "OPEN"}
        try:
            symbols.update(watch["symbol"] for watch in replay.active_replay_watches(
                now=collected, market=market, root=watch_path or replay.DEFAULT_WATCH_PATH))
        except (ValueError, TypeError, KeyError) as exc:
            diagnostics.append({"market": market, "reason": "close_watch_receipts_invalid", "detail": str(exc)[:160]})
        def fetch(symbol: str) -> tuple[str, Any, Any]:
            try:
                value = (observation_fetcher or close_observation)(symbol, market, session, now=collected,
                    collection_clock=(lambda: datetime.now(timezone.utc)) if real_clock else None)
                return symbol, value, None
            except Exception as exc:
                return symbol, None, type(exc).__name__
        # Independent read-only requests, bounded even with a full watch book.
        # map preserves symbol order; capture time is recorded after all fetches.
        with ThreadPoolExecutor(max_workers=6) as pool:
            for symbol, value, error in pool.map(fetch, sorted(symbols - {""})):
                if error is not None:
                    diagnostics.append({"market": market, "symbol": symbol, "reason": "native_close_bar_unavailable", "detail": error})
                else:
                    observed[symbol] = value
    collected = datetime.now(timezone.utc) if real_clock else started
    receipt = replay.capture_frame(root, kind="SESSION_CLOSE", observed_at=collected, recorded_at=collected,
        decision_started_at=started, benchmark=valid_benchmarks, market_sessions=valid_sessions,
        close_observations=close_observations, collection_coverage={"close_collection_diagnostics": diagnostics},
        session_calendar=calendar.load_receipts(now=started), **snapshots)
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

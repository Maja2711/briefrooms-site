#!/usr/bin/env python3
"""Safe orchestration for the BriefRooms 10K model portfolio.

A repository push may migrate the one invalid legacy initialization, but future
code deployments must never reset an already valid active or partially active
portfolio.
"""
from __future__ import annotations

import argparse

import portfolio_10k_weekly as base
import portfolio_10k_weekly_v2 as cost
import portfolio_10k_weekly_v3 as execution


def run(mode: str) -> None:
    data = base.load_json(base.DATA_PATH)
    base.validate_config(data)
    now = execution.utc_now()
    migrated = execution.migrate_invalid_initialization(data, now)

    if mode == "migrate":
        if migrated:
            execution.save_pending(data, now)
            print("Portfolio 10K migrated to pending synchronized entry")
        else:
            print("Portfolio 10K requires no migration; current status preserved")
        return

    if data.get("status") in {"planned", "pending_open"}:
        if not execution.is_common_entry_window(now):
            execution.save_pending(data, now)
            print("Portfolio 10K remains pending: common entry window is closed")
            return
        target = execution.target_timestamp(now)
        try:
            daily = execution.fetch_daily_markets(data)
            quotes, fx_quotes = execution.fetch_entry_quotes(data, target)
            execution.initialize_from_intraday(data, quotes, fx_quotes, target)
            summary = cost.update_current_state(data, execution.entry_markets(daily, quotes), {})
            data.update(summary)
            data["last_market_session"] = target.date().isoformat()
            data["last_updated_at"] = base.now_local().isoformat(timespec="seconds")
            data["last_run_error"] = None
            base.upsert_snapshot(data, summary, target.date().isoformat())
            base.upsert_weekly_review(data, summary)
            base.write_json_atomic(base.DATA_PATH, data)
            print(
                "Portfolio 10K opened from synchronized intraday bars: "
                f"{summary['total_value_pln']:.2f} PLN"
            )
            return
        except Exception as exc:
            execution.save_pending(data, now, str(exc))
            raise

    if data.get("status") == "partial_open":
        # The dedicated staged-entry workflow owns completion of pending positions.
        # Most importantly, do not downgrade this state back to pending_open and do
        # not erase the valid live entries that have already been frozen.
        print("Portfolio 10K partially active; pending positions are handled by staged live entry")
        return

    if data.get("status") != "active":
        execution.save_pending(data, now, "Unsupported portfolio status")
        return

    if mode == "auto" and now.weekday() != 6:
        print("Portfolio 10K active; no weekly review due today")
        return

    try:
        markets = execution.fetch_daily_markets(data)
        summary = cost.update_current_state(data, markets, {})
        market_date = max(record.market_date for record in markets.values())
        data.update(summary)
        data["last_market_session"] = market_date
        data["last_updated_at"] = base.now_local().isoformat(timespec="seconds")
        data["last_run_error"] = None
        base.upsert_snapshot(data, summary, market_date)
        base.upsert_weekly_review(data, summary)
        base.write_json_atomic(base.DATA_PATH, data)
        print(f"Portfolio 10K reviewed for {market_date}: {summary['total_value_pln']:.2f} PLN")
    except Exception as exc:
        data["last_run_error"] = str(exc)
        data["last_updated_at"] = base.now_local().isoformat(timespec="seconds")
        base.write_json_atomic(base.DATA_PATH, data)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("auto", "initialize", "review", "migrate"), default="auto")
    args = parser.parse_args()
    run(args.mode)


if __name__ == "__main__":
    main()

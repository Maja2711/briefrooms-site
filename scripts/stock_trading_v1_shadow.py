#!/usr/bin/env python3
"""Run legacy Stock Trading v1 as a non-production shadow challenger.

The shadow uses the same market observations and portfolio mechanics as the
canonical book, but writes to an isolated state file and can never affect the
production portfolio.  V1 candidate generation remains useful evidence after
Champion Inversion, so it must be evaluated fairly: no production writeback and
no legacy score-veto inherited from the old Champion privilege.
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from scripts import stock_trading_portfolio as portfolio
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_portfolio as portfolio

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data/investments/stock_trading_v1_shadow_portfolio.json"
UTC = ZoneInfo("UTC")


def _shadow_policy() -> dict:
    policy = deepcopy(portfolio.load_policy())
    # Canonical production disables legacy candidate admission after Champion
    # Inversion.  The challenger must still admit its own candidates into its
    # isolated counterfactual book so that CASH opportunity cost is measurable.
    policy["legacy_candidate_admission_enabled"] = True
    for market in ("GPW", "US"):
        # Score is a ranking/conviction feature, not a hard veto.  Data quality,
        # liquidity, SL/TP geometry, R:R and conservative EV remain hard gates.
        policy["markets"][market]["minimum_entry_score"] = 0
    return policy


def _ensure_state(policy: dict) -> None:
    if STATE_PATH.exists():
        return
    now = datetime.now(UTC)
    state = portfolio.empty_state(now=now, policy=policy)
    state["engine"] = "v1"
    state["role"] = "SHADOW_CHALLENGER"
    state["production_decision_influence"] = False
    portfolio.save_state(state, STATE_PATH, now=now)


def run(markets: list[str]) -> dict:
    policy = _shadow_policy()
    _ensure_state(policy)
    state = portfolio.load_state(STATE_PATH, now=datetime.now(UTC), policy=policy)
    actions: list[dict] = []
    for market in markets:
        now = datetime.now(portfolio.MARKET_TZ[market])
        state, market_actions = portfolio.run_market(state, market, now=now, policy=policy)
        actions.extend(market_actions)
    state["engine"] = "v1"
    state["role"] = "SHADOW_CHALLENGER"
    state["production_decision_influence"] = False
    verified = portfolio.verify_state(state, policy)
    if verified["status"] != "OK":
        raise RuntimeError("v1 shadow invariant violation: " + "; ".join(verified["errors"]))
    portfolio.save_state(state, STATE_PATH, now=datetime.now(UTC))
    return {"engine": "v1", "role": "SHADOW_CHALLENGER", "actions": actions, **verified}


def verify() -> dict:
    policy = _shadow_policy()
    _ensure_state(policy)
    state = portfolio.load_state(STATE_PATH, now=datetime.now(UTC), policy=policy)
    result = portfolio.verify_state(state, policy)
    if state.get("production_decision_influence") is not False:
        result.setdefault("errors", []).append("shadow_must_not_influence_production")
        result["status"] = "ERROR"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["all", "GPW", "US"], default="all")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    markets = ["GPW", "US"] if args.market == "all" else [args.market]
    result = verify() if args.verify else run(markets)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())

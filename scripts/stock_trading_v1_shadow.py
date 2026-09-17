#!/usr/bin/env python3
"""Run legacy Stock Trading v1 as a fair, isolated shadow challenger.

V1 no longer inherits the defects of its former production path: there is no
arbitrary morning publication cutoff, score is ranking rather than a hard veto,
and rejecting the first company does not end the search.  The challenger writes
only to its own counterfactual portfolio and can never mutate production.
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from scripts import gpw_stock_candidate_shadow
    from scripts import stock_trading_portfolio as portfolio
    from scripts import us_stock_candidate_shadow
except ModuleNotFoundError:  # pragma: no cover
    import gpw_stock_candidate_shadow
    import stock_trading_portfolio as portfolio
    import us_stock_candidate_shadow

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data/investments/stock_trading_v1_shadow_portfolio.json"
UTC = ZoneInfo("UTC")


def _shadow_policy() -> dict:
    policy = deepcopy(portfolio.load_policy())
    policy["legacy_candidate_admission_enabled"] = True
    for market in ("GPW", "US"):
        # Score remains useful for ranking and conviction, but it may not veto a
        # setup that already passed the hard data/risk/evidence gates.
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


def _review_existing(state: dict, market: str, *, now: datetime, policy: dict) -> tuple[dict, list[dict]]:
    observations = {}
    audits: list[dict] = []
    for position in portfolio.open_positions(state, market):
        symbol = str(position.get("symbol") or "")
        try:
            observations[symbol] = portfolio._daily_observation(symbol, market, now)
        except Exception as exc:
            audits.append({
                "action": "observation_error",
                "market": market,
                "symbol": symbol,
                "error": f"{type(exc).__name__}:{str(exc)[:160]}",
            })
    state, review_audit = portfolio.review_market(
        state,
        market,
        observations=observations,
        now=now,
        policy=policy,
    )
    audits.extend(review_audit)
    return state, audits


def _generate_candidate(state: dict, market: str, *, now: datetime) -> dict:
    held = {str(row.get("symbol") or "").upper() for row in portfolio.open_positions(state, market)}
    if market == "US":
        return us_stock_candidate_shadow.generate(now, exclude_symbols=held)
    return gpw_stock_candidate_shadow.generate(now, exclude_symbols=held)


def run(markets: list[str]) -> dict:
    policy = _shadow_policy()
    _ensure_state(policy)
    state = portfolio.load_state(STATE_PATH, now=datetime.now(UTC), policy=policy)
    actions: list[dict] = []

    for market in markets:
        now = datetime.now(portfolio.MARKET_TZ[market])
        state, reviews = _review_existing(state, market, now=now, policy=policy)
        actions.extend(reviews)

        if portfolio.available_slots(state, market, policy) <= 0:
            actions.append({"action": "shadow_capacity_full", "market": market})
            continue

        try:
            candidate = _generate_candidate(state, market, now=now)
        except Exception as exc:
            actions.append({
                "action": "shadow_candidate_error",
                "market": market,
                "error": f"{type(exc).__name__}:{str(exc)[:300]}",
            })
            continue

        state, admission = portfolio.admit_candidate(
            state,
            market,
            candidate,
            now=now,
            policy=policy,
        )
        admission["engine"] = "v1"
        admission["role"] = "SHADOW_CHALLENGER"
        actions.append(admission)
        if admission.get("action") == "open":
            row = portfolio.market_state(state, market)
            for position in row.get("open_positions") or []:
                if position.get("position_id") == admission.get("position_id"):
                    position["source_engine"] = "stock_trading_v1"
                    position["champion_role"] = "SHADOW_CHALLENGER"
                    break

    state["engine"] = "v1"
    state["role"] = "SHADOW_CHALLENGER"
    state["production_decision_influence"] = False
    state["search_contract"] = "continuous_session_continue_after_candidate_rejection"
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

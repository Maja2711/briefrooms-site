#!/usr/bin/env python3
"""Canonical dual-account runtime for the locked BriefRooms AI Tournament.

This is the only financial runtime for campaign briefrooms-ai-tournament-2026-02.
It is deliberately deterministic and fail-closed:

* every full submission must match its locked SHA-256 commitment;
* all portfolios execute once at the first eligible US regular-session open;
* target weights include transaction costs, avoiding order-dependent truncation;
* no rebalancing is possible after execution;
* a 10,000 USD account and a separate 10,000 PLN account are valued daily;
* the PLN account includes USD/PLN translation and both cash balances earn IORB;
* snapshots, execution, rounds and hash-chained ledgers are append-only.

No broker orders are sent. This is paper trading only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ai_tournament_engine as engine  # noqa: E402
from ai_tournament_cash_interest import accrue_cash, load_policy  # noqa: E402

CONFIG_PATH = ROOT / "data" / "ai_tournament" / "config.json"
MODE_PATH = ROOT / "data" / "ai_tournament" / "manual_mode.json"
PUBLIC_PATH = ROOT / "data" / "ai_tournament" / "public.json"
EXECUTION_PATH = ROOT / "data" / "ai_tournament" / "execution.json"
LEDGER_DIR = ROOT / "data" / "ai_tournament" / "buy_hold_ledger"
RUNTIME_VERSION = "ai-tournament-locked-buy-hold-v2"
EXPECTED_CAMPAIGN = "briefrooms-ai-tournament-2026-02"


class BuyHoldError(RuntimeError):
    pass


@dataclass(frozen=True)
class AccountExecution:
    starting_capital: float
    currency: str
    fx_open: float
    cash_usd: float
    shares: dict[str, float]
    fees: float
    gross_stock_notional: float


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        tmp = Path(handle.name)
    tmp.replace(path)


def participant_slug(agent_id: str) -> str:
    return engine.slug(agent_id)


def ledger_path(agent_id: str) -> Path:
    return LEDGER_DIR / f"{participant_slug(agent_id)}.jsonl"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise BuyHoldError(f"invalid ledger row in {path}")
            rows.append(value)
    return rows


def verify_ledger(path: Path) -> None:
    previous = "GENESIS"
    for index, row in enumerate(read_jsonl(path), start=1):
        expected = str(row.get("event_hash") or "")
        body = dict(row)
        body.pop("event_hash", None)
        if body.get("previous_hash") != previous:
            raise BuyHoldError(f"ledger previous_hash mismatch: {path}:{index}")
        actual = sha256_text(canonical_json(body))
        if actual != expected:
            raise BuyHoldError(f"ledger event_hash mismatch: {path}:{index}")
        previous = expected


def append_ledger_once(path: Path, event_key: str, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    verify_ledger(path)
    rows = read_jsonl(path)
    if any(row.get("event_key") == event_key for row in rows):
        return
    previous = rows[-1]["event_hash"] if rows else "GENESIS"
    row = {
        "schema_version": "ai-tournament-buy-hold-ledger-v1",
        "recorded_at": engine.utc_now(),
        "previous_hash": previous,
        "event_key": event_key,
        **event,
    }
    row["event_hash"] = sha256_text(canonical_json(row))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_contract() -> tuple[dict[str, Any], dict[str, Any]]:
    config = read_json(CONFIG_PATH)
    mode = read_json(MODE_PATH)
    if not isinstance(config, dict) or not isinstance(mode, dict):
        raise BuyHoldError("tournament config or manual mode is missing")
    engine.validate_config(config)
    if config.get("tournament_id") != EXPECTED_CAMPAIGN:
        raise BuyHoldError("unexpected tournament campaign")
    if mode.get("mode") != "manual_locked_buy_and_hold" or mode.get("api_calls_enabled") is not False:
        raise BuyHoldError("manual locked buy-and-hold mode is not active")
    if mode.get("rebalancing_allowed") is not False:
        raise BuyHoldError("rebalancing must remain disabled")
    if config.get("start_date") != "2026-08-04" or config.get("end_date") != "2026-11-04":
        raise BuyHoldError("campaign dates do not match the locked execution window")
    if float(config.get("starting_capital_pln", 0)) != 10000.0:
        raise BuyHoldError("PLN starting capital must equal 10,000")
    if float(config.get("starting_capital_usd", 0)) != 10000.0:
        raise BuyHoldError("USD starting capital must equal 10,000")
    return config, mode


def validate_locked_set(config: dict[str, Any]) -> dict[str, Any]:
    readiness = engine.manual_submission_readiness(config)
    if not readiness.get("ready"):
        problems = [
            f"{row.get('agent_id')}: {row.get('error')}"
            for row in readiness.get("participants", [])
            if not row.get("ready")
        ]
        raise BuyHoldError("locked submission set is invalid: " + "; ".join(problems))
    if len(readiness.get("participants", [])) != len(config.get("agents", [])):
        raise BuyHoldError("readiness participant count differs from config")
    return readiness


def preflight() -> dict[str, Any]:
    config, mode = load_contract()
    readiness = validate_locked_set(config)
    start = date.fromisoformat(config["start_date"])
    for row in readiness["participants"]:
        agent = next(agent for agent in config["agents"] if agent["id"] == row["agent_id"])
        locked = engine.load_locked_submission(agent, config)
        commitment = locked["commitment"]
        locked_at_raw = commitment.get("received_at") or commitment.get("locked_at")
        if agent["id"] != "BRACE" and locked_at_raw:
            locked_at = datetime.fromisoformat(str(locked_at_raw).replace("Z", "+00:00"))
            if locked_at.date() >= start:
                # Same calendar date in UTC is not enough: all manual commitments are
                # from the previous UTC day in this campaign. Fail closed otherwise.
                raise BuyHoldError(f"{agent['id']} was not locked before the execution date")
    policy = load_policy()
    return {
        "ready": True,
        "runtime_version": RUNTIME_VERSION,
        "campaign_id": config["tournament_id"],
        "start_date": config["start_date"],
        "end_date": config["end_date"],
        "participants": readiness["participants"],
        "cash_benchmark": policy["benchmark"],
        "execution_policy": mode["execution_policy"],
    }


def account_execution(
    *,
    starting_capital: float,
    currency: str,
    target_weights: dict[str, float],
    cash_weight: float,
    open_prices: dict[str, float],
    fx_open: float,
    transaction_cost: float,
    slippage: float,
) -> AccountExecution:
    if currency not in {"USD", "PLN"}:
        raise BuyHoldError(f"unsupported account currency: {currency}")
    if starting_capital <= 0 or fx_open <= 0:
        raise BuyHoldError("starting capital and FX must be positive")
    if abs(sum(target_weights.values()) + cash_weight - 1.0) > 1e-8:
        raise BuyHoldError("target weights and cash do not total 100%")

    capital_usd = starting_capital if currency == "USD" else starting_capital / fx_open
    shares: dict[str, float] = {}
    gross_total_usd = 0.0
    fee_total_usd = 0.0
    for ticker, weight in sorted(target_weights.items()):
        if ticker not in open_prices or float(open_prices[ticker]) <= 0:
            raise BuyHoldError(f"missing valid opening price for {ticker}")
        total_cash_spend_usd = capital_usd * float(weight)
        gross_notional_usd = total_cash_spend_usd / (1.0 + transaction_cost)
        fee_usd = gross_notional_usd * transaction_cost
        execution_price = float(open_prices[ticker]) * (1.0 + slippage)
        shares[ticker] = gross_notional_usd / execution_price
        gross_total_usd += gross_notional_usd
        fee_total_usd += fee_usd

    cash_usd = capital_usd * cash_weight
    spent_usd = gross_total_usd + fee_total_usd + cash_usd
    if abs(spent_usd - capital_usd) > max(1e-8, capital_usd * 1e-10):
        raise BuyHoldError("execution does not preserve starting capital")
    factor = 1.0 if currency == "USD" else fx_open
    return AccountExecution(
        starting_capital=starting_capital,
        currency=currency,
        fx_open=fx_open,
        cash_usd=cash_usd,
        shares=shares,
        fees=fee_total_usd * factor,
        gross_stock_notional=gross_total_usd * factor,
    )


def build_execution(config: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot["session_date"] != config["start_date"]:
        raise BuyHoldError("execution snapshot is not the declared start session")
    rules = config["rules"]
    rows: list[dict[str, Any]] = []
    for agent in config["agents"]:
        locked = engine.load_locked_submission(agent, config)
        target_weights = locked["target_weights"]
        cash_weight = float(locked["cash_weight"])
        usd = account_execution(
            starting_capital=float(config["starting_capital_usd"]),
            currency="USD",
            target_weights=target_weights,
            cash_weight=cash_weight,
            open_prices=snapshot["open_prices"],
            fx_open=float(snapshot["fx_open"]),
            transaction_cost=float(rules["transaction_cost_pct"]),
            slippage=float(rules["slippage_pct"]),
        )
        pln = account_execution(
            starting_capital=float(config["starting_capital_pln"]),
            currency="PLN",
            target_weights=target_weights,
            cash_weight=cash_weight,
            open_prices=snapshot["open_prices"],
            fx_open=float(snapshot["fx_open"]),
            transaction_cost=float(rules["transaction_cost_pct"]),
            slippage=float(rules["slippage_pct"]),
        )
        rows.append({
            "agent_id": agent["id"],
            "provider": agent["provider"],
            "model": engine.resolve_model(agent),
            "submission_hash": locked["submission_hash"],
            "target_weights": target_weights,
            "cash_weight": cash_weight,
            "accounts": {
                "usd": {
                    "starting_capital": usd.starting_capital,
                    "currency": usd.currency,
                    "cash_usd": usd.cash_usd,
                    "shares": usd.shares,
                    "fees_usd": usd.fees,
                    "gross_stock_notional_usd": usd.gross_stock_notional,
                },
                "pln": {
                    "starting_capital": pln.starting_capital,
                    "currency": pln.currency,
                    "entry_fx_usdpln": pln.fx_open,
                    "cash_usd": pln.cash_usd,
                    "shares": pln.shares,
                    "fees_pln": pln.fees,
                    "gross_stock_notional_pln": pln.gross_stock_notional,
                },
            },
        })
    payload = {
        "schema_version": "ai-tournament-execution-v2",
        "runtime_version": RUNTIME_VERSION,
        "tournament_id": config["tournament_id"],
        "execution_session": snapshot["session_date"],
        "snapshot_hash": snapshot["snapshot_hash"],
        "execution_source": "daily regular-session opening prices from the frozen market snapshot",
        "paper_trading_only": True,
        "transaction_cost_pct": rules["transaction_cost_pct"],
        "slippage_pct": rules["slippage_pct"],
        "participants": rows,
        "created_at": engine.utc_now(),
    }
    payload["execution_hash"] = sha256_text(canonical_json(payload))
    return payload


def load_or_create_execution(config: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    existing = read_json(EXECUTION_PATH)
    if isinstance(existing, dict):
        if existing.get("tournament_id") != config["tournament_id"]:
            raise BuyHoldError("existing execution belongs to another campaign")
        if existing.get("execution_session") != config["start_date"]:
            raise BuyHoldError("existing execution date differs from config")
        body = dict(existing)
        expected = str(body.pop("execution_hash", ""))
        actual = sha256_text(canonical_json(body))
        if expected != actual:
            raise BuyHoldError("execution hash mismatch")
        return existing
    execution = build_execution(config, snapshot)
    atomic_write_json(EXECUTION_PATH, execution)
    for row in execution["participants"]:
        append_ledger_once(
            ledger_path(row["agent_id"]),
            f"EXECUTION:{execution['execution_session']}",
            {
                "event_type": "EXECUTION",
                "tournament_id": config["tournament_id"],
                "session_date": execution["execution_session"],
                "snapshot_hash": execution["snapshot_hash"],
                "submission_hash": row["submission_hash"],
                "accounts": row["accounts"],
                "target_weights": row["target_weights"],
                "paper_trading_only": True,
            },
        )
    return execution


def cash_at_session(opening_cash_usd: float, start_date: str, session_date: str) -> float:
    result = accrue_cash(opening_cash_usd, start_date, session_date)
    return float(result.closing_balance)


def max_drawdown(values: list[float]) -> float:
    peak = -math.inf
    result = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            result = min(result, value / peak - 1.0)
    return result


def sharpe(values: list[float]) -> float | None:
    returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values)) if values[i - 1] > 0]
    if len(returns) < 5:
        return None
    deviation = statistics.stdev(returns)
    if deviation <= 0:
        return None
    return statistics.mean(returns) / deviation * math.sqrt(252)


def available_snapshots(config: dict[str, Any]) -> list[dict[str, Any]]:
    paths = engine.config_paths(config)
    start = config["start_date"]
    end = config["end_date"]
    rows: list[dict[str, Any]] = []
    for path in sorted(paths.snapshots.glob("*.json")):
        value = read_json(path)
        if not isinstance(value, dict):
            raise BuyHoldError(f"invalid snapshot: {path}")
        session = str(value.get("session_date") or "")
        if start <= session <= end:
            rows.append(value)
    return rows


def account_nav(account: dict[str, Any], snapshot: dict[str, Any], start_date: str, currency: str) -> tuple[float, float]:
    cash_usd = cash_at_session(float(account["cash_usd"]), start_date, snapshot["session_date"])
    stock_usd = 0.0
    for ticker, shares in account["shares"].items():
        close = snapshot["close_prices"].get(ticker)
        if close is None:
            raise BuyHoldError(f"missing closing price for held ticker {ticker}")
        stock_usd += float(shares) * float(close)
    nav_usd = cash_usd + stock_usd
    nav = nav_usd if currency == "USD" else nav_usd * float(snapshot["fx_close"])
    return nav, cash_usd if currency == "USD" else cash_usd * float(snapshot["fx_close"])


def benchmark_returns(first: dict[str, Any], current: dict[str, Any]) -> tuple[float, float]:
    start_open = float(first["benchmark"]["open"])
    current_close = float(current["benchmark"]["close"])
    usd = current_close / start_open - 1.0
    pln = (current_close * float(current["fx_close"])) / (start_open * float(first["fx_open"])) - 1.0
    return usd, pln


def metrics_from_history(values: list[float], starting_capital: float, benchmark_return: float) -> dict[str, Any]:
    nav = values[-1]
    ret = nav / starting_capital - 1.0
    ratio = sharpe(values)
    return {
        "portfolio_value": round(nav, 2),
        "return_pct": round(ret, 8),
        "alpha_pct": round(ret - benchmark_return, 8),
        "max_drawdown_pct": round(max_drawdown(values), 8),
        "sharpe": round(ratio, 4) if ratio is not None else None,
        "closed_trades": 0,
        "positions_count": None,
    }


def prior_ranks(config: dict[str, Any], session_date: str) -> dict[str, int]:
    paths = engine.config_paths(config)
    previous = [path for path in sorted(paths.rounds.glob("*.json")) if path.stem < session_date]
    if not previous:
        return {}
    value = read_json(previous[-1], {})
    return {row["agent_id"]: int(row["rank"]) for row in value.get("leaderboard", [])}


def build_round(config: dict[str, Any], execution: dict[str, Any], snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    if not snapshots or snapshots[0]["session_date"] != config["start_date"]:
        raise BuyHoldError("the first execution-session snapshot is unavailable")
    current = snapshots[-1]
    benchmark_usd, benchmark_pln = benchmark_returns(snapshots[0], current)
    previous = prior_ranks(config, current["session_date"])
    rows: list[dict[str, Any]] = []
    execution_by_agent = {row["agent_id"]: row for row in execution["participants"]}

    for agent in config["agents"]:
        row = execution_by_agent[agent["id"]]
        usd_values: list[float] = []
        pln_values: list[float] = []
        cash_usd_value = 0.0
        cash_pln_value = 0.0
        for snapshot in snapshots:
            usd_nav, cash_usd_value = account_nav(row["accounts"]["usd"], snapshot, config["start_date"], "USD")
            pln_nav, cash_pln_value = account_nav(row["accounts"]["pln"], snapshot, config["start_date"], "PLN")
            usd_values.append(usd_nav)
            pln_values.append(pln_nav)
        metrics_usd = metrics_from_history(usd_values, float(config["starting_capital_usd"]), benchmark_usd)
        metrics_pln = metrics_from_history(pln_values, float(config["starting_capital_pln"]), benchmark_pln)
        metrics_usd["positions_count"] = len(row["target_weights"])
        metrics_pln["positions_count"] = len(row["target_weights"])
        current_usd_nav = usd_values[-1]
        holdings = []
        for ticker, shares in row["accounts"]["usd"]["shares"].items():
            value = float(shares) * float(current["close_prices"][ticker])
            holdings.append({"ticker": ticker, "weight": round(value / current_usd_nav, 8)})
        holdings.sort(key=lambda item: item["weight"], reverse=True)
        locked = engine.load_locked_submission(agent, config)
        submission = locked["submission"]
        rows.append({
            "agent_id": agent["id"],
            "provider": agent["provider"],
            "model": engine.resolve_model(agent),
            "status": "ACTIVE" if current["session_date"] < config["end_date"] else "FINISHED",
            "error": None,
            "metrics": {
                "portfolio_value_pln": metrics_pln["portfolio_value"],
                **{key: value for key, value in metrics_pln.items() if key != "portfolio_value"},
            },
            "metrics_pln": {
                "portfolio_value_pln": metrics_pln["portfolio_value"],
                **{key: value for key, value in metrics_pln.items() if key != "portfolio_value"},
            },
            "metrics_usd": {
                "portfolio_value_usd": metrics_usd["portfolio_value"],
                **{key: value for key, value in metrics_usd.items() if key != "portfolio_value"},
            },
            "cash_pln": round(cash_pln_value, 2),
            "cash_usd": round(cash_usd_value, 2),
            "cash_weight_pln": round(cash_pln_value / max(pln_values[-1], 1e-12), 8),
            "cash_weight_usd": round(cash_usd_value / max(usd_values[-1], 1e-12), 8),
            "cash_weight": round(cash_pln_value / max(pln_values[-1], 1e-12), 8),
            "positions": holdings,
            "latest_decision": {
                "schema_version": "ai-tournament-decision-v2",
                "agent_id": agent["id"],
                "target_weights": row["target_weights"],
                "cash_weight": row["cash_weight"],
                "rationale": submission.get("portfolio_thesis") or "Locked buy-and-hold submission.",
                "confidence": submission.get("confidence_pct"),
                "locked_submission_hash": row["submission_hash"],
                "execution_session": execution["execution_session"],
                "rebalancing_allowed": False,
            },
        })

    rows.sort(
        key=lambda item: (
            float(item["metrics_usd"]["return_pct"]),
            float(item["metrics_usd"]["max_drawdown_pct"]),
            float(item["metrics_usd"]["sharpe"] if item["metrics_usd"]["sharpe"] is not None else -999),
            item["agent_id"],
        ),
        reverse=True,
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
        old = previous.get(row["agent_id"])
        row["rank_change"] = None if old is None else old - rank

    payload = {
        "schema_version": "ai-tournament-round-v2",
        "runtime_version": RUNTIME_VERSION,
        "tournament_id": config["tournament_id"],
        "session_date": current["session_date"],
        "snapshot_hash": current["snapshot_hash"],
        "execution_hash": execution["execution_hash"],
        "benchmark": {
            "ticker": config["benchmark_ticker"],
            "return_pct_usd": round(benchmark_usd, 8),
            "return_pct_pln": round(benchmark_pln, 8),
        },
        "leaderboard": rows,
        "created_at": engine.utc_now(),
    }
    payload["round_hash"] = sha256_text(canonical_json(payload))
    return payload


def write_round_append_only(config: dict[str, Any], round_data: dict[str, Any]) -> dict[str, Any]:
    paths = engine.config_paths(config)
    path = paths.rounds / f"{round_data['session_date']}.json"
    existing = read_json(path)
    if isinstance(existing, dict):
        if existing.get("tournament_id") != config["tournament_id"]:
            raise BuyHoldError(f"round already exists for another campaign: {path.name}")
        return existing
    atomic_write_json(path, round_data)
    for row in round_data["leaderboard"]:
        append_ledger_once(
            ledger_path(row["agent_id"]),
            f"VALUATION:{round_data['session_date']}",
            {
                "event_type": "VALUATION",
                "tournament_id": config["tournament_id"],
                "session_date": round_data["session_date"],
                "snapshot_hash": round_data["snapshot_hash"],
                "metrics_pln": row["metrics_pln"],
                "metrics_usd": row["metrics_usd"],
                "rank": row["rank"],
            },
        )
    return round_data


def build_history(config: dict[str, Any]) -> list[dict[str, Any]]:
    paths = engine.config_paths(config)
    history: list[dict[str, Any]] = []
    for path in sorted(paths.rounds.glob("*.json"), reverse=True)[:30]:
        value = read_json(path, {})
        if value.get("tournament_id") != config["tournament_id"]:
            continue
        history.append({
            "session_date": value.get("session_date"),
            "leaderboard": [
                {
                    "rank": row.get("rank"),
                    "agent_id": row.get("agent_id"),
                    "return_pct_pln": (row.get("metrics_pln") or {}).get("return_pct"),
                    "return_pct_usd": (row.get("metrics_usd") or {}).get("return_pct"),
                    "return_pct": (row.get("metrics_pln") or {}).get("return_pct"),
                }
                for row in value.get("leaderboard", [])
            ],
        })
    return history


def publish_public(config: dict[str, Any], readiness: dict[str, Any], round_data: dict[str, Any] | None, status: str) -> dict[str, Any]:
    public = {
        "schema_version": engine.SCHEMA_VERSION,
        "engine_version": RUNTIME_VERSION,
        "generated_at": engine.utc_now(),
        "tournament": {
            "id": config["tournament_id"],
            "title_pl": config["title_pl"],
            "title_en": config["title_en"],
            "start_date": config["start_date"],
            "end_date": config["end_date"],
            "starting_capital_pln": config["starting_capital_pln"],
            "starting_capital_usd": config["starting_capital_usd"],
            "status": status,
            "ranking_rule": "cumulative_usd_return_desc_then_drawdown_then_sharpe",
            "decision_time": "all_allocations_locked_before_first_eligible_open",
            "execution_time": f"{config['start_date']}_us_regular_session_open",
            "rebalancing_allowed": False,
            "paper_trading_only": True,
        },
        "latest_session": round_data.get("session_date") if round_data else None,
        "participants": [
            {"agent_id": agent["id"], "provider": agent["provider"], "model": engine.resolve_model(agent)}
            for agent in config["agents"]
        ],
        "readiness": readiness,
        "benchmark": round_data.get("benchmark") if round_data else None,
        "rules": config["rules"],
        "leaderboard": round_data.get("leaderboard", []) if round_data else [],
        "history": build_history(config),
        "valuation_method": {
            "runtime_version": RUNTIME_VERSION,
            "accounts": ["PLN with daily USD/PLN translation", "USD without FX translation"],
            "cash_benchmark": "IORB ACT/365 daily compounding",
            "transaction_costs_included": True,
            "slippage_included": True,
            "opening_execution_is_order_independent": True,
        },
        "disclaimer_pl": "Publiczny eksperyment portfeli modelowych. Nie jest rekomendacją ani poradą inwestycyjną.",
        "disclaimer_en": "A public model-portfolio experiment. It is not investment advice or a recommendation.",
    }
    atomic_write_json(PUBLIC_PATH, public)
    return public


def validate_snapshot(snapshot: dict[str, Any], config: dict[str, Any]) -> None:
    required = {"session_date", "snapshot_hash", "open_prices", "close_prices", "fx_open", "fx_close", "benchmark"}
    if not required.issubset(snapshot):
        raise BuyHoldError("market snapshot is incomplete")
    if float(snapshot["fx_open"]) <= 0 or float(snapshot["fx_close"]) <= 0:
        raise BuyHoldError("snapshot FX values are invalid")
    for agent in config["agents"]:
        locked = engine.load_locked_submission(agent, config)
        for ticker in locked["target_weights"]:
            if ticker not in snapshot["open_prices"] or ticker not in snapshot["close_prices"]:
                raise BuyHoldError(f"snapshot lacks held ticker {ticker}")


def run() -> dict[str, Any]:
    config, _ = load_contract()
    readiness = validate_locked_set(config)
    paths = engine.config_paths(config)
    for folder in (paths.snapshots, paths.rounds, LEDGER_DIR):
        folder.mkdir(parents=True, exist_ok=True)

    snapshot = engine.fetch_market_snapshot(config)
    validate_snapshot(snapshot, config)
    session_date = snapshot["session_date"]
    start = config["start_date"]
    end = config["end_date"]

    if session_date < start:
        publish_public(config, readiness, None, "READY_FOR_FIRST_EXECUTION")
        print(f"Tournament ready; latest completed session {session_date} precedes execution date {start}")
        return {"status": "READY_FOR_FIRST_EXECUTION", "latest_completed_session": session_date}
    if session_date > end:
        rounds = [read_json(path, {}) for path in sorted(paths.rounds.glob("*.json"))]
        latest = next((row for row in reversed(rounds) if row.get("tournament_id") == config["tournament_id"]), None)
        publish_public(config, readiness, latest, "FINISHED")
        print("Tournament finished")
        return latest or {"status": "FINISHED"}

    snapshot_path = paths.snapshots / f"{session_date}.json"
    existing_snapshot = read_json(snapshot_path)
    if isinstance(existing_snapshot, dict):
        if existing_snapshot.get("snapshot_hash") != snapshot.get("snapshot_hash"):
            raise BuyHoldError(f"append-only snapshot mismatch for {session_date}")
        snapshot = existing_snapshot
    else:
        atomic_write_json(snapshot_path, snapshot)

    snapshots = available_snapshots(config)
    if not snapshots or snapshots[0]["session_date"] != start:
        if session_date != start:
            raise BuyHoldError("execution-day snapshot is missing; refusing retrospective reconstruction")
        snapshots = [snapshot]
    execution = load_or_create_execution(config, snapshots[0])
    round_data = build_round(config, execution, snapshots)
    round_data = write_round_append_only(config, round_data)
    status = "FINISHED" if session_date == end else "ACTIVE"
    publish_public(config, readiness, round_data, status)
    print(f"Published locked buy-and-hold tournament session {session_date}")
    return round_data


def validate_repository() -> dict[str, Any]:
    report = preflight()
    config, _ = load_contract()
    paths = engine.config_paths(config)
    if EXECUTION_PATH.exists():
        execution = read_json(EXECUTION_PATH)
        if not isinstance(execution, dict):
            raise BuyHoldError("invalid execution file")
        body = dict(execution)
        expected = str(body.pop("execution_hash", ""))
        if sha256_text(canonical_json(body)) != expected:
            raise BuyHoldError("execution file hash mismatch")
    last_session = None
    for path in sorted(paths.rounds.glob("*.json")):
        value = read_json(path)
        if not isinstance(value, dict) or value.get("tournament_id") != config["tournament_id"]:
            continue
        session = value.get("session_date")
        if not (config["start_date"] <= session <= config["end_date"]):
            raise BuyHoldError(f"round outside campaign dates: {session}")
        ranks = [row.get("rank") for row in value.get("leaderboard", [])]
        if ranks != list(range(1, len(ranks) + 1)):
            raise BuyHoldError(f"invalid ranking in round {session}")
        for row in value.get("leaderboard", []):
            if not isinstance(row.get("metrics_pln"), dict) or not isinstance(row.get("metrics_usd"), dict):
                raise BuyHoldError(f"dual-account metrics missing in round {session}")
        last_session = session
    for agent in config["agents"]:
        verify_ledger(ledger_path(agent["id"]))
    public = read_json(PUBLIC_PATH)
    if isinstance(public, dict) and public.get("tournament", {}).get("id") == config["tournament_id"]:
        if public.get("latest_session") != last_session:
            if not (last_session is None and public.get("latest_session") is None):
                raise BuyHoldError("public latest_session differs from append-only rounds")
    report["latest_validated_session"] = last_session
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preflight", "run", "validate"))
    args = parser.parse_args()
    try:
        if args.command == "preflight":
            result = preflight()
        elif args.command == "run":
            result = run()
        else:
            result = validate_repository()
    except Exception as exc:  # noqa: BLE001
        print(f"AI Tournament {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

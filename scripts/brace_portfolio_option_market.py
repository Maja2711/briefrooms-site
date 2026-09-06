#!/usr/bin/env python3
"""Collect real, conservative option quotes for BRACE Portfolio 10K hedging.

Quotes are research/paper inputs only. Long legs enter at ask and liquidate at
bid; short legs enter at bid and liquidate at ask. No synthetic option price is
created when a chain/contract is unavailable. Strategies are emitted only when
one standard contract can be covered by the actual underlying position.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO = ROOT / "data" / "investments" / "portfolio_10k.json"
ENVELOPE = ROOT / "data" / "portfolio10k" / "brace_learning_envelope_v1.json"
OUTPUT = ROOT / "data" / "portfolio10k" / "option_market_snapshot.json"
SNAPSHOTS = ROOT / "data" / "portfolio10k" / "brace_learning_v1" / "snapshots"
MULTIPLIER = 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _relative_spread(legs: list[dict[str, Any]]) -> float:
    gross_mid = 0.0
    gross_spread = 0.0
    for leg in legs:
        bid, ask = _finite(leg.get("bid")), _finite(leg.get("ask"))
        if bid < 0 or ask <= 0 or ask < bid:
            return 999.0
        gross_mid += (bid + ask) / 2.0
        gross_spread += ask - bid
    return gross_spread / max(gross_mid, 1e-9)


def _leg(row: Mapping[str, Any], side: str, option_type: str) -> dict[str, Any]:
    return {
        "contract_symbol": str(row.get("contractSymbol") or ""),
        "side": side,
        "option_type": option_type,
        "strike": _finite(row.get("strike")),
        "bid": _finite(row.get("bid")),
        "ask": _finite(row.get("ask")),
        "implied_volatility": _finite(row.get("impliedVolatility")),
        "volume": int(_finite(row.get("volume"))),
        "open_interest": int(_finite(row.get("openInterest"))),
    }


def _closest(rows: list[dict[str, Any]], target: float) -> dict[str, Any] | None:
    usable = [r for r in rows if _finite(r.get("ask")) > 0 and _finite(r.get("bid")) >= 0 and str(r.get("contractSymbol") or "")]
    return min(usable, key=lambda r: abs(_finite(r.get("strike")) - target)) if usable else None


def _net_entry_local(legs: list[dict[str, Any]]) -> float:
    total = 0.0
    for leg in legs:
        if leg["side"] == "LONG":
            total += _finite(leg.get("ask"))
        else:
            total -= _finite(leg.get("bid"))
    return total * MULTIPLIER


def _liquidation_local(legs: list[dict[str, Any]]) -> float:
    total = 0.0
    for leg in legs:
        if leg["side"] == "LONG":
            total += _finite(leg.get("bid"))
        else:
            total -= _finite(leg.get("ask"))
    return total * MULTIPLIER


def _strategy(strategy_type: str, symbol: str, expiry: str, spot: float, fx: float, legs: list[dict[str, Any]]) -> dict[str, Any]:
    strategy_id = f"{symbol}:{expiry}:{strategy_type}:" + ":".join(x["contract_symbol"] for x in legs)
    return {
        "strategy_id": strategy_id,
        "strategy_type": strategy_type,
        "underlying_symbol": symbol,
        "expiry": expiry,
        "dte": (date.fromisoformat(expiry) - _now().date()).days,
        "contract_multiplier": MULTIPLIER,
        "contracts": 1,
        "underlying_spot": round(spot, 8),
        "fx_to_pln": round(fx, 8),
        "hedged_notional_pln": round(spot * MULTIPLIER * fx, 8),
        "net_premium_pln": round(_net_entry_local(legs) * fx, 8),
        "liquidation_value_pln": round(_liquidation_local(legs) * fx, 8),
        "relative_bid_ask_spread": round(_relative_spread(legs), 8),
        "contains_naked_short_option": False,
        "market_data_complete": all(x["contract_symbol"] and x["ask"] > 0 and x["bid"] >= 0 for x in legs),
        "legs": legs,
    }


def _tracked_contracts() -> dict[tuple[str, str], set[str]]:
    tracked: dict[tuple[str, str], set[str]] = {}
    if not SNAPSHOTS.exists():
        return tracked
    for path in SNAPSHOTS.glob("*.json"):
        snap = _read(path, {}) or {}
        for strategy in snap.get("option_strategies", []) or []:
            symbol = str(strategy.get("underlying_symbol") or "")
            expiry = str(strategy.get("expiry") or "")
            if not symbol or not expiry:
                continue
            key = (symbol, expiry)
            tracked.setdefault(key, set()).update(str(x.get("contract_symbol")) for x in strategy.get("legs", []) or [] if x.get("contract_symbol"))
    return tracked


def _fx_regime(yf: Any, pairs: list[str]) -> tuple[str, dict[str, Any]]:
    observations: dict[str, Any] = {}
    max_vol = 0.0
    strongest_return = 0.0
    for pair in pairs:
        try:
            hist = yf.Ticker(pair).history(period="3mo", interval="1d", auto_adjust=False)
            closes = [float(x) for x in hist["Close"].dropna().tolist()]
            if len(closes) < 21:
                continue
            returns = [(closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes))]
            tail = returns[-20:]
            avg = sum(tail) / len(tail)
            var = sum((x - avg) ** 2 for x in tail) / max(len(tail) - 1, 1)
            vol = math.sqrt(var) * math.sqrt(252.0)
            horizon = min(60, len(closes) - 1)
            ret = closes[-1] / closes[-1 - horizon] - 1.0
            max_vol = max(max_vol, vol)
            if abs(ret) > abs(strongest_return):
                strongest_return = ret
            observations[pair] = {"annualized_vol_20d": round(vol, 8), "return_window": round(ret, 8), "observations": len(closes)}
        except Exception as exc:  # network/provider failure is a data status, not a synthetic fallback
            observations[pair] = {"status": "DATA_GAP", "error": type(exc).__name__}
    if max_vol >= 0.10:
        regime = "HIGH_VOL"
    elif strongest_return >= 0.04:
        regime = "FX_UP"
    elif strongest_return <= -0.04:
        regime = "FX_DOWN"
    else:
        regime = "NORMAL"
    return regime, observations


def collect() -> dict[str, Any]:
    try:
        import yfinance as yf
    except ModuleNotFoundError as exc:
        raise RuntimeError("yfinance is required for live option collection") from exc

    portfolio = _read(PORTFOLIO, {}) or {}
    envelope = _read(ENVELOPE, {}) or {}
    cfg = envelope.get("options_hedge") or {}
    min_dte, max_dte = int(cfg.get("minimum_dte", 20)), int(cfg.get("maximum_dte", 90))
    diagnostics: list[dict[str, Any]] = []
    strategies: list[dict[str, Any]] = []
    active = [p for p in portfolio.get("positions", []) or [] if p.get("status") == "active" and p.get("currency") in {"USD", "EUR"}]
    tracked = _tracked_contracts()

    for p in active:
        symbol = str(p.get("market_symbol") or p.get("data_symbol") or "")
        quantity = _finite(p.get("quantity"))
        spot = _finite(p.get("current_price"))
        fx = _finite(p.get("current_fx_to_pln"))
        if not symbol or spot <= 0 or fx <= 0:
            diagnostics.append({"symbol": symbol or None, "status": "DATA_GAP_POSITION_PRICE_OR_FX"})
            continue
        if quantity + 1e-9 < MULTIPLIER:
            diagnostics.append({"symbol": symbol, "status": "CONTRACT_SIZE_EXCEEDS_POSITION", "position_quantity": quantity, "standard_contract_underlying_units": MULTIPLIER})
            continue
        try:
            ticker = yf.Ticker(symbol)
            expiries = [x for x in ticker.options if min_dte <= (date.fromisoformat(x) - _now().date()).days <= max_dte]
            if not expiries:
                diagnostics.append({"symbol": symbol, "status": "NO_ELIGIBLE_EXPIRY"})
                continue
            expiry = min(expiries, key=lambda x: abs((date.fromisoformat(x) - _now().date()).days - 45))
            chain = ticker.option_chain(expiry)
            puts = chain.puts.to_dict("records")
            calls = chain.calls.to_dict("records")
            long_put = _closest(puts, spot * 0.95)
            short_put = _closest(puts, spot * 0.85)
            short_call = _closest(calls, spot * 1.05)
            if long_put:
                strategies.append(_strategy("PROTECTIVE_PUT", symbol, expiry, spot, fx, [_leg(long_put, "LONG", "PUT")]))
            if long_put and short_put and long_put.get("contractSymbol") != short_put.get("contractSymbol"):
                strategies.append(_strategy("PUT_SPREAD", symbol, expiry, spot, fx, [_leg(long_put, "LONG", "PUT"), _leg(short_put, "SHORT", "PUT")]))
            if long_put and short_call:
                strategies.append(_strategy("COLLAR", symbol, expiry, spot, fx, [_leg(long_put, "LONG", "PUT"), _leg(short_call, "SHORT", "CALL")]))
            diagnostics.append({"symbol": symbol, "status": "CHAIN_COLLECTED", "expiry": expiry})
        except Exception as exc:
            diagnostics.append({"symbol": symbol, "status": "OPTION_CHAIN_DATA_GAP", "error": type(exc).__name__})

    # Refresh liquidation quotes for previously frozen strategies even when they
    # are no longer today's preferred strike.
    existing_ids = {x["strategy_id"] for x in strategies}
    for (symbol, expiry), contract_symbols in tracked.items():
        if date.fromisoformat(expiry) < _now().date():
            continue
        try:
            ticker = yf.Ticker(symbol)
            chain = ticker.option_chain(expiry)
            rows = {str(x.get("contractSymbol")): (x, "PUT") for x in chain.puts.to_dict("records")}
            rows.update({str(x.get("contractSymbol")): (x, "CALL") for x in chain.calls.to_dict("records")})
            # Historical strategy composition is kept in the frozen snapshot;
            # current file only exposes raw tracked contract quotes for audit.
            for contract in sorted(contract_symbols):
                if contract in rows:
                    row, kind = rows[contract]
                    diagnostics.append({"symbol": symbol, "status": "TRACKED_CONTRACT_REFRESHED", "contract_symbol": contract, "option_type": kind, "bid": _finite(row.get("bid")), "ask": _finite(row.get("ask")), "expiry": expiry})
        except Exception as exc:
            diagnostics.append({"symbol": symbol, "status": "TRACKED_CONTRACT_DATA_GAP", "expiry": expiry, "error": type(exc).__name__})

    pairs = ["USDPLN=X", "EURPLN=X"]
    regime, fx_context = _fx_regime(yf, pairs)
    payload = {
        "schema_version": "brace-option-market-snapshot-v1",
        "observed_at": _now().isoformat(timespec="seconds"),
        "source": "Yahoo Finance via yfinance live option chains; conservative bid/ask valuation",
        "market_context": {"fx_regime": regime, "fx": fx_context},
        "strategies": strategies,
        "diagnostics": diagnostics,
        "governance": {"real_market_prices_only": True, "synthetic_option_prices": False, "real_broker_integration": False, "trade_execution_authority": False},
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "COLLECTED", "strategies": len(strategies), "diagnostics": len(diagnostics), "fx_regime": regime}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        payload = _read(OUTPUT, {}) or {}
        governance = payload.get("governance") or {}
        if governance.get("real_market_prices_only") is not True or governance.get("synthetic_option_prices") is not False:
            raise ValueError("option market provenance contract failed")
        if governance.get("real_broker_integration") is not False or governance.get("trade_execution_authority") is not False:
            raise ValueError("option collector gained execution authority")
        result = {"ok": True, "strategies": len(payload.get("strategies", []) or [])}
    else:
        result = collect()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

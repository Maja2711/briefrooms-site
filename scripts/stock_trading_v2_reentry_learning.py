#!/usr/bin/env python3
"""Small production Stock Trading re-entry rule learner.

One frozen, prospective observation per post-stop episode. Five subsequent
COMPLETED sessions determine a research-only mark-to-mark outcome. No synthetic
fills, no mutation of trade history, no shadow execution or portfolio writes.
A deterministic rule grid requires a separate future observation cohort before
an exception can be activated in production policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data/investments/stock_trading_reentry_learning.json"
POLICY_PATH = ROOT / "data/investments/stock_trading_policy.json"
PRODUCTION_PATH = ROOT / "data/investments/stock_trading_v2_production_state.json"
TZ = {"GPW": ZoneInfo("Europe/Warsaw"), "US": ZoneInfo("America/New_York")}
COST = {"GPW": 0.002, "US": 0.001}
SCHEMA = "stock-trading-reentry-learning-v1"
GRID = tuple(
    {"drop_atr_min": d, "rebound_atr_min": b, "volume_ratio_min": v}
    for d in (1.0, 1.5, 2.0)
    for b in (0.3, 0.5, 0.7)
    for v in (1.0, 1.2)
)


def _read(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        tmp = Path(handle.name)
    tmp.replace(path)


def _dt(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else None
    except (ValueError, TypeError):
        return None


def _num(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError):
        return None


def _symbol(value: Any) -> str:
    value = str(value or "").upper().strip()
    return value[:-3] if value.endswith(".WA") else value


def recent_stop(state: Mapping[str, Any], market: str, symbol: str, now: datetime, days: int = 7) -> Mapping[str, Any] | None:
    row = (state.get("markets") or {}).get(market) or {}
    valid = []
    for entry in row.get("closed_positions") or []:
        if not isinstance(entry, Mapping) or entry.get("exit_reason") != "stop_loss":
            continue
        if _symbol(entry.get("symbol")) != _symbol(symbol):
            continue
        closed = _dt(entry.get("closed_at"))
        if closed and 0 <= (now - closed).total_seconds() <= days * 86400:
            valid.append((closed, entry))
    return max(valid, key=lambda pair: pair[0])[1] if valid else None


def _chart_hash(chart: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(chart, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def freeze_chart(market: str, symbol: str, chart: Mapping[str, Any], *, received_at: datetime) -> dict[str, Any]:
    """Freeze bounded raw formed 15m bars before any actual/virtual stop query."""
    meta = chart.get("meta") or {}
    if (market not in TZ or received_at.tzinfo is None
            or str(meta.get("symbol") or "").upper() != symbol.upper()
            or (market == "GPW") != symbol.upper().endswith(".WA")
            or meta.get("currency") != {"GPW": "PLN", "US": "USD"}[market]):
        raise ValueError("native reentry chart issuer identity invalid")
    quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    if not isinstance(quote, Mapping):
        raise ValueError("native reentry chart quote invalid")
    selected: list[tuple[int, int]] = []
    for index, value in enumerate(chart.get("timestamp") or []):
        try:
            stamp = int(value)
            end = datetime.fromtimestamp(stamp, timezone.utc) + timedelta(minutes=15)
            if received_at - timedelta(days=32) <= end <= received_at:
                selected.append((index, stamp))
        except (ValueError, TypeError, OSError, OverflowError):
            continue
    if not selected or len({stamp for _, stamp in selected}) != len(selected):
        raise ValueError("native reentry chart formed candles missing or duplicated")
    selected.sort(key=lambda pair: pair[1])
    frozen = {"meta": {"symbol": symbol, "currency": meta["currency"]},
              "timestamp": [stamp for _, stamp in selected], "indicators": {"quote": [{
                  key: [values[index] if index < len(values) else None for index, _ in selected]
                  for key in ("open", "high", "low", "close", "volume")
                  for values in [quote.get(key) or []]
              }]}}
    observed = datetime.fromtimestamp(selected[-1][1], timezone.utc) + timedelta(minutes=15)
    return {"schema_version": "stock-trading-reentry-chart-v1", "market": market, "symbol": symbol,
            "source": "Yahoo Finance chart", "interval_seconds": 900,
            "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/" + symbol,
            "observed_at": observed.isoformat(), "received_at": received_at.isoformat(),
            "chart": frozen, "chart_sha256": _chart_hash(frozen)}


def features_from_chart(receipt: Any, market: str, symbol: str, stop: Mapping[str, Any], price: float,
                        quote_observed_at: str, atr_fraction: Any, volume_ratio: Any,
                        *, decision_at: datetime) -> dict[str, float] | None:
    """Replay derives features for its OWN stopped position, without networking."""
    if not isinstance(receipt, Mapping):
        raise ValueError("native reentry chart receipt missing")
    observed, received = _dt(receipt.get("observed_at")), _dt(receipt.get("received_at"))
    chart = receipt.get("chart") or {}
    if (receipt.get("schema_version") != "stock-trading-reentry-chart-v1"
            or receipt.get("market") != market or receipt.get("symbol") != symbol
            or receipt.get("source") != "Yahoo Finance chart" or receipt.get("interval_seconds") != 900
            or _chart_hash(chart) != receipt.get("chart_sha256")
            or (chart.get("meta") or {}).get("symbol") != symbol
            or (chart.get("meta") or {}).get("currency") != {"GPW": "PLN", "US": "USD"}.get(market)
            or observed is None or received is None or not observed <= received <= decision_at
            or decision_at - received > timedelta(minutes=20)):
        raise ValueError("native reentry chart receipt identity/time/hash invalid")
    return quote_features(market, symbol, stop, price, quote_observed_at, atr_fraction, volume_ratio, chart=chart)


def quote_features(
    market: str, symbol: str, stop: Mapping[str, Any], price: float,
    quote_observed_at: str, atr_fraction: Any, volume_ratio: Any,
    *, chart: Mapping[str, Any] | None = None
) -> dict[str, float] | None:
    """Read fully formed 15m candles AFTER the SL and BEFORE quote observation.

    Failure to establish the intraday low returns None: no learned bypass.
    """
    observed, stopped = _dt(quote_observed_at), _dt(stop.get("closed_at"))
    af, vr, stopped_price = _num(atr_fraction), _num(volume_ratio), _num(stop.get("exit_price"))
    if not observed or not stopped or observed <= stopped or not af or af <= 0 or not vr or vr <= 0 or not stopped_price:
        return None
    if price <= 0 or af > 0.2 or vr > 30:
        return None
    if chart is None:
        try:
            from scripts import stock_trading_portfolio as portfolio
        except ModuleNotFoundError:
            import stock_trading_portfolio as portfolio
        try:
            chart = portfolio._chart(symbol, interval="15m", range_value="1mo")
        except Exception:
            return None
    timestamps = chart.get("timestamp") or []
    quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    if not timestamps or not isinstance(quote, Mapping):
        return None
    lows = quote.get("low") or []
    low_prices: list[float] = []
    for i, ts in enumerate(timestamps):
        try:
            start = datetime.fromtimestamp(int(ts), timezone.utc)
            low = _num(lows[i])
        except (ValueError, IndexError, TypeError, OSError):
            continue
        # Exclude the partly formed 15m bar and any pre-stop candle.
        if start > stopped.astimezone(timezone.utc) and start + timedelta(minutes=15) <= observed.astimezone(timezone.utc):
            if low is not None and low > 0:
                low_prices.append(low)
    if not low_prices:
        return None
    atr = price * af
    return {
        "drop_atr": round((stopped_price - price) / atr, 6),
        "rebound_atr": round(max(0.0, price - min(low_prices)) / atr, 6),
        "volume_ratio": round(vr, 6),
    }


def matches(rule: Mapping[str, Any], features: Mapping[str, Any]) -> bool:
    allowed = {"drop_atr_min": (0.5, 5.0), "rebound_atr_min": (0.1, 3.0), "volume_ratio_min": (0.8, 5.0)}
    if set(rule) != set(allowed):
        return False
    for key, (low, high) in allowed.items():
        threshold = _num(rule.get(key))
        observed = _num(features.get(key.removesuffix("_min")))
        if threshold is None or not low <= threshold <= high or observed is None or observed < threshold:
            return False
    return True


def active_exception(rules: Any, features: Any, *, now: datetime) -> dict[str, Any] | None:
    if not isinstance(features, Mapping) or not isinstance(rules, list):
        return None
    for item in rules:
        if not isinstance(item, Mapping) or item.get("status") != "ACTIVE":
            continue
        if not isinstance(item.get("conditions"), Mapping) or not matches(item["conditions"], features):
            continue
        stats = item.get("validation") or {}
        if (_num(stats.get("n")) or 0) < 12 or (_num(stats.get("unique_symbols")) or 0) < 5:
            continue
        if (_num(stats.get("conservative_lower_bound_r")) or 0) <= 0:
            continue
        approved = _dt(item.get("activated_at"))
        if approved is None or approved > now:
            continue
        return {"rule_id": item.get("id"), "conditions": dict(item["conditions"]), "activated_at": item["activated_at"]}
    return None


def empty_state() -> dict[str, Any]:
    return {"schema_version": SCHEMA, "observations": [], "markets": {"GPW": {"pending": None}, "US": {"pending": None}}}


def capture(state: dict[str, Any], audits: list[Mapping[str, Any]]) -> int:
    existing = {str(row.get("episode_id")) for row in state["observations"]}
    count = 0
    for action in audits:
        if action.get("market") not in TZ:
            continue
        reason = action.get("reentry_justification") if action.get("action") == "open" else action
        if not isinstance(reason, Mapping):
            continue
        probe = reason.get("reentry_probe")
        if not isinstance(probe, Mapping):
            continue
        episode = str(probe.get("episode_id") or "")
        features = probe.get("features")
        at = _dt(probe.get("observed_at"))
        if not episode or episode in existing or not at or not isinstance(features, Mapping):
            continue
        if not all(_num(features.get(key)) is not None for key in ("drop_atr", "rebound_atr", "volume_ratio")):
            continue
        price = _num(probe.get("price"))
        risk_fraction = _num(probe.get("risk_fraction"))
        if not price or not risk_fraction or risk_fraction <= 0:
            continue
        state["observations"].append({
            "episode_id": episode, "market": action["market"],
            "symbol": str(probe["symbol"]), "observed_at": at.isoformat(),
            "price": price, "risk_fraction": risk_fraction, "features": dict(features),
            "decision": "OPEN" if action.get("action") == "open" else "BLOCK",
            "outcome": None, "outcome_type": "RESEARCH_CLOSE_ONLY_NO_EXECUTION",
        })
        existing.add(episode)
        count += 1
    return count


def resolve_one(observation: dict[str, Any], chart: Mapping[str, Any], now: datetime) -> bool:
    """Require five *subsequent completed* regular sessions; no current-day close."""
    at = _dt(observation.get("observed_at"))
    market = observation.get("market")
    if at is None or market not in TZ:
        return False
    start_day = at.astimezone(TZ[market]).date()
    today = now.astimezone(TZ[market]).date()
    quotes = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    timestamps, closes = chart.get("timestamp") or [], quotes.get("close") or []
    by_day: dict[str, float] = {}
    for i, stamp in enumerate(timestamps):
        try:
            day = datetime.fromtimestamp(int(stamp), TZ[market]).date()
            close = _num(closes[i])
        except (ValueError, TypeError, IndexError, OSError):
            continue
        if start_day < day < today and close is not None and close > 0:
            by_day[day.isoformat()] = close
    days = sorted(by_day)
    if len(days) < 5:
        return False
    end_day = days[4]
    initial = _num(observation.get("price"))
    risk = _num(observation.get("risk_fraction"))
    if not initial or not risk or risk <= 0:
        return False
    close = by_day[end_day]
    research_net_r = ((close / initial) - 1.0 - COST[market]) / risk
    observation["outcome"] = {
        "completed_sessions": 5, "end_day": end_day, "close": round(close, 8),
        "net_research_r": round(research_net_r, 6),
        "cost_stress_fraction": COST[market],
        "execution_proof": False,
    }
    return True


def evaluate(records: list[Mapping[str, Any]]) -> dict[str, float]:
    values = [_num((x.get("outcome") or {}).get("net_research_r")) for x in records]
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0}
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if len(values) > 1 else float("inf")
    conservative = mean - (1.64 * sd / math.sqrt(len(values)))
    return {
        "n": len(values), "unique_symbols": len({_symbol(x.get("symbol")) for x in records}),
        "mean_net_r": round(mean, 6), "conservative_lower_bound_r": round(conservative, 6),
        "positive_rate": round(sum(v > 0 for v in values) / len(values), 6),
    }


def update_rules(state: dict[str, Any], policy: dict[str, Any], now: datetime) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for market in TZ:
        cfg = policy["markets"][market]["reentry_guard"]
        learned = cfg.setdefault("learned_exception_rules", [])
        pending_state = state["markets"].setdefault(market, {"pending": None})
        records = sorted(
            (x for x in state["observations"] if x.get("market") == market and x.get("outcome")),
            key=lambda x: x["observed_at"]
        )
        pending = pending_state.get("pending")
        if pending:
            future = [x for x in records if (_dt(x["observed_at"]) or now) > (_dt(pending["frozen_at"]) or now)]
            matched = [x for x in future if matches(pending["conditions"], x["features"])]
            baseline, measured = evaluate(future), evaluate(matched)
            unique = len({_symbol(x["symbol"]) for x in matched})
            report[market] = {"phase": "FRESH_PRODUCTION_COHORT", "matched": len(matched), "baseline": baseline}
            if (
                measured.get("n", 0) >= 12 and unique >= 5
                and measured["mean_net_r"] >= 0.20
                and measured["conservative_lower_bound_r"] > 0
                and measured["positive_rate"] >= 0.55
                and baseline.get("n", 0) >= 12
                and measured["mean_net_r"] >= baseline["mean_net_r"] + 0.10
                and not any(x.get("status") == "ACTIVE" for x in learned)
            ):
                learned.append({
                    "id": f"REENTRY_REVERSAL_{market}_001",
                    "status": "ACTIVE", "conditions": dict(pending["conditions"]),
                    "discovered_at": pending["frozen_at"], "activated_at": now.isoformat(),
                    "validation": measured, "baseline": baseline,
                    "outcome_kind": "PROSPECTIVE_MARK_TO_MARK_NO_FILL_PROOF",
                })
                pending_state["pending"] = None
                report[market]["phase"] = "ACTIVATED"
            continue
        if any(x.get("status") == "ACTIVE" for x in learned):
            report[market] = {"phase": "ACTIVE"}
            continue
        if len(records) < 25 or len({_symbol(x["symbol"]) for x in records}) < 5:
            report[market] = {"phase": "COLLECTING", "settled": len(records)}
            continue
        options = []
        for conditions in GRID:
            matched = [x for x in records if matches(conditions, x["features"])]
            metrics = evaluate(matched)
            if (
                metrics.get("n", 0) >= 15 and metrics["unique_symbols"] >= 5
                and metrics["mean_net_r"] >= 0.20 and metrics["positive_rate"] >= 0.55
                and metrics["conservative_lower_bound_r"] > 0
            ):
                options.append((metrics["conservative_lower_bound_r"], conditions, metrics))
        if not options:
            report[market] = {"phase": "INSUFFICIENT_EDGE", "settled": len(records)}
            continue
        options.sort(key=lambda row: row[0], reverse=True)
        _, conditions, metrics = options[0]
        pending_state["pending"] = {
            "conditions": dict(conditions), "frozen_at": now.isoformat(),
            "discovery_stats": metrics,
        }
        report[market] = {"phase": "RULE_FROZEN_AWAITING_FUTURE_COHORT", "conditions": conditions}
    return report


def run(*, now: datetime | None = None, state_path: Path = STATE_PATH,
        policy_path: Path = POLICY_PATH, production_path: Path = PRODUCTION_PATH,
        chart_fetcher: Any = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stored_state = _read(state_path)
    state = stored_state if stored_state is not None else empty_state()
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA:
        raise ValueError("reentry learning state schema mismatch")
    policy = _read(policy_path)
    production = _read(production_path)
    if not isinstance(policy, dict) or not isinstance(production, dict):
        raise ValueError("missing production inputs")
    original_policy = json.dumps(policy, sort_keys=True)
    old_report = dict(state.get("last_report") or {})
    audits = (production.get("audit") or [])
    latest = audits[-1] if audits else {}
    captured = capture(state, latest.get("actions") or [])
    pending_by_symbol = {}
    for item in state["observations"]:
        if item.get("outcome") or not _dt(item.get("observed_at")):
            continue
        if (now - _dt(item["observed_at"])).days > 100:
            continue
        pending_by_symbol.setdefault((item["market"], item["symbol"]), []).append(item)
    resolved = 0
    if chart_fetcher is None:
        try:
            from scripts import stock_trading_portfolio as portfolio
        except ModuleNotFoundError:
            import stock_trading_portfolio as portfolio
        chart_fetcher = lambda symbol: portfolio._chart(symbol, interval="1d", range_value="6mo")
    for (_, symbol), observations in pending_by_symbol.items():
        # Never fabricate a result when a data provider is unavailable.
        try:
            chart = chart_fetcher(symbol)
        except Exception:
            continue
        for item in observations:
            resolved += bool(resolve_one(item, chart, now))
    report = update_rules(state, policy, now)
    current_report = {"captured": captured, "resolved": resolved, "markets": report}
    if stored_state is None or captured or resolved or old_report.get("markets") != report:
        state["updated_at"] = now.isoformat()
        state["last_report"] = current_report
        _write(state_path, state)
    if json.dumps(policy, sort_keys=True) != original_policy:
        _write(policy_path, policy)
    return current_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        payload = _read(STATE_PATH, empty_state())
        if payload.get("schema_version") != SCHEMA:
            raise SystemExit("reentry state invalid")
        print(json.dumps({"status": "OK", "observations": len(payload["observations"])}, ensure_ascii=False))
    else:
        print(json.dumps(run(), ensure_ascii=False))


if __name__ == "__main__":
    main()

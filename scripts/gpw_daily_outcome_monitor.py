#!/usr/bin/env python3
"""Monitor all selected GPW daily paper trades until target/stop/expiry.

Unlike the old expiry-only settlement, this monitor can resolve a trade on the
same session when TP or SL is reached. It preserves every selected stock in a
durable history index and uses 5-minute Yahoo bars after activation. If both TP
and SL occur in the same unresolved bar, stop wins conservatively.
"""
from __future__ import annotations

import json
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from scripts import daily_stock_trade_timestamp_normalizer as timestamp_normalizer
    from scripts import gpw_daily_pick as gpw
except ModuleNotFoundError:
    import daily_stock_trade_timestamp_normalizer as timestamp_normalizer
    import gpw_daily_pick as gpw

ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "data/investments/gpw_daily_pick_history_index.json"
WARSAW = ZoneInfo("Europe/Warsaw")
COST_PERCENT = 0.38


def atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(body)
        name = tmp.name
    Path(name).replace(path)


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WARSAW)
    return dt.astimezone(timezone.utc)


def fetch_intraday(symbol: str) -> list[dict[str, Any]]:
    encoded = urllib.parse.quote(symbol, safe="")
    params = urllib.parse.urlencode({"range": "5d", "interval": "5m", "events": "history", "includeAdjustedClose": "true"})
    errors = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            payload = json.loads(gpw.request_bytes(f"https://{host}/v8/finance/chart/{encoded}?{params}"))
            result = (payload.get("chart", {}).get("result") or [None])[0]
            if not result:
                raise ValueError("empty chart")
            stamps = result.get("timestamp") or []
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            rows = []
            for idx, stamp in enumerate(stamps):
                values = {key: (quote.get(key) or [None] * len(stamps))[idx] for key in ("open", "high", "low", "close")}
                if any(values[key] is None for key in values):
                    continue
                observed = datetime.fromtimestamp(int(stamp), timezone.utc)
                rows.append({"timestamp": observed, **{key: float(value) for key, value in values.items()}})
            return rows
        except Exception as exc:
            errors.append(f"{host}:{type(exc).__name__}")
    raise RuntimeError(f"No intraday data for {symbol}: {';'.join(errors)}")


def activation_from_snapshot(payload: dict[str, Any]) -> tuple[datetime, float, str] | None:
    selection = payload.get("selection") or {}
    snapshot = selection.get("market_snapshot") or {}
    observed = parse_time(snapshot.get("observed_at"))
    last = snapshot.get("last")
    zone = selection.get("entry_zone") or []
    if observed is None or last is None or len(zone) != 2:
        return None
    low, high = map(float, zone)
    price = float(last)
    if low <= price <= high:
        return observed, price, "frozen_selection_market_snapshot"
    return None


def activation_from_bars(payload: dict[str, Any], bars: list[dict[str, Any]]) -> tuple[datetime, float, str] | None:
    selection = payload.get("selection") or {}
    zone = selection.get("entry_zone") or []
    if len(zone) != 2:
        return None
    low, high = map(float, zone)
    generated = parse_time(payload.get("generated_at"))
    if generated is None:
        return None
    for bar in bars:
        if bar["timestamp"] < generated:
            continue
        if low <= bar["open"] <= high:
            return bar["timestamp"], bar["open"], "first_post_publication_5m_open_in_zone"
        if bar["open"] > high and bar["low"] <= high:
            return bar["timestamp"], high, "first_post_publication_5m_touch_from_above"
        if bar["open"] < low and bar["high"] >= low:
            return bar["timestamp"], low, "first_post_publication_5m_touch_from_below"
    return None


def resolve_pending(payload: dict[str, Any], now: datetime) -> bool:
    if payload.get("decision") != "TRANSAKCJA" or (payload.get("outcome") or {}).get("status") == "RESOLVED":
        return False
    selection = payload.get("selection") or {}
    bars = fetch_intraday(str(selection.get("symbol")))
    activation = activation_from_snapshot(payload) or activation_from_bars(payload, bars)
    expiry = datetime.fromisoformat(str(selection.get("valid_until"))).date()
    outcome = dict(payload.get("outcome") or {})
    if activation is None:
        if now.astimezone(WARSAW).date() > expiry:
            payload["outcome"] = {
                "status": "RESOLVED",
                "activated": False,
                "reason": "Cena nie weszła w strefę aktywacji w okresie ważności planu.",
                "resolved_at": now.astimezone(WARSAW).isoformat(timespec="seconds"),
            }
            return True
        outcome.update({"status": "PENDING", "activated": False, "last_checked_at": now.isoformat(timespec="seconds")})
        payload["outcome"] = outcome
        return True

    activated_at, entry, evidence = activation
    stop = float(selection["stop"])
    target = float(selection["target"])
    risk = max(entry - stop, 0.01)
    eligible = [bar for bar in bars if bar["timestamp"] >= activated_at]
    for bar in eligible:
        stop_hit = bar["low"] <= stop
        target_hit = bar["high"] >= target
        if stop_hit or target_hit:
            if stop_hit:
                exit_price, reason = stop, "stop"
            else:
                exit_price, reason = target, "target"
            gross = (exit_price / entry - 1.0) * 100.0
            payload["outcome"] = {
                "status": "RESOLVED",
                "activated": True,
                "activated_at": activated_at.astimezone(WARSAW).isoformat(timespec="seconds"),
                "activation_evidence": evidence,
                "entry_price": gpw.round2(entry),
                "exit_price": gpw.round2(exit_price),
                "exit_reason": reason,
                "exit_bar_at": bar["timestamp"].astimezone(WARSAW).isoformat(timespec="seconds"),
                "return_percent": round(gross - COST_PERCENT, 3),
                "gross_return_percent": round(gross, 3),
                "r_multiple": round((exit_price - entry) / risk, 3),
                "cost_assumption_percent": COST_PERCENT,
                "settlement_policy": "intraday_5m_stop_first_if_same_bar",
                "resolved_at": now.astimezone(WARSAW).isoformat(timespec="seconds"),
            }
            return True

    if now.astimezone(WARSAW).date() > expiry:
        # Prefer the actual last 5-minute bar on/before the expiry session so
        # horizon exits get a real market timestamp rather than a guessed time.
        expiry_bars = [
            bar for bar in eligible
            if bar["timestamp"].astimezone(WARSAW).date() <= expiry
        ]
        if expiry_bars:
            exit_bar = expiry_bars[-1]
            exit_price = exit_bar["close"]
            gross = (exit_price / entry - 1.0) * 100.0
            payload["outcome"] = {
                "status": "RESOLVED",
                "activated": True,
                "activated_at": activated_at.astimezone(WARSAW).isoformat(timespec="seconds"),
                "activation_evidence": evidence,
                "entry_price": gpw.round2(entry),
                "exit_price": gpw.round2(exit_price),
                "exit_reason": "koniec_horyzontu",
                "exit_bar_at": exit_bar["timestamp"].astimezone(WARSAW).isoformat(timespec="seconds"),
                "return_percent": round(gross - COST_PERCENT, 3),
                "gross_return_percent": round(gross, 3),
                "r_multiple": round((exit_price - entry) / risk, 3),
                "cost_assumption_percent": COST_PERCENT,
                "settlement_policy": "expiry_last_observed_5m_close",
                "resolved_at": now.astimezone(WARSAW).isoformat(timespec="seconds"),
            }
            return True

        # Legacy fallback keeps the price if exact intraday timestamp evidence is
        # unavailable. The timestamp is deliberately omitted rather than guessed.
        daily = gpw.fetch_yahoo_bars(str(selection.get("symbol")), range_value="3mo")
        eligible_daily = [row for row in daily if row.day <= expiry and row.day >= datetime.fromisoformat(payload["date"]).date()]
        if eligible_daily:
            exit_price = eligible_daily[-1].close
            gross = (exit_price / entry - 1.0) * 100.0
            payload["outcome"] = {
                "status": "RESOLVED",
                "activated": True,
                "activated_at": activated_at.astimezone(WARSAW).isoformat(timespec="seconds"),
                "activation_evidence": evidence,
                "entry_price": gpw.round2(entry),
                "exit_price": gpw.round2(exit_price),
                "exit_reason": "koniec_horyzontu",
                "return_percent": round(gross - COST_PERCENT, 3),
                "gross_return_percent": round(gross, 3),
                "r_multiple": round((exit_price - entry) / risk, 3),
                "cost_assumption_percent": COST_PERCENT,
                "settlement_policy": "expiry_close_without_exact_timestamp",
                "resolved_at": now.astimezone(WARSAW).isoformat(timespec="seconds"),
            }
            return True

    outcome.update({
        "status": "PENDING",
        "activated": True,
        "activated_at": activated_at.astimezone(WARSAW).isoformat(timespec="seconds"),
        "activation_evidence": evidence,
        "entry_price": gpw.round2(entry),
        "last_checked_at": now.astimezone(WARSAW).isoformat(timespec="seconds"),
    })
    payload["outcome"] = outcome
    return True


def build_index(history: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    trades = []
    for row in sorted(history, key=lambda item: str(item.get("date") or ""), reverse=True):
        if row.get("decision") != "TRANSAKCJA":
            continue
        selection = row.get("selection") or {}
        outcome = row.get("outcome") or {}
        trades.append({
            "date": row.get("date"),
            "generated_at": row.get("generated_at"),
            "ticker": selection.get("ticker"),
            "symbol": selection.get("symbol"),
            "name": selection.get("name"),
            "entry_zone": selection.get("entry_zone"),
            "stop": selection.get("stop"),
            "target": selection.get("target"),
            "score": selection.get("score"),
            "valid_until": selection.get("valid_until"),
            "outcome": outcome,
        })
    return {
        "schema_version": "gpw-daily-pick-history-index-v1",
        "updated_at": now.astimezone(WARSAW).isoformat(timespec="seconds"),
        "selected_trades": len(trades),
        "resolved_trades": sum(1 for row in trades if (row.get("outcome") or {}).get("status") == "RESOLVED"),
        "trades": trades,
    }


def main() -> None:
    now = datetime.now(timezone.utc)
    changed = 0
    for path in sorted(gpw.HISTORY_DIR.glob("????-??-??.json")):
        payload = gpw.load_json(path)
        if not isinstance(payload, dict):
            continue
        try:
            if resolve_pending(payload, now):
                atomic(path, payload)
                changed += 1
        except Exception as exc:
            outcome = dict(payload.get("outcome") or {})
            outcome["monitor_warning"] = f"{type(exc).__name__}: {exc}"
            outcome["last_checked_at"] = now.astimezone(WARSAW).isoformat(timespec="seconds")
            payload["outcome"] = outcome
            atomic(path, payload)

    # Future GPW trades fail closed if an activated entry or resolved exit lacks
    # its durable timestamp. Legacy records remain untouched when evidence is absent.
    timestamp_normalizer.validate_gpw_history(gpw.HISTORY_DIR)

    history = gpw.all_history()
    metrics = gpw.metric_summary(history)
    atomic(gpw.METRICS_PATH, metrics)
    index = build_index(history, now)
    atomic(INDEX_PATH, index)

    current = gpw.load_json(gpw.PUBLIC_PATH)
    if isinstance(current, dict):
        today_row = next((row for row in history if row.get("date") == current.get("date")), None)
        if today_row and today_row.get("decision") == "TRANSAKCJA":
            current["outcome"] = today_row.get("outcome")
        current["metrics"] = metrics
        current["history_index"] = {
            "url": "/data/investments/gpw_daily_pick_history_index.json",
            "selected_trades": index["selected_trades"],
            "resolved_trades": index["resolved_trades"],
        }
        atomic(gpw.PUBLIC_PATH, current)
    print(json.dumps({"status": "OK", "changed_records": changed, "metrics": metrics, "history": index["selected_trades"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

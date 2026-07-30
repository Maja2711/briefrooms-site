#!/usr/bin/env python3
"""Fail-safe SL/TP audit for current weekly paper positions.

Scans completed intraday OHLC bars independently of the main strategy runtime.
The primary source is Yahoo Finance 5-minute data; a 60-minute fallback is used
when the 5-minute response is unavailable or incomplete. Threshold execution is
recorded at the frozen SL/TP level, never at a later observed price.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
AUDIT_PATH = ROOT / "data" / "investments" / "intraday_risk_audit.json"


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def sf(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if pd.notna(number) else None
    except (TypeError, ValueError):
        return None


def current_week_path() -> Optional[Path]:
    files = sorted(WEEKLY_DIR.glob("*.json"), reverse=True)
    return files[0] if files else None


def download(symbol: str, interval: str, period: str) -> Optional[pd.DataFrame]:
    try:
        frame = yf.download(
            symbol,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=False,
            prepost=True,
            threads=False,
        )
        if frame is None or frame.empty:
            return None
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = frame.columns.get_level_values(0)
        if "High" not in frame.columns or "Low" not in frame.columns:
            return None
        index = pd.to_datetime(frame.index, utc=True, errors="coerce")
        frame = frame.copy()
        frame.index = index
        return frame[~frame.index.isna()].sort_index()
    except Exception:
        return None


def first_hit(frame: pd.DataFrame, side: str, sl: float, tp: float, after: datetime) -> Optional[tuple[str, float, datetime]]:
    bars = frame[frame.index > after]
    for ts, row in bars.iterrows():
        high = sf(row.get("High"))
        low = sf(row.get("Low"))
        if high is None or low is None:
            continue
        if side == "long":
            sl_hit, tp_hit = low <= sl, high >= tp
        else:
            sl_hit, tp_hit = high >= sl, low <= tp
        # Conservative rule when both thresholds occur inside the same bar.
        if sl_hit:
            return "stop_loss", sl, ts.to_pydatetime()
        if tp_hit:
            return "take_profit", tp, ts.to_pydatetime()
    return None


def set_result(item: dict[str, Any], exit_price: float) -> None:
    entry = sf(item.get("entry_price"))
    if entry is None:
        return
    side = str(item.get("direction") or "")
    move = exit_price - entry if side == "long" else entry - exit_price
    pct = move / entry * 100.0
    if str(item.get("instrument_id")) == "eurusd":
        notional = sf(item.get("notional_eur")) or 10000.0
        value = move * notional
        units = move / 0.0001
    else:
        notional = sf(item.get("notional_usd")) or 10000.0
        value = move / entry * notional
        units = pct if str(item.get("instrument_id")) == "btcusd" else move
    item["result"] = "profit" if value > 0 else "loss" if value < 0 else "flat"
    item["result_value"] = round(value, 8)
    item["result_percent"] = round(pct, 4)
    item["result_units"] = round(units, 8)
    item["result_currency"] = "USD"


def audit() -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "checked_at": checked_at.isoformat(timespec="seconds"),
        "status": "no_week_file",
        "closed": [],
        "kept": [],
        "errors": [],
    }
    path = current_week_path()
    if path is None:
        write_json(AUDIT_PATH, report)
        return report

    week = read_json(path)
    changed = False
    for item in week.get("instruments") or []:
        if not isinstance(item, dict):
            continue
        iid = str(item.get("instrument_id") or "")
        side = str(item.get("direction") or "")
        if side not in {"long", "short"} or sf(item.get("entry_price")) is None or sf(item.get("exit_price")) is not None:
            continue
        plan = item.get("risk_plan") if isinstance(item.get("risk_plan"), dict) else {}
        sl, tp = sf(plan.get("stop_loss_price")), sf(plan.get("take_profit_price"))
        if sl is None or tp is None:
            report["errors"].append({"instrument_id": iid, "reason": "missing_sl_tp"})
            continue
        raw_start = item.get("entry_captured_at")
        try:
            after = pd.Timestamp(raw_start).to_pydatetime().astimezone(timezone.utc)
        except Exception:
            report["errors"].append({"instrument_id": iid, "reason": "invalid_entry_timestamp"})
            continue

        symbol = str(item.get("symbol") or "")
        source_interval = "5m"
        frame = download(symbol, "5m", "5d")
        if frame is None or frame.empty or frame.index.min().to_pydatetime() > after:
            source_interval = "60m_fallback"
            frame = download(symbol, "60m", "1mo")
        if frame is None or frame.empty:
            report["errors"].append({"instrument_id": iid, "reason": "intraday_data_unavailable"})
            continue

        hit = first_hit(frame, side, sl, tp, after)
        item["last_risk_review_at"] = checked_at.isoformat(timespec="seconds")
        item["last_risk_review_source"] = f"Yahoo Finance:{symbol}:{source_interval}:OHLC_threshold_audit"
        changed = True
        if hit is None:
            report["kept"].append({"instrument_id": iid, "source_interval": source_interval})
            continue

        reason, level, ts = hit
        item["exit_price"] = level
        item["exit_captured_at"] = ts.astimezone(timezone.utc).isoformat(timespec="seconds")
        item["exit_source"] = f"Yahoo Finance:{symbol}:{source_interval}:OHLC_threshold_audit"
        item["exit_reason"] = reason
        item["exit_execution_model"] = "frozen_planned_level_first_intraday_bar_conservative"
        item["risk_status"] = "stop_loss_hit" if reason == "stop_loss" else "take_profit_hit"
        item["trade_status"] = "closed"
        item["continuous_exposure_active"] = False
        item["continuous_exposure_status"] = "closed_by_risk_exit"
        set_result(item, level)
        report["closed"].append({
            "instrument_id": iid,
            "reason": reason,
            "exit_price": level,
            "bar_timestamp": item["exit_captured_at"],
            "source_interval": source_interval,
        })

    if changed:
        write_json(path, week)
    report["status"] = "completed"
    report["week_path"] = str(path.relative_to(ROOT))
    report["changed"] = changed
    write_json(AUDIT_PATH, report)
    return report


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False))

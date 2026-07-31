#!/usr/bin/env python3
"""Data boundaries, freshness checks and market-data adapters for BRACE."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from brace_portfolio_config import EngineConfig

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PORTFOLIO_PATH = ROOT / "data" / "investments" / "portfolio_10k.json"
ENGINE_DATA_ROOT = ROOT / "data" / "portfolio10k"

IMMUTABLE_POSITION_FIELDS = (
    "id",
    "broker_symbol",
    "market_symbol",
    "entry_date",
    "entry_timestamp_utc",
    "entry_price",
    "entry_price_type",
    "entry_fx_to_pln",
    "entry_value_local",
    "entry_notional_pln",
    "entry_fee_pln",
    "entry_value_pln",
    "quantity",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def baseline_invariants(portfolio: Mapping[str, Any]) -> Dict[str, Any]:
    positions = []
    for position in portfolio.get("positions", []) or []:
        positions.append(
            {key: copy.deepcopy(position.get(key)) for key in IMMUTABLE_POSITION_FIELDS}
        )
    return {
        "portfolio_id": portfolio.get("portfolio_id"),
        "launch_date": portfolio.get("launch_date"),
        "model_entry_date": portfolio.get("model_entry_date"),
        "positions": positions,
        "closed_positions": copy.deepcopy(portfolio.get("closed_positions") or []),
        "staged_entry_batches": copy.deepcopy(
            portfolio.get("staged_entry_batches") or []
        ),
    }


def assert_baseline_unchanged(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> None:
    if baseline_invariants(before) != baseline_invariants(after):
        raise ValueError("Baseline entry prices or append-only history were modified")


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def data_freshness_report(
    portfolio: Mapping[str, Any],
    config: EngineConfig,
    now: datetime,
    mode: str,
    previous_analysis: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    max_age = (
        config.monitoring_max_price_age_hours
        if mode == "monitor"
        else config.analysis_max_price_age_hours
    )
    reasons: List[str] = []
    rows: List[Dict[str, Any]] = []
    missing = 0
    previous_prices = {
        str(item.get("instrument_id")): _finite(item.get("current_price"))
        for item in (previous_analysis or {}).get("positions", []) or []
    }

    instruments = list(portfolio.get("positions", []) or [])
    benchmark = portfolio.get("benchmark") or {}
    if benchmark:
        instruments.append({"id": "__benchmark__", **benchmark})
    else:
        reasons.append("BENCHMARK_UNAVAILABLE")

    for item in instruments:
        instrument_id = str(item.get("id") or item.get("broker_symbol") or "")
        item_max_age = max_age
        if str(item.get("market_status") or "").lower() == "closed":
            # A completed prior-session close remains valid while that market is shut.
            item_max_age = max(item_max_age, config.analysis_max_price_age_hours)
        price = _finite(item.get("current_price"))
        fx = _finite(item.get("current_fx_to_pln"))
        observed = parse_timestamp(item.get("current_price_updated_at"))
        fx_observed = parse_timestamp(item.get("current_fx_updated_at"))
        item_reasons: List[str] = []
        age_hours = None
        if price is None or price <= 0:
            item_reasons.append("PRICE_UNAVAILABLE")
        if str(item.get("currency") or "PLN") != "PLN" and (fx is None or fx <= 0):
            item_reasons.append("FX_UNAVAILABLE")
        if observed is None:
            item_reasons.append("PRICE_TIMESTAMP_MISSING")
        else:
            age_hours = (now - observed).total_seconds() / 3600
            if age_hours < -0.1:
                item_reasons.append("PRICE_TIMESTAMP_IN_FUTURE")
            elif age_hours > item_max_age:
                item_reasons.append("PRICE_STALE")
        if str(item.get("currency") or "PLN") != "PLN":
            if fx_observed is None:
                item_reasons.append("FX_TIMESTAMP_MISSING")
            elif (now - fx_observed).total_seconds() / 3600 > max_age:
                item_reasons.append("FX_STALE")
            elif (now - fx_observed).total_seconds() < -300:
                item_reasons.append("FX_TIMESTAMP_IN_FUTURE")
        previous = previous_prices.get(instrument_id)
        if price and previous and previous > 0:
            jump = abs(price / previous - 1.0)
            if jump > config.maximum_single_price_jump:
                item_reasons.append("PRICE_JUMP_REQUIRES_CORPORATE_ACTION_REVIEW")
        if item_reasons:
            missing += 1
        rows.append(
            {
                "instrument_id": instrument_id,
                "price_age_hours": (
                    round(age_hours, 3) if age_hours is not None else None
                ),
                "status": "OK" if not item_reasons else "ERROR",
                "reasons": item_reasons,
            }
        )

    if missing > config.maximum_missing_instruments:
        reasons.append("TOO_MANY_INVALID_INSTRUMENTS")
    reasons.extend(
        sorted(
            {
                reason
                for row in rows
                for reason in row["reasons"]
                if reason
                in {
                    "PRICE_TIMESTAMP_IN_FUTURE",
                    "FX_TIMESTAMP_IN_FUTURE",
                    "PRICE_JUMP_REQUIRES_CORPORATE_ACTION_REVIEW",
                }
            }
        )
    )
    if config.safe_mode_on_stale_data and any(
        reason in {"PRICE_STALE", "FX_STALE"}
        for row in rows
        for reason in row["reasons"]
    ):
        reasons.append("STALE_MARKET_DATA")

    quality = max(0.0, 1.0 - missing / max(1, len(instruments)))
    return {
        "status": "SAFE_MODE" if reasons else "READY",
        "safe_mode": bool(reasons),
        "reasons": sorted(set(reasons)),
        "quality_score": round(quality * 100, 2),
        "checked_at": now.isoformat(timespec="seconds"),
        "maximum_age_hours": max_age,
        "instruments": rows,
    }


@dataclass
class InstrumentData:
    symbol: str
    history: List[Dict[str, Any]]
    fundamentals: Dict[str, Any]
    observed_at: str
    errors: List[str]


class YFinanceProvider:
    """Network adapter kept outside deterministic scoring and decision code."""

    @staticmethod
    def _chart_history(symbol: str, period: str, interval: str) -> List[Dict[str, Any]]:
        params = urlencode(
            {
                "range": period,
                "interval": interval,
                "events": "history",
                "includeAdjustedClose": "true",
            }
        )
        request = Request(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?{params}",
            headers={"User-Agent": "BriefRooms-BRACE/1.0"},
        )
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
        result = ((payload.get("chart") or {}).get("result") or [None])[0] or {}
        timestamps = result.get("timestamp") or []
        indicators = result.get("indicators") or {}
        adjusted = ((indicators.get("adjclose") or [{}])[0]).get("adjclose") or []
        closes = ((indicators.get("quote") or [{}])[0]).get("close") or []
        values = adjusted if len(adjusted) == len(timestamps) else closes
        rows: Dict[str, float] = {}
        for timestamp, raw_value in zip(timestamps, values):
            value = _finite(raw_value)
            if value is None or value <= 0:
                continue
            day = datetime.fromtimestamp(int(timestamp), timezone.utc).date().isoformat()
            rows[day] = value
        return [{"date": day, "close": rows[day]} for day in sorted(rows)]

    def history(
        self,
        symbol: str,
        period: str = "10y",
        interval: str = "1d",
    ) -> List[Dict[str, Any]]:
        try:
            import yfinance as yf

            frame = yf.Ticker(symbol).history(
                period=period,
                interval=interval,
                auto_adjust=True,
                actions=False,
            )
            rows: List[Dict[str, Any]] = []
            if frame is not None and not frame.empty:
                for index, row in frame.iterrows():
                    value = _finite(row.get("Close"))
                    if value is None or value <= 0:
                        continue
                    rows.append({"date": index.date().isoformat(), "close": value})
                if rows:
                    return rows
        except Exception:
            pass
        return self._chart_history(symbol, period, interval)

    def fundamentals(self, symbol: str) -> Dict[str, Any]:
        import yfinance as yf

        try:
            info = yf.Ticker(symbol).get_info() or {}
        except Exception:
            return {}
        keys = (
            "revenueGrowth",
            "earningsGrowth",
            "profitMargins",
            "operatingMargins",
            "freeCashflow",
            "marketCap",
            "debtToEquity",
            "returnOnEquity",
            "returnOnAssets",
            "trailingPE",
            "forwardPE",
            "priceToSalesTrailing12Months",
            "enterpriseToEbitda",
            "recommendationMean",
            "averageVolume",
            "currency",
        )
        return {key: info.get(key) for key in keys if info.get(key) is not None}

    def fetch(self, symbol: str) -> InstrumentData:
        errors: List[str] = []
        try:
            history = self.history(symbol)
        except Exception as exc:
            history = []
            errors.append(f"HISTORY_UNAVAILABLE:{type(exc).__name__}")
        try:
            fundamentals = self.fundamentals(symbol)
        except Exception as exc:
            fundamentals = {}
            errors.append(f"FUNDAMENTALS_UNAVAILABLE:{type(exc).__name__}")
        return InstrumentData(
            symbol=symbol,
            history=history,
            fundamentals=fundamentals,
            observed_at=utc_now().isoformat(timespec="seconds"),
            errors=errors,
        )


def source_metadata(
    portfolio_path: Path = BASELINE_PORTFOLIO_PATH,
) -> Dict[str, Any]:
    return {
        "baseline_portfolio_path": str(portfolio_path.relative_to(ROOT)).replace(
            "\\", "/"
        ),
        "baseline_portfolio_sha256": file_sha256(portfolio_path),
        "source_type": "repository_state_plus_market_data",
    }


def universe_records(payload: Mapping[str, Any]) -> Iterable[Dict[str, Any]]:
    for item in payload.get("instruments", []) or []:
        if item.get("active") and item.get("availability") == "AVAILABLE":
            yield dict(item)

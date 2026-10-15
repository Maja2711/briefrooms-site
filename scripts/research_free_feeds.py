#!/usr/bin/env python3
"""Independent, non-trading research feed QA. NO Belief/EPE/website authority.

CheckMySwap: verified public USD/EUR dated OIS PAR curve snapshot (not live OIS DFs).
Twelve Data: personal/testing EUR/USD 1-minute bars only when entitled/opted in.
Persist metadata and health only, never licensed bar or rate values.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

CHECKMYSWAP_URL = "https://www.checkmyswap.com/api/curves/all"
TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_CHECKMYSWAP_AGE_DAYS = 3
MAX_TWELVE_DATA_BAR_AGE_MINUTES = 5
SAFE_AUTH_ENV = "TWELVE_DATA_RESEARCH_ALLOWED"
TWELVE_API_KEY_ENV = "TWELVE_DATA_RESEARCH_API_KEY"
SOURCE_SCHEMA = "briefrooms-free-research-feed-health-v1"

def _utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp_missing")
    # Twelve Data explicitly requested with timezone=UTC returns naive UTC.
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp_invalid") from exc
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _date(value: Any) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("snapshot_date_invalid")
    return date.fromisoformat(value)


def _request_json(url: str, headers: Mapping[str, str]) -> Mapping[str, Any]:
    req = urllib.request.Request(url, headers=dict(headers))
    with urllib.request.urlopen(req, timeout=10) as response:
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise ValueError("upstream_payload_too_large")
        if response.status != 200:
            raise ValueError("upstream_unexpected_http_status")
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError("upstream_schema_invalid")
    return data


def _par_curve(row: Any, currency: str, now: datetime) -> dict[str, Any]:
    if not isinstance(row, Mapping) or row.get("currency") != currency:
        raise ValueError(f"checkmyswap_{currency}_currency_missing")
    stamp = _date(row.get("date"))
    age = (now.date() - stamp).days
    if age < 0:
        raise ValueError(f"checkmyswap_{currency}_future_date")
    points = row.get("curve")
    if not isinstance(points, list) or not points:
        raise ValueError(f"checkmyswap_{currency}_curve_missing")
    tenors: set[str] = set()
    rates_by_tenor: dict[str, float] = {}
    counts = {"close": 0, "thin_close": 0, "derived": 0, "all_day": 0, "other": 0}
    total_records = 0
    for point in points:
        if not isinstance(point, Mapping):
            raise ValueError(f"checkmyswap_{currency}_malformed_point")
        tenor = str(point.get("tenor") or "")
        if not re.fullmatch(r"(?:[1-9]|[12][0-9]|30)Y", tenor) or tenor in tenors:
            raise ValueError(f"checkmyswap_{currency}_unexpected_or_duplicate_tenor")
        tenors.add(tenor)
        if point.get("date") is not None and point.get("date") != stamp.isoformat():
            raise ValueError(f"checkmyswap_{currency}_point_date_mismatch")
        try:
            rate = float(point["rate"])
            trades = int(point.get("trades") or 0)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"checkmyswap_{currency}_invalid_rate_or_records") from exc
        if not math.isfinite(rate) or not -5 <= rate <= 40 or trades < 0:
            raise ValueError(f"checkmyswap_{currency}_invalid_rate_or_records")
        rates_by_tenor[tenor] = rate
        method = str(point.get("method") or "").lower().replace("-", "_")
        if "thin" in method:
            counts["thin_close"] += 1
        elif method in {"close", "settlement"}:
            counts["close"] += 1
        elif method.startswith("derived"):
            counts["derived"] += 1
        elif method in {"all_day", "allday"}:
            counts["all_day"] += 1
        else:
            counts["other"] += 1
        total_records += trades
    if "1Y" not in tenors:
        raise ValueError(f"checkmyswap_{currency}_missing_1Y")
    return {"currency": currency, "observation_date": stamp.isoformat(), "age_days": age,
            "source_ref": f"https://www.checkmyswap.com/api/curves/{currency}?date={stamp.isoformat()}",
            "tenors": sorted(tenors, key=lambda v: int(v[:-1])),
            "records": total_records, "method_counts": counts,
            "par_slope_1y_to_2y_bps": (round((rates_by_tenor["2Y"] - rates_by_tenor["1Y"]) * 100, 4)
                                        if "2Y" in rates_by_tenor else None),
            "contains_3m_6m_ois_discount_factors": False}


def evaluate_checkmyswap(payload: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    at = now.astimezone(timezone.utc)
    result: dict[str, Any] = {
        "source": "checkmyswap", "role": "dated_ois_par_curve_research_only",
        "status": "unavailable", "research_usable": False,
        "live_policy_evidence_authorized": False, "trade_authority": False,
        "daily_epe_price_authority": False, "no_forward_curve_substitution": True,
    }
    try:
        usd = _par_curve(payload.get("USD"), "USD", at)
        eur = _par_curve(payload.get("EUR"), "EUR", at)
        if usd["observation_date"] != eur["observation_date"]:
            result["reason"] = "different_currency_snapshot_dates"
            result["usd_date"] = usd["observation_date"]
            result["eur_date"] = eur["observation_date"]
            return result
        age = max(usd["age_days"], eur["age_days"])
        result.update({
            "observation_date": usd["observation_date"],
            "age_days": age,
            "tenors_usd": usd["tenors"], "tenors_eur": eur["tenors"],
            "usd_accepted_records": usd["records"], "eur_accepted_records": eur["records"],
            "method_counts_usd": usd["method_counts"], "method_counts_eur": eur["method_counts"],
            "relative_usd_minus_eur_par_slope_1y_2y_bps": (
                round(usd["par_slope_1y_to_2y_bps"] - eur["par_slope_1y_to_2y_bps"], 4)
                if usd["par_slope_1y_to_2y_bps"] is not None and eur["par_slope_1y_to_2y_bps"] is not None else None),
            "research_metric_only_not_ois_forward": True,
            "source_refs": [usd["source_ref"], eur["source_ref"]],
            "status": "current_daily_archive" if age <= MAX_CHECKMYSWAP_AGE_DAYS else "stale_daily_archive",
            "research_usable": age <= MAX_CHECKMYSWAP_AGE_DAYS,
            "reason": "par_rates_1Y_plus_only_not_3M_6M_12M_OIS_discount_factors",
        })
    except (TypeError, ValueError, KeyError) as exc:
        result["reason"] = str(exc)[:100]
    return result


def fetch_checkmyswap(*, fetcher: Callable[..., Mapping[str, Any]] = _request_json) -> Mapping[str, Any]:
    return fetcher(CHECKMYSWAP_URL, {
        "Accept": "application/json",
        "User-Agent": "BriefRooms-Free-Feed-Research/1.0 (https://briefrooms.com)",
    })


def evaluate_twelvedata(payload: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source": "twelvedata", "role": "eurusd_private_research_comparison_only",
        "status": "invalid_data", "trade_authority": False,
        "daily_epe_price_authority": False, "public_redistribution": False,
    }
    if payload.get("status") == "error" or "code" in payload:
        result["reason"] = "api_error"
        result["upstream_error_code"] = str(payload.get("code", "unknown"))[:16]
        return result
    meta = payload.get("meta")
    if not isinstance(meta, Mapping) or meta.get("symbol") != "EUR/USD" or meta.get("interval") != "1min":
        result["reason"] = "eurusd_1min_metadata_invalid"
        return result
    values = payload.get("values")
    if not isinstance(values, list) or not values:
        result["reason"] = "eurusd_1min_bars_missing"
        return result
    try:
        stamps: list[datetime] = []
        for row in values:
            if not isinstance(row, Mapping):
                raise ValueError("bar_invalid")
            # API is called with explicit timezone=UTC, not vendor default.
            instant = _utc(row.get("datetime"))
            raw = row.get("close")
            if isinstance(raw, bool):
                raise ValueError("close_invalid")
            close = float(raw)
            if not math.isfinite(close) or not 0.3 <= close <= 3.0:
                raise ValueError("close_invalid")
            stamps.append(instant)
        if len(set(stamps)) != len(stamps):
            raise ValueError("duplicate_bars")
        latest = max(stamps)
        lag = (now.astimezone(timezone.utc) - latest).total_seconds() / 60.0
        if lag < -2:
            raise ValueError("future_bar")
        result.update({
            "bar_observed_at": latest.isoformat().replace("+00:00", "Z"),
            "bar_age_minutes": round(lag, 3),
            "bar_count": len(values),
            "status": "fresh_private_comparison" if lag <= MAX_TWELVE_DATA_BAR_AGE_MINUTES else "stale_private_comparison",
            "reason": "comparison_only_no_executable_bid_ask_or_provider_license",
        })
    except (TypeError, ValueError, KeyError) as exc:
        result["reason"] = str(exc)[:90]
    return result


def fetch_twelvedata(apikey: str, *,
                     fetcher: Callable[..., Mapping[str, Any]] = _request_json) -> Mapping[str, Any]:
    if not apikey or any(c.isspace() for c in apikey):
        raise ValueError("twelvedata_api_key_missing")
    url = TWELVE_DATA_URL + "?" + urllib.parse.urlencode({
        "symbol": "EUR/USD", "interval": "1min", "outputsize": 3,
        "timezone": "UTC", "order": "desc",
    })
    # Keep bearer credential in a request HEADER, never URL, artifact or logs.
    return fetcher(url, {"Accept": "application/json", "Authorization": "apikey " + apikey,
                         "User-Agent": "BriefRooms-Private-FX-Research/1.0"})


def collect(now: datetime, *, checkmyswap_fetcher: Callable[..., Mapping[str, Any]] = fetch_checkmyswap,
            td_fetcher: Callable[..., Mapping[str, Any]] = fetch_twelvedata,
            td_key: str | None = None, research_allowed: bool | None = None) -> dict[str, Any]:
    now = now.astimezone(timezone.utc)
    output: dict[str, Any] = {
        "schema_version": SOURCE_SCHEMA,
        "collected_at": now.isoformat().replace("+00:00", "Z"),
        "authority": {"mode": "RESEARCH_ONLY", "production_trading": False,
                      "policy_differential_evidence": False, "daily_epe": False,
                      "website_quote": False},
    }
    try:
        output["checkmyswap"] = evaluate_checkmyswap(checkmyswap_fetcher(), now)
    except Exception as exc:
        output["checkmyswap"] = {"source": "checkmyswap", "status": "source_unavailable",
                                 "reason": type(exc).__name__, "trade_authority": False}
    if td_key is None:
        td_key = os.environ.get(TWELVE_API_KEY_ENV, "")
    if research_allowed is None:
        research_allowed = os.environ.get(SAFE_AUTH_ENV, "").strip() == "1"
    if not research_allowed:
        output["twelvedata"] = {
            "source": "twelvedata", "status": "disabled_by_usage_rights_gate",
            "reason": "free_plan_is_testing_internal_only_require_explicit_research_permission",
            "trade_authority": False, "public_redistribution": False,
        }
    elif not td_key:
        output["twelvedata"] = {
            "source": "twelvedata", "status": "api_key_not_configured",
            "trade_authority": False, "public_redistribution": False,
        }
    else:
        try:
            output["twelvedata"] = evaluate_twelvedata(td_fetcher(td_key), now)
        except Exception as exc:
            output["twelvedata"] = {
                "source": "twelvedata", "status": "source_unavailable",
                "reason": type(exc).__name__,
                "trade_authority": False, "public_redistribution": False,
            }
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="Metadata-only research health JSON")
    args = parser.parse_args()
    report = collect(datetime.now(timezone.utc))
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print("FREE_MARKET_FEEDS", json.dumps({
        "checkmyswap_status": report["checkmyswap"]["status"],
        "checkmyswap_date": report["checkmyswap"].get("observation_date"),
        "twelvedata_status": report["twelvedata"]["status"],
        "authority": report["authority"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

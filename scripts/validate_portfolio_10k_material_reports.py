#!/usr/bin/env python3
"""Strict validator for Portfolio 10K material-event reports."""
from __future__ import annotations

import argparse
import math
import urllib.parse
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import portfolio_10k_weekly as base

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORTS_PATH = ROOT / "data" / "investments" / "portfolio_10k_material_reports.json"

EVENT_TYPES = {
    "EARNINGS", "GUIDANCE", "PRICE_ALERT", "ANALYST_CHANGE", "REGULATORY",
    "POLITICAL", "FX", "OPERATIONS", "DIVIDEND", "BUYBACK", "MATERIAL_NEWS",
}
IMPACTS = {"POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED"}
SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
MODEL_ACTIONS = {"HOLD", "ADD_SMALL", "TRIM", "WAIT", "THESIS_REVIEW"}
QUOTE_KINDS = {"BID", "ASK", "LAST", "CLOSE", "INDICATIVE"}
REQUIRED_TEXT = ("title_pl", "title_en", "summary_pl", "summary_en")
SNAPSHOT_NUMBERS = {
    "quantity", "entry_price", "entry_fx_to_pln", "cost_basis_local",
    "market_value_local", "unrealized_pnl_local", "unrealized_pnl_percent",
    "current_fx_to_pln", "cost_basis_pln", "market_value_pln",
    "unrealized_pnl_pln", "instrument_effect_pln", "fx_effect_pln", "entry_fee_pln",
}


def _error(errors: List[str], report_id: str, field: str, message: str) -> None:
    errors.append(f"report[{report_id}].{field}: {message}")


def _finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _valid_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _valid_https(value: Any) -> bool:
    try:
        parsed = urllib.parse.urlparse(str(value or ""))
    except ValueError:
        return False
    return parsed.scheme.lower() == "https" and bool(parsed.netloc)


def _check_optional_numbers(
    errors: List[str], report_id: str, prefix: str, payload: Dict[str, Any]
) -> None:
    for field in SNAPSHOT_NUMBERS:
        value = payload.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or _finite_number(value) is None:
            _error(errors, report_id, f"{prefix}.{field}", "must be a finite number or null")


def _check_close(
    errors: List[str], report_id: str, field: str, actual: Any, expected: float,
    tolerance: float,
) -> None:
    number = _finite_number(actual)
    if number is not None and abs(number - expected) > tolerance:
        _error(errors, report_id, field, f"expected {expected:.6f} within tolerance {tolerance}")


def validate_payload(portfolio: Dict[str, Any], payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if payload.get("schema_version") != "1.0.0":
        errors.append("payload.schema_version: expected 1.0.0")
    if payload.get("portfolio_id") != portfolio.get("portfolio_id"):
        errors.append("payload.portfolio_id: does not match portfolio")
    if payload.get("last_updated_at") is not None and not _valid_timestamp(payload.get("last_updated_at")):
        errors.append("payload.last_updated_at: expected an ISO timestamp with timezone or null")
    reports = payload.get("reports")
    if not isinstance(reports, list):
        return errors + ["payload.reports: expected a list"]
    positions = {str(position.get("id")): position for position in portfolio.get("positions", [])}
    seen_ids = set()
    for index, report in enumerate(reports):
        if not isinstance(report, dict):
            errors.append(f"report[{index}]: expected an object")
            continue
        report_id = str(report.get("id") or f"index-{index}")
        if not report.get("id"):
            _error(errors, report_id, "id", "required")
        elif report_id in seen_ids:
            _error(errors, report_id, "id", "duplicate id")
        seen_ids.add(report_id)
        position_id = str(report.get("position_id") or "")
        position = positions.get(position_id)
        if not position:
            _error(errors, report_id, "position_id", "does not reference a portfolio position")
        elif report.get("symbol") != position.get("broker_symbol"):
            _error(errors, report_id, "symbol", f"expected {position.get('broker_symbol')}")
        if not _valid_timestamp(report.get("published_at")):
            _error(errors, report_id, "published_at", "expected an ISO timestamp with timezone")
        if not _valid_date(report.get("event_date")):
            _error(errors, report_id, "event_date", "expected YYYY-MM-DD")
        for field, allowed in (
            ("type", EVENT_TYPES), ("severity", SEVERITIES), ("impact", IMPACTS),
            ("model_action", MODEL_ACTIONS),
        ):
            if report.get(field) not in allowed:
                _error(errors, report_id, field, f"invalid value {report.get(field)!r}")
        if not isinstance(report.get("category"), str) or not report.get("category", "").strip():
            _error(errors, report_id, "category", "required non-empty text")
        score = report.get("impact_score")
        if score is not None and (isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5):
            _error(errors, report_id, "impact_score", "expected an integer from 1 to 5 or null")
        for field in REQUIRED_TEXT:
            if not isinstance(report.get(field), str) or not report.get(field, "").strip():
                _error(errors, report_id, field, "required non-empty text")
        for field in ("thesis_effect_pl", "thesis_effect_en"):
            if report.get(field) is not None and not isinstance(report.get(field), str):
                _error(errors, report_id, field, "expected text or null")

        sources = report.get("sources")
        if not isinstance(sources, list) or not sources:
            _error(errors, report_id, "sources", "at least one source is required")
        else:
            for source_index, source in enumerate(sources):
                if not isinstance(source, dict):
                    _error(errors, report_id, f"sources[{source_index}]", "expected an object")
                    continue
                if not isinstance(source.get("label"), str) or not source.get("label", "").strip():
                    _error(errors, report_id, f"sources[{source_index}].label", "required")
                if not _valid_https(source.get("url")):
                    _error(errors, report_id, f"sources[{source_index}].url", "only https URLs are allowed")

        quote = report.get("quote")
        if quote is not None:
            if not isinstance(quote, dict):
                _error(errors, report_id, "quote", "expected an object or null")
            else:
                value = _finite_number(quote.get("value"))
                if isinstance(quote.get("value"), bool) or not isinstance(quote.get("value"), (int, float)) or value is None or value <= 0:
                    _error(errors, report_id, "quote.value", "expected a finite positive number")
                if quote.get("kind") not in QUOTE_KINDS:
                    _error(errors, report_id, "quote.kind", f"invalid value {quote.get('kind')!r}")
                if not _valid_timestamp(quote.get("quoted_at")):
                    _error(errors, report_id, "quote.quoted_at", "expected an ISO timestamp with timezone")
                if not isinstance(quote.get("source"), str) or not quote.get("source", "").strip():
                    _error(errors, report_id, "quote.source", "required")
                expected_currency = position.get("currency") if position else None
                if expected_currency and quote.get("currency") != expected_currency:
                    _error(errors, report_id, "quote.currency", f"expected {expected_currency}")
                if quote.get("market") is not None and not isinstance(quote.get("market"), str):
                    _error(errors, report_id, "quote.market", "expected text or null")

        snapshot = report.get("position_snapshot")
        if snapshot is not None:
            if not isinstance(snapshot, dict):
                _error(errors, report_id, "position_snapshot", "expected an object or null")
            else:
                _check_optional_numbers(errors, report_id, "position_snapshot", snapshot)
                expected_currency = position.get("currency") if position else None
                if expected_currency and snapshot.get("position_currency") != expected_currency:
                    _error(errors, report_id, "position_snapshot.position_currency", f"expected {expected_currency}")
                for field in ("entry_fx_to_pln", "current_fx_to_pln"):
                    value = snapshot.get(field)
                    number = _finite_number(value) if value is not None else None
                    if value is not None and (number is None or number <= 0):
                        _error(errors, report_id, f"position_snapshot.{field}", "expected a finite positive number or null")
                quantity = _finite_number(snapshot.get("quantity"))
                if snapshot.get("quantity") is not None and (quantity is None or quantity <= 0):
                    _error(errors, report_id, "position_snapshot.quantity", "expected a finite positive number or null")
                cost_local = _finite_number(snapshot.get("cost_basis_local"))
                market_local = _finite_number(snapshot.get("market_value_local"))
                pnl_local = _finite_number(snapshot.get("unrealized_pnl_local"))
                pnl_percent = _finite_number(snapshot.get("unrealized_pnl_percent"))
                if cost_local is not None and market_local is not None and pnl_local is not None:
                    _check_close(errors, report_id, "position_snapshot.unrealized_pnl_local", pnl_local, market_local - cost_local, 0.02)
                    if cost_local > 0 and pnl_percent is not None:
                        _check_close(errors, report_id, "position_snapshot.unrealized_pnl_percent", pnl_percent, pnl_local / cost_local, 0.0002)
                cost_pln = _finite_number(snapshot.get("cost_basis_pln"))
                market_pln = _finite_number(snapshot.get("market_value_pln"))
                pnl_pln = _finite_number(snapshot.get("unrealized_pnl_pln"))
                if cost_pln is not None and market_pln is not None and pnl_pln is not None:
                    _check_close(errors, report_id, "position_snapshot.unrealized_pnl_pln", pnl_pln, market_pln - cost_pln, 0.03)
                instrument = _finite_number(snapshot.get("instrument_effect_pln"))
                fx_effect = _finite_number(snapshot.get("fx_effect_pln"))
                entry_fee = _finite_number(snapshot.get("entry_fee_pln"))
                if pnl_pln is not None and instrument is not None and fx_effect is not None:
                    expected = instrument + fx_effect - (entry_fee or 0.0)
                    _check_close(errors, report_id, "position_snapshot.unrealized_pnl_pln", pnl_pln, expected, 0.05)
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portfolio", type=Path, default=base.DATA_PATH)
    parser.add_argument("--reports", type=Path, default=DEFAULT_REPORTS_PATH)
    args = parser.parse_args()
    errors = validate_payload(base.load_json(args.portfolio), base.load_json(args.reports))
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)
    print("Portfolio 10K material reports are valid")


if __name__ == "__main__":
    main()

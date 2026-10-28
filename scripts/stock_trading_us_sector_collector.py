#!/usr/bin/env python3
"""Collect current US sectors from Nasdaq's bounded company endpoints.

The official company profile and quote summary are an alternative transport to
the large screener download. Both must agree with the existing official issuer
universe before a receipt enters the existing sourced-metadata ledger. This
adapter does not write positions, policies, learning trials or execution state.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping
import urllib.parse
import urllib.request

try:
    from scripts import stock_trading_sourced_metadata as metadata
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_sourced_metadata as metadata

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data/investments/stock_trading_portfolio.json"
UNIVERSE_PATH = ROOT / "data/investments/stock_trading_v2_universe/us.json"
REPORT_PATH = ROOT / "data/investments/stock_trading_us_sector_collection.json"
SCHEMA = "stock-trading-us-sector-collection-v1"
MAX_ISSUERS = 6
MAX_RESPONSE_BYTES = 1_000_000
REQUEST_TIMEOUT_SECONDS = 8

# These are source-label spelling aliases, not a cross-provider taxonomy map.
# Keep Nasdaq's published spaced labels in the existing risk view, which uses
# case folding. Retain each exact reported source value in the receipt as well.
SECTORS = {"".join(label.casefold().split()): label for label in (
    "Basic Materials", "Consumer Discretionary", "Consumer Staples", "Energy",
    "Finance", "Health Care", "Industrials", "Miscellaneous", "Real Estate",
    "Technology", "Telecommunications", "Utilities",
)}
EXCHANGES = {
    "NYSE": "NYSE", "NASDAQ": "NASDAQ", "NASDAQ-GS": "NASDAQ",
    "NASDAQ-GM": "NASDAQ", "NASDAQ-CM": "NASDAQ",
    "AMEX": "NYSE_AMERICAN", "NYSE AMERICAN": "NYSE_AMERICAN",
    "NYSE ARCA": "NYSE_ARCA", "BATS": "BATS",
}


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("sector collection requires timezone-aware time")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _completion(clock: Callable[[], datetime] | None, fallback: datetime, *, after: datetime) -> datetime:
    value = clock() if clock is not None else fallback
    _iso(value)
    if value < after:
        raise ValueError("sector collection clock moved backwards")
    return value


def fetch_json_bytes(url: str) -> bytes:
    """Fetch one complete bounded public response without authentication."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "api.nasdaq.com":
        raise ValueError("Nasdaq source URL invalid")
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "identity", "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/market-activity/stocks",
    })
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError("Nasdaq response status or final URL invalid")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if not raw or len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Nasdaq response empty or exceeds size bound")
    return raw


def _response(raw: bytes) -> Mapping[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Nasdaq response bytes invalid")
    payload = json.loads(raw.decode("utf-8"))
    if (not isinstance(payload, Mapping) or not isinstance(payload.get("status"), Mapping)
            or payload["status"].get("rCode") != 200 or not isinstance(payload.get("data"), Mapping)):
        raise ValueError("Nasdaq response data/status invalid")
    return payload["data"]


def _field(payload: Mapping[str, Any], key: str) -> str:
    field = payload.get(key)
    value = field.get("value") if isinstance(field, Mapping) else None
    if not isinstance(value, str) or not value.strip() or "\ufffd" in value:
        raise ValueError("Nasdaq profile/summary field missing: " + key)
    return value.strip()


def _sector(value: str) -> str:
    key = "".join(value.casefold().split())
    if key not in SECTORS:
        raise ValueError("Nasdaq sector label is not an approved source category")
    return SECTORS[key]


def observation_from_responses(
    position: Mapping[str, Any], issuer: Mapping[str, Any], *,
    profile_raw: bytes, summary_raw: bytes, profile_received_at: datetime,
    summary_received_at: datetime,
) -> dict[str, Any]:
    """Validate both endpoint payloads before constructing a receipt."""
    profile, summary = _response(profile_raw), _response(summary_raw)
    ticker = str(issuer.get("listing_symbol") or "")
    if (not ticker or _field(profile, "Symbol") != ticker
            or summary.get("symbol") != ticker or str(summary.get("assetClass") or "").upper() != "STOCKS"
            or not metadata._name_matches(str(issuer.get("name") or ""), _field(profile, "CompanyName"))):
        raise ValueError("Nasdaq profile/summary issuer identity mismatch")
    summary_data = summary.get("summaryData")
    if not isinstance(summary_data, Mapping):
        raise ValueError("Nasdaq quote summary fields missing")
    reported_exchange = _field(summary_data, "Exchange")
    if EXCHANGES.get(reported_exchange.upper()) != issuer.get("exchange"):
        raise ValueError("Nasdaq quote summary listing exchange mismatch")
    profile_sector, summary_sector = _field(profile, "Sector"), _field(summary_data, "Sector")
    canonical = _sector(profile_sector)
    if canonical != _sector(summary_sector):
        raise ValueError("Nasdaq profile and summary sectors disagree")
    if profile_received_at > summary_received_at:
        raise ValueError("Nasdaq profile receipt follows summary receipt")
    symbol = str(position.get("symbol") or position.get("ticker") or "")
    quoted = urllib.parse.quote(ticker, safe="")
    body = {
        "kind": "SECTOR", "market": "US", "symbol": symbol, "sector": canonical,
        "source": {
            "provider": "Nasdaq", "url": f"https://api.nasdaq.com/api/company/{quoted}/company-profile",
            "taxonomy": "NASDAQ_SECTOR", "response_sha256": hashlib.sha256(profile_raw).hexdigest(),
            "corroborating_exchange_url": f"https://api.nasdaq.com/api/quote/{quoted}/summary?assetclass=stocks",
            "corroborating_response_sha256": hashlib.sha256(summary_raw).hexdigest(),
            "collection_method": "NASDAQ_COMPANY_PROFILE_AND_QUOTE_SUMMARY_V1",
            "reported_sector_profile": profile_sector, "reported_sector_summary": summary_sector,
            "reported_exchange": reported_exchange,
            "sector_label_normalization": "NASDAQ_LABEL_WHITESPACE_AND_CASE_ONLY_V1",
            "transport_scope": "COMPLETE_JSON_RESPONSES",
        },
        "identity": dict(issuer), "observed_at": _iso(profile_received_at),
        "received_at": _iso(summary_received_at),
        "observation_time_basis": "CURRENT_CLASSIFICATION_FETCH_RECEIPT_NOT_HISTORICAL_EFFECTIVE_DATE",
    }
    candidate = {"observations": [{**body, "observation_id": metadata._hash(body)}]}
    if metadata.sector_observation(position, "US", candidate, now=summary_received_at) is None:
        raise ValueError("new Nasdaq observation fails existing metadata contract")
    return body


def collect(
    state: Mapping[str, Any], universe: Mapping[str, Any], ledger: dict[str, Any], *,
    now: datetime, fetcher: Callable[[str], bytes] | None = None,
    clock: Callable[[], datetime] | None = None,
    opportunity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Append current US classification receipts; never alter input positions.

    Source failures leave any still-valid cached observation in place. The
    default CLI supplies a real completion clock; injection supports isolated
    validation without fabricating historical receipt times.
    """
    _iso(now)
    fetcher = fetcher or fetch_json_bytes
    report: dict[str, Any] = {
        "schema_version": SCHEMA, "authority": "CURRENT_SOURCED_RISK_OBSERVATIONS_ONLY",
        "market": "US", "started_at": _iso(now), "generated_at": _iso(now),
        "positions": [], "errors": [], "candidate_symbols_skipped_by_limit": [],
        "counts": {"current_positions": 0, "candidate_targets": 0, "collected": 0,
                   "reused": 0, "verified": 0, "unavailable": 0},
    }
    positions = [row for row in ((state.get("markets") or {}).get("US") or {}).get("open_positions") or []
                 if isinstance(row, Mapping) and str(row.get("status") or "OPEN").upper() == "OPEN"]
    targets = [(position, "HOLDING") for position in positions]
    target_symbols = {str(position.get("symbol") or position.get("ticker") or "").upper() for position in positions}
    if opportunity is not None:
        try:
            if str(opportunity.get("market") or "US").upper() != "US":
                raise ValueError("candidate classification requires a US opportunity")
            # Reuse the production bridge's pure ordering/filtering function.
            # Collection grants the candidates no admission authority.
            try:
                from scripts.stock_trading_v2_production_bridge import candidate_frontier
            except ModuleNotFoundError:  # pragma: no cover
                from stock_trading_v2_production_bridge import candidate_frontier
            for candidate in candidate_frontier(opportunity):
                symbol = str(candidate.get("market_data_symbol") or candidate.get("symbol") or "").upper()
                name = candidate.get("name")
                if symbol in target_symbols:
                    continue
                if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,15}", symbol) or not isinstance(name, str) or not 0 < len(name.strip()) <= 300:
                    report["errors"].append({"market": "US", "symbol": symbol, "reason": "invalid candidate classification identity"})
                    continue
                if len(target_symbols) >= MAX_ISSUERS:
                    report["candidate_symbols_skipped_by_limit"].append(symbol)
                    continue
                target_symbols.add(symbol)
                targets.append(({"market": "US", "symbol": symbol, "name": name}, "CANDIDATE"))
                report["counts"]["candidate_targets"] += 1
        except (ValueError, TypeError, AttributeError) as exc:
            report["errors"].append({"market": "US", "symbol": "", "reason": f"invalid opportunity input: {exc}"[:200]})
    attempted: set[str] = set()
    completion = now
    for position, input_role in targets:
        symbol = str(position.get("symbol") or position.get("ticker") or "")
        existing = metadata.sector_observation(position, "US", ledger, now=completion)
        row = {"symbol": symbol, "position_id": position.get("position_id"), "input_role": input_role, "status": "UNAVAILABLE"}
        if input_role == "HOLDING":
            report["counts"]["current_positions"] += 1
        try:
            if existing is not None and completion - metadata._dt(existing["observed_at"]) < metadata.REFRESH_AFTER:
                row["status"] = "CACHED"
                report["counts"]["reused"] += 1
            else:
                if symbol in attempted or len(attempted) >= MAX_ISSUERS:
                    raise ValueError("US issuer request budget or duplicate identity reached")
                attempted.add(symbol)
                issuer = metadata._issuer(position, "US", universe)
                ticker = urllib.parse.quote(str(issuer["listing_symbol"]), safe="")
                profile = fetcher(f"https://api.nasdaq.com/api/company/{ticker}/company-profile")
                profile_at = _completion(clock, now, after=completion)
                completion = profile_at
                summary = fetcher(f"https://api.nasdaq.com/api/quote/{ticker}/summary?assetclass=stocks")
                summary_at = _completion(clock, now, after=completion)
                completion = summary_at
                body = observation_from_responses(position, issuer, profile_raw=profile, summary_raw=summary,
                    profile_received_at=profile_at, summary_received_at=summary_at)
                metadata.append_observation(ledger, body)
                row["status"] = "COLLECTED"
                report["counts"]["collected"] += 1
        except Exception as exc:
            completion = _completion(clock, completion, after=completion)
            report["errors"].append({"market": "US", "symbol": symbol, "reason": f"{type(exc).__name__}: {exc}"[:200]})
        resolved = metadata.sector_observation(position, "US", ledger, now=completion)
        row.update(sector=resolved["sector"] if resolved else "UNKNOWN",
                   observation_id=resolved["observation_id"] if resolved else None)
        if resolved is not None:
            report["counts"]["verified"] += 1
            if row["status"] == "UNAVAILABLE":
                row["status"] = "RETAINED_AFTER_SOURCE_FAILURE"
        else:
            report["counts"]["unavailable"] += 1
        report["positions"].append(row)
    report["generated_at"] = _iso(_completion(clock, completion, after=completion))
    report["status"] = "PARTIAL" if report["errors"] else ("OBSERVED" if targets else "EMPTY")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    parser.add_argument("--ledger", type=Path, default=metadata.LEDGER_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--universe", type=Path, default=UNIVERSE_PATH)
    parser.add_argument("--opportunity", type=Path, help="Optional fetched US frontier; missing file is allowed")
    args = parser.parse_args()
    # Existing ledger corruption fails before collection or persistence. A
    # partial source response, on the other hand, must permit the established
    # screener collector to try its normal path afterward.
    ledger = metadata.load_ledger(args.ledger)
    state = json.loads(args.state.read_text(encoding="utf-8"))
    universe = json.loads(args.universe.read_text(encoding="utf-8"))
    opportunity = None
    opportunity_error = None
    if args.opportunity is not None and args.opportunity.exists():
        try:
            opportunity = json.loads(args.opportunity.read_text(encoding="utf-8"))
            if not isinstance(opportunity, Mapping):
                raise ValueError("opportunity must be a JSON object")
        except (ValueError, OSError) as exc:
            opportunity, opportunity_error = None, str(exc)[:200]
    report = collect(state, universe, ledger, now=datetime.now(timezone.utc),
                     clock=lambda: datetime.now(timezone.utc), opportunity=opportunity)
    if opportunity_error is not None:
        report["errors"].append({"market": "US", "symbol": "", "reason": "optional opportunity unreadable: " + opportunity_error})
        report["status"] = "PARTIAL"
    metadata._atomic(args.ledger, ledger)
    metadata._atomic(args.report, report)
    print(json.dumps({"status": report["status"], "counts": report["counts"], "errors": report["errors"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

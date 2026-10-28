#!/usr/bin/env python3
"""Sourced current risk observations; never edits positions or execution history.

Uses the existing official universe/GPW provider and Nasdaq scanner, not the UI
logo map, as issuer identity. The append-only ledger is input to a derived risk
view. A current classification is never represented as an historical fact.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import tempfile
import urllib.parse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import company_logo_resolver as identity
    from scripts import stock_trading_v2_fast_scan as fast_scan
    from scripts import stock_trading_v2_gpw_universe_provider as gpw
except ModuleNotFoundError:  # pragma: no cover
    import company_logo_resolver as identity
    import stock_trading_v2_fast_scan as fast_scan
    import stock_trading_v2_gpw_universe_provider as gpw

ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "data/investments/stock_trading_sourced_metadata.json"
REPORT_PATH = ROOT / "data/investments/stock_trading_risk_data_coverage.json"
SCHEMA = "stock-trading-sourced-metadata-v1"
MAX_SECTOR_AGE = timedelta(days=30)
REFRESH_AFTER = timedelta(hours=24)


def _dt(value: Any) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return value.astimezone(timezone.utc) if value.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else deepcopy(default)


def empty_ledger() -> dict[str, Any]:
    return {"schema_version": SCHEMA, "authority": "CURRENT_SOURCED_RISK_OBSERVATIONS_ONLY", "observations": []}


def load_ledger(path: Path = LEDGER_PATH) -> dict[str, Any]:
    ledger = _read(path, empty_ledger())
    if ledger.get("schema_version") != SCHEMA or not isinstance(ledger.get("observations"), list):
        raise ValueError("sourced metadata ledger schema invalid")
    seen = set()
    for row in ledger["observations"]:
        body = dict(row)
        digest = body.pop("observation_id", None)
        if not digest or digest != _hash(body) or digest in seen:
            raise ValueError("immutable sourced metadata receipt invalid")
        seen.add(digest)
    return ledger


def _atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def append_observation(ledger: dict[str, Any], body: Mapping[str, Any]) -> dict[str, Any]:
    record = deepcopy(dict(body))
    record.pop("observation_id", None)
    record["observation_id"] = _hash(record)
    if not any(item.get("observation_id") == record["observation_id"] for item in ledger["observations"]):
        ledger["observations"].append(record)
    return record


def _symbol(market: str, value: Any) -> str:
    symbol = str(value or "").upper().strip()
    return symbol.removesuffix(".WA") if market == "GPW" else symbol


def _name_matches(expected: str, actual: str) -> bool:
    # Existing resolver's normalized legal-name comparison, without its logo
    # fallback registry or website claim. Exact market ticker is required too.
    return identity.matches_name(expected, {"labels": {"en": {"value": actual}}})


def sector_observation(position: Mapping[str, Any], market: str, ledger: Mapping[str, Any] | None,
                       *, now: datetime) -> dict[str, Any] | None:
    """Select only fresh, correctly identified observations available by now."""
    candidates = []
    for row in (ledger or {}).get("observations") or []:
        if not isinstance(row, Mapping) or row.get("kind") != "SECTOR":
            continue
        body = dict(row)
        digest = body.pop("observation_id", None)
        observed, received = _dt(row.get("observed_at")), _dt(row.get("received_at"))
        issuer = row.get("identity") or {}
        source = row.get("source") or {}
        sector = str(row.get("sector") or "").strip()
        provider = source.get("provider")
        url = urllib.parse.urlsplit(str(source.get("url") or ""))
        correct_source = (market == "GPW" and provider == "GPW" and url.hostname == "www.gpw.pl"
                          and issuer.get("exchange") == "GPW_MAIN_MARKET" and bool(issuer.get("isin"))) or (
            market == "US" and provider == "Nasdaq" and url.hostname == "api.nasdaq.com"
            and issuer.get("exchange") in {"NASDAQ", "NYSE", "NYSE_AMERICAN", "NYSE_ARCA", "BATS"})
        if (digest != _hash(body) or not correct_source or url.scheme != "https"
                or row.get("market") != market or issuer.get("market") != market
                or _symbol(market, row.get("symbol")) != _symbol(market, position.get("symbol") or position.get("ticker"))
                or _symbol(market, issuer.get("market_data_symbol") or issuer.get("symbol")) != _symbol(market, row.get("symbol"))
                or not _name_matches(str(position.get("name") or ""), str(issuer.get("name") or ""))
                or not sector or sector.upper() in {"UNKNOWN", "N/A", "NONE", "NULL"} or "\ufffd" in sector
                or observed is None or received is None or not observed <= received <= now
                or now - observed > MAX_SECTOR_AGE):
            continue
        candidates.append((observed, received, row))
    return deepcopy(dict(max(candidates, key=lambda item: item[:2])[2])) if candidates else None


def risk_sector(position: Mapping[str, Any], market: str, ledger: Mapping[str, Any] | None,
                *, now: datetime) -> dict[str, Any] | None:
    row = sector_observation(position, market, ledger, now=now)
    if row is None:
        return None
    return {"sector": row["sector"], "sector_provenance": {
        "source": row["source"]["provider"], "source_url": row["source"]["url"],
        "observed_at": row["observed_at"], "received_at": row["received_at"],
        "observation_id": row["observation_id"], "identity": deepcopy(row["identity"]),
        "basis": "CURRENT_CLASSIFICATION_NOT_HISTORICAL_ENTRY_METADATA",
    }}


def gpw_sector(page: str, *, ticker: str, isin: str) -> str:
    """Read the official financial-tab sector, bound to its ticker AND ISIN."""
    headline = re.search(r'<h1\b[^>]*\bid=[\"\']setH1[\"\'][^>]*>(.*?)</h1>', page, re.I | re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", headline.group(1))) if headline else ""
    headline_identity = re.fullmatch(r"([A-Z0-9._-]{1,32})\s*\(([A-Z0-9]{12})\)", " ".join(text.split()).upper())
    if headline_identity is None or headline_identity.group(2) != isin.upper():
        raise ValueError("GPW company headline identity mismatch")
    portal_alias = headline_identity.group(1)
    for match in re.finditer(r'data-href=[\"\']([^\"\']+)[\"\']', page, re.I):
        url = urllib.parse.urlsplit(html.unescape(match.group(1)))
        params = urllib.parse.parse_qs(url.query)
        if (url.hostname == "www.gpw.pl" and params.get("action") == ["GPWListaSp"]
                and params.get("code") == [portal_alias] and params.get("isin") == [isin]):
            sector = str((params.get("sektor") or [""])[0]).strip()
            if sector and "\ufffd" not in sector:
                return sector
    raise ValueError("GPW sourced sector missing")


def _issuer(position: Mapping[str, Any], market: str, universe: Mapping[str, Any]) -> dict[str, Any]:
    quote_symbol = str(position.get("symbol") or position.get("ticker") or "").upper()
    rows = [item for item in universe.get("instruments") or []
            if item.get("market") == market and str(item.get("market_data_symbol") or item.get("listing_symbol") or "").upper() == quote_symbol]
    if len(rows) != 1 or not _name_matches(str(position.get("name") or ""), str(rows[0].get("name") or "")):
        raise ValueError("official universe issuer identity mismatch")
    row = rows[0]
    source = row.get("source") or {}
    provider = source.get("provider")
    if (market == "GPW" and provider != "GPW") or (market == "US" and provider != "Nasdaq Trader"):
        raise ValueError("official universe source missing")
    return {"market": market, "symbol": row.get("market_data_symbol") or row["listing_symbol"],
            "market_data_symbol": row.get("market_data_symbol") or row["listing_symbol"], "listing_symbol": row["listing_symbol"],
            "name": row["name"], "exchange": row.get("exchange"),
            "isin": (source.get("raw_status") or {}).get("isin")}


def current_spread(position: Mapping[str, Any], market: str, *, now: datetime) -> dict[str, Any]:
    spread = deepcopy((position.get("current_mark") or {}).get("spread_observation") or
                      {"status": "UNAVAILABLE", "reason": "provider_bid_ask_not_observed"})
    if spread.get("status") != "OBSERVED":
        return spread
    try:
        bid, ask = float(spread["bid"]), float(spread["ask"])
        observed, received = _dt(spread.get("observed_at")), _dt(spread.get("received_at"))
        if (spread.get("symbol") != position.get("symbol") or spread.get("market") != market
                or not all(math.isfinite(value) for value in (bid, ask)) or not 0 < bid <= ask or observed is None or received is None
                or not observed <= received <= now
                or spread.get("measurement") != "PROVIDER_BID_ASK_NOT_BROKER_FILL"):
            raise ValueError("invalid bid/ask identity, geometry or time")
        if now - observed > timedelta(seconds=300):
            spread.update(status="STALE", reason="bid_ask_older_than_300_seconds")
    except (KeyError, TypeError, ValueError, OverflowError):
        spread.update(status="INVALID", reason="invalid_bid_ask_identity_geometry_or_time")
    return spread


def collect(state: Mapping[str, Any], universes: Mapping[str, Any], ledger: dict[str, Any], *, now: datetime,
            nasdaq_fetcher: Any = None, gpw_fetcher: Any = None, clock: Any = None) -> dict[str, Any]:
    """At most one Nasdaq snapshot and six official GPW pages per run.

    A fresh observation is reused for 24h. Failed/ambiguous sources never
    materialize an UNKNOWN label as a successful sector observation.
    """
    report: dict[str, Any] = {"generated_at": now.isoformat(), "authority": "DERIVED_READ_ONLY_RISK_COVERAGE", "markets": {}, "errors": []}
    nasdaq_rows = None
    nasdaq_error = None
    pages = 0
    for market in ("GPW", "US"):
        coverage = report["markets"][market] = []
        for position in ((state.get("markets") or {}).get(market) or {}).get("open_positions") or []:
            if not isinstance(position, Mapping) or str(position.get("status") or "OPEN").upper() != "OPEN":
                continue
            symbol = str(position.get("symbol") or position.get("ticker") or "")
            existing = sector_observation(position, market, ledger, now=now)
            try:
                if existing is None or now - _dt(existing["observed_at"]) >= REFRESH_AFTER:
                    issuer = _issuer(position, market, universes.get(market) or {})
                    ticker = _symbol(market, issuer["listing_symbol"])
                    if market == "GPW":
                        if pages >= 6 or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", str(issuer.get("isin") or "")):
                            raise ValueError("GPW bounded page budget or ISIN invalid")
                        pages += 1
                        source_url = "https://www.gpw.pl/spolka?" + urllib.parse.urlencode({"isin": issuer["isin"]})
                        page = gpw_fetcher(source_url) if gpw_fetcher else gpw._request(gpw._opener(), source_url, timeout=12, attempts=2)
                        sector = gpw_sector(page, ticker=ticker, isin=issuer["isin"])
                        source = {"provider": "GPW", "url": source_url, "taxonomy": "GPW_SECTOR", "response_sha256": hashlib.sha256(page.encode()).hexdigest(),
                                  "identity_method": "OFFICIAL_UNIVERSE_ISIN_TO_MATCHING_GPW_HEADLINE_AND_FINANCIAL_TAB_ISIN_AND_PORTAL_ALIAS"}
                    else:
                        if nasdaq_rows is None and nasdaq_error is None:
                            try:
                                nasdaq_rows, _ = (nasdaq_fetcher or fast_scan.fetch_nasdaq_screener_rows)(timeout=20)
                            except Exception as exc:
                                nasdaq_error = type(exc).__name__
                        if nasdaq_error:
                            raise ValueError("Nasdaq source unavailable: " + nasdaq_error)
                        matched = [row for row in nasdaq_rows if str(row.get("symbol") or "").upper() == ticker]
                        if len(matched) != 1 or not _name_matches(issuer["name"], str(matched[0].get("name") or "")):
                            raise ValueError("Nasdaq screener issuer identity mismatch")
                        sector = str(matched[0].get("sector") or "").strip()
                        source = {"provider": "Nasdaq", "url": fast_scan.NASDAQ_SCREENER_URL, "taxonomy": "NASDAQ_SECTOR", "response_sha256": _hash(matched[0])}
                    # Classification has no provider effective timestamp. Record
                    # the actual completed fetch receipt, never run-start time.
                    receipt_at = clock() if clock is not None else now
                    body = {"kind": "SECTOR", "market": market, "symbol": symbol, "sector": sector,
                            "source": source, "identity": issuer, "observed_at": receipt_at.isoformat(), "received_at": receipt_at.isoformat(),
                            "observation_time_basis": "CURRENT_CLASSIFICATION_FETCH_RECEIPT_NOT_HISTORICAL_EFFECTIVE_DATE"}
                    candidate_ledger = {"observations": [{**body, "observation_id": _hash(body)}]}
                    if sector_observation(position, market, candidate_ledger, now=receipt_at) is None:
                        raise ValueError("invalid sourced sector observation")
                    append_observation(ledger, body)
            except Exception as exc:
                report["errors"].append({"market": market, "symbol": symbol, "reason": str(exc)[:180]})
            coverage_at = clock() if clock is not None else now
            resolved = sector_observation(position, market, ledger, now=coverage_at)
            spread = current_spread(position, market, now=coverage_at)
            if spread.get("status") == "OBSERVED":
                append_observation(ledger, {"kind": "BID_ASK", "market": market, "symbol": symbol,
                                           "observed_at": spread.get("observed_at"), "received_at": spread.get("received_at"), "observation": spread})
            coverage.append({"position_id": position.get("position_id"), "symbol": symbol,
                             "sector": resolved["sector"] if resolved else "UNKNOWN",
                             "sector_observation_id": resolved.get("observation_id") if resolved else None,
                             "spread_observation": spread,
                             "frozen_paper_cost_model": deepcopy(position.get("transaction_cost_model")),
                             "historical_net_cost_status": "FROZEN_PAPER_ASSUMPTION_NOT_BROKER_FILL" if position.get("transaction_cost_model") else "UNSCORABLE_MISSING_FROZEN_HISTORICAL_COSTS",
                             "actual_broker_execution_costs": {"status": "UNAVAILABLE", "reason": "no_broker_fill_receipt"}})
    report["generated_at"] = (clock() if clock is not None else now).isoformat()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=ROOT / "data/investments/stock_trading_portfolio.json")
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    ledger = load_ledger(args.ledger)
    state = _read(args.state, {})
    universes = {market: _read(ROOT / f"data/investments/stock_trading_v2_universe/{market.lower()}.json", {}) for market in ("GPW", "US")}
    report = collect(state, universes, ledger, now=datetime.now(timezone.utc), clock=lambda: datetime.now(timezone.utc))
    try:
        from scripts import stock_trading_portfolio as portfolio
    except ModuleNotFoundError:
        import stock_trading_portfolio as portfolio
    report["current_portfolio_risk"] = {}
    for market in ("GPW", "US"):
        risk = portfolio.portfolio_admission_risk(state, market, {}, now=datetime.now(timezone.utc), risk_metadata=ledger)
        report["current_portfolio_risk"][market] = {key: risk[key] for key in (
            "currency", "risk_basis", "aggregate_stop_loss_cash", "sector_positions", "unknown_sector_positions",
            "current_sector_observation_ids", "remaining_exit_cost_basis", "missing_risk_position_ids")}
    _atomic(args.ledger, ledger)
    _atomic(args.report, report)
    print(json.dumps({"status": "OBSERVED" if not report["errors"] else "PARTIAL", "observation_count": len(ledger["observations"]), "errors": report["errors"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

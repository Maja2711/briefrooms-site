#!/usr/bin/env python3
"""Dynamic universe foundation for Stock Trading v2.

Phase 1B broadens discovery without changing production trading decisions.
US listings are sourced from Nasdaq Trader's official symbol-directory files;
GPW remains on the legacy seed until its dynamic source receives the same audit
and parser coverage. Universe snapshots are research/shadow inputs only.

Universe persistence is semantic: a refresh with identical instruments and
metadata does not rewrite a large snapshot merely because wall-clock time moved.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import tempfile
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from scripts import stock_trading_v2_contracts as contracts
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_contracts as contracts

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data/investments/stock_trading_v2_universe_config.json"
SNAPSHOT_ROOT = ROOT / "data/investments/stock_trading_v2_universe"
US_NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
US_OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
SNAPSHOT_SCHEMA = "stock-trading-v2-universe-snapshot-v2"
INSTRUMENT_SCHEMA = "stock-trading-v2-instrument-v1"

EXCLUDED_NAME_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bwarrants?\b",
        r"\brights?\b",
        r"\bunits?\b",
        r"\bpreferred\b",
        r"\bpreference shares?\b",
        r"\bsenior notes?\b",
        r"\bsubordinated notes?\b",
        r"\bnotes due\b",
        r"\bbonds?\b",
        r"\bdebentures?\b",
        r"\bexchange[- ]traded fund\b",
        r"\betf\b",
        r"\bclosed[- ]end fund\b",
        r"\bincome fund\b",
        r"\bmunicipal.*fund\b",
        r"\btrust preferred\b",
    )
)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def _download_text(url: str, *, timeout: int = 30) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "BriefRooms-Stock-Trading-v2/1.0 (+https://www.briefrooms.com/)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - audited HTTPS source
        raw = response.read()
    return raw.decode("utf-8-sig", errors="strict")


def _rows(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text), delimiter="|")
    return [
        {str(key or "").strip(): str(value or "").strip() for key, value in row.items()}
        for row in reader
        if row and not str(next(iter(row.values()), "")).startswith("File Creation Time")
    ]


def _excluded_name(name: str) -> str | None:
    for pattern in EXCLUDED_NAME_PATTERNS:
        if pattern.search(name):
            return pattern.pattern
    return None


def _yahoo_us_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if "$" in value:
        return value
    return value.replace(".", "-")


def _classify_security(name: str) -> str:
    lowered = name.lower()
    if "american deposit" in lowered or re.search(r"\badr\b", lowered):
        return "ADR"
    if "ordinary share" in lowered:
        return "ORDINARY_SHARE"
    if "common" in lowered:
        return "COMMON_STOCK"
    if "limited partnership" in lowered or re.search(r"\bl\.p\.\b", lowered):
        return "PARTNERSHIP"
    return "EQUITY_UNSPECIFIED"


def _instrument(
    *,
    market: str,
    listing_symbol: str,
    name: str,
    exchange: str,
    security_type: str,
    source_dataset: str,
    raw_status: Mapping[str, Any],
) -> dict[str, Any]:
    listing = listing_symbol.strip().upper()
    market_symbol = _yahoo_us_symbol(listing) if market == "US" else listing
    return {
        "schema_version": INSTRUMENT_SCHEMA,
        "market": market,
        "listing_symbol": listing,
        "market_data_symbol": market_symbol,
        "name": name.strip(),
        "exchange": exchange,
        "security_type": security_type,
        "eligibility": {
            "discovery_eligible": True,
            "liquidity_pending": True,
            "price_history_pending": True,
            "production_admission": False,
        },
        "source": {
            "provider": "Nasdaq Trader" if market == "US" else "legacy",
            "dataset": source_dataset,
            "raw_status": dict(raw_status),
        },
    }


def parse_nasdaq_listed(text: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    instruments: list[dict[str, Any]] = []
    counters = {
        "rows": 0,
        "accepted": 0,
        "excluded_etf": 0,
        "excluded_test": 0,
        "excluded_financial_status": 0,
        "excluded_security_type": 0,
        "excluded_symbol": 0,
    }
    for row in _rows(text):
        counters["rows"] += 1
        symbol = row.get("Symbol", "").strip()
        name = row.get("Security Name", "").strip()
        if not symbol or not name:
            counters["excluded_symbol"] += 1
            continue
        if row.get("Test Issue", "N") != "N":
            counters["excluded_test"] += 1
            continue
        if row.get("ETF", "N") == "Y":
            counters["excluded_etf"] += 1
            continue
        if row.get("Financial Status", "N") not in {"", "N"}:
            counters["excluded_financial_status"] += 1
            continue
        if _excluded_name(name):
            counters["excluded_security_type"] += 1
            continue
        instruments.append(
            _instrument(
                market="US",
                listing_symbol=symbol,
                name=name,
                exchange="NASDAQ",
                security_type=_classify_security(name),
                source_dataset="nasdaqlisted.txt",
                raw_status={
                    "market_category": row.get("Market Category"),
                    "financial_status": row.get("Financial Status"),
                    "round_lot_size": row.get("Round Lot Size"),
                },
            )
        )
        counters["accepted"] += 1
    return instruments, counters


def parse_other_listed(text: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    exchange_names = {
        "N": "NYSE",
        "A": "NYSE_AMERICAN",
        "P": "NYSE_ARCA",
        "Z": "CBOE_BZX",
        "V": "IEX",
    }
    instruments: list[dict[str, Any]] = []
    counters = {
        "rows": 0,
        "accepted": 0,
        "excluded_etf": 0,
        "excluded_test": 0,
        "excluded_security_type": 0,
        "excluded_symbol": 0,
    }
    for row in _rows(text):
        counters["rows"] += 1
        symbol = row.get("ACT Symbol", "").strip()
        name = row.get("Security Name", "").strip()
        if not symbol or not name or "$" in symbol:
            counters["excluded_symbol"] += 1
            continue
        if row.get("Test Issue", "N") != "N":
            counters["excluded_test"] += 1
            continue
        if row.get("ETF", "N") == "Y":
            counters["excluded_etf"] += 1
            continue
        if _excluded_name(name):
            counters["excluded_security_type"] += 1
            continue
        exchange_code = row.get("Exchange", "")
        instruments.append(
            _instrument(
                market="US",
                listing_symbol=symbol,
                name=name,
                exchange=exchange_names.get(exchange_code, f"OTHER:{exchange_code or 'UNKNOWN'}"),
                security_type=_classify_security(name),
                source_dataset="otherlisted.txt",
                raw_status={
                    "exchange_code": exchange_code,
                    "round_lot_size": row.get("Round Lot Size"),
                    "nasdaq_symbol": row.get("NASDAQ Symbol"),
                },
            )
        )
        counters["accepted"] += 1
    return instruments, counters


def _dedupe(instruments: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in instruments:
        item = dict(raw)
        key = (str(item.get("market")), str(item.get("listing_symbol")))
        if key in result:
            raise contracts.ContractError(f"duplicate universe instrument: {key}")
        result[key] = item
    return sorted(result.values(), key=lambda item: (str(item["market"]), str(item["listing_symbol"])))


def _semantic_snapshot_body(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return the universe identity independent of refresh wall-clock time."""
    body = deepcopy(dict(snapshot))
    body.pop("generated_at", None)
    body.pop("semantic_sha256", None)
    body.pop("snapshot_sha256", None)
    return body


def _finalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    snapshot["semantic_sha256"] = contracts.payload_sha256(_semantic_snapshot_body(snapshot))
    body = deepcopy(snapshot)
    body.pop("snapshot_sha256", None)
    snapshot["snapshot_sha256"] = contracts.payload_sha256(body)
    validate_snapshot(snapshot)
    return snapshot


def build_us_snapshot(
    nasdaq_text: str,
    other_text: str,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    nasdaq, nasdaq_stats = parse_nasdaq_listed(nasdaq_text)
    other, other_stats = parse_other_listed(other_text)
    instruments = _dedupe([*nasdaq, *other])
    snapshot: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA,
        "market": "US",
        "generated_at": generated_at or _now_utc(),
        "mode": "shadow_no_production_influence",
        "source": {
            "provider": "Nasdaq Trader",
            "datasets": [US_NASDAQ_URL, US_OTHER_URL],
            "authority": "official_exchange_symbol_directory",
        },
        "filters": {
            "exclude_etf": True,
            "exclude_test_issues": True,
            "exclude_nasdaq_abnormal_financial_status": True,
            "exclude_obvious_non_common_equity_instruments": True,
            "liquidity_filter_stage": "downstream_fast_scanner",
        },
        "instrument_count": len(instruments),
        "provider_stats": {"nasdaq": nasdaq_stats, "other": other_stats},
        "instruments": instruments,
        "governance": {
            "production_decision_influence": False,
            "automatic_portfolio_admission": False,
            "requires_downstream_liquidity_gate": True,
            "requires_downstream_history_gate": True,
        },
    }
    return _finalize_snapshot(snapshot)


def build_legacy_gpw_seed_snapshot(*, generated_at: str | None = None) -> dict[str, Any]:
    legacy = _read_json(ROOT / "data/investments/gpw_daily_pick_config.json")
    instruments: list[dict[str, Any]] = []
    for raw in legacy.get("universe") or []:
        if not isinstance(raw, Mapping):
            continue
        symbol = str(raw.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        instruments.append(
            {
                "schema_version": INSTRUMENT_SCHEMA,
                "market": "GPW",
                "listing_symbol": symbol.removesuffix(".WA"),
                "market_data_symbol": symbol,
                "name": str(raw.get("name") or symbol),
                "exchange": "GPW_MAIN_MARKET",
                "security_type": "COMMON_STOCK",
                "sector": raw.get("sector"),
                "eligibility": {
                    "discovery_eligible": True,
                    "liquidity_pending": False,
                    "price_history_pending": False,
                    "production_admission": False,
                },
                "source": {
                    "provider": "legacy_seed",
                    "dataset": "gpw_daily_pick_config.json",
                    "raw_status": {},
                },
            }
        )
    instruments = _dedupe(instruments)
    snapshot: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA,
        "market": "GPW",
        "generated_at": generated_at or _now_utc(),
        "mode": "shadow_no_production_influence",
        "source": {
            "provider": "legacy_seed",
            "datasets": ["data/investments/gpw_daily_pick_config.json"],
            "authority": "temporary_compatibility_seed_pending_dynamic_gpw_provider",
        },
        "instrument_count": len(instruments),
        "instruments": instruments,
        "governance": {
            "production_decision_influence": False,
            "automatic_portfolio_admission": False,
            "dynamic_provider_ready": False,
        },
    }
    return _finalize_snapshot(snapshot)


def validate_snapshot(snapshot: Mapping[str, Any]) -> None:
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA:
        raise contracts.ContractError("universe snapshot schema mismatch")
    if snapshot.get("market") not in contracts.SUPPORTED_MARKETS:
        raise contracts.ContractError("unsupported snapshot market")
    items = snapshot.get("instruments")
    if not isinstance(items, list):
        raise contracts.ContractError("instruments must be an array")
    if int(snapshot.get("instrument_count", -1)) != len(items):
        raise contracts.ContractError("instrument_count mismatch")
    keys: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, Mapping):
            raise contracts.ContractError("instrument must be an object")
        if item.get("schema_version") != INSTRUMENT_SCHEMA:
            raise contracts.ContractError("instrument schema mismatch")
        market = str(item.get("market") or "")
        symbol = str(item.get("listing_symbol") or "")
        if not market or not symbol or not str(item.get("market_data_symbol") or ""):
            raise contracts.ContractError("instrument identity missing")
        key = (market, symbol)
        if key in keys:
            raise contracts.ContractError(f"duplicate instrument: {key}")
        keys.add(key)
        eligibility = item.get("eligibility")
        if not isinstance(eligibility, Mapping) or eligibility.get("production_admission") is not False:
            raise contracts.ContractError("Phase 1B universe cannot influence production admission")
    governance = snapshot.get("governance")
    if not isinstance(governance, Mapping) or governance.get("production_decision_influence") is not False:
        raise contracts.ContractError("universe governance must be shadow-only")

    semantic_hash = str(snapshot.get("semantic_sha256") or "")
    if not semantic_hash or semantic_hash != contracts.payload_sha256(_semantic_snapshot_body(snapshot)):
        raise contracts.ContractError("universe semantic hash mismatch")
    body = deepcopy(dict(snapshot))
    stored = str(body.pop("snapshot_sha256", ""))
    if not stored or contracts.payload_sha256(body) != stored:
        raise contracts.ContractError("universe snapshot hash mismatch")


def write_snapshot(snapshot: Mapping[str, Any], *, root: Path = SNAPSHOT_ROOT) -> Path:
    """Persist only a semantic universe change, never a timestamp-only refresh."""
    validate_snapshot(snapshot)
    market = str(snapshot["market"]).lower()
    path = root / f"{market}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = _read_json(path)
            validate_snapshot(existing)
            if existing.get("semantic_sha256") == snapshot.get("semantic_sha256"):
                return path
        except (contracts.ContractError, json.JSONDecodeError):
            # A legacy v1 snapshot is rewritten once into the v2 contract.
            pass
    content = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temp = Path(handle.name)
    temp.replace(path)
    return path


def refresh_us(*, root: Path = SNAPSHOT_ROOT) -> dict[str, Any]:
    nasdaq_text = _download_text(US_NASDAQ_URL)
    other_text = _download_text(US_OTHER_URL)
    snapshot = build_us_snapshot(nasdaq_text, other_text)
    path = write_snapshot(snapshot, root=root)
    return _read_json(path)


def refresh_gpw_seed(*, root: Path = SNAPSHOT_ROOT) -> dict[str, Any]:
    snapshot = build_legacy_gpw_seed_snapshot()
    path = write_snapshot(snapshot, root=root)
    return _read_json(path)


def verify_root(root: Path = SNAPSHOT_ROOT) -> dict[str, Any]:
    result: dict[str, Any] = {"schema_version": SNAPSHOT_SCHEMA, "ok": True, "markets": {}}
    for market in ("gpw", "us"):
        path = root / f"{market}.json"
        if not path.exists():
            continue
        snapshot = _read_json(path)
        validate_snapshot(snapshot)
        result["markets"][market.upper()] = {
            "instrument_count": snapshot["instrument_count"],
            "provider": (snapshot.get("source") or {}).get("provider"),
            "semantic_sha256": snapshot.get("semantic_sha256"),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["US", "GPW", "us", "gpw"])
    parser.add_argument("--root", type=Path, default=SNAPSHOT_ROOT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify_root(args.root), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if not args.market:
        parser.error("--market is required unless --verify is used")
    market = str(args.market).upper()
    snapshot = refresh_us(root=args.root) if market == "US" else refresh_gpw_seed(root=args.root)
    print(
        json.dumps(
            {
                "market": market,
                "instrument_count": snapshot["instrument_count"],
                "provider": snapshot["source"]["provider"],
                "semantic_sha256": snapshot["semantic_sha256"],
                "production_decision_influence": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

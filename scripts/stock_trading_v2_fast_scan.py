#!/usr/bin/env python3
"""Cheap stage-zero opportunity scanner for Stock Trading v2.

The scanner intentionally does *not* perform expensive historical/news/LLM
analysis.  It joins the audited broad US universe to Nasdaq's public stock
screener snapshot and creates a diversified research shortlist for the next
stage.  It is shadow-only and cannot admit a portfolio position.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_universe as universe
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_universe as universe

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data/investments/stock_trading_v2_fast_scan_config.json"
US_UNIVERSE_PATH = ROOT / "data/investments/stock_trading_v2_universe/us.json"
NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"
SCHEMA_VERSION = "stock-trading-v2-fast-scan-v1"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    payload = _read_json(path)
    if payload.get("schema_version") != "stock-trading-v2-fast-scan-config-v1":
        raise contracts.ContractError("fast-scan config schema mismatch")
    if payload.get("governance", {}).get("production_decision_influence") is not False:
        raise contracts.ContractError("fast scanner must remain shadow-only")
    return payload


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    if not text or text in {"--", "N/A", "n/a"}:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def fetch_nasdaq_screener_rows(*, timeout: int = 30) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    query = urllib.parse.urlencode({"tableonly": "true", "download": "true"})
    request = urllib.request.Request(
        f"{NASDAQ_SCREENER_URL}?{query}",
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; BriefRooms-Stock-Trading-v2/1.0; +https://www.briefrooms.com/)",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed audited HTTPS endpoint
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Nasdaq screener returned no rows")
    return [dict(row) for row in rows if isinstance(row, Mapping)], {
        "provider": "Nasdaq",
        "endpoint": NASDAQ_SCREENER_URL,
        "rows_received": len(rows),
        "as_of": data.get("asOf"),
    }


def _universe_map(snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    universe.validate_snapshot(snapshot)
    if snapshot.get("market") != "US":
        raise contracts.ContractError("fast US scanner requires US universe snapshot")
    return {
        str(item["listing_symbol"]).upper(): dict(item)
        for item in snapshot.get("instruments") or []
        if isinstance(item, Mapping)
    }


def _normalise_row(row: Mapping[str, Any], instrument: Mapping[str, Any]) -> dict[str, Any] | None:
    symbol = str(row.get("symbol") or "").upper().strip()
    price = _number(row.get("lastsale"))
    volume = _number(row.get("volume"))
    pct_change = _number(row.get("pctchange"))
    market_cap = _number(row.get("marketCap"))
    if not symbol or price is None or price <= 0 or volume is None or volume < 0:
        return None
    dollar_volume = price * volume
    return {
        "symbol": symbol,
        "market_data_symbol": instrument.get("market_data_symbol"),
        "name": row.get("name") or instrument.get("name"),
        "exchange": instrument.get("exchange"),
        "security_type": instrument.get("security_type"),
        "sector": row.get("sector") or None,
        "industry": row.get("industry") or None,
        "country": row.get("country") or None,
        "price": price,
        "volume": int(volume),
        "dollar_volume": dollar_volume,
        "pct_change": pct_change or 0.0,
        "market_cap": market_cap,
    }


def _eligible(row: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    filters = config.get("filters") or {}
    if float(row.get("price") or 0.0) < float(filters.get("minimum_price_usd", 1.0)):
        return False
    if float(row.get("dollar_volume") or 0.0) < float(filters.get("absolute_minimum_dollar_volume_usd", 0.0)):
        return False
    blocked_industries = {str(value).strip().lower() for value in filters.get("excluded_industries") or []}
    if str(row.get("industry") or "").strip().lower() in blocked_industries:
        return False
    blocked_name_patterns = filters.get("excluded_name_patterns") or []
    name = str(row.get("name") or "")
    if any(re.search(str(pattern), name, re.IGNORECASE) for pattern in blocked_name_patterns):
        return False
    return True


def _top(rows: Iterable[dict[str, Any]], *, key, n: int) -> list[dict[str, Any]]:
    return sorted(rows, key=key, reverse=True)[: max(0, int(n))]


def build_shortlist(
    universe_snapshot: Mapping[str, Any],
    screener_rows: Iterable[Mapping[str, Any]],
    config: Mapping[str, Any],
    *,
    generated_at: str | None = None,
    source_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    instruments = _universe_map(universe_snapshot)
    joined: list[dict[str, Any]] = []
    screener_seen = 0
    for raw in screener_rows:
        screener_seen += 1
        symbol = str(raw.get("symbol") or "").upper().strip()
        instrument = instruments.get(symbol)
        if instrument is None:
            continue
        row = _normalise_row(raw, instrument)
        if row is not None and _eligible(row, config):
            joined.append(row)

    selection = config.get("selection") or {}
    max_candidates = int(selection.get("maximum_candidates", 600))
    liquidity_n = int(selection.get("liquidity_lane", 300))
    mover_n = int(selection.get("mover_lane", 150))
    midcap_n = int(selection.get("midcap_lane", 150))
    mover_floor = float(selection.get("mover_minimum_dollar_volume_usd", 1_000_000.0))
    midcap_low = float(selection.get("midcap_market_cap_min_usd", 100_000_000.0))
    midcap_high = float(selection.get("midcap_market_cap_max_usd", 15_000_000_000.0))

    lanes: dict[str, list[dict[str, Any]]] = {
        "liquidity": _top(joined, key=lambda row: float(row["dollar_volume"]), n=liquidity_n),
        "movers": _top(
            [row for row in joined if float(row["dollar_volume"]) >= mover_floor],
            key=lambda row: (abs(float(row["pct_change"])), float(row["dollar_volume"])),
            n=mover_n,
        ),
        "midcap": _top(
            [
                row
                for row in joined
                if row.get("market_cap") is not None
                and midcap_low <= float(row["market_cap"]) <= midcap_high
            ],
            key=lambda row: (float(row["dollar_volume"]), abs(float(row["pct_change"]))),
            n=midcap_n,
        ),
    }

    selected: dict[str, dict[str, Any]] = {}
    lane_membership: dict[str, set[str]] = {}
    for lane_name, lane_rows in lanes.items():
        for row in lane_rows:
            symbol = str(row["symbol"])
            selected.setdefault(symbol, dict(row))
            lane_membership.setdefault(symbol, set()).add(lane_name)

    for symbol, row in selected.items():
        row["lanes"] = sorted(lane_membership[symbol])
        row["stage_zero_score"] = round(
            50.0 * math.log1p(max(0.0, float(row["dollar_volume"]))) / math.log1p(1_000_000_000.0)
            + 30.0 * min(abs(float(row["pct_change"])), 15.0) / 15.0
            + 20.0 * (1.0 if "midcap" in lane_membership[symbol] else 0.0),
            6,
        )

    shortlist = sorted(
        selected.values(),
        key=lambda row: (
            float(row["stage_zero_score"]),
            float(row["dollar_volume"]),
        ),
        reverse=True,
    )[:max_candidates]
    for rank, row in enumerate(shortlist, start=1):
        row["stage_zero_rank"] = rank

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "market": "US",
        "generated_at": generated_at or _now_utc(),
        "mode": "shadow_no_production_influence",
        "source": dict(source_meta or {}),
        "universe_semantic_sha256": universe_snapshot.get("semantic_sha256"),
        "universe_size": int(universe_snapshot.get("instrument_count") or 0),
        "screener_rows_seen": screener_seen,
        "eligible_after_join": len(joined),
        "shortlist_size": len(shortlist),
        "lane_sizes": {name: len(rows) for name, rows in lanes.items()},
        "candidates": shortlist,
        "governance": {
            "production_decision_influence": False,
            "automatic_portfolio_admission": False,
            "expensive_analysis_completed": False,
            "purpose": "cheap_broad_market_prefilter",
        },
    }
    semantic = dict(payload)
    semantic.pop("generated_at", None)
    semantic.pop("snapshot_sha256", None)
    payload["snapshot_sha256"] = contracts.payload_sha256(semantic)
    validate_shortlist(payload, config)
    return payload


def validate_shortlist(payload: Mapping[str, Any], config: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("market") != "US":
        raise contracts.ContractError("fast-scan schema/market mismatch")
    governance = payload.get("governance")
    if not isinstance(governance, Mapping) or governance.get("production_decision_influence") is not False:
        raise contracts.ContractError("fast scan escaped shadow governance")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise contracts.ContractError("fast scan candidates must be an array")
    maximum = int((config.get("selection") or {}).get("maximum_candidates", 600))
    if len(candidates) > maximum or int(payload.get("shortlist_size", -1)) != len(candidates):
        raise contracts.ContractError("fast scan shortlist size mismatch")
    seen: set[str] = set()
    for row in candidates:
        symbol = str((row or {}).get("symbol") or "")
        if not symbol or symbol in seen:
            raise contracts.ContractError("fast scan duplicate/missing symbol")
        seen.add(symbol)
    body = dict(payload)
    stored = str(body.pop("snapshot_sha256", ""))
    body.pop("generated_at", None)
    if not stored or contracts.payload_sha256(body) != stored:
        raise contracts.ContractError("fast scan hash mismatch")


def run_live(*, universe_path: Path = US_UNIVERSE_PATH, config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    universe_snapshot = _read_json(universe_path)
    config = load_config(config_path)
    rows, meta = fetch_nasdaq_screener_rows()
    return build_shortlist(universe_snapshot, rows, config, source_meta=meta)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", type=Path, default=US_UNIVERSE_PATH)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = run_live(universe_path=args.universe, config_path=args.config)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "market": payload["market"],
                "universe_size": payload["universe_size"],
                "eligible_after_join": payload["eligible_after_join"],
                "shortlist_size": payload["shortlist_size"],
                "lane_sizes": payload["lane_sizes"],
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

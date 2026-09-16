#!/usr/bin/env python3
"""Dynamic official-GPW universe adapter for Stock Trading v2."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import stock_trading_v2_gpw_universe_provider as provider
    from scripts import stock_trading_v2_universe as universe
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_gpw_universe_provider as provider
    import stock_trading_v2_universe as universe


def build_snapshot(
    companies: list[Mapping[str, Any]],
    *,
    generated_at: str | None = None,
    provider_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    instruments: list[dict[str, Any]] = []
    for raw in companies:
        ticker = str(raw.get("ticker") or "").strip().upper()
        isin = str(raw.get("isin") or "").strip().upper()
        name = str(raw.get("name") or ticker).strip()
        if not ticker or not isin:
            continue
        instruments.append(
            {
                "schema_version": universe.INSTRUMENT_SCHEMA,
                "market": "GPW",
                "listing_symbol": ticker,
                "market_data_symbol": f"{ticker}.WA",
                "name": name,
                "exchange": "GPW_MAIN_MARKET",
                "security_type": "COMMON_STOCK",
                "eligibility": {
                    "discovery_eligible": True,
                    "liquidity_pending": True,
                    "price_history_pending": True,
                    "production_admission": False,
                },
                "source": {
                    "provider": "GPW",
                    "dataset": "official_main_market_company_search",
                    "raw_status": {"isin": isin},
                },
            }
        )
    instruments = universe._dedupe(instruments)
    if len(instruments) < 200:
        raise universe.contracts.ContractError(f"dynamic GPW snapshot unexpectedly small: {len(instruments)}")
    snapshot: dict[str, Any] = {
        "schema_version": universe.SNAPSHOT_SCHEMA,
        "market": "GPW",
        "generated_at": generated_at or universe._now_utc(),
        "mode": "shadow_no_production_influence",
        "source": {
            "provider": "GPW",
            "datasets": [provider.GPW_COMPANIES_URL, provider.GPW_AJAX_URL],
            "authority": "official_exchange_company_search",
            "provider_meta": dict(provider_meta or {}),
        },
        "filters": {
            "main_market_only": True,
            "do_not_filter_by_market_cap": True,
            "liquidity_filter_stage": "downstream_fast_scanner",
            "price_history_filter_stage": "downstream_fast_scanner",
        },
        "instrument_count": len(instruments),
        "instruments": instruments,
        "governance": {
            "production_decision_influence": False,
            "automatic_portfolio_admission": False,
            "dynamic_provider_ready": True,
            "requires_downstream_liquidity_gate": True,
            "requires_downstream_history_gate": True,
        },
    }
    return universe._finalize_snapshot(snapshot)


def refresh(*, root: Path = universe.SNAPSHOT_ROOT) -> dict[str, Any]:
    companies, meta = provider.fetch_companies()
    snapshot = build_snapshot(companies, provider_meta=meta)
    path = universe.write_snapshot(snapshot, root=root)
    return universe._read_json(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=universe.SNAPSHOT_ROOT)
    args = parser.parse_args()
    snapshot = refresh(root=args.root)
    print(
        json.dumps(
            {
                "market": "GPW",
                "instrument_count": snapshot["instrument_count"],
                "provider": snapshot["source"]["provider"],
                "dynamic_provider_ready": snapshot["governance"].get("dynamic_provider_ready"),
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

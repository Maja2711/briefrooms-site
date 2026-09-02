#!/usr/bin/env python3
"""Compatibility adapters into the P0.2 CanonicalMarketSnapshot contract.

Only adapters with a real observed timestamp and an explicit runtime receipt
timestamp may produce a canonical snapshot. Legacy publication timestamps are
never substituted for missing market-data lineage.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping

try:
    from canonical_market_snapshot import (
        CanonicalMarketSnapshot,
        DataQualityAssessment,
        FreshnessPolicy,
        MarketSnapshotError,
        assess_snapshot,
        build_snapshot,
        require_usable,
    )
except ModuleNotFoundError:
    from scripts.canonical_market_snapshot import (
        CanonicalMarketSnapshot,
        DataQualityAssessment,
        FreshnessPolicy,
        MarketSnapshotError,
        assess_snapshot,
        build_snapshot,
        require_usable,
    )

COVERAGE_CANONICALIZED = "CANONICALIZED"
COVERAGE_PARTIAL = "PARTIAL"
COVERAGE_NOT_YET = "NOT_YET_CANONICALIZED"


def canonical_equity_id(market: str, symbol: str) -> str:
    """Stable dynamic equity identity until those universes enter P0.1 registry.

    Dynamic GPW/US universes were deliberately deferred by P0.1. This adapter
    uses a deterministic namespace without pretending that every equity has a
    static InstrumentRegistry row.
    """
    normalized_market = str(market or "").strip().lower()
    if normalized_market not in {"gpw", "us"}:
        raise MarketSnapshotError(f"unsupported dynamic equity market: {market!r}")
    raw = str(symbol or "").strip().upper()
    if normalized_market == "gpw":
        raw = raw.removesuffix(".WA")
        country = "pl"
    else:
        country = "us"
    token = re.sub(r"[^A-Z0-9.-]+", "-", raw).strip(".-").lower()
    if not token:
        raise MarketSnapshotError("equity symbol is required")
    return f"equity.{country}.{token}"


def _provider_key(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if "yahoo" in text:
        return "yahoo"
    if "stooq" in text:
        return "stooq"
    return text.replace(" ", "_") or "unknown"


def _source_ref(provider: str, symbol: str) -> str:
    if provider == "yahoo":
        return f"https://finance.yahoo.com/quote/{symbol}"
    if provider == "stooq":
        return f"https://stooq.pl/q/?s={symbol.removesuffix('.WA').lower()}"
    return f"provider://{provider}/{symbol}"


def equity_execution_policy(
    market: str,
    *,
    max_age_seconds: float,
    max_future_skew_seconds: float = 120.0,
) -> FreshnessPolicy:
    market = str(market).lower()
    if market not in {"gpw", "us"}:
        raise MarketSnapshotError(f"unsupported equity market: {market}")
    return FreshnessPolicy(
        policy_id=f"{market}-daily-execution-market-snapshot-v1",
        max_age_seconds=max_age_seconds,
        required_price_fields=("open", "high", "low", "last"),
        max_future_skew_seconds=max_future_skew_seconds,
        allowed_market_statuses=("OPEN",),
    )


def adapt_equity_opening_snapshot(
    raw: Mapping[str, Any],
    *,
    market: str,
    received_at: str | datetime,
    created_at: str | datetime | None = None,
) -> CanonicalMarketSnapshot:
    symbol = str(raw.get("symbol") or "").strip()
    observed_at = raw.get("observed_at")
    if not symbol or not observed_at:
        raise MarketSnapshotError("opening snapshot requires symbol and observed_at")
    provider = _provider_key(raw.get("provider"))
    return build_snapshot(
        instrument_id=canonical_equity_id(market, symbol),
        provider=provider,
        provider_symbol=symbol,
        source_ref=_source_ref(provider, symbol),
        observed_at=observed_at,
        received_at=received_at,
        created_at=created_at or received_at,
        market_status="OPEN",
        quote_kind="INTRADAY_OHLC_LAST",
        last=raw.get("last"),
        open=raw.get("open"),
        high=raw.get("high"),
        low=raw.get("low"),
        volume=raw.get("volume"),
        source_schema=str(raw.get("schema_version") or "legacy-opening-snapshot"),
    )


def attach_equity_canonical_snapshot(
    raw: Mapping[str, Any],
    *,
    market: str,
    received_at: str | datetime,
    policy: FreshnessPolicy,
    created_at: str | datetime | None = None,
    decision_at: str | datetime | None = None,
) -> dict[str, Any]:
    canonical = adapt_equity_opening_snapshot(
        raw,
        market=market,
        received_at=received_at,
        created_at=created_at,
    )
    quality = assess_snapshot(canonical, as_of=decision_at or created_at or received_at, policy=policy)
    require_usable(quality)
    enriched = dict(raw)
    enriched["market_snapshot_id"] = canonical.snapshot_id
    enriched["market_snapshot_hash"] = canonical.snapshot_hash
    enriched["canonical_market_snapshot"] = canonical.to_dict()
    enriched["canonical_data_quality"] = quality.to_dict()
    return enriched


def adapt_yahoo_ohlc_snapshot(
    *,
    instrument_id: str,
    provider_symbol: str,
    observed_at: str | datetime,
    received_at: str | datetime,
    created_at: str | datetime | None = None,
    market_status: str = "OPEN",
    open: Any = None,
    high: Any = None,
    low: Any = None,
    close: Any = None,
    last: Any = None,
    volume: Any = None,
) -> CanonicalMarketSnapshot:
    """Generic adapter for WES/EURUSD migration; bid/ask are not fabricated."""
    return build_snapshot(
        instrument_id=instrument_id,
        provider="yahoo",
        provider_symbol=provider_symbol,
        source_ref=f"https://finance.yahoo.com/quote/{provider_symbol}",
        observed_at=observed_at,
        received_at=received_at,
        created_at=created_at or received_at,
        market_status=market_status,
        quote_kind="OHLC",
        open=open,
        high=high,
        low=low,
        close=close,
        last=last,
        volume=volume,
        source_schema="yahoo-chart",
    )


def coverage_report() -> dict[str, Any]:
    """Explicit migration state; unknown consumers are never implied PASS."""
    return {
        "contract_version": "briefrooms-market-snapshot-coverage-v1",
        "components": {
            "gpw_daily": {
                "status": COVERAGE_CANONICALIZED,
                "adapter": "adapt_equity_opening_snapshot",
                "lineage": "observed_at + runtime received_at + created_at",
                "decision_gate": True,
            },
            "us_daily": {
                "status": COVERAGE_CANONICALIZED,
                "adapter": "adapt_equity_opening_snapshot",
                "lineage": "observed_at + runtime received_at + created_at",
                "decision_gate": True,
            },
            "eurusd_daily": {
                "status": COVERAGE_PARTIAL,
                "reason": "engine has point-in-time timestamps but canonical snapshot is not yet attached to the active decision lifecycle",
                "decision_gate": False,
            },
            "eurusd_abc_shadow": {
                "status": COVERAGE_PARTIAL,
                "reason": "Yahoo OHLC is point-in-time but remains in the existing research-shadow signal snapshot",
                "decision_gate": False,
            },
            "wes": {
                "status": COVERAGE_PARTIAL,
                "reason": "P0.1 canonical instrument routing exists; P0.2 snapshot attachment is deferred to a dedicated WES migration",
                "decision_gate": False,
            },
            "brace_spx": {
                "status": COVERAGE_NOT_YET,
                "reason": "BRACE currently consumes its existing point-in-time engine state plus EpistemicState, not P0.2 MarketSnapshot",
                "decision_gate": False,
            },
        },
        "legacy_backfill": False,
        "unknown_means_pass": False,
    }

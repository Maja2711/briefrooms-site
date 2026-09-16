#!/usr/bin/env python3
"""Live stage-zero US discovery adapter for Stock Trading v2.

The preferred path joins the audited dynamic US universe with Nasdaq's live
screener. If that external endpoint is temporarily unavailable, the adapter
falls back to a clearly labelled rotating shard of the already-audited universe.
The fallback makes no live-liquidity claim: the next discovery stage must still
fetch price history and enforce median-turnover, momentum and volatility gates.
Everything remains shadow-only and cannot admit a portfolio trade.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_fast_scan as fast_scan
    from scripts import stock_trading_v2_us_screener_provider as provider
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_fast_scan as fast_scan
    import stock_trading_v2_us_screener_provider as provider


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_rotation_fallback(
    universe_snapshot: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    error: Exception,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return an auditable stage-zero shard when the live screener is down.

    This is an availability fallback, not a substitute liquidity model. The
    shard rotates deterministically each configured period so repeated provider
    outages still cover the broad universe instead of pinning analysis to a
    static large-cap list. Historical discovery downstream supplies the actual
    liquidity/momentum/volatility evidence.
    """
    fast_scan.universe.validate_snapshot(universe_snapshot)
    if universe_snapshot.get("market") != "US":
        raise contracts.ContractError("US stage-zero fallback requires US universe")
    fallback_cfg = config.get("provider_fallback") or {}
    if fallback_cfg.get("enabled") is not True:
        raise provider.ProviderUnavailable("live screener failed and provider fallback is disabled") from error
    if fallback_cfg.get("live_liquidity_claim_allowed") is not False:
        raise contracts.ContractError("fallback must not claim live liquidity")

    generated = now or datetime.now(timezone.utc)
    period_minutes = max(1, int(fallback_cfg.get("rotation_period_minutes") or 60))
    count = max(1, int(fallback_cfg.get("candidates_per_cycle") or 180))
    stage_score = float(fallback_cfg.get("stage_zero_score") or 50.0)
    instruments = sorted(
        [dict(row) for row in universe_snapshot.get("instruments") or [] if isinstance(row, Mapping)],
        key=lambda row: str(row.get("listing_symbol") or ""),
    )
    instruments = [row for row in instruments if str(row.get("listing_symbol") or "").strip()]
    if not instruments:
        raise provider.ProviderUnavailable("audited universe is empty; no safe stage-zero fallback") from error

    bucket = int(generated.timestamp() // (period_minutes * 60))
    # Advance by exactly the number consumed by the expensive historical stage.
    # This provides deterministic broad coverage over successive degraded cycles.
    start = (bucket * count) % len(instruments)
    chosen = [instruments[(start + index) % len(instruments)] for index in range(min(count, len(instruments)))]
    candidates: list[dict[str, Any]] = []
    for rank, instrument in enumerate(chosen, start=1):
        symbol = str(instrument.get("listing_symbol") or "").upper().strip()
        candidates.append(
            {
                "symbol": symbol,
                "market_data_symbol": instrument.get("market_data_symbol") or symbol,
                "name": instrument.get("name"),
                "exchange": instrument.get("exchange"),
                "security_type": instrument.get("security_type"),
                "price": None,
                "volume": None,
                "dollar_volume": None,
                "pct_change": None,
                "market_cap": None,
                "lanes": ["provider_fallback_rotation"],
                "stage_zero_score": round(stage_score, 6),
                "stage_zero_rank": rank,
                "live_screener_metrics_available": False,
            }
        )

    source = {
        "provider": "audited_dynamic_us_universe",
        "mode": str(fallback_cfg.get("mode") or "rotating_audited_universe_shard"),
        "degraded": True,
        "complete": False,
        "live_liquidity_claim": False,
        "rotation_bucket": bucket,
        "rotation_start": start,
        "rotation_period_minutes": period_minutes,
        "fallback_reason": f"{type(error).__name__}: {' '.join(str(error).split())}"[:900],
        "next_stage_requirement": "fresh_history_and_median_turnover_gates",
    }
    payload: dict[str, Any] = {
        "schema_version": fast_scan.SCHEMA_VERSION,
        "market": "US",
        "generated_at": _iso(generated),
        "mode": "shadow_no_production_influence",
        "source": source,
        "universe_semantic_sha256": universe_snapshot.get("semantic_sha256"),
        "universe_size": int(universe_snapshot.get("instrument_count") or 0),
        "screener_rows_seen": 0,
        "eligible_after_join": len(candidates),
        "shortlist_size": len(candidates),
        "lane_sizes": {"provider_fallback_rotation": len(candidates)},
        "candidates": candidates,
        "governance": {
            "production_decision_influence": False,
            "automatic_portfolio_admission": False,
            "expensive_analysis_completed": False,
            "purpose": "degraded_broad_market_coverage_until_live_screener_recovers",
            "live_liquidity_claim": False,
            "fallback_explicit": True,
        },
    }
    semantic = dict(payload)
    semantic.pop("generated_at", None)
    semantic.pop("snapshot_sha256", None)
    payload["snapshot_sha256"] = contracts.payload_sha256(semantic)
    fast_scan.validate_shortlist(payload, config)
    return payload


def run_live(
    *,
    universe_path: Path = fast_scan.US_UNIVERSE_PATH,
    config_path: Path = fast_scan.CONFIG_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    universe_snapshot = fast_scan._read_json(universe_path)
    config = fast_scan.load_config(config_path)
    try:
        rows, meta = provider.fetch_rows()
        payload = fast_scan.build_shortlist(
            universe_snapshot,
            rows,
            config,
            generated_at=_iso(now) if now else None,
            source_meta=meta,
        )
    except provider.ProviderUnavailable as exc:
        payload = build_rotation_fallback(universe_snapshot, config, error=exc, now=now)
    if payload.get("governance", {}).get("production_decision_influence") is not False:
        raise RuntimeError("live stage-zero adapter escaped shadow governance")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", type=Path, default=fast_scan.US_UNIVERSE_PATH)
    parser.add_argument("--config", type=Path, default=fast_scan.CONFIG_PATH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = run_live(universe_path=args.universe, config_path=args.config)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "market": payload["market"],
                "universe_size": payload["universe_size"],
                "eligible_after_join": payload["eligible_after_join"],
                "shortlist_size": payload["shortlist_size"],
                "lane_sizes": payload["lane_sizes"],
                "source_mode": payload.get("source", {}).get("mode"),
                "source_degraded": payload.get("source", {}).get("degraded", False),
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

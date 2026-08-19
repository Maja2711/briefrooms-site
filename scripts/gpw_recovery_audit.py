#!/usr/bin/env python3
"""Runtime audit helpers for GPW Daily Pick market-data and decision pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MarketAudit:
    symbol: str
    status: str
    provider: str | None
    sessions: int
    latest_session: str | None
    detail: str | None = None


def summarize_market_audit(rows: list[MarketAudit], universe_size: int) -> dict[str, Any]:
    healthy = [row for row in rows if row.status == "healthy"]
    failed = [row for row in rows if row.status != "healthy"]
    return {
        "universe_size": universe_size,
        "healthy_symbols": len(healthy),
        "failed_symbols": len(failed),
        "data_complete_ratio": round(len(healthy) / max(universe_size, 1), 4),
        "providers": sorted({row.provider for row in healthy if row.provider}),
        "failures": [
            {"symbol": row.symbol, "status": row.status, "detail": row.detail}
            for row in failed
        ],
    }

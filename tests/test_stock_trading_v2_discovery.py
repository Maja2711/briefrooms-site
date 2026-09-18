from __future__ import annotations

import unittest
from datetime import datetime, timezone

from scripts import stock_trading_v2_discovery as discovery
from scripts import stock_trading_v2_universe as universe


CONFIG = {
    "schema_version": "stock-trading-v2-discovery-config-v1",
    "holding_horizon": "OPEN_ENDED_MODEL_CONTROLLED",
    "no_global_selection_cutoff": True,
    "markets": {
        "US": {
            "minimum_history_sessions": 80,
            "history_candidates_from_stage_zero": 2,
            "frontier_size": 2,
            "relationship_pool_size": 3,
            "discovery_minimum_median_turnover": 1000,
            "production_liquidity_reference": 100000,
            "candidate_freshness_minutes": 75,
        },
        "GPW": {
            "minimum_history_sessions": 80,
            "history_candidates_from_stage_zero": 0,
            "frontier_size": 2,
            "discovery_minimum_median_turnover": 1000,
            "production_liquidity_reference": 100000,
            "candidate_freshness_minutes": 180,
        },
    },
    "weights": {
        "relative_momentum": 30,
        "trend_quality": 20,
        "liquidity": 15,
        "volume_impulse": 10,
        "volatility_quality": 10,
        "risk_adjusted_momentum": 10,
        "stage_zero": 5,
    },
    "features": {
        "momentum_horizons_sessions": [1, 5, 20, 60],
        "atr_sessions": 14,
        "volatility_sessions": 20,
        "turnover_sessions": 20,
        "trend_fast_sessions": 20,
        "trend_slow_sessions": 60,
    },
    "frontier": {"cash_utility": 0.0},
    "governance": {"production_decision_influence": False},
}


def bars(*, slope: float, volume: int = 100000) -> list[discovery.Bar]:
    result = []
    price = 20.0
    for index in range(100):
        price *= 1.0 + slope
        result.append(
            discovery.Bar(
                day=f"2026-05-{(index % 28) + 1:02d}",
                open=price * 0.995,
                high=price * 1.01,
                low=price * 0.99,
                close=price,
                volume=volume + index * 100,
            )
        )
    return result


def universe_snapshot() -> dict:
    snapshot = {
        "schema_version": universe.SNAPSHOT_SCHEMA,
        "market": "US",
        "generated_at": "2026-09-16T16:00:00Z",
        "mode": "shadow_no_production_influence",
        "source": {"provider": "test"},
        "instrument_count": 3,
        "instruments": [
            {
                "schema_version": universe.INSTRUMENT_SCHEMA,
                "market": "US",
                "listing_symbol": symbol,
                "market_data_symbol": symbol,
                "name": symbol,
                "exchange": "NASDAQ",
                "security_type": "COMMON_STOCK",
                "eligibility": {
                    "discovery_eligible": True,
                    "liquidity_pending": True,
                    "price_history_pending": True,
                    "production_admission": False,
                },
                "source": {"provider": "test"},
            }
            for symbol in ("UP", "FLAT", "DOWN")
        ],
        "governance": {"production_decision_influence": False},
    }
    return universe._finalize_snapshot(snapshot)


class StockTradingV2DiscoveryTests(unittest.TestCase):
    def test_compute_features_has_multihorizon_and_open_ended_inputs(self):
        features = discovery.compute_features(bars(slope=0.002), config={**CONFIG, **CONFIG["markets"]["US"]})
        self.assertIsNotNone(features)
        assert features is not None
        self.assertIn("60", features["returns"])
        self.assertGreater(features["median_turnover_20d"], 0)
        self.assertIsNotNone(features["atr_fraction"])

    def test_stage_zero_limits_expensive_history_candidates_without_large_cap_gate(self):
        snapshot = universe_snapshot()
        stage_zero = {"candidates": [{"symbol": "DOWN"}, {"symbol": "UP"}, {"symbol": "FLAT"}]}
        selected = discovery.select_instruments(
            snapshot,
            market="US",
            market_config=CONFIG["markets"]["US"],
            stage_zero=stage_zero,
        )
        self.assertEqual([row["listing_symbol"] for row in selected], ["DOWN", "UP"])

    def test_frontier_ranks_opportunities_and_keeps_cash_explicit(self):
        snapshot = universe_snapshot()
        feature_map = {
            "UP": discovery.compute_features(bars(slope=0.004, volume=200000), config={**CONFIG, **CONFIG["markets"]["US"]}),
            "FLAT": discovery.compute_features(bars(slope=0.0001, volume=150000), config={**CONFIG, **CONFIG["markets"]["US"]}),
            "DOWN": discovery.compute_features(bars(slope=-0.002, volume=120000), config={**CONFIG, **CONFIG["markets"]["US"]}),
        }
        feature_map = {key: value for key, value in feature_map.items() if value is not None}
        stage_zero = {
            "candidates": [
                {"symbol": "UP", "stage_zero_score": 80, "stage_zero_rank": 1, "lanes": ["movers"]},
                {"symbol": "FLAT", "stage_zero_score": 60, "stage_zero_rank": 2, "lanes": ["liquidity"]},
                {"symbol": "DOWN", "stage_zero_score": 40, "stage_zero_rank": 3, "lanes": ["midcap"]},
            ]
        }
        payload = discovery.build_frontier(
            snapshot,
            feature_map,
            CONFIG,
            market="US",
            stage_zero=stage_zero,
            generated_at=datetime(2026, 9, 16, 16, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(payload["candidates"][0]["symbol"], "UP")
        self.assertEqual(3, payload["relationship_pool_size"])
        self.assertEqual(["UP", "FLAT", "DOWN"], [row["symbol"] for row in payload["relationship_pool"]])
        self.assertEqual(3, payload["relationship_pool"][2]["relationship_rank"])
        self.assertEqual(payload["cash_alternative"]["status"], "AVAILABLE")
        self.assertIsNone(payload["global_selection_cutoff"])
        self.assertFalse(payload["governance"]["production_decision_influence"])
        self.assertEqual(payload["candidates"][0]["admission"]["status"], "PENDING_DEEP_EVIDENCE_AND_RISK_PLAN")
        discovery.validate_frontier(payload)

    def test_tamper_is_detected(self):
        snapshot = universe_snapshot()
        feature = discovery.compute_features(bars(slope=0.002), config={**CONFIG, **CONFIG["markets"]["US"]})
        payload = discovery.build_frontier(snapshot, {"UP": feature}, CONFIG, market="US")
        payload["candidates"][0]["opportunity_score"] = 999
        with self.assertRaises(Exception):
            discovery.validate_frontier(payload)


if __name__ == "__main__":
    unittest.main()

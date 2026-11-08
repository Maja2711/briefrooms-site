"""Offline contract tests for the private Saxo -> Belief -> Epistemic path."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from belief_adapter_contract import EvidenceAssessment, Observation, observation_to_evidence
from belief_core import BeliefCore, iso_z
from belief_market_data_adapter import Bar
from belief_wes_assets_adapter import WES_ASSET_BELIEFS
from saxo_private_belief import PrivateSaxoBeliefFailure, build_private_saxo_epistemic

REQUIRED_ENV = {
    "BRIEFROOMS_PRIVATE_RUNTIME": "1",
    "SAXO_PRIVATE_PRIMARY": "1",
    "SAXO_STREAM_PRIMARY": "1",
    "SAXO_CHART_PRIMARY": "1",
    "SAXO_PRIVATE_BELIEF_PRIMARY": "1",
}


class PrivateSaxoBeliefTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="test-private-belief-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime.now(timezone.utc)
        self.upstream = self.root / "upstream"
        self.upstream.mkdir()
        self.core = BeliefCore(self.upstream)
        self.core.register_beliefs(WES_ASSET_BELIEFS)
        self.original_observation = Observation.make(
            adapter="wes_asset_evidence", metric="eurusd_trend_composite",
            entity="EURUSD", observed_at=iso_z(self.now - timedelta(hours=1)),
            value=.1, unit="score", source="Derived from Yahoo Finance OHLCV",
            source_type="derived", source_ref="derived:yahoo:EURUSD:old",
            reliability=.74, independence_cluster="derived:EURUSD:technical",
            metadata={"serial_state_snapshot": True},
        )
        self.core.ingest((observation_to_evidence(
            self.original_observation,
            EvidenceAssessment("eurusd.trend.bullish", 1, .1, "technical_trend", "prior"),
        ),))
        self.core.recompute(self.now - timedelta(minutes=15))
        self.core_path = self.upstream / "state.json"
        self.epistemic_path = self.upstream / "epistemic_state.json"
        self.epistemic_payload = {
            "contract_version": "belief-epistemic-state-v1",
            "created_at": iso_z(self.now),
            "authority": {
                "llm_may_ignore_aggregate": False,
                "llm_may_override_probability": False,
            },
            "controls": {"belief_core_writeback_enabled": False},
            "states": {},
        }
        self.epistemic_path.write_text(json.dumps(self.epistemic_payload))
        self.private_dir = self.root / "private"
        self.private_dir.mkdir()
        self.env = {
            **REQUIRED_ENV,
            "BELIEF_CORE_STATE": str(self.core_path),
            "BELIEF_EPISTEMIC_STATE": str(self.epistemic_path),
        }
        # Last completed bar ended 15 minutes ago; all returns are in bounds.
        start = self.now - timedelta(minutes=30 * 91 + 15)
        self.candles = [
            Bar(timestamp=start + timedelta(minutes=i * 30),
                close=1.08 + i * .00004,
                open=1.08 + i * .00004,
                high=1.0801 + i * .00004,
                low=1.0799 + i * .00004)
            for i in range(91)
        ]

    def test_saxo_is_exclusive_private_trend_authority(self):
        original = self.core_path.read_bytes()
        with patch.dict(os.environ, self.env):
            with patch("saxo_private_belief.SaxoPrivateChartClient") as source:
                source.return_value.bars.return_value = self.candles
                result = build_private_saxo_epistemic(self.private_dir, self.now)
                source.return_value.bars.assert_called_once_with("EURUSD=X", "10d", "30m")
            state = json.loads((result.parent / "state.json").read_text())
            price_evidence = [
                e for e in state["evidence"]
                if e["belief_id"] == "eurusd.trend.bullish"
            ]
            self.assertEqual(len(price_evidence), 1)
            self.assertEqual(price_evidence[0]["source_type"], "derived")
            self.assertTrue(price_evidence[0]["source_ref"].startswith(
                "private:saxo_sim_chart:EURUSD:30m:"
            ))
            self.assertNotIn("Yahoo", price_evidence[0]["source"])
            projection = json.loads(result.read_text())
            lineage = projection["states"]["eurusd.trend.bullish"][
                "provenance_root"]["evidence_lineage"]
            self.assertEqual(len(lineage), 1)
            self.assertEqual(lineage[0]["source_ref"], price_evidence[0]["source_ref"])
            self.assertEqual(os.environ["BELIEF_EPISTEMIC_STATE"], str(result))
            self.assertEqual(os.environ["BELIEF_CORE_STATE"], str(result.parent / "state.json"))
        self.assertEqual(self.core_path.read_bytes(), original)
        self.assertFalse((self.root / "private" / "eurusd_daily_spot.json").exists())

    def test_stale_upstream_blocks_without_retimestamping(self):
        self.epistemic_payload["created_at"] = iso_z(self.now - timedelta(hours=3))
        self.epistemic_path.write_text(json.dumps(self.epistemic_payload))
        with patch.dict(os.environ, self.env):
            with patch("saxo_private_belief.SaxoPrivateChartClient") as source:
                with self.assertRaisesRegex(
                    PrivateSaxoBeliefFailure, "private_belief_upstream_stale_or_invalid"
                ):
                    build_private_saxo_epistemic(self.private_dir, self.now)
                source.assert_not_called()
            self.assertEqual(os.environ["BELIEF_CORE_STATE"], str(self.core_path))

    def test_rejects_missing_isolation_and_does_not_fetch(self):
        with patch.dict(os.environ, {**self.env, "SAXO_PRIVATE_BELIEF_PRIMARY": "0"}):
            with patch("saxo_private_belief.SaxoPrivateChartClient") as source:
                with self.assertRaises(PrivateSaxoBeliefFailure):
                    build_private_saxo_epistemic(self.private_dir, self.now)
                source.assert_not_called()

    def test_stale_saxo_bar_blocks_without_epistemic_override(self):
        stale = [
            Bar(timestamp=b.timestamp - timedelta(hours=4), close=b.close)
            for b in self.candles
        ]
        with patch.dict(os.environ, self.env):
            with patch("saxo_private_belief.SaxoPrivateChartClient") as source:
                source.return_value.bars.return_value = stale
                with self.assertRaisesRegex(PrivateSaxoBeliefFailure, "private_belief_saxo_bar_stale"):
                    build_private_saxo_epistemic(self.private_dir, self.now)
            self.assertEqual(os.environ["BELIEF_CORE_STATE"], str(self.core_path))


if __name__ == "__main__":
    unittest.main()

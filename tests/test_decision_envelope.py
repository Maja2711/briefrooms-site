from __future__ import annotations

import copy
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import decision_envelope as envelope_contract
from scripts import decision_envelope_adapters as adapters
from scripts import gpw_daily_risk_policy
from scripts import market_snapshot_adapters as snapshots
from scripts import us_daily_stock_risk_policy

WARSAW = ZoneInfo("Europe/Warsaw")
NY = ZoneInfo("America/New_York")


def gpw_config():
    return {
        "policy_version": "gpw-test-v1",
        "minimum_reward_risk": 1.5,
        "maximum_risk_percent": 0.07,
        "data_gates": {
            "maximum_execution_quote_age_minutes": 20,
            "maximum_future_clock_skew_minutes": 2,
        },
    }


def us_config():
    return {
        "policy_version": "us-test-v1",
        "minimum_reward_risk": 1.5,
        "maximum_risk_percent": 0.06,
        "data_gates": {
            "maximum_execution_quote_age_minutes": 20,
            "maximum_future_clock_skew_minutes": 2,
        },
    }


def canonical_equity_snapshot(market: str, symbol: str, now: datetime):
    raw = {
        "provider": "Yahoo",
        "symbol": symbol,
        "date": now.date().isoformat(),
        "observed_at": (now - timedelta(minutes=1)).isoformat(timespec="seconds"),
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "last": 101.0,
        "volume": 100_000,
    }
    policy = snapshots.equity_execution_policy(
        market,
        max_age_seconds=1200,
        max_future_skew_seconds=120,
    )
    return snapshots.attach_equity_canonical_snapshot(
        raw,
        market=market,
        received_at=now,
        created_at=now,
        decision_at=now,
        policy=policy,
    )


def gpw_trade(now: datetime):
    snapshot = canonical_equity_snapshot("gpw", "PKO.WA", now)
    return {
        "decision": "TRANSAKCJA",
        "generated_at": now.isoformat(timespec="seconds"),
        "selection": {
            "symbol": "PKO.WA",
            "score": 77.0,
            "reference_price": 101.0,
            "entry_zone": [100.7, 101.6],
            "skip_above": 101.6,
            "stop": 96.0,
            "target": 111.0,
            "risk_percent": 0.0495,
            "reward_risk": 2.0,
            "valid_until": "2026-09-07",
            "time_stop": "2 sessions",
            "market_snapshot": snapshot,
        },
        "data_quality": {},
    }


def us_trade(now: datetime):
    snapshot = canonical_equity_snapshot("us", "AAPL", now)
    return {
        "decision": "TRADE",
        "generated_at": now.isoformat(timespec="seconds"),
        "selection": {
            "symbol": "AAPL",
            "score": 75.0,
            "reference_price": 101.0,
            "entry_zone": [100.7, 101.6],
            "skip_above": 101.6,
            "stop": 96.0,
            "target": 111.0,
            "reward_risk": 2.0,
            "valid_until": "2026-09-07",
            "market_snapshot": snapshot,
        },
        "data_quality": {},
    }


class DecisionEnvelopeTests(unittest.TestCase):
    def test_gpw_trade_binds_snapshot_and_engine_owned_risk_policy(self):
        now = datetime(2026, 9, 3, 9, 20, tzinfo=WARSAW)
        module = SimpleNamespace(load_config=gpw_config, PublicationError=RuntimeError)
        payload = adapters.attach_gpw_decision_envelope(gpw_trade(now), module)
        env = envelope_contract.envelope_from_mapping(payload["decision_envelope"])
        self.assertEqual(env.action, "LONG")
        self.assertEqual(env.engine_id, "gpw_daily")
        self.assertEqual(env.market_snapshot_id, payload["selection"]["market_snapshot"]["market_snapshot_id"])
        self.assertEqual(env.risk_policy_id, gpw_daily_risk_policy.POLICY_ID)
        self.assertEqual(payload["decision_envelope_id"], env.envelope_id)
        self.assertEqual(payload["risk_assessment"]["status"], "APPROVED")

    def test_us_and_gpw_keep_distinct_policy_owners_and_limits(self):
        now = datetime(2026, 9, 3, 9, 40, tzinfo=NY)
        gpw_risk = gpw_daily_risk_policy.evaluate(
            {"decision": "BRAK_TRANSAKCJI"},
            assessed_at=now.isoformat(),
            config=gpw_config(),
        )
        us_risk = us_daily_stock_risk_policy.evaluate(
            {"decision": "NO_TRADE"},
            assessed_at=now.isoformat(),
            config=us_config(),
        )
        self.assertNotEqual(gpw_risk.policy_id, us_risk.policy_id)
        self.assertNotEqual(gpw_risk.policy_fingerprint, us_risk.policy_fingerprint)

    def test_gpw_mandatory_limit_does_not_leak_into_primary_path(self):
        now = datetime(2026, 9, 3, 9, 20, tzinfo=WARSAW)
        strict_mandatory = {
            "enabled": True,
            "schema_version": "mandatory-test-v1",
            "maximum_candidate_risk_percent": 0.03,
            "maximum_published_risk_percent": 0.03,
        }
        primary = gpw_trade(now)
        mandatory = gpw_trade(now)
        mandatory["selection"]["selection_mode"] = "MANDATORY_DAILY_FINAL"
        with patch.object(gpw_daily_risk_policy, "_mandatory_policy", return_value=strict_mandatory):
            primary_risk = gpw_daily_risk_policy.evaluate(
                primary,
                assessed_at=primary["generated_at"],
                config=gpw_config(),
            )
            mandatory_risk = gpw_daily_risk_policy.evaluate(
                mandatory,
                assessed_at=mandatory["generated_at"],
                config=gpw_config(),
            )
        self.assertEqual(primary_risk.status, "APPROVED")
        self.assertEqual(mandatory_risk.status, "BLOCKED")
        self.assertNotEqual(primary_risk.policy_fingerprint, mandatory_risk.policy_fingerprint)

    def test_gpw_flat_has_no_fabricated_instrument_or_snapshot(self):
        now = datetime(2026, 9, 3, 9, 20, tzinfo=WARSAW)
        module = SimpleNamespace(load_config=gpw_config, PublicationError=RuntimeError)
        payload = adapters.attach_gpw_decision_envelope(
            {
                "decision": "BRAK_TRANSAKCJI",
                "generated_at": now.isoformat(timespec="seconds"),
                "selection": None,
                "data_quality": {"status": "healthy"},
            },
            module,
        )
        self.assertEqual(payload["risk_assessment"]["status"], "NO_POSITION_RISK")
        self.assertIsNone(payload["decision_envelope"]["instrument_id"])
        self.assertIsNone(payload["decision_envelope"]["market_snapshot_id"])

    def test_risk_policy_blocks_invalid_geometry_before_persistence(self):
        now = datetime(2026, 9, 3, 9, 20, tzinfo=WARSAW)
        module = SimpleNamespace(load_config=gpw_config, PublicationError=RuntimeError)
        payload = gpw_trade(now)
        payload["selection"]["stop"] = 80.0
        payload["selection"]["risk_percent"] = 0.20
        with self.assertRaisesRegex(RuntimeError, "RISK_POLICY_BLOCKED"):
            adapters.attach_gpw_decision_envelope(payload, module)

    def test_tampered_market_snapshot_is_rejected(self):
        now = datetime(2026, 9, 3, 9, 40, tzinfo=NY)
        module = SimpleNamespace(load_config=us_config, PublicationError=RuntimeError)
        payload = us_trade(now)
        payload["selection"]["market_snapshot"]["canonical_market_snapshot"]["last"] = 999.0
        with self.assertRaises(RuntimeError):
            adapters.attach_us_decision_envelope(payload, module)

    def test_same_frozen_decision_has_same_envelope_id(self):
        now = datetime(2026, 9, 3, 9, 40, tzinfo=NY)
        module = SimpleNamespace(load_config=us_config, PublicationError=RuntimeError)
        first = adapters.attach_us_decision_envelope(us_trade(now), module)
        second = adapters.attach_us_decision_envelope(us_trade(now), module)
        self.assertEqual(first["decision_envelope_id"], second["decision_envelope_id"])
        self.assertEqual(first["risk_assessment_id"], second["risk_assessment_id"])

    def test_us_hold_does_not_reuse_entry_envelope_as_hold_lineage(self):
        payload = {
            "decision": "TRADE",
            "position_action": "HOLD",
            "decision_envelope_id": "dec-old",
            "risk_assessment_id": "risk-old",
            "market_snapshot_id": "mkt-old",
            "instrument_id": "equity.us.aapl",
            "selection": {
                "decision_envelope_id": "dec-old",
                "risk_assessment_id": "risk-old",
                "market_snapshot_id": "mkt-old",
                "instrument_id": "equity.us.aapl",
            },
            "data_quality": {},
        }
        module = SimpleNamespace(load_config=us_config, PublicationError=RuntimeError)
        result = adapters.attach_us_decision_envelope(payload, module)
        self.assertNotIn("decision_envelope_id", result)
        self.assertNotIn("market_snapshot_id", result)
        self.assertNotIn("instrument_id", result)
        self.assertNotIn("market_snapshot_id", result["selection"])
        self.assertNotIn("instrument_id", result["selection"])
        self.assertEqual(result["decision_envelope_coverage"], "PARTIAL")
        self.assertEqual(result["data_quality"]["decision_envelope"], "DEFERRED_US_POSITION_MARK_LINEAGE")

    def test_gpw_persistence_guard_attaches_before_write(self):
        now = datetime(2026, 9, 3, 9, 20, tzinfo=WARSAW)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            writes = []

            def atomic(path, payload):
                writes.append((Path(path), copy.deepcopy(payload)))

            module = SimpleNamespace(
                load_config=gpw_config,
                PublicationError=RuntimeError,
                PUBLIC_PATH=root / "gpw.json",
                HISTORY_DIR=root / "history",
                atomic_json=atomic,
            )
            adapters.install_gpw_persistence_guard(module)
            payload = gpw_trade(now)
            module.atomic_json(module.PUBLIC_PATH, payload)
            self.assertEqual(len(writes), 1)
            self.assertTrue(writes[0][1]["decision_envelope_id"].startswith("dec-"))
            self.assertEqual(writes[0][1]["risk_assessment"]["status"], "APPROVED")

    def test_coverage_is_explicit_not_implied(self):
        report = adapters.coverage_report()
        self.assertEqual(report["components"]["gpw_daily_final_decisions"]["status"], "CANONICALIZED")
        self.assertEqual(report["components"]["us_daily_new_entry_and_flat"]["status"], "CANONICALIZED")
        self.assertEqual(report["components"]["us_position_hold_close"]["status"], "PARTIAL")
        self.assertFalse(report["centralized_risk_limits"])
        self.assertFalse(report["legacy_backfill"])
        self.assertFalse(report["unknown_means_pass"])


if __name__ == "__main__":
    unittest.main()

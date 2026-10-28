"""Synthetic executable paths; no fixture is a real historical market record."""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from eurusd_central_bank_wire import iso
from eurusd_cb_event_distribution import SCHEMA_VERSION, estimate_distribution, load_model

NOW = datetime(2026, 10, 28, 18, 1, tzinfo=timezone.utc)
START = datetime(2024, 1, 3, 18, tzinfo=timezone.utc)


def sample(index=0, *, direction="SHORT", bank="FED", published=None, shock=False):
    published = published or START + timedelta(days=7 * index)
    def quote(minute):
        delta = -50. if shock and minute == 1 else 10. * minute / 120
        if direction == "SHORT":
            ask = 1.10002 - delta / 10000
            bid = ask - .00002
        else:
            bid = 1.10000 + delta / 10000
            ask = bid + .00002
        observed = published + timedelta(minutes=minute)
        return {"quote_id": f"synthetic:{index}:{minute}", "observed_at": iso(observed), "ingested_at": iso(observed + timedelta(seconds=1)),
                "bid": bid, "ask": ask, "verified": True}
    host = "www.federalreserve.gov" if bank == "FED" else "www.ecb.europa.eu"
    return {"event_id": f"synthetic:event:{index}", "bank": bank, "event_type": "FOMC_DECISION" if bank == "FED" else "ECB_DECISION",
            "direction": direction, "immutable_id": f"synthetic:receipt:{index}",
            "event_source": {"source_url": f"https://{host}/synthetic-fixture-{index}.html", "published_at": iso(published),
                             "first_seen_at": iso(published + timedelta(seconds=1)), "ingested_at": iso(published + timedelta(seconds=2)),
                             "statement_verified": True, "document_sha256": "a" * 64},
            "regime": {"label": "SYNTHETIC_LOW_VOL", "observed_at": iso(published - timedelta(minutes=1)),
                       "source_ref": "https://quotes.example.test/regime-fixture"},
            "outcome_closed_at": iso(published + timedelta(minutes=120)), "ingested_at": iso(published + timedelta(minutes=121)),
            "price_source": {"kind": "BID_ASK", "verified": True, "source": "SYNTHETIC_QUOTES", "source_ref": "https://quotes.example.test/synthetic", "maximum_quote_gap_seconds": 60},
            "incremental_cost_pips": 1., "event_slippage_pips": .5, "cost_semantics": "INCREMENTAL_HOLD_VS_CLOSE_NOW",
            "quotes": [quote(minute) for minute in range(121)]}


def estimate(samples, **overrides):
    arguments = dict(now=NOW, bank="FED", event_type="FOMC_DECISION", regime="SYNTHETIC_LOW_VOL", direction="SHORT",
                     current_net_R=.8, risk_pips=50., stop_distance_pips=30., tp_distance_pips=100.)
    arguments.update(overrides)
    return estimate_distribution(samples, **arguments)


class EventDistributionTests(unittest.TestCase):
    def test_missing_or_small_sample_returns_null_not_fabricated_probabilities(self):
        for samples in ([], [sample(index) for index in range(39)]):
            result = estimate(samples)
            self.assertEqual(result["status"], "RISK_MODEL_UNCALIBRATED")
            self.assertIsNone(result["expected_net_R"])
            self.assertIsNone(result["expected_shortfall_95_R"])
            self.assertIsNone(result["probability_of_profit_giveback"])
            self.assertEqual(result["windows"], {})
            self.assertFalse(result["activation_allowed"])

    def test_forty_independent_events_are_empirical_unvalidated_with_explicit_costs(self):
        samples = [sample(index) for index in range(40)]
        before = copy.deepcopy(samples)
        result = estimate(samples)
        self.assertEqual(samples, before)
        self.assertEqual(result["status"], "EMPIRICAL_UNVALIDATED")
        self.assertEqual(result["effective_sample_size"], 40)
        self.assertEqual(list(result["windows"]), ["1", "5", "15", "30", "60", "120"])
        self.assertAlmostEqual(result["expected_net_R"], .8 + (10. - 1.5) / 50.)
        self.assertEqual(result["observed_profit_giveback_frequency"], 0.)
        self.assertIsNone(result["probability_SL"])
        self.assertFalse(result["activation_allowed"])
        self.assertLess(result["windows"]["120"]["cost_sensitivity"][-1]["expected_net_R"], result["expected_net_R"])

    def test_long_and_short_use_the_correct_executable_side(self):
        short = estimate([sample(index) for index in range(40)])
        long = estimate([sample(index, direction="LONG") for index in range(40)], direction="LONG")
        self.assertAlmostEqual(short["expected_net_R"], long["expected_net_R"])
        self.assertEqual(estimate([sample(index, direction="LONG") for index in range(40)])["effective_sample_size"], 0)

    def test_gap_sl_uses_observed_overshoot_and_sample_tail_not_ideal_stop(self):
        result = estimate([sample(index, shock=index < 2) for index in range(40)])
        endpoint = result["windows"]["120"]
        self.assertAlmostEqual(endpoint["observed_SL_frequency"], .05)
        self.assertAlmostEqual(endpoint["observed_profit_giveback_frequency"], .05)
        self.assertAlmostEqual(endpoint["observed_gap_beyond_stop_frequency"], .05)
        self.assertAlmostEqual(endpoint["expected_shortfall_95_R"], -.23)
        ideal_stop = .8 - (30. + 1.5) / 50.
        self.assertLess(endpoint["expected_shortfall_95_R"], ideal_stop)

    def test_remaining_tp_caps_scenario_without_claiming_a_fill(self):
        result = estimate([sample(index) for index in range(40)], tp_distance_pips=4.)
        self.assertAlmostEqual(result["expected_net_R"], .8 + (4. - 1.5) / 50.)
        self.assertEqual(result["windows"]["120"]["payoff_scenario"], "OBSERVED_SL_OVERSHOOT_TP_CAPPED_NOT_A_FILL")

    def test_no_future_or_equal_time_outcome_enters_decision(self):
        for key in ("ingested_at", "outcome_closed_at"):
            samples = [sample(index) for index in range(40)]
            samples[-1][key] = iso(NOW)
            result = estimate(samples)
            self.assertEqual(result["effective_sample_size"], 39)
            self.assertEqual(result["status"], "RISK_MODEL_UNCALIBRATED")
        samples = [sample(index) for index in range(40)]
        samples[0]["regime"]["observed_at"] = iso(START + timedelta(minutes=1))
        self.assertEqual(estimate(samples)["effective_sample_size"], 39)

    def test_duplicate_retries_do_not_manufacture_independence(self):
        samples = [sample(index) for index in range(39)]
        result = estimate(samples + copy.deepcopy(samples))
        self.assertEqual(result["effective_sample_size"], 39)
        conflict = copy.deepcopy(samples[0])
        conflict["incremental_cost_pips"] = 0.
        result = estimate(samples + [conflict])
        self.assertEqual(result["effective_sample_size"], 38)
        self.assertTrue(any(row["reason"] == "CONFLICTING_EVENT_IDENTITY" for row in result["rejected_samples"]))

    def test_overlapping_event_windows_are_excluded_before_estimation(self):
        samples = [sample(index) for index in range(40)]
        samples[1] = sample(1, published=START + timedelta(minutes=1))
        result = estimate(samples)
        self.assertEqual(result["effective_sample_size"], 38)
        self.assertEqual(result["status"], "RISK_MODEL_UNCALIBRATED")

    def test_source_quotes_costs_and_summary_integrity_are_required(self):
        mutations = (lambda row: row["price_source"].update(kind="MID_PROXY"),
                     lambda row: row["event_source"].update(source_url="https://example.test/fed-headline"),
                     lambda row: row["quotes"][5].pop("ask"),
                     lambda row: row["quotes"][5].update(ingested_at=iso(NOW + timedelta(seconds=1))),
                     lambda row: row.pop("event_slippage_pips"),
                     lambda row: row.update(windows={"120": {"delta_pips": 999, "mae_pips": 0, "mfe_pips": 999}}))
        for mutate in mutations:
            samples = [sample(index) for index in range(40)]
            mutate(samples[0])
            result = estimate(samples)
            self.assertEqual(result["effective_sample_size"], 39)
            self.assertEqual(result["status"], "RISK_MODEL_UNCALIBRATED")

    def test_bank_type_regime_and_declared_independent_sample_minimum(self):
        samples = [sample(index, bank="ECB") for index in range(40)]
        self.assertEqual(estimate(samples)["effective_sample_size"], 0)
        self.assertEqual(estimate(samples, bank="ECB", event_type="ECB_DECISION")["status"], "EMPIRICAL_UNVALIDATED")
        self.assertEqual(estimate(samples, bank="ECB", event_type="ECB_DECISION", regime="OTHER_REGIME")["effective_sample_size"], 0)
        with self.assertRaises(ValueError):
            estimate([], minimum_samples=39)
        with self.assertRaises(ValueError):
            estimate([], bank="ECB", event_type="FOMC_DECISION")
        with self.assertRaises(ValueError):
            estimate([], risk_pips=float("nan"))

    def test_partial_quote_windows_do_not_change_sample_per_horizon(self):
        samples = [sample(index) for index in range(40)]
        samples[-1]["quotes"] = samples[-1]["quotes"][:-1]
        # Ending a minute early is explicit permitted 1m coverage. Two minutes
        # early cannot pretend the +120m horizon was fully observed.
        samples[-1]["quotes"] = samples[-1]["quotes"][:-1]
        result = estimate(samples)
        self.assertEqual(result["effective_sample_size"], 39)
        self.assertEqual(result["windows"], {})

    def test_load_model_is_read_only_and_missing_data_stays_empty(self):
        self.assertEqual(load_model(None), [])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "model.json"
            self.assertEqual(load_model(target), [])
            self.assertFalse(target.exists())
            target.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "samples": [sample()]}), encoding="utf-8")
            self.assertEqual(len(load_model(target)), 1)
            target.write_text('{"samples": []}', encoding="utf-8")
            self.assertEqual(load_model(target), [])


if __name__ == "__main__":
    unittest.main()

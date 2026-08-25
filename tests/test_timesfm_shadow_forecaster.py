import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.belief_market_data_adapter import Bar
from scripts.learning_ledger import read_events, verify_chain
from scripts.timesfm_shadow_forecaster import (
    HORIZONS,
    MODEL_ID,
    SCHEMA_VERSION,
    run_cycle,
    safety_controls,
    verify_state,
)


class FakeClient:
    def __init__(self, bars):
        self._bars = list(bars)

    def bars(self, symbol, range_, interval):
        self.last_call = (symbol, range_, interval)
        return list(self._bars)


class FakeRuntime:
    def __init__(self):
        self.calls = 0

    def forecast(self, context, horizon_steps):
        self.calls += 1
        base = float(context[-1])
        points = [base + 0.0001 * (idx + 1) for idx in range(horizon_steps)]
        quantiles = []
        for point in points:
            quantiles.append({
                "mean": point,
                "q10": point - 0.0010,
                "q20": point - 0.0008,
                "q30": point - 0.0006,
                "q40": point - 0.0003,
                "q50": point,
                "q60": point + 0.0003,
                "q70": point + 0.0006,
                "q80": point + 0.0008,
                "q90": point + 0.0010,
            })
        return points, quantiles


def make_bars(start, count, start_price=1.1500):
    return [
        Bar(timestamp=start + timedelta(minutes=30 * idx), close=start_price + 0.00005 * idx)
        for idx in range(count)
    ]


class TimesFMShadowForecasterTests(unittest.TestCase):
    def test_zero_authority(self):
        self.assertTrue(safety_controls())
        self.assertTrue(all(value is False for value in safety_controls().values()))

    def test_bootstrap_required(self):
        now = datetime(2026, 8, 25, 16, 0, tzinfo=timezone.utc)
        bars = make_bars(now - timedelta(minutes=30 * 300), 300)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                run_cycle(Path(tmp), now=now, client=FakeClient(bars), runtime=FakeRuntime())

    def test_forecasts_are_frozen_hash_chained_and_idempotent_for_same_origin(self):
        now = datetime(2026, 8, 25, 16, 0, tzinfo=timezone.utc)
        bars = make_bars(now - timedelta(minutes=30 * 300), 300)
        runtime = FakeRuntime()
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            first = run_cycle(state_dir, now=now, bootstrap=True, client=FakeClient(bars), runtime=runtime)
            self.assertEqual(len(HORIZONS), first.forecasts_appended)
            self.assertEqual(0, first.outcomes_appended)
            self.assertTrue(verify_chain(state_dir / "timesfm_shadow_ledger.jsonl")["ok"])

            events = read_events(state_dir / "timesfm_shadow_ledger.jsonl")
            forecasts = [row for row in events if row["event_type"] == "forecast"]
            self.assertEqual(len(HORIZONS), len(forecasts))
            for row in forecasts:
                self.assertEqual(MODEL_ID, row["payload"]["model_id"])
                self.assertTrue(row["payload"]["frozen_before_outcome"])
                self.assertFalse(row["payload"]["decision_influence"])
                self.assertEqual("EUR/USD", row["payload"]["instrument"])
                self.assertIn("q10", row["payload"]["quantiles"])
                self.assertIn("q90", row["payload"]["quantiles"])

            second = run_cycle(state_dir, now=now + timedelta(minutes=5), client=FakeClient(bars), runtime=runtime)
            self.assertEqual(0, second.forecasts_appended)
            self.assertEqual(len(HORIZONS), len([row for row in read_events(state_dir / "timesfm_shadow_ledger.jsonl") if row["event_type"] == "forecast"]))

    def test_future_bars_settle_prior_forecasts(self):
        now = datetime(2026, 8, 25, 16, 0, tzinfo=timezone.utc)
        history = make_bars(now - timedelta(minutes=30 * 300), 300)
        runtime = FakeRuntime()
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            run_cycle(state_dir, now=now, bootstrap=True, client=FakeClient(history), runtime=runtime)

            latest = history[-1]
            future = [
                Bar(timestamp=latest.timestamp + timedelta(minutes=30 * (idx + 1)), close=float(latest.close) + 0.00012 * (idx + 1))
                for idx in range(50)
            ]
            later = now + timedelta(hours=25)
            result = run_cycle(state_dir, now=later, client=FakeClient(history + future), runtime=runtime)
            self.assertEqual(len(HORIZONS), result.outcomes_appended)

            events = read_events(state_dir / "timesfm_shadow_ledger.jsonl")
            outcomes = [row for row in events if row["event_type"] == "outcome"]
            self.assertEqual(len(HORIZONS), len(outcomes))
            for row in outcomes:
                payload = row["payload"]
                self.assertGreater(payload["target_observed_at"], payload["forecast_at"])
                self.assertIn("absolute_error", payload)
                self.assertIn("squared_error", payload)
                self.assertIn("direction_correct", payload)
                self.assertIn("interval_80_contains_actual", payload)
                self.assertFalse(payload["decision_influence"])

    def test_stale_market_does_not_create_forecast(self):
        now = datetime(2026, 8, 25, 16, 0, tzinfo=timezone.utc)
        bars = make_bars(now - timedelta(minutes=30 * 400), 300)
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cycle(Path(tmp), now=now, bootstrap=True, client=FakeClient(bars), runtime=FakeRuntime())
            self.assertTrue(result.skipped_stale_origin)
            self.assertEqual(0, result.forecasts_appended)

    def test_tamper_is_detected(self):
        now = datetime(2026, 8, 25, 16, 0, tzinfo=timezone.utc)
        bars = make_bars(now - timedelta(minutes=30 * 300), 300)
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            run_cycle(state_dir, now=now, bootstrap=True, client=FakeClient(bars), runtime=FakeRuntime())
            ledger = state_dir / "timesfm_shadow_ledger.jsonl"
            rows = ledger.read_text(encoding="utf-8").splitlines()
            first = json.loads(rows[0])
            first["payload"]["forecast_price"] = 99.0
            rows[0] = json.dumps(first)
            ledger.write_text("\n".join(rows) + "\n", encoding="utf-8")
            verified = verify_state(state_dir)
            self.assertFalse(verified["ok"])
            with self.assertRaises(RuntimeError):
                run_cycle(state_dir, now=now + timedelta(minutes=10), client=FakeClient(bars), runtime=FakeRuntime())

    def test_activation_contract_is_explicit(self):
        now = datetime(2026, 8, 25, 16, 0, tzinfo=timezone.utc)
        bars = make_bars(now - timedelta(minutes=30 * 300), 300)
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            run_cycle(state_dir, now=now, bootstrap=True, client=FakeClient(bars), runtime=FakeRuntime())
            activation = json.loads((state_dir / "timesfm_shadow_activation.json").read_text(encoding="utf-8"))
            self.assertEqual(SCHEMA_VERSION, activation["schema_version"])
            self.assertEqual(MODEL_ID, activation["model_id"])
            self.assertFalse(activation["anti_hindsight"]["historical_backfill"])
            self.assertFalse(activation["anti_hindsight"]["same_cycle_new_forecast_outcome_binding"])
            self.assertTrue(all(value is False for value in activation["authority"].values()))


if __name__ == "__main__":
    unittest.main()

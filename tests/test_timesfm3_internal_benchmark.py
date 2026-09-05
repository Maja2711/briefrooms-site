import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.belief_market_data_adapter import Bar
from scripts.timesfm3_internal_benchmark import (
    ACTIVATION_FILENAME,
    DAILY_HORIZON_STEPS,
    MODEL_ID,
    _resolve_request_horizon,
    _wes_requests,
    build_status,
    ensure_activation,
    infer_cycle,
    prepare_cycle,
    verify_state,
)


class FakeRuntime:
    def forecast(self, context, horizon_steps):
        origin = float(context[-1])
        return [origin * (1.0 + 0.0001 * (i + 1)) for i in range(horizon_steps)]


def bars_30m(start: datetime, count: int, *, base: float = 1.10, drift: float = 0.00005):
    return [Bar(timestamp=start + timedelta(minutes=30 * i), close=base * (1.0 + drift * i)) for i in range(count)]


class TimesFM3InternalBenchmarkTests(unittest.TestCase):
    def test_activation_is_private_zero_authority_and_prospective(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            now = datetime(2026, 9, 7, 8, tzinfo=timezone.utc)
            activation = ensure_activation(state, now=now, bootstrap=True)
            self.assertEqual(activation["model_id"], MODEL_ID)
            self.assertFalse(activation["comparison_policy"]["pnl_benchmark"])
            self.assertTrue(activation["comparison_policy"]["directional_skill_only"])
            self.assertFalse(activation["authority"]["public_projection"])
            self.assertTrue(all(value is False for value in activation["authority"].values()))
            self.assertEqual(json.loads((state / ACTIVATION_FILENAME).read_text())["activated_at"], "2026-09-07T08:00:00Z")
            self.assertTrue(verify_state(state)["ok"])

    def test_daily_pair_is_created_after_activation_inferred_and_later_settled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state = root / "state"; daily_spot = root / "spot.json"; daily_history = root / "history.json"; weekly = root / "weekly"; weekly.mkdir()
            activation_at = datetime(2026, 9, 7, 7, tzinfo=timezone.utc); ensure_activation(state, now=activation_at, bootstrap=True)
            decision_at = datetime(2026, 9, 7, 8, 15, tzinfo=timezone.utc); trade_id = "eurusd:20260907T081500Z:LONG"
            daily_spot.write_text(json.dumps({"engine_version": "eurusd-daily-spot-v1.6.0", "metadata": {"position": {"trade_id": trade_id, "status": "OPEN", "direction": "LONG", "opened_at": "2026-09-07T08:15:00Z", "engine_version": "eurusd-daily-spot-v1.6.0"}}}))
            daily_history.write_text(json.dumps({"trades": []}))
            start = decision_at - timedelta(minutes=30 * 300); early_bars = bars_30m(start, 304)
            prepared = prepare_cycle(state, now=decision_at + timedelta(minutes=5), daily_spot=daily_spot, daily_history=daily_history, weekly_dir=weekly, bootstrap=False, bars=early_bars, license_gate_enabled=True)
            self.assertEqual(prepared["pending_added"], 1); self.assertTrue(prepared["needs_inference"])
            inferred = infer_cycle(state, now=decision_at + timedelta(minutes=6), runtime=FakeRuntime(), bars=early_bars, license_gate_enabled=True)
            self.assertEqual(inferred["forecasts_appended"], 1)
            status = build_status(state, license_gate_enabled=True); self.assertEqual(status["counts"]["forecast_pairs"], 1); self.assertEqual(status["counts"]["resolved_pairs"], 0)
            late_bars = bars_30m(start, 380)
            prepared2 = prepare_cycle(state, now=decision_at + timedelta(days=2), daily_spot=daily_spot, daily_history=daily_history, weekly_dir=weekly, bootstrap=False, bars=late_bars, license_gate_enabled=True)
            self.assertEqual(prepared2["settled"], 1)
            status2 = build_status(state, license_gate_enabled=True); self.assertEqual(status2["daily_eurusd"]["paired_resolved"], 1); self.assertEqual(status2["daily_eurusd"]["engine_direction_hit_rate"], 1.0); self.assertEqual(status2["daily_eurusd"]["timesfm3_direction_hit_rate"], 1.0)

    def test_pre_activation_daily_trade_is_never_backfilled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state = root / "state"; daily_spot = root / "spot.json"; daily_history = root / "history.json"; weekly = root / "weekly"; weekly.mkdir()
            activation_at = datetime(2026, 9, 7, 8, tzinfo=timezone.utc); ensure_activation(state, now=activation_at, bootstrap=True)
            daily_spot.write_text(json.dumps({"metadata": {}})); daily_history.write_text(json.dumps({"trades": [{"trade_id": "old", "direction": "SHORT", "opened_at": "2026-09-07T07:00:00Z", "engine_version": "v1"}]}))
            bars = bars_30m(datetime(2026, 9, 1, tzinfo=timezone.utc), 400)
            result = prepare_cycle(state, now=activation_at + timedelta(hours=1), daily_spot=daily_spot, daily_history=daily_history, weekly_dir=weekly, bootstrap=False, bars=bars, license_gate_enabled=False)
            self.assertEqual(result["pending_added"], 0); self.assertEqual(result["pending_inference"], 0)

    def test_wes_uses_frozen_exit_target_and_only_economic_direction(self):
        with tempfile.TemporaryDirectory() as tmp:
            weekly = Path(tmp); activation = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
            payload = {"week_id": "2026-W37", "method_version": "5.0.0-experimental", "forecast_locked_at": "2026-09-07T06:30:00+02:00", "market_window": {"exit_target_local": "2026-09-11T22:00:00+02:00"}, "instruments": [{"instrument_id": "eurusd", "symbol": "EURUSD=X", "direction": "short", "trade_status": "open"}]}
            (weekly / "2026-W37.json").write_text(json.dumps(payload))
            requests = _wes_requests(activation_at=activation, weekly_dir=weekly); self.assertEqual(len(requests), 1); self.assertEqual(requests[0].engine_direction, "SHORT"); self.assertEqual(requests[0].target_at, "2026-09-11T20:00:00Z")
            decision = datetime(2026, 9, 7, 4, 30, tzinfo=timezone.utc); context = bars_30m(decision - timedelta(minutes=30 * 300), 300); resolved = _resolve_request_horizon(requests[0], context)
            self.assertGreater(resolved.horizon_steps, DAILY_HORIZON_STEPS); self.assertLessEqual(resolved.horizon_steps, 256)
            payload["instruments"][0]["trade_status"] = "no_trade"; (weekly / "2026-W37.json").write_text(json.dumps(payload)); self.assertEqual(_wes_requests(activation_at=activation, weekly_dir=weekly), [])

    def test_inference_fails_closed_without_license_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp); ensure_activation(state, now=datetime(2026, 9, 7, 8, tzinfo=timezone.utc), bootstrap=True)
            with self.assertRaisesRegex(RuntimeError, "license gate"):
                infer_cycle(state, now=datetime(2026, 9, 7, 9, tzinfo=timezone.utc), runtime=FakeRuntime(), bars=[], license_gate_enabled=False)


if __name__ == "__main__":
    unittest.main()

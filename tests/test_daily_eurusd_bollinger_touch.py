"""EURUSD D1 band-touch cooldown: prospective state, dynamic bands and Belief authority."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from scripts import daily_eurusd_bollinger_d1 as d1
from scripts import daily_eurusd_bollinger_touch as touch

START = datetime(2026, 5, 1, tzinfo=timezone.utc)


def daily_bars(count=40):
    return [SimpleNamespace(timestamp=START + timedelta(days=i), close=1.1 + 0.0005 * i)
            for i in range(count)]


def minute(stamp, close, *, high=None, low=None):
    return SimpleNamespace(timestamp=stamp, close=close,
                           high=close if high is None else high,
                           low=close if low is None else low)


class DailyEURUSDBollingerTouchTests(unittest.TestCase):
    def setUp(self):
        self.now = START + timedelta(days=40, hours=12)
        self.ctx = d1.evaluate(daily_bars(), self.now)
        self.assertTrue(self.ctx["available"])
        self.upper = self.ctx["upper"]
        self.lower = self.ctx["lower"]
        self.long_level = (self.ctx["upper"] + self.ctx["middle"]) / 2
        self.short_level = (self.ctx["lower"] + self.ctx["middle"]) / 2

    def test_upper_touch_blocks_long_until_half_correction(self):
        at = self.now - timedelta(minutes=10)
        first = touch.update(self.ctx, [minute(at, self.upper, high=self.upper + 0.0001)], self.now)
        self.assertTrue(first["upper_long_blocked"])
        self.assertFalse(first["lower_short_blocked"])
        self.assertFalse(first["opens_trade"])
        partial = touch.update(self.ctx,
            [minute(at, self.upper, high=self.upper + 0.0001),
             minute(at + timedelta(minutes=1), self.long_level + 0.0001)],
            self.now + timedelta(minutes=2), previous_state=first["state"])
        self.assertTrue(partial["upper_long_blocked"])
        corrected = touch.update(self.ctx,
            [minute(at, self.upper, high=self.upper + 0.0001),
             minute(at + timedelta(minutes=1), self.long_level + 0.0001),
             minute(at + timedelta(minutes=2), self.long_level - 0.0001)],
            self.now + timedelta(minutes=3), previous_state=partial["state"])
        self.assertFalse(corrected["upper_long_blocked"])
        self.assertEqual(corrected["fraction"], 0.5)
        self.assertTrue(corrected["requires_belief_recheck"])
        repeat = touch.update(self.ctx,
            [minute(at, self.upper, high=self.upper + 0.0001)],
            self.now + timedelta(minutes=4), previous_state=corrected["state"])
        self.assertFalse(repeat["upper_long_blocked"], "replayed touch must not re-arm")

    def test_lower_touch_blocks_short_until_half_rebound(self):
        at = self.now - timedelta(minutes=10)
        first = touch.update(self.ctx, [minute(at, self.lower, low=self.lower - 0.0001)], self.now)
        self.assertTrue(first["lower_short_blocked"])
        self.assertFalse(first["upper_long_blocked"])
        corrected = touch.update(self.ctx,
            [minute(at + timedelta(minutes=1), self.short_level + 0.0001)],
            self.now + timedelta(minutes=2), previous_state=first["state"])
        self.assertFalse(corrected["lower_short_blocked"])
        self.assertTrue(corrected["requires_belief_recheck"])

    def test_recomputes_halfway_price_after_daily_band_changes(self):
        at = self.now - timedelta(minutes=10)
        first = touch.update(self.ctx, [minute(at, self.upper, high=self.upper + 0.0001)], self.now)
        changed_now = self.now + timedelta(days=1)
        changed = d1.evaluate(daily_bars(41), changed_now)
        later = touch.update(changed, [], changed_now, previous_state=first["state"])
        self.assertTrue(later["upper_long_blocked"])
        self.assertNotEqual(later["long_release_price"], first["long_release_price"])
        self.assertEqual(later["state"]["upper"]["touched_at"], first["state"]["upper"]["touched_at"])

    def test_missing_d1_data_preserves_block(self):
        at = self.now - timedelta(minutes=10)
        first = touch.update(self.ctx, [minute(at, self.upper, high=self.upper + 0.0001)], self.now)
        missing = touch.update({"available": False, "reason": "stale_d1"}, [],
                               self.now + timedelta(hours=1), previous_state=first["state"])
        self.assertFalse(missing["available"])
        self.assertTrue(missing["upper_long_blocked"])
        self.assertIsNone(missing["long_release_price"])

    def test_same_minute_upper_hit_and_correction_cannot_release(self):
        at = self.now - timedelta(minutes=10)
        ambiguous = minute(at, self.long_level - 0.0001,
                           high=self.upper + 0.0001, low=self.long_level - 0.0001)
        result = touch.update(self.ctx, [ambiguous], self.now)
        self.assertTrue(result["upper_long_blocked"])

    def test_m1_bar_not_yet_closed_is_not_used(self):
        at = self.now - timedelta(seconds=20)
        result = touch.update(self.ctx, [minute(at, self.upper, high=self.upper + 0.0001)], self.now)
        self.assertFalse(result["upper_long_blocked"])

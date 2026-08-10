import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import investments_wes as wes

TZ = ZoneInfo('Europe/Warsaw')


class WesTests(unittest.TestCase):
    def test_friday_near_close_requires_extreme_trigger(self):
        now = datetime(2026, 8, 14, 20, 45, tzinfo=TZ)
        p = wes.trigger_profile(now, 75)
        self.assertTrue(p['allowed'])
        self.assertGreaterEqual(p['raw'], 80)
        self.assertGreaterEqual(p['confirmations'], 3)

    def test_friday_too_late_blocks_entry(self):
        now = datetime(2026, 8, 14, 21, 30, tzinfo=TZ)
        p = wes.trigger_profile(now, 30)
        self.assertFalse(p['allowed'])

    def test_friday_tactical_uses_low_tp_and_positive_rr(self):
        stats = {'classes': {}}
        sl, tp, meta = wes.adaptive_distances(100.0, 160.0, 2.0, 85.0, 'friday_tactical', stats)
        self.assertLessEqual(tp, 160.0 * 0.35 + 1e-9)
        self.assertGreaterEqual(tp / sl, 1.05 - 1e-9)
        self.assertLess(sl, 100.0)
        self.assertEqual(meta['target_min_rr'], 1.05)

    def test_midweek_plan_is_wider_than_friday_for_same_base(self):
        stats = {'classes': {}}
        _, tp_mid, _ = wes.adaptive_distances(100.0, 160.0, 55.0, 70.0, 'midweek_trigger', stats)
        _, tp_fri, _ = wes.adaptive_distances(100.0, 160.0, 2.0, 70.0, 'friday_tactical', stats)
        self.assertGreater(tp_mid, tp_fri)

    def test_weak_history_raises_entry_hurdle(self):
        stats = {'classes': {'midweek_trigger': {'count': 8, 'mean_net_percent': -0.1, 'win_rate': 0.375}}}
        self.assertEqual(wes.learning_threshold_penalty(stats, 'midweek_trigger'), 6.0)

    def test_good_history_can_reduce_hurdle_but_not_force_trade(self):
        stats = {'classes': {'midweek_trigger': {'count': 10, 'mean_net_percent': 0.12, 'win_rate': 0.6}}}
        self.assertEqual(wes.learning_threshold_penalty(stats, 'midweek_trigger'), -3.0)


if __name__ == '__main__':
    unittest.main()

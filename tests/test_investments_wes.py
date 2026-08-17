import unittest
from datetime import datetime
from unittest.mock import patch
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

    def test_governed_candidate_uses_full_contextual_learning_and_candidate_counts(self):
        now = datetime(2026, 8, 17, 18, 0, tzinfo=TZ)
        cfg = {'symbol': 'EURUSD=X'}
        p_cfg = {'default_tie_direction': 'long'}
        week = {'week_id': '2026-W34'}
        policy = {'contextual_learning': {'enabled': True}}
        method = {}
        fresh = {'data_quality': 'passed', 'score': 40.0, 'signals': {'ret5_pct': 1.0, 'ret20_pct': 1.0}}
        weekly = {'data_quality': 'passed', 'score': 20.0, 'regime': 'trend_up:vol_normal'}
        macro_context = {'data_quality': 'passed', 'direction': 'long', 'ma_structure': {'data_quality': 'passed', 'score': 1.0, 'direction': 'long'}}
        selected_learning = {'methods': {'base_v2': {'count': 1, 'adjustment': 0.0}}}
        base_candidates = {'base_v2': {'direction': 'long', 'raw_score': 40.0, 'conviction': 6.0}}
        macro_candidates = {'base_v2': {'direction': 'long', 'raw_score': 40.0, 'conviction': 6.2}}
        adjusted_candidates = {'base_v2': {'direction': 'long', 'raw_score': 40.0, 'conviction': 6.5}}
        contextual = {'methods': {'base_v2': {'candidate_observation_count': 8}}}
        choice_learning = {'methods': {'base_v2': {'count': 8, 'adjustment': 0.0}}}
        decision = {'strategy_id': 'base_v2', 'direction': 'long', 'raw_score': 40.0, 'utility': 8.0}

        with patch.object(wes.v2, 'model_signal', return_value=fresh), \
             patch.object(wes.v3, 'weekly_candle_signal', return_value=weekly), \
             patch.object(wes.macro, 'context', return_value=macro_context), \
             patch.object(wes.v4, 'learning_stats', return_value=selected_learning), \
             patch.object(wes.v4, 'candidate_methods', return_value=base_candidates), \
             patch.object(wes.macro, 'apply_to_candidates', return_value=macro_candidates), \
             patch.object(wes.v5, 'apply_contextual_learning', return_value=(adjusted_candidates, contextual)) as contextual_mock, \
             patch.object(wes.v5, 'learning_with_candidate_observations', return_value=choice_learning) as learning_mock, \
             patch.object(wes.v4, 'choose', return_value=decision) as choose_mock:
            result = wes.governed_candidate('eurusd', cfg, p_cfg, week, policy, method, now)

        contextual_mock.assert_called_once_with(
            'eurusd', macro_candidates, fresh, policy, weekly=weekly, macro_context=macro_context
        )
        learning_mock.assert_called_once_with(selected_learning, contextual)
        choose_mock.assert_called_once_with(adjusted_candidates, choice_learning, policy)
        self.assertEqual(choice_learning, result['learning'])
        self.assertEqual(selected_learning, result['selected_leg_learning'])


if __name__ == '__main__':
    unittest.main()

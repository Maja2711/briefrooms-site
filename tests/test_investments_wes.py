import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import investments_wes as wes
import investments_wes_lifecycle as lifecycle

TZ = ZoneInfo('Europe/Warsaw')


class WesTests(unittest.TestCase):
    def test_friday_near_close_requires_extreme_trigger(self):
        now = datetime(2026, 8, 14, 20, 45, tzinfo=TZ)
        p = wes.trigger_profile(now, 75)
        self.assertTrue(p['allowed'])
        self.assertGreaterEqual(p['raw'], 80)
        self.assertGreaterEqual(p['confirmations'], 3)

    def test_friday_too_late_blocks_normal_weekly_entry(self):
        now = datetime(2026, 8, 14, 21, 30, tzinfo=TZ)
        p = wes.trigger_profile(now, 30)
        self.assertFalse(p['allowed'])

    def test_early_reentry_uses_full_horizon_profile_even_on_friday(self):
        now = datetime(2026, 8, 14, 21, 30, tzinfo=TZ)
        p = lifecycle.early_reentry_trigger_profile(now)
        self.assertTrue(p['allowed'])
        self.assertEqual('early_close_reentry', p['profile'])
        self.assertEqual(7, p['holding_horizon_days'])
        self.assertEqual(48.0, p['raw'])

    def test_monday_or_tuesday_close_is_eligible_for_fresh_wes_reentry(self):
        now = datetime(2026, 8, 12, 12, 0, tzinfo=TZ)  # Wednesday
        week = {
            'week_id': '2026-W33',
            'market_window': {'exit_target_local': '2026-08-14T22:00:00+02:00'},
        }
        monday_close = {
            'entry_price': 1.1,
            'exit_price': 1.11,
            'exit_captured_at': '2026-08-10T23:50:00+02:00',
            'exit_reason': 'daily_model_confirmed_opposite_signal',
            'risk_status': 'closed_by_daily_model_review',
        }
        self.assertTrue(lifecycle.early_close_reentry_candidate(monday_close, week, now))
        tuesday_close = {**monday_close, 'exit_captured_at': '2026-08-11T12:00:00+02:00'}
        self.assertTrue(lifecycle.early_close_reentry_candidate(tuesday_close, week, now))

    def test_wednesday_close_is_not_eligible_for_same_week_reentry(self):
        now = datetime(2026, 8, 12, 15, 0, tzinfo=TZ)
        week = {
            'week_id': '2026-W33',
            'market_window': {'exit_target_local': '2026-08-14T22:00:00+02:00'},
        }
        item = {
            'entry_price': 1.1,
            'exit_price': 1.11,
            'exit_captured_at': '2026-08-12T12:00:00+02:00',
            'exit_reason': 'daily_model_confirmed_opposite_signal',
        }
        self.assertFalse(lifecycle.early_close_reentry_candidate(item, week, now))

    def test_material_event_exit_remains_fail_closed(self):
        now = datetime(2026, 8, 12, 12, 0, tzinfo=TZ)
        week = {
            'week_id': '2026-W33',
            'market_window': {'exit_target_local': '2026-08-14T22:00:00+02:00'},
        }
        item = {
            'entry_price': 1.1,
            'exit_price': 1.09,
            'exit_captured_at': '2026-08-10T12:00:00+02:00',
            'exit_reason': 'event_review_material_event_exit_request',
            'risk_status': 'closed_by_material_event_review',
        }
        self.assertFalse(lifecycle.early_close_reentry_candidate(item, week, now))

    def test_rolling_deadline_is_seven_calendar_days_from_actual_entry(self):
        entry = datetime(2026, 8, 13, 14, 25, tzinfo=TZ)
        self.assertEqual(datetime(2026, 8, 20, 14, 25, tzinfo=TZ), lifecycle.rolling_deadline(entry))

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

    def test_wes_workflow_persists_v5_context_state_and_fails_on_unstaged_changes(self):
        workflow = (ROOT / '.github' / 'workflows' / 'investments-wes.yml').read_text(encoding='utf-8')
        self.assertIn('data/investments/multi_instrument_exposure_state_v5.json', workflow)
        self.assertIn('data/investments/multi_instrument_exposure_report_v5.json', workflow)
        self.assertIn('if ! git diff --quiet; then', workflow)
        self.assertIn('Unexpected unstaged WES changes block safe rebase', workflow)


if __name__ == '__main__':
    unittest.main()

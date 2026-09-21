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
import investments_weekly_v4 as v4
import investments_weekly_v5 as v5

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
             patch.object(wes.v4, 'choose_governed', return_value=decision) as choose_mock:
            result = wes.governed_candidate('eurusd', cfg, p_cfg, week, policy, method, now)

        contextual_mock.assert_called_once_with(
            'eurusd', macro_candidates, fresh, policy, weekly=weekly, macro_context=macro_context
        )
        learning_mock.assert_called_once_with(selected_learning, contextual)
        choose_mock.assert_called_once_with(adjusted_candidates, choice_learning, policy, 'eurusd')
        self.assertEqual(choice_learning, result['learning'])
        self.assertEqual(selected_learning, result['selected_leg_learning'])

    def test_wes_1_1_inverse_is_shadow_and_cannot_win_exact_btc_tie(self):
        policy = {
            'strategy_tournament': {
                'candidate_methods': ['base_v2', 'inverse_v2'],
                'selection_priority': ['base_v2', 'inverse_v2'],
                'exploration_bonus': 2.5,
                'champion_challenger': {
                    'execution_methods': ['base_v2'],
                    'challenger_shadow_methods': ['inverse_v2'],
                    'challenger_execution_enabled': False,
                    'opposing_direction_utility_margin_no_trade': 0.5,
                },
            }
        }
        candidates = {
            'base_v2': {'direction': 'long', 'raw_score': 65.0, 'conviction': 9.75},
            'inverse_v2': {'direction': 'short', 'raw_score': -65.0, 'conviction': 9.75},
        }
        learning = {'methods': {
            'base_v2': {'count': 9, 'adjustment': 0.0},
            'inverse_v2': {'count': 9, 'adjustment': 0.0},
        }}
        decision = v4.choose_governed(candidates, learning, policy, 'btcusd')
        self.assertEqual('base_v2', decision['strategy_id'])
        self.assertEqual('long', decision['direction'])
        self.assertTrue(decision['candidates']['base_v2']['execution_eligible'])
        self.assertFalse(decision['candidates']['inverse_v2']['execution_eligible'])
        self.assertEqual('challenger_shadow', decision['candidates']['inverse_v2']['execution_authority'])

    def test_wes_1_1_opposing_champions_inside_margin_resolve_to_no_trade(self):
        policy = {
            'strategy_tournament': {
                'candidate_methods': ['base_v2', 'weekly_trend'],
                'selection_priority': ['base_v2', 'weekly_trend'],
                'exploration_bonus': 2.5,
                'champion_challenger': {
                    'execution_methods': ['base_v2', 'weekly_trend'],
                    'challenger_shadow_methods': [],
                    'challenger_execution_enabled': False,
                    'opposing_direction_utility_margin_no_trade': 0.5,
                },
            }
        }
        candidates = {
            'base_v2': {'direction': 'long', 'raw_score': 60.0, 'conviction': 9.0},
            'weekly_trend': {'direction': 'short', 'raw_score': -60.0, 'conviction': 9.0},
        }
        learning = {'methods': {
            'base_v2': {'count': 4, 'adjustment': 0.0},
            'weekly_trend': {'count': 4, 'adjustment': 0.0},
        }}
        decision = v4.choose_governed(candidates, learning, policy, 'btcusd')
        self.assertEqual('no_trade', decision['strategy_id'])
        self.assertEqual('neutral', decision['direction'])
        self.assertIn('opposing_execution_candidates_within_utility_margin', decision['reason_codes'])

    def test_wes_1_1_blocks_exact_btc_incident_short_against_daily_and_weekly_long(self):
        policy = {
            'directional_admission': {
                'enabled': True,
                'minimum_confirmations': 2,
                'daily_min_abs_score': 25,
                'weekly_min_abs_score': 15,
                'ma_min_abs_score': 1,
                'block_against_aligned_daily_weekly': True,
            }
        }
        decision = {
            'strategy_id': 'test_short',
            'direction': 'short',
            'raw_score': -65.0,
            'utility': 10.54,
            'execution_authority': 'champion_execution',
            'execution_eligible': True,
        }
        fresh = {'data_quality': 'passed', 'score': 65.0}
        weekly = {'data_quality': 'passed', 'score': 49.0}
        admitted, reasons, diagnostics = v5.directional_admission(decision, fresh, weekly, {}, policy)
        self.assertFalse(admitted)
        self.assertEqual(0, diagnostics['confirmations'])
        self.assertIn('insufficient_directional_confirmations', reasons)
        self.assertIn('candidate_opposes_aligned_daily_weekly', reasons)

    def test_wes_1_1_allows_direction_with_two_independent_confirmations(self):
        policy = {
            'directional_admission': {
                'enabled': True,
                'minimum_confirmations': 2,
                'daily_min_abs_score': 25,
                'weekly_min_abs_score': 15,
                'ma_min_abs_score': 1,
                'block_against_aligned_daily_weekly': True,
            }
        }
        decision = {
            'strategy_id': 'base_v2',
            'direction': 'long',
            'raw_score': 65.0,
            'utility': 10.54,
            'execution_authority': 'champion_execution',
            'execution_eligible': True,
        }
        fresh = {'data_quality': 'passed', 'score': 65.0}
        weekly = {'data_quality': 'passed', 'score': 49.0}
        admitted, reasons, diagnostics = v5.directional_admission(decision, fresh, weekly, {}, policy)
        self.assertTrue(admitted)
        self.assertEqual([], reasons)
        self.assertEqual(['daily', 'weekly'], diagnostics['confirmation_sources'])

    def test_wes_1_1_every_new_entry_requires_matching_non_shadow_authorization(self):
        now = datetime(2026, 9, 21, 9, 24, tzinfo=TZ)
        policy = {'directional_admission': {'require_for_all_new_entries': True}}
        decision = {'strategy_id': 'base_v2', 'direction': 'long'}
        ok, reason = v5.wes_authorization_matches({}, decision, now, policy)
        self.assertFalse(ok)
        self.assertEqual('wes_entry_authorization_missing', reason)

        item = {
            'wes_entry_authorization': {
                'expires_at': '2026-09-21T09:40:00+02:00',
                'directional_admission_passed': True,
                'candidate': {
                    'strategy_id': 'inverse_v2',
                    'direction': 'short',
                    'execution_authority': 'challenger_shadow',
                },
            }
        }
        ok, reason = v5.wes_authorization_matches(item, {'strategy_id': 'inverse_v2', 'direction': 'short'}, now, policy)
        self.assertFalse(ok)
        self.assertEqual('wes_candidate_not_execution_authorized', reason)

    def test_wes_version_upgrade_never_rewrites_existing_frozen_risk_plan(self):
        frozen = {
            'model_version': 'WES-1.0.0',
            'generated_at': '2026-09-21T09:26:26+02:00',
            'direction': 'short',
            'stop_loss_price': 1.15285363,
            'take_profit_price': 1.13786575,
            'wes_entry_class': 'monday_weekly',
        }
        week = {
            'week_id': '2026-W39',
            'instruments': [{
                'instrument_id': 'eurusd',
                'direction': 'short',
                'entry_price': 1.1478420496,
                'exit_price': None,
                'risk_plan': dict(frozen),
                'wes_status': 'open_wes_governed_position',
                'wes_methodology': 'WES-1.0.0',
            }],
        }
        existing_path = ROOT / 'data' / 'investments' / 'multi_instrument_exposure_policy.json'
        with patch.object(wes, 'current_week_path', return_value=existing_path), \
             patch.object(wes, 'read', return_value=week), \
             patch.object(wes, 'write'), \
             patch.object(wes, 'learning_stats', return_value={'classes': {}}), \
             patch.object(wes, 'build_wes_plan') as build:
            wes.postflight()
        build.assert_not_called()
        self.assertEqual(frozen, week['instruments'][0]['risk_plan'])
        self.assertEqual('WES-1.2.0', week['instruments'][0]['wes_methodology'])

    def test_wes_1_2_legacy_pending_without_price_plan_is_never_reused(self):
        item = {
            'wes_entry_authorization': {
                'authorized_at': '2026-09-21T12:30:00+02:00',
                'directional_admission_passed': True,
                'candidate': {
                    'strategy_id': 'weekly_trend',
                    'direction': 'long',
                    'execution_authority': 'champion_execution',
                },
            }
        }
        stale = {
            'decided_at': '2026-09-21T09:24:34+02:00',
            'entry_not_before': '2026-09-21T09:24:34+02:00',
            'decision': {'strategy_id': 'weekly_trend', 'direction': 'long'},
        }
        self.assertFalse(v5.pending_matches_wes_authorization(item, stale))

        fresh_but_legacy = {
            'decided_at': '2026-09-21T12:31:00+02:00',
            'entry_not_before': '2026-09-21T12:31:00+02:00',
            'decision': {'strategy_id': 'weekly_trend', 'direction': 'long'},
        }
        self.assertFalse(v5.pending_matches_wes_authorization(item, fresh_but_legacy))

    def test_wes_1_1_pending_must_match_authorized_method_and_direction(self):
        item = {
            'wes_entry_authorization': {
                'authorized_at': '2026-09-21T12:30:00+02:00',
                'candidate': {'strategy_id': 'base_v2', 'direction': 'long'},
            }
        }
        wrong = {
            'decided_at': '2026-09-21T12:31:00+02:00',
            'entry_not_before': '2026-09-21T12:31:00+02:00',
            'decision': {'strategy_id': 'inverse_v2', 'direction': 'short'},
        }
        self.assertFalse(v5.pending_matches_wes_authorization(item, wrong))

    def test_repository_policy_marks_inverse_v2_shadow_only(self):
        import json
        policy = json.loads((ROOT / 'data' / 'investments' / 'multi_instrument_exposure_policy.json').read_text(encoding='utf-8'))
        cc = policy['strategy_tournament']['champion_challenger']
        self.assertIn('inverse_v2', cc['challenger_shadow_methods'])
        self.assertNotIn('inverse_v2', cc['execution_methods'])
        self.assertFalse(cc['challenger_execution_enabled'])
        self.assertTrue(policy['directional_admission']['require_for_all_new_entries'])

    def test_wes_workflow_persists_v5_context_state_and_fails_on_unstaged_changes(self):
        workflow = (ROOT / '.github' / 'workflows' / 'investments-wes.yml').read_text(encoding='utf-8')
        self.assertIn('data/investments/multi_instrument_exposure_state_v5.json', workflow)
        self.assertIn('data/investments/multi_instrument_exposure_report_v5.json', workflow)
        self.assertIn('if ! git diff --quiet; then', workflow)
        self.assertIn('Unexpected unstaged WES changes block safe rebase', workflow)


if __name__ == '__main__':
    unittest.main()

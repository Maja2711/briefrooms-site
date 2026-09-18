import unittest
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import stock_trading_portfolio as stock

UTC = ZoneInfo('UTC')


def policy():
    return {
        'policy_version': 'test',
        'forced_trade_allowed': False,
        'markets': {
            'GPW': {'max_open_positions': 3, 'minimum_entry_score': 72.0, 'minimum_reward_risk': 1.5, 'maximum_risk_percent': 0.07, 'atr_multiple': 1.1, 'risk_floor_percent': 0.012, 'model_exit_score': 30.0, 'candidate_file': 'x', 'target_position_notional': 5000.0, 'position_currency': 'PLN', 'fractional_quantity_allowed': True, 'quantity_precision': 8, 'sizing_policy_version': 'FIXED_NOTIONAL_V1'},
            'US': {'max_open_positions': 3, 'minimum_entry_score': 72.0, 'minimum_reward_risk': 1.5, 'maximum_risk_percent': 0.07, 'atr_multiple': 1.05, 'risk_floor_percent': 0.011, 'model_exit_score': 30.0, 'candidate_file': 'y', 'target_position_notional': 5000.0, 'position_currency': 'USD', 'fractional_quantity_allowed': True, 'quantity_precision': 8, 'sizing_policy_version': 'FIXED_NOTIONAL_V1'},
        },
    }


def candidate(market='US', symbol='AAA', score=80.0, forced=False, governed_final=False):
    decision = 'TRADE' if market == 'US' else 'TRANSAKCJA'
    return {
        'date': '2026-09-09',
        'generated_at': f'2026-09-09T12:00:00+00:00-{symbol}',
        'decision': decision,
        'reason': 'qualified model signal',
        'selection': {
            'symbol': symbol,
            'ticker': symbol,
            'name': symbol,
            'sector': 'test',
            'score': score,
            'reference_price': 100.0,
            'stop': 95.0,
            'target': 110.0,
            'risk_percent': 0.05,
            'reward_risk': 2.0,
            'selection_mode': 'MANDATORY_DAILY_FINAL' if forced or governed_final else 'MODEL_QUALIFIED',
            'market_snapshot': {'last': 100.0, 'high': 101.0, 'low': 99.0},
            'expected_value_model': {'conservative_ev_r': 0.25},
        },
        'data_quality': {
            'status': 'healthy',
            'mandatory_selection': {'applied': True},
        } if governed_final else {},
    }


class StockTradingPortfolioTests(unittest.TestCase):
    def setUp(self):
        self.policy = policy()
        self.now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
        self.state = stock.empty_state(self.now, self.policy)

    def test_cash_is_valid_and_empty_slots_do_not_force_entry(self):
        low = candidate(score=71.99)
        updated, action = stock.admit_candidate(self.state, 'US', low, now=self.now, policy=self.policy)
        self.assertEqual('cash', action['action'])
        self.assertEqual(0, len(stock.open_positions(updated, 'US')))
        self.assertEqual(3, stock.available_slots(updated, 'US', self.policy))

    def test_forced_daily_candidate_is_rejected_even_with_high_score(self):
        ok, reason = stock.qualify_candidate('US', candidate(score=99, forced=True), self.policy)
        self.assertFalse(ok)
        self.assertEqual('forced_daily_candidate_rejected', reason)

    def test_governed_gpw_final_candidate_uses_score_as_ranking_not_veto(self):
        payload = candidate('GPW', 'PGE.WA', score=68.87, governed_final=True)
        ok, reason = stock.qualify_candidate('GPW', payload, self.policy)
        self.assertTrue(ok)
        self.assertEqual('qualified_high_expectancy_candidate', reason)

    def test_governed_candidate_is_retried_after_obsolete_policy_rejection(self):
        payload = candidate('GPW', 'PGE.WA', score=68.87, governed_final=True)
        key = stock.candidate_key(payload)
        self.state['markets']['GPW']['last_candidate_key'] = key
        self.state['markets']['GPW']['last_candidate_decision'] = 'CASH'
        self.state['markets']['GPW']['last_candidate_reason'] = 'forced_daily_candidate_rejected'
        updated, action = stock.admit_candidate(self.state, 'GPW', payload, now=self.now, policy=self.policy)
        self.assertEqual('open', action['action'])
        self.assertEqual('PGE.WA', stock.open_positions(updated, 'GPW')[0]['symbol'])

    def test_gpw_cap_is_three(self):
        state = self.state
        for symbol in ('AAA.WA', 'BBB.WA', 'CCC.WA'):
            state, action = stock.admit_candidate(state, 'GPW', candidate('GPW', symbol), now=self.now, policy=self.policy)
            self.assertEqual('open', action['action'])
        state, action = stock.admit_candidate(state, 'GPW', candidate('GPW', 'DDD.WA'), now=self.now, policy=self.policy)
        self.assertEqual('portfolio_full', action['action'])
        self.assertEqual(3, len(stock.open_positions(state, 'GPW')))

    def test_us_cap_is_three_and_independent_from_gpw(self):
        state = self.state
        for symbol in ('A', 'B', 'C'):
            state, _ = stock.admit_candidate(state, 'US', candidate('US', symbol), now=self.now, policy=self.policy)
        self.assertEqual(0, stock.available_slots(state, 'US', self.policy))
        self.assertEqual(3, stock.available_slots(state, 'GPW', self.policy))
        state, action = stock.admit_candidate(state, 'GPW', candidate('GPW', 'PKO.WA'), now=self.now, policy=self.policy)
        self.assertEqual('open', action['action'])
        self.assertEqual(1, len(stock.open_positions(state, 'GPW')))

    def test_every_opened_position_has_explicit_sl_tp_and_no_time_stop(self):
        state, action = stock.admit_candidate(self.state, 'US', candidate(), now=self.now, policy=self.policy)
        self.assertEqual('open', action['action'])
        position = stock.open_positions(state, 'US')[0]
        self.assertLess(position['stop'], position['entry'])
        self.assertGreater(position['target'], position['entry'])
        self.assertEqual('OPEN_ENDED_MODEL_CONTROLLED', position['holding_policy'])
        self.assertIsNone(position['scheduled_exit'])
        self.assertIsNone(position['valid_until'])
        self.assertIsNone(position['time_stop'])

    def test_every_new_position_uses_fixed_5000_market_currency_notional(self):
        us_state, _ = stock.admit_candidate(self.state, 'US', candidate('US', 'AAA'), now=self.now, policy=self.policy)
        us = stock.open_positions(us_state, 'US')[0]
        self.assertEqual('FIXED_NOTIONAL_V1', us['sizing_policy_version'])
        self.assertEqual('USD', us['position_currency'])
        self.assertEqual(5000.0, us['target_position_notional'])
        self.assertAlmostEqual(5000.0, us['entry'] * us['quantity'], places=4)
        self.assertEqual(50.0, us['quantity'])

        gpw_state, _ = stock.admit_candidate(self.state, 'GPW', candidate('GPW', 'PKO.WA'), now=self.now, policy=self.policy)
        gpw = stock.open_positions(gpw_state, 'GPW')[0]
        self.assertEqual('PLN', gpw['position_currency'])
        self.assertEqual(5000.0, gpw['target_position_notional'])
        self.assertAlmostEqual(5000.0, gpw['entry'] * gpw['quantity'], places=4)
        self.assertEqual(50.0, gpw['quantity'])

    def test_expensive_share_uses_fractional_quantity_to_keep_5000_notional(self):
        payload = candidate('GPW', 'LPP.WA', score=90.0)
        payload['selection']['reference_price'] = 24040.0
        payload['selection']['market_snapshot']['last'] = 24040.0
        payload['selection']['stop'] = 23264.0
        payload['selection']['target'] = 26000.0
        payload['selection']['risk_percent'] = (24040.0 - 23264.0) / 24040.0
        state, action = stock.admit_candidate(self.state, 'GPW', payload, now=self.now, policy=self.policy)
        self.assertEqual('open', action['action'])
        position = stock.open_positions(state, 'GPW')[0]
        self.assertLess(position['quantity'], 1.0)
        self.assertAlmostEqual(5000.0, position['entry'] * position['quantity'], places=2)

    def test_history_normalization_uses_fixed_5000_and_fractional_quantity(self):
        row = {
            'market': 'GPW',
            'entry': 24040.0,
            'exit_price': 23264.3494,
        }
        metrics = stock.history_normalized_metrics(row)
        self.assertEqual('FIXED_NOTIONAL_HISTORY_V1', metrics['history_normalization_version'])
        self.assertEqual('PLN', metrics['history_position_currency'])
        self.assertEqual(5000.0, metrics['history_target_position_notional'])
        self.assertLess(metrics['history_normalized_quantity'], 1.0)
        self.assertAlmostEqual(5000.0, 24040.0 * metrics['history_normalized_quantity'], places=2)
        self.assertEqual(-161.33, metrics['history_normalized_pnl_amount'])

    def test_legacy_closure_gets_5k_history_normalization_without_fake_execution_quantity(self):
        legacy = {
            'position_id': 'us:legacy:MU',
            'market': 'US',
            'status': 'OPEN',
            'entry': 1017.91,
            'stop': 981.48601074,
            'target': 1111.0,
        }
        closure = stock._closure(
            legacy,
            now=self.now,
            exit_price=981.48601074,
            reason='stop_loss',
        )
        self.assertNotIn('quantity', closure)
        self.assertEqual('USD', closure['history_position_currency'])
        self.assertEqual(5000.0, closure['history_target_position_notional'])
        self.assertEqual(-178.92, closure['history_normalized_pnl_amount'])

    def test_fixed_notional_pnl_is_cash_exposure_not_one_share_move(self):
        state, _ = stock.admit_candidate(self.state, 'US', candidate('US', 'AAA'), now=self.now, policy=self.policy)
        position = stock.open_positions(state, 'US')[0]
        state, _ = stock.close_position(
            state,
            'US',
            position['position_id'],
            now=self.now,
            exit_price=110.0,
            reason='test_exit',
        )
        closed = state['markets']['US']['closed_positions'][-1]
        self.assertEqual(500.0, closed['pnl_amount'])
        self.assertEqual(5500.0, closed['exit_notional'])
        self.assertEqual(10.0, closed['return_percent'])

    def test_fixed_notional_verifier_rejects_wrong_notional(self):
        state, _ = stock.admit_candidate(self.state, 'US', candidate('US', 'AAA'), now=self.now, policy=self.policy)
        state['markets']['US']['open_positions'][0]['quantity'] = 1.0
        result = stock.verify_state(state, self.policy)
        self.assertEqual('ERROR', result['status'])
        self.assertTrue(any('fixed_notional_not_5000' in error for error in result['errors']))

    def test_open_ended_position_gets_ambitious_three_r_target(self):
        state, _ = stock.admit_candidate(self.state, 'GPW', candidate('GPW', 'PGE.WA', score=68.87, governed_final=True), now=self.now, policy=self.policy)
        position = stock.open_positions(state, 'GPW')[0]
        initial_risk = position['entry'] - position['stop']
        self.assertAlmostEqual(position['target'], position['entry'] + 3.0 * initial_risk)
        self.assertEqual(3.0, position['strategic_target_rr'])

    def test_risk_review_never_moves_long_stop_or_target_down(self):
        state, _ = stock.admit_candidate(self.state, 'GPW', candidate('GPW', 'PGE.WA'), now=self.now, policy=self.policy)
        before = stock.open_positions(state, 'GPW')[0]
        reviewed, _ = stock.recalculate_risk(
            before,
            mark=99.0,
            atr=2.0,
            now=self.now,
            market_cfg=self.policy['markets']['GPW'],
            thesis_score_value=70.0,
        )
        self.assertGreaterEqual(reviewed['stop'], before['stop'])
        self.assertGreaterEqual(reviewed['target'], before['target'])
        self.assertGreaterEqual(reviewed['reward_risk'], 3.5)

    def test_daily_risk_recalculation_can_change_sl_tp(self):
        state, _ = stock.admit_candidate(self.state, 'US', candidate(), now=self.now, policy=self.policy)
        position = stock.open_positions(state, 'US')[0]
        position['risk_review_date'] = '2026-09-08'
        observations = {
            'AAA': {
                'snapshot': {
                    'high': 104.0, 'low': 99.0, 'last': 103.0,
                    'trigger_window_start': '2026-09-09T12:00:00+00:00',
                    'post_effective_only': True,
                },
                'closes': [100 + i * 0.1 for i in range(50)],
                'atr': 2.0,
            }
        }
        state['markets']['US']['open_positions'] = [position]
        updated, _ = stock.review_market(state, 'US', observations=observations, now=self.now, policy=self.policy)
        after = stock.open_positions(updated, 'US')[0]
        self.assertNotEqual(95.0, after['stop'])
        self.assertNotEqual(110.0, after['target'])
        self.assertEqual('updated', after['risk_reviews'][-1]['status'])

    def test_invalid_daily_risk_recalculation_preserves_previous_sl_tp(self):
        state, _ = stock.admit_candidate(self.state, 'US', candidate(), now=self.now, policy=self.policy)
        position = stock.open_positions(state, 'US')[0]
        position['risk_review_date'] = '2026-09-08'
        old_stop, old_target = position['stop'], position['target']
        observations = {
            'AAA': {
                'snapshot': {
                    'high': 101.0, 'low': 99.0, 'last': 100.0,
                    'trigger_window_start': '2026-09-09T12:00:00+00:00',
                    'post_effective_only': True,
                },
                'closes': [100.0] * 50,
                'atr': 20.0,
            }
        }
        state['markets']['US']['open_positions'] = [position]
        updated, _ = stock.review_market(state, 'US', observations=observations, now=self.now, policy=self.policy)
        after = stock.open_positions(updated, 'US')[0]
        self.assertEqual(old_stop, after['stop'])
        self.assertEqual(old_target, after['target'])
        self.assertEqual('preserved_last_valid_risk', after['risk_reviews'][-1]['status'])

    def test_intraday_observation_excludes_pre_entry_bars(self):
        tz = ZoneInfo('Europe/Warsaw')
        now = datetime(2026, 9, 18, 12, 30, tzinfo=tz)
        opened_at = '2026-09-18T12:00:00+02:00'
        daily_stamps = [int(datetime(2026, 7, 1, 17, 0, tzinfo=tz).timestamp()) + i * 86400 for i in range(60)]
        daily = {
            'timestamp': daily_stamps,
            'indicators': {'quote': [{
                'high': [102.0] * 60,
                'low': [98.0] * 60,
                'close': [100.0] * 60,
            }]},
        }
        before = int(datetime(2026, 9, 18, 11, 55, tzinfo=tz).timestamp())
        after = int(datetime(2026, 9, 18, 12, 5, tzinfo=tz).timestamp())
        intraday = {
            'timestamp': [before, after],
            'indicators': {'quote': [{
                'high': [101.0, 102.0],
                'low': [90.0, 99.0],
                'close': [100.0, 101.0],
            }]},
        }
        with patch.object(stock, '_chart', side_effect=[daily, intraday]):
            observation = stock._daily_observation('AAA.WA', 'GPW', now, opened_at=opened_at)
        self.assertEqual(99.0, observation['snapshot']['low'])
        self.assertEqual(102.0, observation['snapshot']['high'])
        self.assertEqual(101.0, observation['snapshot']['last'])

    def test_intraday_observation_excludes_bars_before_current_risk_geometry(self):
        tz = ZoneInfo('Europe/Warsaw')
        now = datetime(2026, 9, 18, 12, 30, tzinfo=tz)
        opened_at = '2026-09-18T09:00:00+02:00'
        risk_effective_at = '2026-09-18T12:00:00+02:00'
        daily_stamps = [int(datetime(2026, 7, 1, 17, 0, tzinfo=tz).timestamp()) + i * 86400 for i in range(60)]
        daily = {
            'timestamp': daily_stamps,
            'indicators': {'quote': [{
                'high': [102.0] * 60,
                'low': [98.0] * 60,
                'close': [100.0] * 60,
            }]},
        }
        before_risk_change = int(datetime(2026, 9, 18, 11, 55, tzinfo=tz).timestamp())
        after_risk_change = int(datetime(2026, 9, 18, 12, 5, tzinfo=tz).timestamp())
        intraday = {
            'timestamp': [before_risk_change, after_risk_change],
            'indicators': {'quote': [{
                'high': [101.0, 102.0],
                'low': [90.0, 99.0],
                'close': [100.0, 101.0],
            }]},
        }
        with patch.object(stock, '_chart', side_effect=[daily, intraday]):
            observation = stock._daily_observation(
                'AAA.WA',
                'GPW',
                now,
                opened_at=opened_at,
                risk_effective_at=risk_effective_at,
            )
        self.assertEqual(99.0, observation['snapshot']['low'])
        self.assertEqual('2026-09-18T12:05:00+02:00', observation['snapshot']['trigger_window_start'])
        self.assertTrue(observation['snapshot']['post_effective_only'])

    def test_review_fails_closed_when_same_day_trigger_window_is_unverified(self):
        state, _ = stock.admit_candidate(self.state, 'US', candidate(), now=self.now, policy=self.policy)
        position = stock.open_positions(state, 'US')[0]
        state['markets']['US']['open_positions'] = [position]
        observations = {
            'AAA': {
                'snapshot': {'high': 102.0, 'low': 94.0, 'last': 96.0},
                'closes': [100.0] * 50,
                'atr': 2.0,
            }
        }
        updated, audit = stock.review_market(state, 'US', observations=observations, now=self.now, policy=self.policy)
        self.assertEqual(1, len(stock.open_positions(updated, 'US')))
        self.assertEqual('hold_data_error', audit[0]['action'])
        self.assertEqual('unverified_post_effective_trigger_window', audit[0]['reason'])
        self.assertEqual([], updated['markets']['US']['closed_positions'])

    def test_stop_or_take_profit_closes_immediately_regardless_of_age(self):
        state, _ = stock.admit_candidate(self.state, 'US', candidate(), now=self.now, policy=self.policy)
        position = stock.open_positions(state, 'US')[0]
        position['opened_at'] = '2026-01-01T10:00:00+00:00'
        position['risk_last_changed_at'] = '2026-01-01T10:00:00+00:00'
        state['markets']['US']['open_positions'] = [position]
        observations = {
            'AAA': {'snapshot': {'high': 102.0, 'low': 94.0, 'last': 96.0}, 'closes': [100.0] * 50, 'atr': 2.0}
        }
        updated, audit = stock.review_market(state, 'US', observations=observations, now=self.now, policy=self.policy)
        self.assertEqual([], stock.open_positions(updated, 'US'))
        self.assertEqual('stop_loss', audit[0]['reason'])

    def test_model_thesis_invalidation_can_close_at_any_time(self):
        state, _ = stock.admit_candidate(self.state, 'US', candidate(), now=self.now, policy=self.policy)
        position = stock.open_positions(state, 'US')[0]
        state['markets']['US']['open_positions'] = [position]
        closes = [150.0] * 30 + [140.0] * 10 + [130.0] * 9 + [100.0]
        observations = {'AAA': {'snapshot': {
            'high': 101.0, 'low': 99.0, 'last': 100.0,
            'trigger_window_start': '2026-09-09T12:00:00+00:00',
            'post_effective_only': True,
        }, 'closes': closes, 'atr': 2.0}}
        updated, audit = stock.review_market(state, 'US', observations=observations, now=self.now, policy=self.policy)
        self.assertEqual([], stock.open_positions(updated, 'US'))
        self.assertEqual('model_thesis_invalidated', audit[0]['reason'])

    def test_closed_position_frees_slot(self):
        state = self.state
        for symbol in ('A', 'B', 'C'):
            state, _ = stock.admit_candidate(state, 'US', candidate('US', symbol), now=self.now, policy=self.policy)
        first = stock.open_positions(state, 'US')[0]
        state, action = stock.close_position(state, 'US', first['position_id'], now=self.now, exit_price=101.0, reason='model_exit')
        self.assertEqual('close', action['action'])
        self.assertEqual(1, stock.available_slots(state, 'US', self.policy))
        state, action = stock.admit_candidate(state, 'US', candidate('US', 'D'), now=self.now, policy=self.policy)
        self.assertEqual('open', action['action'])
        self.assertEqual(3, len(stock.open_positions(state, 'US')))

    def test_state_verifier_rejects_fixed_deadline(self):
        state, _ = stock.admit_candidate(self.state, 'US', candidate(), now=self.now, policy=self.policy)
        state['markets']['US']['open_positions'][0]['valid_until'] = '2026-09-11'
        result = stock.verify_state(state, self.policy)
        self.assertEqual('ERROR', result['status'])
        self.assertTrue(any('fixed_holding_deadline_present' in error for error in result['errors']))


if __name__ == '__main__':
    unittest.main()

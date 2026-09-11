import unittest
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
            'GPW': {'max_open_positions': 3, 'minimum_entry_score': 72.0, 'minimum_reward_risk': 1.5, 'maximum_risk_percent': 0.07, 'atr_multiple': 1.1, 'risk_floor_percent': 0.012, 'model_exit_score': 30.0, 'candidate_file': 'x'},
            'US': {'max_open_positions': 3, 'minimum_entry_score': 72.0, 'minimum_reward_risk': 1.5, 'maximum_risk_percent': 0.07, 'atr_multiple': 1.05, 'risk_floor_percent': 0.011, 'model_exit_score': 30.0, 'candidate_file': 'y'},
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

    def test_daily_risk_recalculation_can_change_sl_tp(self):
        state, _ = stock.admit_candidate(self.state, 'US', candidate(), now=self.now, policy=self.policy)
        position = stock.open_positions(state, 'US')[0]
        position['risk_review_date'] = '2026-09-08'
        observations = {
            'AAA': {
                'snapshot': {'high': 104.0, 'low': 99.0, 'last': 103.0},
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
                'snapshot': {'high': 101.0, 'low': 99.0, 'last': 100.0},
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

    def test_stop_or_take_profit_closes_immediately_regardless_of_age(self):
        state, _ = stock.admit_candidate(self.state, 'US', candidate(), now=self.now, policy=self.policy)
        position = stock.open_positions(state, 'US')[0]
        position['opened_at'] = '2026-01-01T10:00:00+00:00'
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
        observations = {'AAA': {'snapshot': {'high': 101.0, 'low': 99.0, 'last': 100.0}, 'closes': closes, 'atr': 2.0}}
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

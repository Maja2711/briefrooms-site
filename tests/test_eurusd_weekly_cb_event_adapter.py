"""Synthetic fixtures; these tests never execute or rewrite production trades."""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import eurusd_weekly_cb_event_adapter as adapter
import eurusd_central_bank_event_risk as risk
import eurusd_central_bank_wire as wire_module
import audit_intraday_risk_exits as hard_risk
import investments_weekly_v5 as v5
import update_weekly_live_prices_fast as fast
from tests.test_eurusd_central_bank_wire import Client, collected

NOW = datetime(2026, 10, 28, 17, 30, tzinfo=timezone.utc)


def fixture():
    item = {'instrument_id': 'eurusd', 'direction': 'short', 'trade_status': 'open',
        'entry_price': 1.11869, 'entry_captured_at': risk.iso(NOW-timedelta(days=1)),
        'exit_price': None, 'risk_plan': {'stop_loss_price': 1.12487, 'take_profit_price': 1.10220},
        'pending_entry_decision': None, 'position_legs': [{'leg_id': 'closed-original', 'exit_price': 1.12}]}
    wire = collected(Client(), now=NOW, calendar=[])
    wire['event_window'] = {'event_id': 'synthetic-fed', 'bank': 'FED', 'event_type': 'FOMC_DECISION',
        'scheduled_at': risk.iso(NOW+timedelta(minutes=30)), 'conference_status': 'UNKNOWN',
        'first_seen_at': risk.iso(NOW-timedelta(hours=2)),
        'source_url': 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'}
    wire['integrity']['snapshot_sha256'] = wire_module.snapshot_hash(wire)
    quotes = {'prices': {'eurusd': {'bid': 1.11359, 'ask': 1.11369, 'price': 1.11364,
        'timestamp': risk.iso(NOW), 'source': 'synthetic executable feed',
        'verified': True, 'cost_pips': 0.0, 'baseline_spread_pips': 1.0}}}
    return item, wire, quotes, copy.deepcopy(risk.DEFAULT_POLICY)


class WeeklyCBAdapterTests(unittest.TestCase):
    def assess(self, parts=None):
        item, wire, quotes, policy = parts or fixture()
        return adapter.evaluate_weekly_cb_event(item, week_id='synthetic-W44', now=NOW,
            wire=wire, live_prices=quotes, policy=policy)

    def test_synthetic_short_full_stop_giveback_and_no_execution(self):
        parts = fixture(); before = copy.deepcopy(parts)
        result = self.assess(parts)
        self.assertAlmostEqual(result['diagnostics']['position_profit_pips'], 50)
        self.assertAlmostEqual(result['diagnostics']['profit_at_risk_pips'], 111.8)
        self.assertEqual(result['mode'], 'SHADOW')
        self.assertEqual(set(result['alternatives']), {'HOLD', 'CLOSE_NOW', 'PROTECT_PROFIT'})
        self.assertFalse(result['event_exit_request_created'])

    def test_pending_forecast_with_leftover_entry_is_not_an_open_position(self):
        parts=fixture();parts[0]['trade_status']='pending'
        before=copy.deepcopy(parts)
        result=self.assess(parts)
        self.assertIsNone(result['position_id'])
        self.assertIsNone(result['current_net_R'])
        self.assertEqual(parts,before)
        self.assertFalse(result['execution_authority'])
        self.assertIsNone(result['executed_at'])
        self.assertEqual(parts, before)

    def test_chart_mid_never_becomes_verified_bid_ask(self):
        parts = fixture()
        parts[2]['prices']['eurusd'] = {'price': 1.11369, 'timestamp': risk.iso(NOW),
            'source': 'synthetic chart', 'fresh': True}
        result = self.assess(parts)
        self.assertFalse(result['quote_verified'])
        self.assertTrue(result['diagnostics']['paper_proxy_uncertain'])
        self.assertIsNone(result['close_now_net_R'])
        self.assertFalse(result['price_watchdog']['fresh_for_execution'])
        self.assertTrue(result['price_watchdog']['fresh_for_display_mark'])

    def test_stale_mark_remains_visible_with_recovery_alarm(self):
        parts = fixture(); parts[2]['prices']['eurusd']['timestamp'] = risk.iso(NOW-timedelta(minutes=20))
        result = self.assess(parts)
        self.assertEqual(result['price_watchdog']['last_known_price'], 1.11364)
        self.assertTrue(result['price_watchdog']['recovery_required'])
        self.assertIn('RECOVERY_REQUIRED', result['price_watchdog']['alerts'])
        self.assertFalse(result['quote_verified'])
        self.assertIsNone(result['executed_at'])

    def test_instrument_scope_is_exact_and_side_effect_free(self):
        for iid in ('btcusd', 'sp500_futures', 'stock_trading', 'gbpusd', 'EURUSD'):
            parts = fixture(); parts[0]['instrument_id'] = iid; before = copy.deepcopy(parts)
            with patch.object(wire_module, 'load_wire', side_effect=AssertionError('no read')):
                self.assertIsNone(self.assess(parts))
            self.assertEqual(parts, before)

    def test_closed_position_is_not_reopened_or_reassessed_as_open(self):
        parts = fixture(); parts[0].update(trade_status='closed', exit_price=1.11369)
        before = copy.deepcopy(parts[0])
        result = self.assess(parts)
        self.assertIsNone(result['position_id'])
        self.assertIsNone(result['current_net_R'])
        self.assertEqual(parts[0], before)

    def test_append_only_idempotency_monotonic_latest_and_conflict(self):
        result = self.assess()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter.persist_weekly_assessment(result, root)
            journal = root / adapter.JOURNAL_NAME / f"{result['immutable_id']}.json"
            original = journal.read_bytes()
            adapter.persist_weekly_assessment(result, root)
            self.assertEqual(journal.read_bytes(), original)
            conflict = copy.deepcopy(result); conflict['action'] = 'HOLD'
            with self.assertRaisesRegex(ValueError, 'immutable'):
                adapter.persist_weekly_assessment(conflict, root)
            old = copy.deepcopy(result); old.update(immutable_id='a'*64,
                evaluated_at=risk.iso(NOW-timedelta(minutes=1)))
            adapter.persist_weekly_assessment(old, root)
            self.assertEqual(json.loads((root / adapter.SNAPSHOT_NAME).read_text())['immutable_id'], result['immutable_id'])
            self.assertEqual(journal.read_bytes(), original)

    def test_enabled_guard_rejects_recovered_price_before_snapshot(self):
        item, wire, quotes, policy = fixture(); policy['entry_guard_enabled'] = True
        assessment = self.assess((item, wire, quotes, policy))
        point = {'timestamp': risk.iso(NOW-timedelta(seconds=20)), 'price': 1.11369}
        passed, reason = adapter.entry_fill_allowed(point, assessment, now=NOW, wire=wire, policy=policy)
        self.assertFalse(passed)
        self.assertEqual(reason, 'CB_NO_POINT_IN_TIME_WIRE_FOR_FILL')

    def test_enabled_guard_rejects_same_time_event_touch(self):
        item, wire, quotes, policy = fixture(); policy['entry_guard_enabled'] = True
        assessment = self.assess((item, wire, quotes, policy))
        passed, reason = adapter.entry_fill_allowed({'timestamp': risk.iso(NOW)}, assessment,
            now=NOW, wire=wire, policy=policy)
        self.assertFalse(passed)
        self.assertEqual(reason, 'CB_ENTRY_BLOCKED_AT_PRICE_TIME')

    def test_enabled_guard_accepts_only_as_of_clear_snapshot(self):
        item, wire, quotes, policy = fixture(); policy['entry_guard_enabled'] = True
        wire['event_window'] = {}
        wire['integrity']['snapshot_sha256'] = wire_module.snapshot_hash(wire)
        assessment = self.assess((item, wire, quotes, policy))
        passed, reason = adapter.entry_fill_allowed({'timestamp': risk.iso(NOW)}, assessment,
            now=NOW, wire=wire, policy=policy)
        self.assertTrue(passed, reason)
        self.assertEqual(reason, 'CB_ENTRY_AS_OF_VERIFIED')

    def test_shadow_guard_does_not_change_existing_entry_verification(self):
        assessment = self.assess()
        self.assertEqual(adapter.entry_fill_allowed({}, assessment, now=NOW), (True, 'ENTRY_GUARD_SHADOW'))

    def test_provider_outage_retains_original_eurusd_mark_only(self):
        previous = {'eurusd': {'price': 1.11369, 'timestamp': risk.iso(NOW-timedelta(minutes=20)),
            'source': 'old unavailable provider'}}
        original = copy.deepcopy(previous)
        with patch.object(fast, 'newest_valid', return_value=(None, ['synthetic outage'])), \
             patch.object(fast, 'now_local', return_value=NOW):
            result = fast.refresh_one('eurusd', previous)
        self.assertEqual(result['price'], 1.11369)
        self.assertEqual(risk.parse_time(result['timestamp']), NOW-timedelta(minutes=20))
        self.assertFalse(result['fresh'])
        self.assertFalse(result['fresh_for_execution'])
        self.assertTrue(result['recovery_required'])
        self.assertEqual(previous, original)

    def test_other_quotes_have_no_new_eurusd_flags(self):
        for iid, price in [('btcusd', 80000), ('sp500_futures', 7000)]:
            quote = {'price': price, 'timestamp': risk.iso(NOW), 'source': 'synthetic provider'}
            with patch.object(fast, 'newest_valid', return_value=(quote, [])), \
                 patch.object(fast, 'now_local', return_value=NOW):
                result = fast.refresh_one(iid, {})
            self.assertEqual(result, {'price': float(price), 'timestamp': NOW.astimezone(fast.WARSAW).isoformat(timespec='seconds'),
                'current_price_updated_at': NOW.astimezone(fast.WARSAW).isoformat(timespec='seconds'), 'source': 'synthetic provider',
                'fresh': True, 'last_attempt_at': NOW.isoformat(timespec='seconds'), 'note': ''})

    def test_eurusd_scoped_hard_risk_does_not_read_or_mutate_other_positions(self):
        item, _, _, _ = fixture()
        item.update(symbol='EURUSD=X'); item['risk_plan']['generated_at'] = risk.iso(NOW-timedelta(hours=1))
        others = []
        for iid in ('btcusd', 'sp500_futures'):
            row = copy.deepcopy(item); row['instrument_id'] = iid
            others.append(row)
        week = {'week_id': 'synthetic-W44', 'instruments': [item, *others]}
        before = copy.deepcopy(week)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'week.json'; path.write_text(json.dumps(week))
            with patch.object(hard_risk, 'canonical_live_risk_hit', return_value=None) as live, \
                 patch.object(hard_risk, '_yahoo_evidence', return_value=(None, [], 'synthetic')):
                hard_risk.audit(path=path, now=NOW, persist_report='never', instrument_id='eurusd')
            self.assertEqual(live.call_count, 1)
            self.assertEqual(live.call_args.args[0], 'eurusd')
            self.assertEqual(json.loads(path.read_text()), before)
            with self.assertRaises(ValueError):
                hard_risk.audit(path=path, now=NOW, instrument_id='btcusd')

    def test_actual_entry_path_runs_hard_risk_then_blocks_before_pending_recovery(self):
        item, _, _, _ = fixture()
        item.update(entry_price=None, trade_status='pending', direction='short')
        opened = copy.deepcopy(item); opened['entry_price'] = 1.11869
        week = {'week_id': 'synthetic-W44', 'instruments': [opened]}
        order = []
        blocked = {'immutable_id': 'b'*64, 'entry_guard_enforced': True, 'entry_guard_enabled': True}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'week.json'; path.write_text(json.dumps(week))
            def read(path_value, default):
                if path_value == v5.POLICY:
                    return {'enabled': True}
                if path_value == path:
                    return copy.deepcopy(week)
                return {}
            def hard_check(**kwargs):
                order.append('hard_risk')
                self.assertEqual(kwargs['instrument_id'], 'eurusd')
                week['instruments'] = [copy.deepcopy(item)]
                return {'changed': True}
            def cb_check(*args, **kwargs):
                order.append('cb_assessment'); return blocked
            with patch.object(v5, 'read', side_effect=read), patch.object(v5, 'write'), \
                 patch.object(v5.legacy, 'now_local', return_value=NOW), \
                 patch.object(v5.decision_ledger, 'verify_ledger'), \
                 patch.object(v5.v4, 'ensure_week', return_value=path), \
                 patch.object(v5.v4, 'active_window', return_value=(True, 'active')), \
                 patch.object(v5.v4, 'policy_instruments', return_value=[{'instrument_id': 'eurusd'}]), \
                 patch.object(v5.v4, 'instrument_cfg', return_value={'symbol': 'EURUSD=X'}), \
                 patch.object(v5, 'archive_closed_learning_samples', return_value=0), \
                 patch.object(hard_risk, 'audit', side_effect=hard_check), \
                 patch.object(adapter, 'evaluate_weekly_cb_event', side_effect=cb_check), \
                 patch.object(adapter, 'persist_weekly_assessment'), \
                 patch.object(v5, 'recover_frozen_pending_touch', side_effect=AssertionError('must block first')):
                report = v5.ensure_all()
        self.assertEqual(order, ['hard_risk', 'cb_assessment'])
        self.assertEqual(report['actions'][0]['action'], 'block_new_entry')


if __name__ == '__main__':
    unittest.main()

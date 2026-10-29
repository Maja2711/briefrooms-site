"""Synthetic scenario fixtures only; no historic trades or market claims."""
import copy
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import eurusd_central_bank_event_risk as risk
import eurusd_central_bank_wire as wire_module
from tests.test_eurusd_central_bank_wire import collected, Client

NOW = datetime(2026, 10, 28, 17, 30, tzinfo=timezone.utc)


def fixture():
    wire = collected(Client(),now=NOW,calendar=[])
    pos = dict(instrument_id='eurusd',position_id='synthetic-short',direction='SHORT',
        entry_price=1.11869,stop_loss=1.12487,take_profit=1.10220,
        opened_at=risk.iso(NOW-timedelta(days=1)),mfe_R=1.0)
    quote = dict(bid=1.11359,ask=1.11369,observed_at=risk.iso(NOW),verified=True,
        source='synthetic executable feed',cost_pips=0.0,baseline_spread_pips=1.0)
    events = [dict(event_id='synthetic-fomc',bank='FED',event_type='FOMC_DECISION',time_precision='exact',
        scheduled_at=risk.iso(NOW+timedelta(minutes=30)),
        source_url='https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm',
        first_seen_at=risk.iso(NOW-timedelta(hours=2)),conference_status='UNKNOWN')]
    policy=copy.deepcopy(risk.DEFAULT_POLICY)
    return [pos,quote,wire,events,policy]


class CentralBankRiskTests(unittest.TestCase):
    def assess(self, parts=None, **kwargs):
        p,q,w,e,policy=parts or fixture()
        return risk.assess_pre_event(p,q,w,e,policy,now=NOW,**kwargs)

    def test_short_full_giveback_50_plus_61_8_pips_and_three_alternatives(self):
        result=self.assess()
        self.assertAlmostEqual(result.diagnostics['position_profit_pips'],50)
        self.assertAlmostEqual(result.diagnostics['profit_at_risk_pips'],111.8)
        self.assertAlmostEqual(result.current_net_R,50/61.8)
        self.assertEqual(set(result.alternatives),{'HOLD','CLOSE_NOW','PROTECT_PROFIT'})
        self.assertEqual(result.mode,'SHADOW')
        self.assertIn(result.action,('PROTECT_PROFIT','CLOSE_BEFORE_EVENT'))
        self.assertFalse(result.audit['execution_authorized'])
        self.assertIsNone(result.hold_es95_R)
        self.assertIn('RISK_MODEL_UNCALIBRATED',result.reasons)

    def test_long_exit_uses_bid_and_charges_explicit_cost(self):
        p,q,w,e,policy=fixture()
        p.update(direction='LONG',entry_price=1.10,stop_loss=1.09,take_profit=1.13)
        q.update(bid=1.105,ask=1.1052,cost_pips=2)
        result=self.assess((p,q,w,e,policy))
        self.assertAlmostEqual(result.current_net_R,.48)
        self.assertAlmostEqual(result.diagnostics['current_executable_exit_price'],1.105)

    def test_stale_quote_never_claims_current_exit_or_close(self):
        parts=fixture();parts[1]['observed_at']=risk.iso(NOW-timedelta(minutes=20))
        result=self.assess(parts)
        self.assertFalse(result.quote_verified)
        self.assertIsNone(result.close_now_net_R)
        self.assertIsNone(result.diagnostics['current_executable_exit_price'])
        self.assertEqual(result.risk_protection_status,'PROTECTION_PENDING')

    def test_fivefold_spread_spike_never_executes_old_mid(self):
        parts=fixture();parts[1].update(bid=1.1092,ask=1.1097,mid=1.11369)
        result=self.assess(parts)
        self.assertFalse(result.quote_verified)
        self.assertEqual(result.action,'NO_ACTION_UNVERIFIED')
        self.assertIsNone(result.close_now_net_R)

    def test_mid_only_and_missing_cost_are_uncertain(self):
        parts=fixture();parts[1].pop('bid');parts[1].pop('ask');parts[1].pop('cost_pips')
        parts[1]['mid']=1.11369
        result=self.assess(parts)
        self.assertTrue(result.diagnostics['paper_proxy_uncertain'])
        self.assertIsNone(result.current_net_R)
        self.assertFalse(result.alternatives['CLOSE_NOW']['executable'])

    def test_wire_outage_no_forced_close_and_candidate_entry_block(self):
        parts=fixture();parts[2]={}
        result=self.assess(parts)
        self.assertTrue(result.block_new_entry)
        self.assertNotEqual(result.action,'CLOSE_BEFORE_EVENT')
        self.assertFalse(result.entry_guard_enforced)
        parts[4]['entry_guard_enabled']=True
        self.assertTrue(self.assess(parts).entry_guard_enforced)

    def test_unknown_conference_does_not_expire_after_30_minutes(self):
        parts=fixture();parts[3][0]['scheduled_at']=risk.iso(NOW-timedelta(hours=6))
        result=self.assess(parts)
        self.assertTrue(result.block_new_entry)
        self.assertIn('RECOVERY_REQUIRED_CONFERENCE_UNCONFIRMED',result.reasons)

    def test_unresolved_guard_survives_next_day_and_fresh_calendar_poll(self):
        parts=fixture();parts[3][0]['scheduled_at']=risk.iso(NOW-timedelta(days=2))
        result=self.assess(parts)
        self.assertTrue(result.block_new_entry)
        self.assertIn('RECOVERY_REQUIRED_CONFERENCE_UNCONFIRMED',result.reasons)

    def test_verified_end_needs_cooldown(self):
        parts=fixture();parts[3][0].update(scheduled_at=risk.iso(NOW-timedelta(hours=2)),
            conference_status='ENDED',conference_ended_at=risk.iso(NOW-timedelta(minutes=20)))
        parts[3][0]['conference_end_evidence']={'statement_verified':True,'event_id':'synthetic-fomc',
            'source_url':'https://www.federalreserve.gov/synthetic-test-only',
            'published_at':risk.iso(NOW-timedelta(minutes=19)),
            'first_seen_at':risk.iso(NOW-timedelta(minutes=18)),
            'ingested_at':risk.iso(NOW-timedelta(minutes=17))}
        self.assertTrue(self.assess(parts).block_new_entry)
        parts[3][0]['conference_ended_at']=risk.iso(NOW-timedelta(minutes=40))
        self.assertFalse(self.assess(parts).block_new_entry)

    def test_end_flag_without_source_does_not_release_guard(self):
        parts=fixture();parts[3][0].update(scheduled_at=risk.iso(NOW-timedelta(hours=2)),
            conference_status='ENDED',conference_ended_at=risk.iso(NOW-timedelta(minutes=40)))
        self.assertTrue(self.assess(parts).block_new_entry)

    def test_fresh_calendar_check_preserves_old_first_seen(self):
        parts=fixture();parts[3][0].update(first_seen_at=risk.iso(NOW-timedelta(days=30)),calendar_checked_at=risk.iso(NOW))
        self.assertTrue(self.assess(parts).event_verified)

    def test_bad_geometry_and_out_of_range_quote_never_verified(self):
        parts=fixture();parts[1].update(bid=10,ask=10)
        self.assertFalse(self.assess(parts).quote_verified)
        parts=fixture();parts[0]['initial_stop_loss']=1.10
        self.assertIn('POSITION_GEOMETRY_UNVERIFIED',self.assess(parts).reasons)

    def test_future_calendar_evidence_not_verified(self):
        parts=fixture();parts[3][0]['first_seen_at']=risk.iso(NOW+timedelta(seconds=1))
        result=self.assess(parts)
        self.assertFalse(result.event_verified)
        self.assertTrue(result.block_new_entry)
        self.assertNotEqual(result.action,'CLOSE_BEFORE_EVENT')

    def test_date_only_and_unknown_calendar_do_not_claim_exact_event(self):
        for precision in ('date_only','unknown'):
            parts=fixture();parts[3][0]['time_precision']=precision
            self.assertFalse(self.assess(parts).event_verified)
            self.assertTrue(self.assess(parts).block_new_entry)

    def test_no_silent_hold_on_total_wire_outage_without_calendar(self):
        parts=fixture();parts[2]={};parts[3]=[]
        self.assertEqual(self.assess(parts).action,'NO_ACTION_UNVERIFIED')

    def test_official_release_verifies_date_only_calendar_prospectively(self):
        from tests.test_eurusd_central_bank_wire import collected
        parts=fixture(); now=NOW+timedelta(hours=2)
        snapshot=collected(now=now)
        parts[1]['observed_at']=risk.iso(now)
        result=risk.assess_pre_event(parts[0],parts[1],snapshot,policy=parts[4],now=now)
        self.assertTrue(result.event_verified)
        self.assertEqual(result.diagnostics['phase'],'RELEASED')

    def test_empirical_model_requires_sourced_current_regime_and_stays_shadow(self):
        from tests.test_eurusd_cb_event_distribution import sample
        p,q,w,e,policy=fixture()
        samples=[sample(index) for index in range(40)]
        result=risk.assess_pre_event(p,q,w,e,policy,now=NOW,historical_samples=samples,
            regime={'label':'SYNTHETIC_LOW_VOL','observed_at':risk.iso(NOW-timedelta(minutes=1)),
                    'source_ref':'https://quotes.example.test/current-regime'})
        self.assertEqual(result.diagnostics['risk_model_status'],'EMPIRICAL_UNVALIDATED')
        self.assertEqual(result.diagnostics['effective_sample_size'],40)
        self.assertIsNotNone(result.hold_expected_net_R)
        self.assertIsNotNone(result.hold_es95_R)
        self.assertIsNone(result.diagnostics['probability_of_profit_giveback'])
        self.assertFalse(result.audit['execution_authorized'])

    def test_entry_guard_cannot_disable_its_monitor(self):
        parts=fixture();parts[4].update(monitoring_enabled=False,entry_guard_enabled=True)
        with self.assertRaises(ValueError):self.assess(parts)

    def test_twenty_minute_cron_delay_is_consumed_as_stale_not_fresh(self):
        parts=fixture();parts[2]=collected(Client(),now=NOW-timedelta(minutes=20),calendar=[])
        result=self.assess(parts)
        self.assertEqual(result.diagnostics['wire_health'],'STALE')
        self.assertEqual(result.diagnostics['producer_wire_health'],'FRESH')
        self.assertIn('RSS_STALE',result.reasons)
        self.assertTrue(result.block_new_entry)

    def test_production_flag_cannot_activate_automatic_actions(self):
        for key,value in [('mode','PRODUCTION'),('automatic_close_enabled',True),('stop_upgrade_enabled',True)]:
            parts=fixture();parts[4][key]=value
            with self.assertRaises(ValueError):self.assess(parts)

    def test_idempotent_immutable_and_distinct_engine_profiles(self):
        parts=fixture();before=copy.deepcopy(parts)
        one=self.assess(parts);two=self.assess(parts)
        self.assertEqual(one.to_dict(),two.to_dict())
        self.assertEqual(parts,before)
        self.assertNotEqual(one.audit['immutable_id'],self.assess(parts,scope='EURUSD_DAILY').audit['immutable_id'])

    def test_other_instruments_rejected_without_mutation(self):
        for instrument in ('btcusd','sp500_futures','stock_trading','gbpusd'):
            parts=fixture();parts[0]['instrument_id']=instrument;before=copy.deepcopy(parts)
            with self.assertRaises(ValueError):self.assess(parts)
            self.assertEqual(parts,before)

    def test_future_or_naive_now_and_quote_rejected(self):
        parts=fixture();parts[1]['observed_at']=risk.iso(NOW+timedelta(seconds=1))
        self.assertFalse(self.assess(parts).quote_verified)
        with self.assertRaises(ValueError):
            risk.assess_pre_event(*fixture()[:4],now=NOW.replace(tzinfo=None))


if __name__=='__main__':unittest.main()

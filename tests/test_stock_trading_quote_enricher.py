import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import stock_trading_quote_enricher as quotes

UTC = ZoneInfo('UTC')


class StockTradingQuoteEnricherTests(unittest.TestCase):
    def test_provider_market_state_has_priority(self):
        now = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
        state, source = quotes._resolve_state({'marketState': 'POST'}, now, 'US')
        self.assertEqual('POST', state)
        self.assertEqual('provider.marketState', source)

    def test_provider_trading_period_precedes_clock_fallback(self):
        now = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
        epoch = int(now.timestamp())
        meta = {'currentTradingPeriod': {'regular': {'start': epoch - 60, 'end': epoch + 60}}}
        state, source = quotes._resolve_state(meta, now, 'US')
        self.assertEqual('REGULAR', state)
        self.assertEqual('provider.currentTradingPeriod', source)

    def test_delay_zero_is_realtime_but_unknown_is_not(self):
        self.assertEqual(('realtime', 0, True), quotes._delay_metadata({'exchangeDataDelayedBy': 0}))
        self.assertEqual(('delayed', 15, False), quotes._delay_metadata({'exchangeDataDelayedBy': 15}))
        self.assertEqual(('unverified', None, False), quotes._delay_metadata({}))

    def test_enrichment_does_not_replace_risk_last_mark(self):
        now = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
        state = {
            'markets': {
                'GPW': {'open_positions': []},
                'US': {'open_positions': [{
                    'position_id': 'us:test:AAA',
                    'market': 'US',
                    'status': 'OPEN',
                    'symbol': 'AAA',
                    'entry': 100.0,
                    'stop': 95.0,
                    'target': 110.0,
                    'last_mark': 101.0,
                }]},
            }
        }
        mark = {
            'price': 102.0,
            'market_state': 'PRE',
            'state_source': 'provider.marketState',
            'price_kind': 'pre',
            'observed_at': '2026-09-10T09:59:00-04:00',
            'received_at': '2026-09-10T14:00:00+00:00',
            'provider': 'Yahoo Finance chart',
            'delay_status': 'realtime',
            'delay_minutes': 0,
            'is_realtime': True,
            'source_verified': True,
            'capture_age_seconds': 60,
        }
        with patch.object(quotes, 'quote_for_symbol', return_value=mark):
            enriched, audits = quotes.enrich(state, markets=['US'], now_utc=now)
        position = enriched['markets']['US']['open_positions'][0]
        self.assertEqual(101.0, position['last_mark'])
        self.assertEqual(102.0, position['current_mark']['price'])
        self.assertEqual('PRE', enriched['markets']['US']['quote_session']['market_state'])
        self.assertEqual('quote_enriched', audits[0]['action'])

    def test_stooq_gpw_symbol_is_bare_ticker(self):
        self.assertEqual('LPP', quotes._stooq_symbol('LPP.WA', 'GPW'))
        self.assertEqual('PGE', quotes._stooq_symbol('PGE', 'GPW'))

    def test_stooq_gpw_uses_polish_endpoint_and_measured_timestamp(self):
        now = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)
        csv_body = (
            "Symbol,Date,Time,Open,High,Low,Close,Volume\n"
            "ASB,2026-09-18,11:59:30,172,174,171,173,1000\n"
        )
        with patch.object(quotes, '_request_text', return_value=csv_body) as request_text:
            result = quotes._stooq_quote('ASB.WA', 'GPW', now_utc=now)
        requested_url = request_text.call_args.args[0]
        self.assertTrue(requested_url.startswith('https://stooq.pl/q/l/?'))
        self.assertIn('s=ASB', requested_url)
        self.assertEqual('Stooq.pl current quote', result['provider'])
        self.assertEqual(173.0, result['price'])
        self.assertEqual(30, result['capture_age_seconds'])
        self.assertTrue(result['is_realtime'])
        self.assertEqual(requested_url, result['source_url'])

    def test_enrichment_prefers_fresher_gpw_display_quote(self):
        now = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)
        state = {
            'markets': {
                'GPW': {'open_positions': [{
                    'position_id': 'gpw:test:ASB',
                    'market': 'GPW',
                    'status': 'OPEN',
                    'symbol': 'ASB.WA',
                    'ticker': 'ASB',
                    'entry': 172.0,
                    'last_mark': 172.5,
                }]},
                'US': {'open_positions': []},
            }
        }
        stale_yahoo = {
            'price': 173.0,
            'market_state': 'REGULAR',
            'state_source': 'provider.currentTradingPeriod',
            'price_kind': 'regular',
            'observed_at': '2026-09-18T11:45:00+02:00',
            'received_at': '2026-09-18T10:00:00+00:00',
            'provider': 'Yahoo Finance chart',
            'delay_status': 'delayed',
            'delay_minutes': 15,
            'is_realtime': False,
            'source_verified': True,
            'capture_age_seconds': 900,
        }
        fresh_stooq = {
            'price': 174.0,
            'market_state': 'REGULAR',
            'state_source': 'clock_fallback_for_stooq',
            'price_kind': 'last',
            'observed_at': '2026-09-18T11:59:30+02:00',
            'received_at': '2026-09-18T10:00:00+00:00',
            'provider': 'Stooq.pl current quote',
            'delay_status': 'measured_from_observation',
            'delay_minutes': 0.5,
            'is_realtime': True,
            'source_verified': True,
            'capture_age_seconds': 30,
        }
        with patch.object(quotes, 'quote_for_symbol', return_value=stale_yahoo), patch.object(
            quotes, '_stooq_quote', return_value=fresh_stooq
        ):
            enriched, _ = quotes.enrich(state, markets=['GPW'], now_utc=now)
        position = enriched['markets']['GPW']['open_positions'][0]
        self.assertEqual(172.5, position['last_mark'])
        self.assertEqual(174.0, position['current_mark']['price'])
        self.assertEqual('Stooq.pl current quote', position['current_mark']['provider'])
        self.assertEqual('freshest_verified_observation', position['current_mark']['display_quote_policy']['selection'])

    def test_execution_quote_uses_fresh_independent_fallback(self):
        now = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)
        stale_yahoo = {
            'price': 100.0,
            'market_state': 'REGULAR',
            'observed_at': '2026-09-18T11:45:00+02:00',
            'received_at': '2026-09-18T10:00:00+00:00',
            'provider': 'Yahoo Finance chart',
            'delay_minutes': 15,
            'capture_age_seconds': 900,
        }
        fresh_stooq = {
            'price': 101.0,
            'market_state': 'REGULAR',
            'observed_at': '2026-09-18T11:59:30+02:00',
            'received_at': '2026-09-18T10:00:00+00:00',
            'provider': 'Stooq.pl current quote',
            'delay_minutes': 0.5,
            'capture_age_seconds': 30,
        }
        with patch.object(quotes, 'quote_for_symbol', return_value=stale_yahoo), patch.object(quotes, '_stooq_quote', return_value=fresh_stooq):
            result = quotes.execution_quote_for_symbol('LPP.WA', 'GPW', now_utc=now, maximum_age_seconds=300)
        self.assertEqual('Stooq.pl current quote', result['provider'])
        self.assertEqual(101.0, result['price'])

    def test_execution_quote_refuses_stale_fill(self):
        now = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)
        stale = {
            'price': 100.0,
            'market_state': 'REGULAR',
            'observed_at': '2026-09-18T11:45:00+02:00',
            'received_at': '2026-09-18T10:00:00+00:00',
            'provider': 'Yahoo Finance chart',
            'delay_minutes': 15,
            'capture_age_seconds': 900,
        }
        with patch.object(quotes, 'quote_for_symbol', return_value=stale), patch.object(quotes, '_stooq_quote', return_value=stale):
            with self.assertRaises(quotes.ExecutionQuoteUnavailable):
                quotes.execution_quote_for_symbol('LPP.WA', 'GPW', now_utc=now, maximum_age_seconds=300)


    def test_execution_quote_accepts_gpw_yahoo_delayed_paper_within_20_minutes(self):
        now = datetime(2026, 9, 21, 14, 19, 14, tzinfo=UTC)
        delayed_yahoo = {
            'price': 173.0,
            'market_state': 'REGULAR',
            'observed_at': '2026-09-21T16:03:57+02:00',
            'received_at': '2026-09-21T14:19:14+00:00',
            'provider': 'Yahoo Finance chart',
            'delay_status': 'delayed',
            'delay_minutes': 15,
            'is_realtime': False,
            'capture_age_seconds': 917,
        }
        with patch.object(quotes, 'quote_for_symbol', return_value=delayed_yahoo), patch.object(
            quotes, '_stooq_quote', side_effect=RuntimeError('Stooq unavailable')
        ):
            result = quotes.execution_quote_for_symbol(
                'ASB.WA',
                'GPW',
                now_utc=now,
                maximum_age_seconds=1200,
            )
        self.assertEqual('Yahoo Finance chart', result['provider'])
        self.assertEqual(173.0, result['price'])
        self.assertEqual('DELAYED_PAPER', result['execution_mode'])
        self.assertEqual(1200, result['execution_quote_policy']['maximum_age_seconds'])

    def test_execution_quote_still_rejects_gpw_quote_older_than_20_minutes(self):
        now = datetime(2026, 9, 21, 14, 30, 0, tzinfo=UTC)
        stale_yahoo = {
            'price': 173.0,
            'market_state': 'REGULAR',
            'observed_at': '2026-09-21T16:00:00+02:00',
            'received_at': '2026-09-21T14:30:00+00:00',
            'provider': 'Yahoo Finance chart',
            'delay_status': 'delayed',
            'delay_minutes': 30,
            'is_realtime': False,
            'capture_age_seconds': 1800,
        }
        with patch.object(quotes, 'quote_for_symbol', return_value=stale_yahoo), patch.object(
            quotes, '_stooq_quote', side_effect=RuntimeError('Stooq unavailable')
        ):
            with self.assertRaises(quotes.ExecutionQuoteUnavailable):
                quotes.execution_quote_for_symbol(
                    'ASB.WA',
                    'GPW',
                    now_utc=now,
                    maximum_age_seconds=1200,
                )



if __name__ == '__main__':
    unittest.main()

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
            'provider': 'Stooq current quote',
            'delay_minutes': 0.5,
            'capture_age_seconds': 30,
        }
        with patch.object(quotes, 'quote_for_symbol', return_value=stale_yahoo), patch.object(quotes, '_stooq_quote', return_value=fresh_stooq):
            result = quotes.execution_quote_for_symbol('LPP.WA', 'GPW', now_utc=now, maximum_age_seconds=300)
        self.assertEqual('Stooq current quote', result['provider'])
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



if __name__ == '__main__':
    unittest.main()

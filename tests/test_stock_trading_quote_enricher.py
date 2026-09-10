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


if __name__ == '__main__':
    unittest.main()

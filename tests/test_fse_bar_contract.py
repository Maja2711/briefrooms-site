"""Offline canonical market-bar/quote clock regressions shared by FSE paths."""
import unittest
from datetime import datetime, timedelta, timezone

from scripts import fractal_structure_engine as fse


def dt(value):
    return fse.parse_time(value)


def payload(stamps, closes=None, **fields):
    closes=closes or [100.0]*len(stamps)
    quote={"close":closes,"open":closes[:],"high":[x+1 for x in closes],"low":[x-1 for x in closes],"volume":[10]*len(stamps)}
    quote.update(fields)
    return {"chart":{"result":[{"timestamp":[int(x.timestamp()) for x in stamps],"indicators":{"quote":[quote]},"meta":{"exchangeTimezoneName":"UTC"}}]}}


class CanonicalFSEBars(unittest.TestCase):
    def test_live_quote_updates_existing_hour_not_fake_candle(self):
        rows=fse.parse_yahoo_bars(payload([dt("2026-10-21T12:00:00Z"),dt("2026-10-21T12:44:28Z")],[100,101]),"BTC-USD","60m",dt("2026-10-21T12:45:00Z"))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].timestamp,dt("2026-10-21T12:00:00Z"))
        self.assertEqual(rows[0].source_observed_at,dt("2026-10-21T12:44:28Z"))
        self.assertEqual(rows[0].close,101)
        self.assertEqual(rows[0].open,100)
        self.assertEqual(rows[0].high,102)
        self.assertEqual(rows[0].closed_at,dt("2026-10-21T13:00:00Z"))

    def test_missing_quote_clock_cannot_gain_fetch_time_freshness(self):
        row=fse.parse_yahoo_bars(payload([dt("2026-10-21T12:00:00Z")]),"BTC-USD","60m",dt("2026-10-21T12:55:00Z"))[0]
        self.assertEqual(row.source_observed_at,row.timestamp)

    def test_unanchored_quote_does_not_invent_next_interval(self):
        rows=fse.parse_yahoo_bars(payload([dt("2026-10-21T11:00:00Z"),dt("2026-10-21T12:44:28Z")]),"BTC-USD","60m",dt("2026-10-21T12:45:00Z"))
        self.assertEqual([r.timestamp.hour for r in rows],[11])

    def test_metadata_quote_refreshes_only_existing_bucket(self):
        data=payload([dt("2026-10-21T12:00:00Z")])
        meta=data["chart"]["result"][0]["meta"]
        meta.update(regularMarketTime=int(dt("2026-10-21T12:44:00Z").timestamp()),regularMarketPrice=102)
        row=fse.parse_yahoo_bars(data,"BTC-USD","60m",dt("2026-10-21T12:45:00Z"))[0]
        self.assertEqual(row.timestamp,dt("2026-10-21T12:00:00Z"))
        self.assertEqual(row.close,102)
        self.assertEqual(row.high,102)
        self.assertEqual(row.source_observed_at,dt("2026-10-21T12:44:00Z"))
        meta["regularMarketTime"]=int(dt("2026-10-21T13:10:00Z").timestamp())
        rows=fse.parse_yahoo_bars(data,"BTC-USD","60m",dt("2026-10-21T13:11:00Z"))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].close,100)

    def test_exchange_calendar_survives_quote_and_aggregate(self):
        # London Monday midnight is Sunday23UTC during BST. Weekly grouping
        # must retain the exchange calendar instead of deriving it from UTC.
        monday=dt("2026-10-18T23:00:00Z")
        data=payload([monday,monday+timedelta(hours=10)],[100,101])
        meta=data["chart"]["result"][0]["meta"]
        meta.update(exchangeTimezoneName="Europe/London",regularMarketTime=int((monday+timedelta(hours=11)).timestamp()),regularMarketPrice=102)
        rows=fse.parse_yahoo_bars(data,"EURUSD=X","1d",monday+timedelta(hours=12))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].timestamp,monday)
        self.assertEqual(rows[0].calendar_timezone,"Europe/London")
        self.assertEqual(rows[0].close,102)
        self.assertEqual(fse.resample_minutes(rows,1440)[0].calendar_timezone,"Europe/London")

    def test_duplicate_and_unsorted_source_is_canonical_and_unique(self):
        stamps=[dt("2026-10-21T11:00:00Z"),dt("2026-10-21T10:00:00Z"),dt("2026-10-21T11:00:00Z")]
        rows=fse.parse_yahoo_bars(payload(stamps,[100,101,102]),"BTC-USD","60m",dt("2026-10-22T00:00:00Z"))
        self.assertEqual([r.timestamp.hour for r in rows],[10,11])
        self.assertEqual(rows[-1].close,102)
        self.assertEqual(rows[-1].source_observed_at,rows[-1].closed_at)

    def test_invalid_prices_ohlc_and_future_rows_are_rejected(self):
        stamps=[dt("2026-10-21T00:00:00Z")+timedelta(hours=i) for i in range(8)]
        data=payload(stamps,[100,float("nan"),0,-5,100,100,100,100])
        quote=data["chart"]["result"][0]["indicators"]["quote"][0]
        quote["high"][4]=99; quote["low"][5]=101; quote["open"][6]=float("inf")
        rows=fse.parse_yahoo_bars(data,"BTC-USD","60m",dt("2026-10-21T06:00:00Z"))
        self.assertEqual([r.timestamp.hour for r in rows],[0])

    def test_four_hour_buckets_survive_sliding_source_window(self):
        start=dt("2026-10-21T00:00:00Z")
        bars=[fse.Bar(start+timedelta(hours=i),100+i,100+i,101+i,99+i) for i in range(13)]
        before=fse.resample_minutes(bars[:-1],240)
        after=fse.resample_minutes(bars[1:],240)
        # Compare complete overlapping buckets; the first partial bucket may differ.
        a={b.timestamp:b for b in before}; b={b.timestamp:b for b in after}
        for hour in (4,8):
            stamp=start+timedelta(hours=hour)
            self.assertEqual(a[stamp],b[stamp])
            self.assertEqual(a[stamp].closed_at,stamp+timedelta(hours=4))

    def test_spy_hour_session_open_and_short_final_hour(self):
        times=[dt("2026-10-21T13:30:00Z"),dt("2026-10-21T19:30:00Z"),dt("2026-10-21T20:00:00Z")]
        rows=fse.parse_yahoo_bars(payload(times,[100,101,102]),"SPY","60m",dt("2026-10-21T20:01:00Z"))
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[-1].timestamp,times[1])
        self.assertEqual(rows[-1].closed_at,times[2])
        self.assertEqual(rows[-1].close,102)
        self.assertTrue(fse.bar_is_closed(rows[-1],times[2]))

    def test_spy_dst_and_daily_close_are_explicit(self):
        for opened,closed in [("2026-03-06T14:30:00Z","2026-03-06T21:00:00Z"),("2026-03-09T13:30:00Z","2026-03-09T20:00:00Z")]:
            row=fse.parse_yahoo_bars(payload([dt(opened)]),"SPY","1d",dt("2026-03-10T00:00:00Z"))[0]
            self.assertEqual(row.timestamp,dt(opened))
            self.assertEqual(fse.bar_closed_at(row,1440),dt(closed))
            self.assertFalse(fse.bar_is_closed(row,dt(closed)-timedelta(seconds=1),1440))

    def test_spy_four_hour_buckets_do_not_cross_sessions(self):
        stamps=[dt("2026-10-23T13:30:00Z")+timedelta(hours=i) for i in range(7)]
        stamps+=[dt("2026-10-26T13:30:00Z")+timedelta(hours=i) for i in range(7)]
        rows=fse.parse_yahoo_bars(payload(stamps),"SPY","60m",dt("2026-10-27T00:00:00Z"))
        aggregated=fse.resample_minutes(rows,240,"SPY")
        self.assertEqual(len(aggregated),4)
        self.assertEqual([b.timestamp.hour for b in aggregated],[13,17,13,17])
        self.assertEqual([b.closed_at.hour for b in aggregated],[17,20,17,20])

    def test_spy_provider_early_close_overrides_regular_session(self):
        opened=dt("2026-11-27T14:30:00Z"); closed=dt("2026-11-27T18:00:00Z")
        stamps=[opened+timedelta(hours=i) for i in range(4)]
        data=payload(stamps)
        data["chart"]["result"][0]["meta"]["currentTradingPeriod"]={"regular":{"start":int(opened.timestamp()),"end":int(closed.timestamp())}}
        rows=fse.parse_yahoo_bars(data,"SPY","60m",closed)
        self.assertEqual(rows[-1].closed_at,closed)
        self.assertEqual(fse.resample_minutes(rows,240,"SPY")[-1].closed_at,closed)

    def test_spy_minute_children_do_not_prematurely_close_forming_bucket(self):
        opened=dt("2026-10-21T13:30:00Z")
        rows=fse.parse_yahoo_bars(payload([opened+timedelta(minutes=i) for i in range(3)]),"SPY","1m",opened+timedelta(minutes=3))
        aggregate=fse.resample_minutes(rows,5)[0]
        self.assertEqual(aggregate.timestamp,opened)
        self.assertEqual(aggregate.closed_at,opened+timedelta(minutes=5))
        self.assertFalse(fse.bar_is_closed(aggregate,opened+timedelta(minutes=3),5))

    def test_spy_early_session_close_survives_minute_and_nested_resampling(self):
        opened=dt("2026-11-27T14:30:00Z"); closed=dt("2026-11-27T18:00:00Z")
        data=payload([opened+timedelta(minutes=i) for i in range(210)])
        data["chart"]["result"][0]["meta"].update(currentTradingPeriod={"regular":{"start":int(opened.timestamp()),"end":int(closed.timestamp())}},regularMarketTime=int(closed.timestamp()),regularMarketPrice=101)
        rows=fse.parse_yahoo_bars(data,"SPY","1m",closed)
        self.assertEqual(rows[-1].session_closed_at,closed)
        five=fse.resample_minutes(rows,5)
        self.assertEqual(five[-1].session_closed_at,closed)
        self.assertEqual(fse.resample_minutes(five,240)[0].closed_at,closed)

    def test_hourly_closed_clock_does_not_use_quote_time(self):
        b=fse.Bar(dt("2026-10-21T12:00:00Z"),100,source_observed_at=dt("2026-10-21T12:44:00Z"))
        self.assertFalse(fse.bar_is_closed(b,"2026-10-21T12:59:59Z"))
        self.assertTrue(fse.bar_is_closed(b,"2026-10-21T13:00:00Z"))


if __name__=="__main__":
    unittest.main()

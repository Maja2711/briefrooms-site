"""Realtime EURUSD evidence: first-seen timestamps, gaps, immutable provenance."""
import unittest
from datetime import datetime, timedelta, timezone
from scripts import daily_eurusd_live_evidence as live

START=datetime(2026,10,9,0,10,tzinfo=timezone.utc)

def spot(status="OPEN"):
    return {"metadata":{"position":{"trade_id":"TEST-LONG","status":status,
              "direction":"LONG","opened_at":live.iso(START),
              "entry":1.1200,"stop":1.1160,"target":1.1270}}}

def candles(size):
    return [{"timestamp":live.iso(START+timedelta(minutes=i)),
             "close":1.12+i*.00002,"high":1.12+i*.00002,
             "low":1.12+i*.00002} for i in range(size)]

class LiveEvidenceTests(unittest.TestCase):
    def test_first_seen_and_late_replay(self):
        now=START+timedelta(minutes=11,seconds=30)
        result=live.collect({},spot(),candles(15),now)
        self.assertEqual(len(result["snapshots"]),11)
        self.assertEqual(result["monitor_health"]["status"],"OK")
        self.assertEqual(sum(s["timely_observation"] for s in result["snapshots"]),2)
        self.assertTrue(all(s["first_seen_at"]==live.iso(now) for s in result["snapshots"]))
        self.assertTrue(all(s["exit_authority"] is False for s in result["snapshots"]))
        self.assertTrue(all(s["retrospective_bar_not_a_live_alert"]
                            for s in result["snapshots"] if not s["timely_observation"]))

    def test_continuous_minutes_and_idempotence(self):
        at=START+timedelta(minutes=11,seconds=30)
        first=live.collect({},spot(),candles(16),at)
        second=live.collect(first,spot(),candles(18),at+timedelta(minutes=1))
        self.assertEqual(len(second["snapshots"]),12)
        self.assertEqual(second["snapshots"][0]["first_seen_at"],live.iso(at))
        self.assertEqual(second["snapshots"][-1]["first_seen_at"],live.iso(at+timedelta(minutes=1)))
        repeat=live.collect(second,spot(),candles(18),at+timedelta(minutes=1))
        self.assertEqual(second["snapshots"],repeat["snapshots"])

    def test_stale_never_timely(self):
        result=live.collect({},spot(),candles(10),START+timedelta(minutes=40))
        self.assertEqual(result["monitor_health"]["status"],"STALE_OR_MISSING_FX_1M_BAR")
        self.assertFalse(any(s["timely_observation"] for s in result["snapshots"]))

    def test_closed_position_never_retroactively_captures(self):
        now=START+timedelta(minutes=10)
        before=live.collect({},spot(),candles(12),now)
        after=live.collect(before,spot("CLOSED"),candles(30),now+timedelta(minutes=10))
        self.assertEqual(before["snapshots"],after["snapshots"])

    def test_earliest_timestamp_wins_duplicate(self):
        base=live.iso(START)
        early={"trade_id":"T","market_bar_at":base,
               "captured_at":live.iso(START+timedelta(minutes=2)),
               "first_seen_at":live.iso(START+timedelta(minutes=2)),
               "timely_observation":True,"exit_authority":False}
        late=dict(early, captured_at=live.iso(START+timedelta(minutes=20)),
                  first_seen_at=live.iso(START+timedelta(minutes=20)),
                  timely_observation=False)
        self.assertEqual(live.merge_snapshots([late],[early]),[early])
        self.assertEqual(live.merge_snapshots([early],[late]),[early])

    def test_preserve_correction_study_on_publication_merge(self):
        now=START+timedelta(minutes=12)
        spool=live.collect({},spot(),candles(15),now)
        remote={"authority":"SHADOW_OBSERVATION_ONLY",
                "correction_study":{"events":[{"id":"archive"}]},
                "snapshots":[]}
        output=live.merge_journal(remote,spool,now)
        self.assertEqual(len(output["snapshots"]),12)
        self.assertEqual(output["correction_study"],remote["correction_study"])
        self.assertFalse(output["realtime_monitor_health"]["publication_pending"])
        self.assertFalse(output["realtime_monitor_health"]["exit_authority"])

    def test_deny_outside_repo(self):
        with self.assertRaises(ValueError):
            live.publish({},"token","wrong/repo",START)

if __name__=="__main__":
    unittest.main()

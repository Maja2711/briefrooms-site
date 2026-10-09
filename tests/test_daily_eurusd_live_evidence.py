"""Realtime EURUSD evidence: first-seen timestamps, gaps, immutable provenance."""
import unittest
import base64
import json
import urllib.error
import tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from scripts import daily_eurusd_live_evidence as live
from scripts import daily_eurusd_exit_intelligence_v2 as observer

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
        self.assertEqual(sum(s["timely_observation"] for s in result["snapshots"]),3)
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

    def test_scheduled_research_does_not_erase_realtime_observer_health(self):
        health={"status":"OK","timely_trade_snapshots":110,
                "last_published_at":live.iso(START)}
        journal={"authority":"SHADOW_OBSERVATION_ONLY",
                 "realtime_monitor_health":health,
                 "realtime_heartbeats":[{"at":live.iso(START),"status":"OK"}],
                 "snapshots":[]}
        after,_=observer.step({"metadata":{"position":None}},
                              {"trades":[]},journal,{},[],[],START)
        self.assertEqual(after["realtime_monitor_health"],health)
        self.assertEqual(after["realtime_heartbeats"],journal["realtime_heartbeats"])
        self.assertEqual(after["monitor_health"]["status"],"NO_OPEN_POSITION")

    def test_3pip_epe_spread_applies_to_live_position_net_profit(self):
        state=spot()
        state["metadata"]["position"]["execution_price_engine"]={
            "synthetic_spread_pips":3,"synthetic_half_spread_pips":1.5}
        now=START+timedelta(minutes=12)
        journal=live.collect({},state,candles(14),now)
        latest=journal["snapshots"][-1]
        self.assertEqual(latest["epe_synthetic_half_spread_pips"],1.5)
        self.assertTrue(latest["epe_spread_from_recorded_entry"])
        self.assertAlmostEqual(
            latest["best_favorable_indicative_net_pips"],
            latest["best_favorable_mid_pips"]-1.5,places=3)
        self.assertTrue(latest["prices_are_executable_bid_ask"] is False)

    def test_conditional_api_conflict_reloads_remote_before_persisting(self):
        t=live.iso(START)
        stale={"authority":"SHADOW_OBSERVATION_ONLY","snapshots":[]}
        fresher={"authority":"SHADOW_OBSERVATION_ONLY",
                 "snapshots":[{"trade_id":"T","market_bar_at":t,
                               "first_seen_at":t,"captured_at":t,"exit_authority":False}],
                 "correction_study":{"evidence":"preserved"}}
        spool={"snapshots":[{"trade_id":"T",
                             "market_bar_at":live.iso(START+timedelta(minutes=1)),
                             "first_seen_at":live.iso(START+timedelta(minutes=2)),
                             "captured_at":live.iso(START+timedelta(minutes=2)),
                             "exit_authority":False}]}
        calls=[]
        def fake_request(url,token,method="GET",payload=None):
            calls.append(method)
            if method=="GET":
                is_latest=calls.count("GET")>1
                doc=fresher if is_latest else stale
                return {"type":"file","sha":"sha2" if is_latest else "sha1",
                        "content":base64.b64encode(json.dumps(doc).encode()).decode()}
            if calls.count("PUT")==1:
                raise urllib.error.HTTPError(url,409,"conflict",{},None)
            self.assertEqual(payload["sha"],"sha2")
            merged=json.loads(base64.b64decode(payload["content"]))
            self.assertEqual(len(merged["snapshots"]),2)
            self.assertEqual(merged["correction_study"],fresher["correction_study"])
            return {"content":{"sha":"saved"},"commit":{"sha":"commit"}}
        with patch.object(live.time,"sleep",return_value=None):
            done=live.publish(spool,"secret",live.REPO,START,request=fake_request)
        self.assertEqual(done["status"],"PUBLISHED")
        self.assertEqual(calls,["GET","PUT","GET","PUT"])

    def test_two_hour_archives_are_small_and_preserve_full_lifetime(self):
        rows=[{"trade_id":"EURUSD-T1",
               "market_bar_at":live.iso(START+timedelta(minutes=i)),
               "first_seen_at":live.iso(START+timedelta(minutes=i+1)),
               "captured_at":live.iso(START+timedelta(minutes=i+1)),
               "exit_authority":False} for i in range(360)]
        shards=live.archive_shards({"snapshots":rows})
        self.assertEqual(len(shards),4)  # 00-02, 02-04, 04-06, 06-08 UTC
        self.assertTrue(all(len(v)<=120 for v in shards.values()))
        self.assertEqual(sum(map(len,shards.values())),360)
        hot=live.merge_snapshots([],rows)
        spool=live.merge_snapshots([],rows,limit=live.MAX_LOCAL_SPOOL_SNAPSHOTS)
        self.assertEqual(len(hot),180)
        self.assertEqual(len(spool),360)
        self.assertEqual(len(live.archive_shards({"snapshots":spool})),4)

    def test_archive_payload_can_be_rehydrated_for_post_trade_review(self):
        rows=[{"trade_id":"T","market_bar_at":live.iso(START),
               "first_seen_at":live.iso(START+timedelta(minutes=1)),
               "captured_at":live.iso(START+timedelta(minutes=1)),
               "exit_authority":False}]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            shard=root/"20261009"/"00.json"
            shard.parent.mkdir(parents=True)
            shard.write_text(json.dumps({"authority":"SHADOW_OBSERVATION_ONLY",
                                         "snapshots":rows}))
            found=observer.read_archived_snapshots(root)
            self.assertEqual(found,rows)
            late=dict(rows[0],first_seen_at=live.iso(START+timedelta(minutes=20)),
                      captured_at=live.iso(START+timedelta(minutes=20)))
            self.assertEqual(observer.combine_complete_snapshots(found,[late]),rows)

    def test_archive_create_is_constrained_to_fixed_utc_shard(self):
        row={"trade_id":"T","market_bar_at":live.iso(START),
             "first_seen_at":live.iso(START)}
        spool={"snapshots":[row]}
        seen=[]
        def fake(url,token,method="GET",payload=None):
            seen.append((url,method))
            if method=="GET":
                raise urllib.error.HTTPError(url,404,"not created",{},None)
            content=json.loads(base64.b64decode(payload["content"]))
            self.assertEqual(content["authority"],"SHADOW_OBSERVATION_ONLY")
            self.assertEqual(content["snapshots"],[row])
            self.assertIn("eurusd_live_signal_archive/20261009/00.json",url)
            self.assertNotIn("sha",payload)
            return {"content":{"sha":"written"}}
        result=live.publish_archives(spool,"token",live.REPO,START,request=fake)
        self.assertEqual(result["shards_verified"],1)
        self.assertEqual([method for _,method in seen],["GET","PUT"])

    def test_old_good_samples_cannot_mask_current_stale_feed(self):
        good_at=START+timedelta(minutes=11,seconds=30)
        good=live.collect({},spot(),candles(11),good_at)
        self.assertEqual(good["monitor_health"]["status"],"OK")
        # Last provider candle was completed at +11m. At +14m20 it is
        # 200s late: historical timely samples must NOT make it OK.
        late=live.collect(good,spot(),candles(11),
                          START+timedelta(minutes=14,seconds=20))
        self.assertEqual(late["monitor_health"]["status"],"DELAYED_SOURCE_1M_BAR")
        self.assertFalse(late["monitor_health"]["whole_open_lifetime_continuity_proven"])

    def test_deny_outside_repo(self):
        with self.assertRaises(ValueError):
            live.publish({},"token","wrong/repo",START)

if __name__=="__main__":
    unittest.main()

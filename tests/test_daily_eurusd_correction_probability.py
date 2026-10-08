"""Correction probability: all eligible states, right censoring, point-in-time OOS."""
import copy
import unittest
from datetime import datetime, timedelta, timezone
from scripts import daily_eurusd_correction_probability as m

T0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)


def bar(i, price, origin=T0):
    return {
        "timestamp": m.iso(origin+timedelta(minutes=i)),
        "close": price, "high": price, "low": price
    }


def trend_bars(origin=T0):
    # Eligible at minute 119: 60-minute EMA20 below EMA60, 90 contiguous.
    rows = [bar(i, 1.1200 - 0.000005*i, origin) for i in range(110)]
    rows += [bar(i, 1.119455 - 0.00008*(i-109), origin) for i in range(110,120)]
    return m.normalize_bars(rows, origin + timedelta(minutes=125))


def labelled_episode(date, feature, label, horizon=15):
    when = date + timedelta(hours=10)
    iso = m.iso(when)
    return {"id": iso, "observed_at": iso,
            "date_utc": m.utc_day(when),
            "features": dict(feature),
            "outcomes": {str(h): {"status": "RESOLVED", "label": label,
                                  "known_at": m.iso(when + timedelta(minutes=h))}
                         for h in m.HORIZONS_MINUTES}}


class CorrectionProbabilityTests(unittest.TestCase):
    def test_features_use_only_closed_bars_no_futures(self):
        rows = trend_bars()
        asof = T0 + timedelta(minutes=120)
        original = m.candidate(rows, asof)
        self.assertIsNotNone(original)
        self.assertTrue(original["features"]["impulse_pips"] >= 8)
        self.assertLess(original["features"]["ema20_minus_ema60_pips"], 0)
        changed = rows + m.normalize_bars([bar(120, 1.1400), bar(121, 1.1350)],
                                          T0 + timedelta(minutes=130))
        self.assertEqual(original, m.candidate(changed, asof))
        self.assertFalse(original["signal_uses_future"])

    def test_5_15_30_label_success_and_failure_from_same_eligible_state(self):
        rows = trend_bars()
        ep = m.candidate(rows, T0 + timedelta(minutes=120))
        self.assertIsNotNone(ep)
        threshold = ep["target_rebound_pips"]*m.PIP
        mid = ep["spot_mid"]
        future = [bar(i, mid + (threshold + 0.0001 if i >= 127 else 0.00001), T0)
                  for i in range(120,150)]
        all_rows = rows + m.normalize_bars(future, T0+timedelta(minutes=150))
        outcome_5 = m.outcome(ep, all_rows, T0+timedelta(minutes=150), 5)
        outcome_15 = m.outcome(ep, all_rows, T0+timedelta(minutes=150), 15)
        outcome_30 = m.outcome(ep, all_rows, T0+timedelta(minutes=150), 30)
        self.assertEqual(outcome_5["label"], 0)
        self.assertEqual(outcome_15["label"], 1)
        self.assertEqual(outcome_30["label"], 1)

    def test_incomplete_paths_censored_not_negatives(self):
        rows = trend_bars()
        ep = m.candidate(rows, T0 + timedelta(minutes=120))
        self.assertEqual(m.outcome(ep, rows, T0 + timedelta(minutes=123), 5)["status"], "PENDING")
        self.assertEqual(m.outcome(ep, rows, T0 + timedelta(minutes=130), 5)["status"], "CENSORED_DATA_GAP")
        missing = m.normalize_bars([bar(120, 1.115), bar(122, 1.115),
                                    bar(123, 1.115), bar(124, 1.115)], T0+timedelta(minutes=130))
        self.assertEqual(m.outcome(ep, rows+missing, T0+timedelta(minutes=130), 5)["status"], "CENSORED_DATA_GAP")

    def test_same_day_and_not_yet_known_outcomes_never_train(self):
        b = trend_bars()
        ep = m.candidate(b, T0+timedelta(minutes=120))
        feature = ep["features"]
        before = T0 - timedelta(days=1)
        previous = labelled_episode(before, feature, 0)
        current = labelled_episode(T0, feature, 1)
        old_but_not_yet_known = labelled_episode(before, feature, 1)
        old_but_not_yet_known["outcomes"]["5"]["known_at"] = m.iso(T0+timedelta(days=1))
        train = m.resolved_for([previous,current,old_but_not_yet_known], 5, m.iso(T0+timedelta(hours=12)))
        self.assertEqual(len(train),1)
        self.assertEqual(train[0]["outcomes"]["5"]["label"],0)

    def test_train_has_true_failures_denominator(self):
        ep = m.candidate(trend_bars(),T0+timedelta(minutes=120))
        train = [labelled_episode(T0 - timedelta(days=i+1),ep["features"], int(i%4==0))
                 for i in range(40)]
        result = m.probability(ep["features"],train,5)
        self.assertEqual(result["status"], "RESEARCH_UNCALIBRATED")
        self.assertEqual(result["positive"],10)
        self.assertEqual(result["negative"],30)
        self.assertAlmostEqual(result["p"],(10/40*.5)+(11/42*.5),delta=0.02)
        self.assertFalse(result["p"] == 0.5)

    def test_low_sample_is_not_a_false_probability(self):
        ep = m.candidate(trend_bars(),T0+timedelta(minutes=120))
        train = [labelled_episode(T0 - timedelta(days=i+1),ep["features"],1) for i in range(15)]
        result = m.probability(ep["features"],train,15)
        self.assertEqual(result["status"], "INSUFFICIENT_TRAINING_EVIDENCE")
        self.assertIsNone(result["p"])

    def test_prospective_freezing_not_revised_by_future_outcomes(self):
        bars = trend_bars()
        now=T0+timedelta(minutes=121)
        old={}
        result = m.step(old,bars,now)
        self.assertFalse(result["authority"]["trade_execution"])
        self.assertFalse(result["authority"]["automatic_promotion"])
        self.assertIsNone(result["latest_market_observation"] if
                          result["latest_market_observation"] is None else
                          result["latest_market_observation"].get("trade_result"))
        self.assertTrue(result["research_note"])
        replay = m.step(result,bars,now)
        self.assertEqual(replay["prospective_episodes"],result["prospective_episodes"])
        self.assertEqual(replay["updated_at"],result["updated_at"])

    def test_date_blocked_walk_forward_does_not_self_fit(self):
        ep = m.candidate(trend_bars(),T0+timedelta(minutes=120))
        train = [labelled_episode(T0-timedelta(days=60-i),ep["features"], i%2) for i in range(65)]
        result = m.replay_validation(train, 15)
        self.assertEqual(result["status"], "BACKTEST_ONLY_NOT_LIVE_VALIDATED")
        self.assertGreater(result["n"],0)
        self.assertLess(result["n"],len(train))
        self.assertIsNotNone(result["brier"])

    def test_market_gaps_excluded(self):
        rows=trend_bars()
        rows[-30]["time"] += timedelta(minutes=10)
        self.assertIsNone(m.candidate(rows,T0+timedelta(minutes=120)))

    def test_observation_input_no_trade_or_belief_or_exit(self):
        result=m.step({},trend_bars(),T0+timedelta(minutes=121))
        serialized=str(result)
        self.assertNotIn("today_trade", serialized)
        self.assertNotIn("result_percent",serialized)
        self.assertNotIn("entry_score",serialized)
        self.assertFalse(result["authority"]["daily_exit_mutation"])
        self.assertFalse(result["authority"]["belief_core_mutation"])
        self.assertFalse(result["authority"]["same_day_training"])
        self.assertTrue(all(not v["production_authority"] for v in result["horizons"].values()))


if __name__ == "__main__":
    unittest.main()

"""EUR/USD Daily v1.9 H1 range, Belief flip, cross-direction admission tests."""
from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from scripts import daily_eurusd_bollinger_h1 as h1
from scripts import daily_eurusd_flip_guard as flip
from scripts import daily_eurusd_spot_v19 as runtime
from scripts import daily_eurusd_belief_decision as belief

UTC = timezone.utc
H1_START = datetime(2026, 10, 19, 0, tzinfo=UTC)
BASE = datetime(2026, 10, 26, 9, tzinfo=UTC)


def h1_bars(values):
    rows = []
    for i, close in enumerate(values):
        for minute in (0, 30):
            rows.append(SimpleNamespace(
                timestamp=H1_START + timedelta(hours=i, minutes=minute),
                open=close, high=close + .0001,
                low=close - .0001, close=close,
            ))
    return rows


def historical_loss(now=BASE, direction="LONG", r=-.35):
    return {
        "trade_id": f"actual-{direction}",
        "direction": direction,
        "instrument": "EUR/USD",
        "closed_at": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "r_multiple": r,
    }


def candidate(now=BASE, direction="SHORT", score=46.2, confidence=.34,
              source_sha="a"*64, source_at=None, anchor_at=None):
    source_at = source_at or now - timedelta(minutes=1)
    anchor_at = anchor_at or source_at
    anchor = .43 if direction == "SHORT" else .57
    final = {
        "direction": direction, "score": score, "confidence": confidence,
        "decision_source": "NATIVE_BELIEF_FIRST",
        "epistemic_aggregate_authoritative": True,
        "used_beliefs": [{
            "belief_id": flip.ANCHOR, "probability": anchor,
            "observed_at": anchor_at.isoformat(),
        }],
        "epistemic_consumer": {
            "available": True,
            "aggregate_authoritative": True,
            "contract": "epistemic-consumer-interface-v1",
            "source_created_at": source_at.isoformat(),
            "source_sha256": source_sha,
        },
    }
    return {"instrument": "EUR/USD", "direction": direction,
            "score": score, "confidence": confidence,
            "metadata": {"final_decision": final}}


class H1RangeGateTests(unittest.TestCase):
    def test_flat_middle_crossing_is_not_trend_confirmation(self):
        # Many completed H1 candles oscillate around the mean, followed by
        # an insignificant local move below it (not a directional breakout).
        prices = [1.1200 + (.00028 if i % 4 < 2 else -.00028)
                  for i in range(40)]
        prices.extend([1.12001, 1.11999, 1.11998, 1.11998])
        result = h1.evaluate(
            h1_bars(prices), "SHORT", H1_START + timedelta(hours=len(prices)))
        self.assertTrue(result["available"], result)
        self.assertFalse(result["allowed"], result)
        self.assertIn(result["reason"],
                      {"h1_range_middle_whipsaw", "h1_middle_break_unconfirmed",
                       "h1_trend_not_confirmed"})
        self.assertTrue(result["range_regime"] or result["marginal_crossing"], result)

    def test_real_0135_pip_cross_is_microstructure_noise(self):
        # Market structure recorded in the 26 Oct 09:02 UTC short decision.
        self.assertLess(abs(1.1209505796432495 - 1.1209640860557557) / h1.PIP,
                        h1.MIDDLE_NOISE_PIPS)
        self.assertLess(abs(-.0000503261884052808) / h1.PIP, 2.0)

    def test_bona_fide_uptrend_and_downtrend_remain_admissible(self):
        for direction, prices in (
            ("LONG", [1.10 + i * .0002 for i in range(42)]),
            ("SHORT", [1.15 - i * .0002 for i in range(42)]),
        ):
            with self.subTest(direction=direction):
                signal = h1.evaluate(
                    h1_bars(prices), direction,
                    H1_START + timedelta(hours=len(prices)))
                self.assertTrue(signal["allowed"], signal)
                self.assertEqual(signal["regime"], "TREND")
                self.assertTrue(signal["strong_trend_confirmed"])

    def test_future_incomplete_candle_cannot_remove_range_veto(self):
        prices = [1.12 + (.0003 if i % 2 else -.0003) for i in range(42)]
        now = H1_START + timedelta(hours=len(prices))
        before = h1.evaluate(h1_bars(prices), "LONG", now)
        future = h1_bars(prices)
        future.append(SimpleNamespace(timestamp=now, open=1.14, high=1.15,
                                      low=1.13, close=1.14))
        after = h1.evaluate(future, "LONG", now)
        self.assertEqual(before, after)

    def test_missing_h1_is_not_reinterpreted_as_trend(self):
        result = h1.evaluate(h1_bars([1.12]*5), "LONG",
                             H1_START + timedelta(hours=5))
        self.assertFalse(result["available"])


class BeliefFlipConfirmationTests(unittest.TestCase):
    def test_short_after_long_loss_needs_second_new_source_and_40_minutes(self):
        history = {"trades": [historical_loss()]}
        first = flip.flip_confirmation(candidate(), history, BASE)
        self.assertTrue(first["required"])
        self.assertFalse(first["allowed"])
        self.assertEqual(first["reason"], "flip_requires_new_sustained_belief")
        same_source = flip.flip_confirmation(
            candidate(BASE + timedelta(minutes=45), source_sha="a"*64,
                      source_at=BASE - timedelta(minutes=1)),
            history, BASE + timedelta(minutes=45), first["state"],
        )
        self.assertFalse(same_source["allowed"])
        second = flip.flip_confirmation(
            candidate(BASE + timedelta(minutes=45), source_sha="b"*64),
            history, BASE + timedelta(minutes=45), first["state"],
        )
        self.assertTrue(second["allowed"], second)
        self.assertEqual(second["reason"], "flip_confirmed")
        self.assertTrue(second["state"]["confirmed"])
        self.assertEqual(history, {"trades": [historical_loss()]})

    def test_new_snapshot_without_new_trend_anchor_does_not_confirm(self):
        history = {"trades": [historical_loss()]}
        first = flip.flip_confirmation(candidate(), history, BASE)
        at = BASE + timedelta(minutes=50)
        recycled = flip.flip_confirmation(
            candidate(at, source_sha="b"*64, anchor_at=BASE - timedelta(minutes=1)),
            history, at, first["state"],
        )
        self.assertFalse(recycled["allowed"], recycled)
        self.assertFalse(recycled["new_source_verified"])
        verified = flip.flip_confirmation(
            candidate(at, source_sha="c"*64),
            history, at, first["state"],
        )
        self.assertTrue(verified["allowed"], verified)

    def test_strong_reversal_still_requires_20_minutes_and_new_evidence(self):
        history = {"trades": [historical_loss()]}
        first = flip.flip_confirmation(
            candidate(score=40, confidence=.49), history, BASE)
        early = flip.flip_confirmation(
            candidate(BASE + timedelta(minutes=10), score=40, confidence=.49,
                      source_sha="b"*64),
            history, BASE + timedelta(minutes=10), first["state"])
        self.assertFalse(early["allowed"])
        later = flip.flip_confirmation(
            candidate(BASE + timedelta(minutes=22), score=40, confidence=.49,
                      source_sha="c"*64),
            history, BASE + timedelta(minutes=22), first["state"])
        self.assertTrue(later["allowed"], later)

    def test_authoritative_snapshot_is_required(self):
        history = {"trades": [historical_loss()]}
        invalid = candidate()
        invalid["metadata"]["final_decision"]["epistemic_consumer"]["aggregate_authoritative"] = False
        result = flip.flip_confirmation(invalid, history, BASE)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "non_authoritative_flip_decision")

    def test_same_direction_or_old_opposite_trade_is_not_additionally_blocked(self):
        history = {"trades": [historical_loss(direction="SHORT")]}
        result = flip.flip_confirmation(candidate(), history, BASE)
        self.assertFalse(result["required"])
        self.assertTrue(result["allowed"])
        history["trades"][0]["closed_at"] = (BASE - timedelta(hours=13)).isoformat()
        result = flip.flip_confirmation(candidate(), history, BASE)
        self.assertFalse(result["required"])
        self.assertTrue(result["allowed"])

    def test_future_trade_cannot_trigger_a_past_flip(self):
        history = {"trades": [{
            **historical_loss(),
            "closed_at": (BASE + timedelta(minutes=1)).isoformat(),
        }]}
        result = flip.flip_confirmation(candidate(), history, BASE)
        self.assertFalse(result["required"])


class CrossDirectionWhipsawTests(unittest.TestCase):
    def test_range_after_opposite_loss_blocks_even_confirmed_belief(self):
        history = {"trades": [historical_loss()]}
        h1_range = {"available": True, "allowed": False,
                    "range_regime": True, "regime": "RANGE"}
        result = flip.cross_direction_whipsaw(
            candidate(score=39, confidence=.8), history, BASE, h1_range)
        self.assertTrue(result["required"])
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "opposite_loss_h1_range_whipsaw")

    def test_genuine_high_confidence_h1_trend_flip_can_pass(self):
        history = {"trades": [historical_loss()]}
        h1_trend = {"available": True, "allowed": True, "aligned": True,
                    "regime": "TREND", "range_regime": False,
                    "strong_trend_confirmed": True}
        strong = flip.cross_direction_whipsaw(
            candidate(score=42, confidence=.48), history, BASE, h1_trend)
        self.assertTrue(strong["allowed"], strong)
        weak = flip.cross_direction_whipsaw(
            candidate(score=46, confidence=.33), history, BASE, h1_trend)
        self.assertFalse(weak["allowed"])

    def test_no_h1_evidence_after_opposite_loss_fails_closed(self):
        result = flip.cross_direction_whipsaw(candidate(),
            {"trades": [historical_loss()]}, BASE, {"available": False})
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "opposite_loss_h1_unavailable")

    def test_win_does_not_trigger_loss_whipsaw_gate(self):
        result = flip.cross_direction_whipsaw(candidate(),
            {"trades": [historical_loss(r=.5)]}, BASE, {"available": False})
        self.assertTrue(result["allowed"])
        self.assertFalse(result["required"])

    def test_v19_admission_preserves_belief_direction_while_vetoing_flip(self):
        payload = candidate()
        h1_trend = {"available": True, "allowed": True, "aligned": True,
                    "regime": "TREND", "range_regime": False,
                    "strong_trend_confirmed": True}
        payload["metadata"]["bollinger_h1"] = h1_trend
        output = runtime.DailyEngineOutput(
            instrument="EUR/USD", timestamp=BASE.isoformat(), direction="SHORT",
            score=46.2, confidence=.34,
            entry=1.12, stop=1.125, target=1.11, horizon="intraday_to_27h",
            engine_version=runtime.ENGINE_VERSION, status="SIGNAL",
            decision_mode="WITH", metadata=payload["metadata"],
        ).validate()
        with patch.object(belief, "calendar_safety",
                          return_value={"blocked": False, "reason": "clear"}):
            result = runtime._execution_admission(
                output, {"trades": [historical_loss()]}, observed_at=BASE)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["final_direction_preserved"], "SHORT")
        self.assertFalse(result["direction_mutation_allowed"])
        self.assertTrue(any("belief_flip_" in r for r in result["reasons"]))
        self.assertTrue(any("cross_direction_" in r for r in result["reasons"]))


if __name__ == "__main__":
    unittest.main()

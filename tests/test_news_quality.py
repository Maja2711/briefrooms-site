from __future__ import annotations

import unittest

from scripts.news_quality import POLICY_VERSION, evaluate_story, has_substantive_headline, public_policy


class NewsQualityTests(unittest.TestCase):
    def test_rejects_standalone_death_notice(self) -> None:
        decision = evaluate_story("Nie żyje znany aktor. Miał 74 lata")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "death_notice")

    def test_rejects_generic_death_teaser_with_death_in_summary(self) -> None:
        decision = evaluate_story(
            "Smutna wiadomość ze świata kultury",
            "W wieku 81 lat zmarł ceniony reżyser.",
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "death_notice")

    def test_keeps_material_casualty_event(self) -> None:
        decision = evaluate_story("Dziesięć osób zginęło w ataku na dworzec")
        self.assertTrue(decision.accepted)

    def test_rejects_plain_interview_announcement(self) -> None:
        decision = evaluate_story("Wywiad z Przemysławem Wiplerem w Polsat News")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "interview_promo_without_substance")

    def test_rejects_tv_guest_announcement(self) -> None:
        decision = evaluate_story("Wipler dziś gościem programu. Oglądaj o 19:30")
        self.assertFalse(decision.accepted)

    def test_rejects_english_interview_promotion(self) -> None:
        decision = evaluate_story("Watch our interview with the finance minister")
        self.assertFalse(decision.accepted)

    def test_allows_concrete_claim_from_interview(self) -> None:
        decision = evaluate_story("Wipler w TVN24: rząd powinien obniżyć podatki")
        self.assertTrue(decision.accepted)

    def test_allows_concrete_forecast_with_number(self) -> None:
        decision = evaluate_story("Wipler ostrzega, że deficyt przekroczy 6 proc. PKB [WYWIAD]")
        self.assertTrue(decision.accepted)

    def test_allows_english_claim_from_interview(self) -> None:
        decision = evaluate_story("Finance minister says rates will fall in September - interview")
        self.assertTrue(decision.accepted)

    def test_topic_list_is_not_a_substantive_headline(self) -> None:
        self.assertFalse(has_substantive_headline("Rozmowa z prezesem: o podatkach, rządzie i wyborach"))

    def test_policy_is_versioned_and_public(self) -> None:
        policy = public_policy()
        self.assertEqual(policy["version"], POLICY_VERSION)
        self.assertEqual({item["id"] for item in policy["excluded"]}, {
            "death_notice",
            "interview_promo_without_substance",
        })


if __name__ == "__main__":
    unittest.main()

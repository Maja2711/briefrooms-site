from __future__ import annotations

import unittest

from scripts.news_quality import POLICY_VERSION, evaluate_story, has_substantive_headline, public_policy


class NewsQualityTests(unittest.TestCase):
    def test_rejects_sports_betting_bonus_ad(self) -> None:
        decision = evaluate_story(
            "Hit! Bonus 300 zł za gola Wisły Kraków w Pucharze Polski",
            "Oferta bukmachera dla nowych klientów.",
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "betting_promotion")

    def test_rejects_guest_listing_without_news(self) -> None:
        decision = evaluate_story(
            "Sportowy wieczór. Gościem Magdalena Śliwa (31.08.2026)",
            "Zapraszamy do programu.",
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "interview_promo_without_substance")

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

    def test_rejects_exact_wipler_guest_listing_from_feed(self) -> None:
        decision = evaluate_story(
            'Przemysław Wipler w "Gościu Wydarzeń" [OGLĄDAJ]',
            "Poseł będzie gościem programu. Transmisja od godz. 19:15.",
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "interview_promo_without_substance")

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

    def test_rejects_exact_eurojackpot_promo_from_feed(self) -> None:
        decision = evaluate_story(
            "Kumulacja rośnie. Do wygrania 140 milionów złotych",
            "Losowanie Eurojackpot nie przyniosło głównej wygranej. Oto wyniki z 4 sierpnia.",
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "lottery_result_or_jackpot_promo")

    def test_rejects_lotto_draw_results(self) -> None:
        decision = evaluate_story("Wyniki Lotto. Wylosowano te numery")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "lottery_result_or_jackpot_promo")

    def test_rejects_english_lottery_numbers(self) -> None:
        decision = evaluate_story("Powerball winning numbers and jackpot results")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "lottery_result_or_jackpot_promo")

    def test_allows_public_interest_lottery_regulation(self) -> None:
        decision = evaluate_story(
            "Rząd podnosi podatek od gier losowych",
            "Nowa ustawa obejmie Lotto i inne loterie państwowe.",
        )
        self.assertTrue(decision.accepted)

    def test_rejects_exact_tvn_self_promotion_from_production(self) -> None:
        decision = evaluate_story(
            'Rewelacyjne wyniki TVN24, "Faktów" TVN i tvn24.pl. Dziękujemy!'
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "media_self_promotion")

    def test_rejects_exact_taxi_dispute_from_production(self) -> None:
        decision = evaluate_story(
            "Konflikt radnej KO z taksówkarzem. Ugoda i decyzja prokuratury"
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "isolated_local_incident")

    def test_rejects_minor_drunk_driver_collision(self) -> None:
        decision = evaluate_story(
            "Dwie kolizje w ciągu kilkunastu minut. Kierowca wydmuchał półtora promila"
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "isolated_local_incident")

    def test_allows_systemic_critical_infrastructure_incident(self) -> None:
        decision = evaluate_story(
            "Cyberatak na infrastrukturę krytyczną. Rząd uruchamia procedury bezpieczeństwa"
        )
        self.assertTrue(decision.accepted)

    def test_allows_media_regulator_story(self) -> None:
        decision = evaluate_story(
            "KRRiT nakłada karę na stację za naruszenie warunków koncesji"
        )
        self.assertTrue(decision.accepted)

    def test_rejects_ambassador_social_post_without_consequence(self) -> None:
        decision = evaluate_story(
            'Ambasador USA zamieścił wpis. "Niezwykła polska odwaga"'
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "political_theater_without_public_consequence")

    def test_rejects_politician_verbal_exchange_without_decision(self) -> None:
        decision = evaluate_story('Morawiecki zwrócił się do Mentzena. "Pomoże pan?"')
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "political_theater_without_public_consequence")

    def test_allows_political_reaction_when_it_changes_policy(self) -> None:
        decision = evaluate_story(
            "Premier zareagował na kryzys. Rząd przyjął ustawę o bezpieczeństwie"
        )
        self.assertTrue(decision.accepted)

    def test_policy_is_versioned_and_public(self) -> None:
        policy = public_policy()
        self.assertEqual(policy["version"], POLICY_VERSION)
        self.assertEqual({item["id"] for item in policy["excluded"]}, {
            "death_notice",
            "interview_promo_without_substance",
            "lottery_result_or_jackpot_promo",
            "betting_promotion",
            "media_self_promotion",
            "isolated_local_incident",
            "political_theater_without_public_consequence",
        })


if __name__ == "__main__":
    unittest.main()

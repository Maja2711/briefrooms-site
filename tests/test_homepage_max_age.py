from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from scripts.enforce_homepage_max_age import (
    HOME_LIMIT,
    HOME_MAX_AGE,
    HOME_RESERVE_LIMIT,
    IMAGE_POLICY_VERSION,
    enforce_payload,
    public_news_preselection_check,
    PUBLICATION_VERIFICATION_BUFFER,
)


class HomepageExposureCapTests(unittest.TestCase):
    def _story(self, name: str, published_at: datetime, category: str = "Health") -> dict:
        slug = name.lower().replace(" ", "-")
        return {
            "title": name,
            "link": f"https://example.com/{slug}",
            "image": f"https://images.example.com/{slug}.jpg",
            "source": "Example",
            "summary": name,
            "published_at": published_at.isoformat(),
            "category": category,
        }

    def test_preselection_excludes_source_expiring_during_verification(self):
        now = datetime(2026, 10, 20, 12, 13, tzinfo=timezone.utc)
        old = self._story("BBC near expiry", now - HOME_MAX_AGE + timedelta(minutes=3), "Business")
        safe = self._story("BBC fresh", now - HOME_MAX_AGE + PUBLICATION_VERIFICATION_BUFFER + timedelta(seconds=1), "Business")
        self.assertEqual(public_news_preselection_check(old, {}, now, "en", "business")[1], "source_stale")
        self.assertEqual(public_news_preselection_check(safe, {}, now, "en", "business")[1], "ok")
        self.assertGreater(now + PUBLICATION_VERIFICATION_BUFFER - datetime.fromisoformat(old["published_at"]), HOME_MAX_AGE)

    def test_preselection_excludes_exposure_expiring_during_verification(self):
        now = datetime(2026, 10, 20, 12, 13, tzinfo=timezone.utc)
        story = self._story("Exposure near expiry", now, "Business")
        from scripts.enforce_homepage_max_age import base
        identity = base.normalized_identity(story)
        state = {identity: {"first_seen_at": (now - HOME_MAX_AGE + timedelta(minutes=5)).isoformat()}}
        self.assertEqual(public_news_preselection_check(story, state, now, "en", "business")[1], "expired")

    def test_public_section_hard_cap_is_fifteen(self) -> None:
        now = datetime(2026, 10, 11, 7, 0, tzinfo=timezone.utc)
        stories = [self._story(f"Unique bulletin {i}", now) for i in range(28)]
        payload = {
            "home": [], "home_reserve": [], "sections": {"health": stories},
            "labels": {"health": "Health"}, "health": {},
        }
        result, _ = enforce_payload(payload, {}, now, lang="en")
        self.assertEqual(len(result["sections"]["health"]), 15)

    def test_refreshed_source_timestamp_cannot_reset_first_display_clock(self) -> None:
        now = datetime(2026, 8, 26, 18, 0, tzinfo=timezone.utc)
        story = self._story("Resurfaced story", now - timedelta(minutes=5))
        identity = "example.com/resurfaced-story"
        state = {
            identity: {
                "first_seen_at": (now - HOME_MAX_AGE - timedelta(seconds=1)).isoformat(),
                "source": "Example",
                "title": story["title"],
            }
        }
        payload = {
            "home": [story],
            "sections": {"health": [story]},
            "labels": {"health": "Health"},
            "health": {},
        }

        result, _ = enforce_payload(payload, state, now)
        self.assertEqual(result["home"], [])
        self.assertEqual(result["health"]["homepage_freshness"]["expired_exposure_rejected"], 1)

    def test_exactly_24_hours_is_allowed_and_one_second_more_is_rejected(self) -> None:
        first_now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
        story = self._story("Boundary story", first_now)
        state: dict = {}
        payload = {
            "home": [story],
            "sections": {"health": [story]},
            "labels": {"health": "Health"},
            "health": {},
        }
        result, state = enforce_payload(payload, state, first_now)
        self.assertEqual(len(result["home"]), 1)

        exact = first_now + HOME_MAX_AGE
        refreshed = dict(story, published_at=exact.isoformat())
        exact_payload = {
            "home": [refreshed],
            "sections": {"health": [refreshed]},
            "labels": {"health": "Health"},
            "health": {},
        }
        exact_result, state = enforce_payload(exact_payload, state, exact)
        self.assertEqual(len(exact_result["home"]), 1)

        late = exact + timedelta(seconds=1)
        refreshed_again = dict(story, published_at=late.isoformat())
        late_payload = {
            "home": [refreshed_again],
            "sections": {"health": [refreshed_again]},
            "labels": {"health": "Health"},
            "health": {},
        }
        late_result, _ = enforce_payload(late_payload, state, late)
        self.assertEqual(late_result["home"], [])

    def test_source_age_blocks_first_briefrooms_display_even_with_fresh_exposure(self) -> None:
        now = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
        story = self._story("Iga Wuhan stale article", datetime(2026, 10, 12, 8, 52, tzinfo=timezone.utc), "Sport")
        payload = {
            "home": [story],
            "home_reserve": [story],
            "sections": {"sport": [story]},
            "labels": {"sport": "Sport"},
            "health": {},
        }
        result, state = enforce_payload(payload, {}, now, lang="pl")
        self.assertEqual(result["sections"]["sport"], [])
        self.assertEqual(result["home"], [])
        self.assertEqual(result["home_reserve"], [])
        self.assertEqual(result["health"]["homepage_freshness"]["source_stale_rejected"], 1)
        self.assertEqual(state, {})
        self.assertEqual(result["homepage_policy"]["source_age_limit_hours"], None)
        self.assertEqual(result["homepage_policy"]["current_news_source_age_limit_hours"], 24)
        self.assertEqual(result["homepage_policy"]["current_news_same_day_timezone"], "Europe/Warsaw")
        self.assertEqual(result["homepage_policy"]["editorial_exception_source_age_limit_hours"], None)

    def test_polish_news_from_yesterday_remains_visible_within_24_hours(self) -> None:
        now = datetime(2026, 10, 15, 22, 15, tzinfo=timezone.utc)  # Oct 16 Warsaw
        story = self._story("Yesterday local", datetime(2026, 10, 15, 21, 30, tzinfo=timezone.utc), "Sport")
        result, _ = enforce_payload(
            {"home": [story], "sections": {"health": [story]}, "health": {}},
            {}, now, lang="pl",
        )
        self.assertEqual(len(result["home"]), 1)
        self.assertEqual(len(result["sections"]["health"]), 1)

    def test_polish_today_story_expires_after_24_hours(self) -> None:
        now = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
        story = self._story("Today Sport", now - timedelta(hours=1), "Sport")
        result, state = enforce_payload(
            {"home": [story], "sections": {"health": [story]}, "health": {}},
            {}, now, lang="pl",
        )
        self.assertEqual(len(result["home"]), 1)
        self.assertEqual(
            result["home"][0]["news_expires_at"], "2026-10-17T08:00:00+00:00",
        )
        after_midnight, _ = enforce_payload(
            {"home": [story], "sections": {"health": [story]}, "health": {}},
            state, datetime(2026, 10, 16, 22, 0, 1, tzinfo=timezone.utc), lang="pl",
        )
        self.assertEqual(len(after_midnight["home"]), 1)

    def test_en_source_age_expiry_shorter_than_first_display_window(self) -> None:
        now = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
        story = self._story("Older but within one day", now - timedelta(hours=23), "Business")
        result, _ = enforce_payload(
            {"home": [story], "sections": {"health": [story]}, "health": {}},
            {}, now, lang="en",
        )
        self.assertEqual(len(result["home"]), 1)
        self.assertEqual(
            result["home"][0]["news_expires_at"],
            (now + timedelta(hours=1)).isoformat(timespec="seconds"),
        )

    def test_science_health_ai_allow_old_sources_for_at_most_first_exposure_24h(self) -> None:
        now = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
        section_names = ("nauka", "zdrowie", "ai-technologia-krypto")
        for section in section_names:
            with self.subTest(section=section):
                story = self._story(f"Verified evergreen {section}", now - timedelta(days=30), "Science")
                payload = {
                    "sections": {section: [story]},
                    "home": [], "home_reserve": [], "health": {},
                }
                exposed, state = enforce_payload(
                    json.loads(json.dumps(payload)), {}, now, lang="pl",
                )
                self.assertEqual(len(exposed["sections"][section]), 1)
                row = exposed["sections"][section][0]
                self.assertEqual(row["news_freshness_profile"], "editorial_exception")
                self.assertEqual(
                    row["news_expires_at"], (now + timedelta(hours=24)).isoformat(timespec="seconds"),
                )
                after = now + timedelta(hours=24, seconds=1)
                expired, _ = enforce_payload(
                    json.loads(json.dumps(payload)), state, after, lang="pl",
                )
                self.assertEqual(expired["sections"][section], [])

    def test_stale_source_is_rejected_in_all_current_news_sections(self) -> None:
        now = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
        for section in ("polityka", "ekonomia", "sport"):
            with self.subTest(section=section):
                story = self._story(f"Old {section}", now - timedelta(days=4), "Health")
                payload = {"sections": {section: [story]}, "home": [], "health": {}}
                result, _ = enforce_payload(payload, {}, now, lang="pl")
                self.assertEqual(result["sections"][section], [])

    def test_english_science_health_ai_allow_old_source_not_old_exposure(self) -> None:
        now = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
        for section in ("science", "health", "ai-technology-crypto"):
            with self.subTest(section=section):
                story = self._story(f"Old verified EN {section}", now - timedelta(days=5), "Science")
                payload = {"sections": {section: [story]}, "home": [], "health": {}}
                fresh, state = enforce_payload(payload, {}, now, lang="en")
                self.assertEqual(len(fresh["sections"][section]), 1)
                late, _ = enforce_payload(
                    {"sections": {section: [story]}, "home": [], "health": {}},
                    state, now + timedelta(days=1, seconds=1), lang="en",
                )
                self.assertEqual(late["sections"][section], [])

    def test_homepage_editorial_exception_uses_first_exposure_not_source_timestamp(self) -> None:
        now = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
        story = self._story("Important old science discovery", now - timedelta(days=10), "Science")
        story["homepage_lane"] = "nauka"
        payload = {"home": [story], "sections": {"nauka": [story]}, "health": {}}
        result, state = enforce_payload(json.loads(json.dumps(payload)), {}, now, lang="pl")
        self.assertEqual(len(result["home"]), 1)
        self.assertEqual(result["home"][0]["news_freshness_profile"], "editorial_exception")
        self.assertEqual(result["home"][0]["news_expires_at"], (now + HOME_MAX_AGE).isoformat(timespec="seconds"))
        after, _ = enforce_payload(
            json.loads(json.dumps(payload)), state, now + HOME_MAX_AGE + timedelta(seconds=1), lang="pl",
        )
        self.assertEqual(after["home"], [])

    def test_missing_source_timestamp_is_not_fresh(self) -> None:
        now = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
        story = self._story("No verifiable timestamp", now)
        story["published_at"] = None
        result, _ = enforce_payload(
            {"home": [story], "sections": {"health": [story]}, "health": {}},
            {}, now, lang="pl",
        )
        self.assertEqual(result["home"], [])
        self.assertEqual(result["sections"]["health"], [])

    def test_expired_home_story_is_replaced_by_next_eligible_reserve_story(self) -> None:
        now = datetime(2026, 8, 26, 18, 0, tzinfo=timezone.utc)
        expired = self._story("Expired", now)
        replacement = self._story("Replacement", now - timedelta(hours=1))
        state = {
            "example.com/expired": {
                "first_seen_at": (now - timedelta(days=4)).isoformat(),
                "source": "Example",
                "title": "Expired",
            }
        }
        payload = {
            "home": [expired],
            "home_reserve": [replacement],
            "sections": {"health": [expired, replacement]},
            "labels": {"health": "Health"},
            "health": {},
        }

        result, _ = enforce_payload(payload, state, now)
        self.assertEqual([item["title"] for item in result["home"]], ["Replacement"])
        self.assertIn("homepage_first_seen_at", result["home"][0])
        self.assertIn("homepage_expires_at", result["home"][0])

    def test_homepage_fills_to_exactly_twelve_from_curated_reserve(self) -> None:
        now = datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc)
        initial = [self._story(f"Home {index}", now - timedelta(minutes=index), "Politics") for index in range(6)]
        replacements = [
            self._story(f"Replacement {index}", now - timedelta(minutes=20 + index), "Economy")
            for index in range(8)
        ]
        payload = {
            "home": initial,
            "home_reserve": replacements,
            "sections": {"politics": initial, "economy": replacements},
            "labels": {"politics": "Politics", "economy": "Economy"},
            "health": {},
        }

        result, _ = enforce_payload(payload, {}, now)
        self.assertEqual(HOME_LIMIT, 12)
        self.assertEqual(len(result["home"]), 12)
        self.assertEqual(len({item["link"] for item in result["home"]}), 12)
        self.assertTrue(all(item["image"].startswith("https://") for item in result["home"]))
        self.assertEqual(result["health"]["homepage_freshness"]["status"], "ok")
        self.assertEqual(result["homepage_policy"]["target_story_count"], 12)
        self.assertEqual(result["homepage_policy"]["max_display_hours"], 24)
        self.assertEqual(result["homepage_policy"]["minimum_story_count"], 0)
        self.assertTrue(result["homepage_policy"]["underfill_allowed_when_needed_for_freshness"])
        self.assertTrue(result["homepage_policy"]["requires_https_image"])
        self.assertEqual(result["homepage_policy"]["image_policy_version"], IMAGE_POLICY_VERSION)

    def test_freshness_reselection_restores_priority_lane_coverage(self) -> None:
        now = datetime(2026, 9, 19, 13, 0, tzinfo=timezone.utc)

        def lane_story(name: str, lane: str, rank: int, minutes: int, source: str) -> dict:
            story = self._story(name, now - timedelta(minutes=minutes), lane)
            story["homepage_lane"] = lane
            story["homepage_priority_rank"] = rank
            story["source"] = source
            return story

        health_expired = lane_story("Badanie profilaktyki serca", "zdrowie", 6, 20, "H0")
        home = [
            lane_story("Ustawa krajowa A", "polityka", 1, 1, "P1"),
            lane_story("Ustawa krajowa B", "polityka", 1, 2, "P2"),
            lane_story("Debata parlamentarna", "polityka", 1, 3, "P3"),
            lane_story("Sankcje wobec Rosji", "geopolityka", 2, 4, "G1"),
            lane_story("NATO wzmacnia wschodnią flankę", "geopolityka", 2, 5, "G2"),
            lane_story("Inflacja spada", "ekonomia", 3, 6, "E1"),
            lane_story("Bank centralny o stopach", "ekonomia", 3, 7, "E2"),
            lane_story("Eksport rośnie", "ekonomia", 3, 8, "E3"),
            lane_story("Nowy model sztucznej inteligencji", "ai_technologia", 4, 9, "A1"),
            lane_story("Nowe odkrycie astronomiczne", "nauka", 5, 10, "N1"),
            health_expired,
            lane_story("Polska wygrała mecz", "sport", 7, 12, "S1"),
        ]
        health_reserve = lane_story("Nowa terapia chorób serca", "zdrowie", 6, 30, "H1")
        reserve = [
            health_reserve,
            lane_story("Kolejna decyzja Sejmu", "polityka", 1, 31, "P4"),
            lane_story("Kolejny mecz reprezentacji", "sport", 7, 32, "S2"),
        ]
        state = {
            "example.com/badanie-profilaktyki-serca": {
                "first_seen_at": (now - HOME_MAX_AGE - timedelta(seconds=1)).isoformat(),
                "source": "H0",
                "title": health_expired["title"],
            }
        }
        payload = {
            "home": home,
            "home_reserve": reserve,
            "health": {},
        }

        result, _ = enforce_payload(payload, state, now)
        lanes = [item["homepage_lane"] for item in result["home"]]
        order = {
            lane: index
            for index, lane in enumerate(
                ("polityka", "geopolityka", "ekonomia", "ai_technologia", "nauka", "zdrowie", "sport")
            )
        }

        self.assertEqual(len(result["home"]), HOME_LIMIT)
        self.assertIn(health_reserve["title"], [item["title"] for item in result["home"]])
        self.assertIn("zdrowie", lanes)
        self.assertEqual(lanes, sorted(lanes, key=lambda lane: order[lane]))
        self.assertLessEqual(lanes.count("sport"), 3)
        freshness = result["health"]["homepage_freshness"]
        self.assertEqual(freshness["post_freshness_selection_version"], "post-freshness-editorial-v2")
        self.assertEqual(freshness["lane_mix"].get("zdrowie"), 1)

    def test_runtime_reserve_cannot_reintroduce_homepage_topic_duplicate(self) -> None:
        now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
        chosen_noise = self._story(
            "Eksperci: zmiany prawne to najskuteczniejszy sposób walki z hałasem w naszym otoczeniu",
            now - timedelta(minutes=10),
            "Health",
        )
        chosen_noise["source"] = "Nauka w Polsce"
        duplicate_noise = self._story(
            "Skąd się bierze hałas w miastach?",
            now - timedelta(minutes=20),
            "Health",
        )
        duplicate_noise["source"] = "Nauka w Polsce"
        water = self._story(
            "Prof. Rybicki: wyzwaniem są zarówno niedobory wody, jak i jej nadmiar",
            now - timedelta(minutes=30),
            "Science",
        )
        water["source"] = "Nauka w Polsce"

        distinct = [
            self._story("Inflacja spadła poniżej prognoz", now - timedelta(minutes=31), "Economy"),
            self._story("Parlament przyjął ustawę o cyberbezpieczeństwie", now - timedelta(minutes=32), "Politics"),
            self._story("Teleskop wykrył atmosferę odległej planety", now - timedelta(minutes=33), "Science"),
            self._story("Polska wygrała mecz kwalifikacyjny", now - timedelta(minutes=34), "Sport"),
            self._story("Bank centralny utrzymał stopy procentowe", now - timedelta(minutes=35), "Economy"),
            self._story("Robot laboratoryjny przyspiesza syntezę leków", now - timedelta(minutes=36), "Science"),
            self._story("Samorządy dostaną nowe finansowanie", now - timedelta(minutes=37), "Politics"),
            self._story("Tenisista awansował do finału turnieju", now - timedelta(minutes=38), "Sport"),
            self._story("Eksport przemysłowy przyspieszył", now - timedelta(minutes=39), "Economy"),
            self._story("Nowa metoda magazynowania energii", now - timedelta(minutes=40), "Science"),
            self._story("Program szczepień sezonowych rozszerzony", now - timedelta(minutes=41), "Health"),
        ]
        payload = {
            "home": [chosen_noise] + distinct,
            "home_reserve": [duplicate_noise, water],
            "sections": {"health": [duplicate_noise], "science": [water]},
            "labels": {"health": "Health", "science": "Science"},
            "health": {},
        }

        result, _ = enforce_payload(payload, {}, now)
        visible_titles = [item["title"] for item in result["home"]]
        reserve_titles = [item["title"] for item in result["home_reserve"]]

        self.assertEqual(len(result["home"]), HOME_LIMIT)
        self.assertNotIn(duplicate_noise["title"], visible_titles + reserve_titles)
        self.assertIn(water["title"], visible_titles + reserve_titles)
        self.assertLessEqual(len(result["home_reserve"]), HOME_RESERVE_LIMIT)
        self.assertEqual(
            result["homepage_policy"]["runtime_backfill_policy"],
            "approved_home_reserve_only",
        )

    def test_en_primary_central_bank_bulletin_is_homepage_day_of_release_only(self) -> None:
        release = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
        fomc = self._story("Federal Reserve issues FOMC statement", release, "Business")
        fomc["source"] = "Federal Reserve"
        fresh = [
            self._story(f"Fresh business story {index}", datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc) - timedelta(minutes=index), "Business")
            for index in range(14)
        ]
        payload = {
            "home": [fomc] + fresh[:11],
            "home_reserve": fresh[11:],
            "health": {},
        }

        same_day, _ = enforce_payload(
            json.loads(json.dumps(payload)),
            {},
            datetime(2026, 9, 16, 22, 0, tzinfo=timezone.utc),
            lang="en",
        )
        self.assertIn(fomc["title"], [item["title"] for item in same_day["home"]])

        next_day, _ = enforce_payload(
            json.loads(json.dumps(payload)),
            {},
            datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc),
            lang="en",
        )
        titles = [item["title"] for item in next_day["home"] + next_day["home_reserve"]]
        self.assertNotIn(fomc["title"], titles)
        self.assertGreaterEqual(
            next_day["health"]["homepage_freshness"]["primary_bulletin_same_day_rejected"],
            1,
        )

    def test_pl_homepage_does_not_apply_en_primary_bulletin_rule(self) -> None:
        release = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
        fomc = self._story("Federal Reserve issues FOMC statement", release, "Ekonomia")
        fomc["source"] = "Federal Reserve"
        payload = {
            "home": [fomc],
            "home_reserve": [],
            "health": {},
        }
        result, _ = enforce_payload(
            payload,
            {},
            datetime(2026, 9, 16, 19, 0, tzinfo=timezone.utc),
            lang="pl",
        )
        self.assertIn(fomc["title"], [item["title"] for item in result["home"]])

    def test_raw_sections_cannot_bypass_homepage_selection(self) -> None:
        now = datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc)
        initial = [self._story(f"Approved {index}", now - timedelta(minutes=index)) for index in range(4)]
        raw_sections = [
            self._story(f"Raw section {index}", now - timedelta(minutes=20 + index))
            for index in range(12)
        ]
        payload = {
            "home": initial,
            "home_reserve": [],
            "sections": {"health": raw_sections},
            "labels": {"health": "Health"},
            "health": {},
        }

        result, _ = enforce_payload(payload, {}, now)
        self.assertEqual([item["title"] for item in result["home"]], [item["title"] for item in initial])
        self.assertEqual(result["home_reserve"], [])
        self.assertEqual(result["health"]["homepage_freshness"]["status"], "underfilled")

    def test_missing_and_http_images_are_rejected_and_replaced(self) -> None:
        now = datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc)
        missing = dict(self._story("Missing image", now), image="")
        insecure = dict(self._story("HTTP image", now), image="http://images.example.com/http.jpg")
        valid = [self._story(f"Valid {index}", now - timedelta(minutes=index + 1)) for index in range(14)]
        payload = {
            "home": [missing, insecure] + valid[:4],
            "home_reserve": valid[4:],
            "sections": {"health": [missing, insecure] + valid},
            "labels": {"health": "Health"},
            "health": {},
        }

        result, _ = enforce_payload(payload, {}, now)
        self.assertEqual(len(result["home"]), 12)
        titles = {item["title"] for item in result["home"]}
        self.assertNotIn("Missing image", titles)
        self.assertNotIn("HTTP image", titles)
        self.assertTrue(all(item["image"].startswith("https://") for item in result["home"]))
        self.assertGreaterEqual(result["health"]["homepage_freshness"]["image_rejected"], 2)


if __name__ == "__main__":
    unittest.main()

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

    def test_exactly_72_hours_is_allowed_and_one_second_more_is_rejected(self) -> None:
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
        self.assertEqual(freshness["post_freshness_selection_version"], "post-freshness-editorial-v1")
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
            datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc),
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

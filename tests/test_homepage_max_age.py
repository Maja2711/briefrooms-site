from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.enforce_homepage_max_age import HOME_LIMIT, HOME_MAX_AGE, enforce_payload


class HomepageExposureCapTests(unittest.TestCase):
    def _story(self, name: str, published_at: datetime, category: str = "Health") -> dict:
        return {
            "title": name,
            "link": f"https://example.com/{name.lower().replace(' ', '-')}",
            "image": "https://example.com/image.jpg",
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

    def test_expired_home_story_is_replaced_by_next_eligible_section_story(self) -> None:
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
            "sections": {"health": [expired, replacement]},
            "labels": {"health": "Health"},
            "health": {},
        }

        result, _ = enforce_payload(payload, state, now)
        self.assertEqual([item["title"] for item in result["home"]], ["Replacement"])
        self.assertIn("homepage_first_seen_at", result["home"][0])
        self.assertIn("homepage_expires_at", result["home"][0])

    def test_homepage_fills_to_exactly_ten_from_section_backfill(self) -> None:
        now = datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc)
        initial = [self._story(f"Home {index}", now - timedelta(minutes=index), "Politics") for index in range(6)]
        replacements = [
            self._story(f"Replacement {index}", now - timedelta(minutes=20 + index), "Economy")
            for index in range(8)
        ]
        payload = {
            "home": initial,
            "sections": {"politics": initial, "economy": replacements},
            "labels": {"politics": "Politics", "economy": "Economy"},
            "health": {},
        }

        result, _ = enforce_payload(payload, {}, now)
        self.assertEqual(HOME_LIMIT, 10)
        self.assertEqual(len(result["home"]), 10)
        self.assertEqual(len({item["link"] for item in result["home"]}), 10)
        self.assertEqual(result["health"]["homepage_freshness"]["status"], "ok")
        self.assertEqual(result["homepage_policy"]["target_story_count"], 10)


if __name__ == "__main__":
    unittest.main()

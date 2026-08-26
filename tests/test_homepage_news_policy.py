from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import homepage_news_policy as policy


NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def story(title: str, section: str, age_hours: float, *, link: str | None = None) -> dict:
    return {
        "title": title,
        "link": link or f"https://example.com/{section}/{title.lower().replace(' ', '-')}",
        "image": "https://example.com/image.jpg",
        "source": "Example",
        "summary": title,
        "published_at": (NOW - timedelta(hours=age_hours)).isoformat(),
    }


class HomepageNewsPolicyTests(unittest.TestCase):
    def test_exact_72_hours_is_allowed_but_older_is_rejected(self) -> None:
        self.assertTrue(policy.is_home_fresh(story("edge", "x", 72), NOW))
        self.assertFalse(policy.is_home_fresh(story("old", "x", 72 + 1 / 3600), NOW))
        item = story("missing", "x", 1)
        item.pop("published_at")
        self.assertFalse(policy.is_home_fresh(item, NOW))

    def test_pl_home_leads_with_two_rounds_of_policy_economy_health(self) -> None:
        payload = {
            "labels": {"polityka": "Polityka / Kraj", "ekonomia": "Ekonomia / Biznes", "zdrowie": "Zdrowie", "sport": "Sport"},
            "sections": {
                "polityka": [story("P1", "p", 1), story("P2", "p", 2)],
                "ekonomia": [story("E1", "e", 1), story("E2", "e", 2)],
                "zdrowie": [story("Z1", "z", 1), story("Z2", "z", 2)],
                "sport": [story("S1", "s", 0.1)],
            },
            "home": [story("S1", "s", 0.1)],
        }
        selected = policy.select_live_home(payload, "pl", NOW)
        self.assertEqual([item["title"] for item in selected[:6]], ["P1", "E1", "Z1", "P2", "E2", "Z2"])
        self.assertEqual(selected[6]["title"], "S1")

    def test_en_home_prioritizes_world_business_health(self) -> None:
        payload = {
            "labels": {"world-news": "World News", "business": "Business", "health": "Health", "science": "Science"},
            "sections": {
                "world-news": [story("W1", "w", 1)],
                "business": [story("B1", "b", 1)],
                "health": [story("H1", "h", 1)],
                "science": [story("N1", "n", 0.1)],
            },
            "home": [story("N1", "n", 0.1)],
        }
        selected = policy.select_live_home(payload, "en", NOW)
        self.assertEqual([item["title"] for item in selected[:3]], ["W1", "B1", "H1"])

    def test_stale_home_story_never_returns_via_existing_home_order(self) -> None:
        stale = story("Old sport", "sport", 73)
        payload = {
            "labels": {"polityka": "Polityka / Kraj", "ekonomia": "Ekonomia / Biznes", "zdrowie": "Zdrowie", "sport": "Sport"},
            "sections": {"polityka": [], "ekonomia": [], "zdrowie": [], "sport": [stale]},
            "home": [stale],
        }
        self.assertEqual(policy.select_live_home(payload, "pl", NOW), [])

    def test_home_brief_filters_stale_and_promotes_target_categories(self) -> None:
        items = [
            {**story("Science", "science", 1), "category": "Nauka"},
            {**story("Health", "health", 1), "category": "Zdrowie"},
            {**story("Economy", "economy", 1), "category": "Ekonomia"},
            {**story("Politics", "politics", 1), "category": "Geopolityka"},
            {**story("Old", "politics", 80), "category": "Geopolityka"},
        ]
        selected = policy._prioritize_home_brief(items, "pl", NOW)
        self.assertEqual([item["title"] for item in selected], ["Politics", "Economy", "Health", "Science"])

    def test_static_enforcement_drops_cards_without_fresh_strict_permalink(self) -> None:
        fresh = {
            **story("Fresh", "politics", 1),
            "permalink": "/pl/briefy/fresh-aaaaaaaaaaaa.html",
            "comment_quality_status": policy.STRICT_STATUS,
            "comment_quality_version": policy.STRICT_VERSION,
            "summary_basis": policy.STRICT_BASIS,
            "comment_generation_status": policy.STRICT_GENERATION,
        }
        stale = {
            **story("Stale", "politics", 80),
            "permalink": "/pl/briefy/stale-bbbbbbbbbbbb.html",
            "comment_quality_status": policy.STRICT_STATUS,
            "comment_quality_version": policy.STRICT_VERSION,
            "summary_basis": policy.STRICT_BASIS,
            "comment_generation_status": policy.STRICT_GENERATION,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pl").mkdir()
            (root / "en").mkdir()
            (root / "pl/home_brief.json").write_text(__import__("json").dumps({"latest": [fresh, stale], "radar": []}), encoding="utf-8")
            (root / "en/home_brief.json").write_text(__import__("json").dumps({"latest": [], "radar": []}), encoding="utf-8")
            (root / "pl/index.html").write_text(
                f'{policy.HOME_BRIEFS_START}<a class="brief-card" href="{fresh["permalink"]}">Fresh</a><a class="brief-card" href="{stale["permalink"]}">Stale</a>{policy.HOME_BRIEFS_END}',
                encoding="utf-8",
            )
            (root / "en/index.html").write_text(f"{policy.HOME_BRIEFS_START}<a class=\"brief-card\" href=\"/en/briefs/old-cccccccccccc.html\">Old</a>{policy.HOME_BRIEFS_END}", encoding="utf-8")
            with patch.object(policy, "ROOT", root):
                policy.enforce_static(NOW)
            pl = (root / "pl/index.html").read_text(encoding="utf-8")
            en = (root / "en/index.html").read_text(encoding="utf-8")
        self.assertIn(fresh["permalink"], pl)
        self.assertNotIn(stale["permalink"], pl)
        self.assertNotIn("old-cccccccccccc", en)


if __name__ == "__main__":
    unittest.main()

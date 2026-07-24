from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from validate_news_home_publish import ALLOWED_HOMEPAGE_COUNTS, assert_fresh, parse_datetime


class NewsHomePublishValidationTests(unittest.TestCase):
    def test_timezone_aware_timestamp_is_normalized(self) -> None:
        value = parse_datetime("2026-07-24T21:54+02:00")
        self.assertEqual(value.isoformat(), "2026-07-24T19:54:00+00:00")

    def test_stale_timestamp_fails_closed(self) -> None:
        now = datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc)
        with self.assertRaisesRegex(AssertionError, "is stale"):
            assert_fresh(now - timedelta(hours=7), now, 360, "feed")

    def test_recent_timestamp_passes(self) -> None:
        now = datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc)
        assert_fresh(now - timedelta(minutes=359), now, 360, "feed")

    def test_only_eight_or_ten_homepage_briefs_are_allowed(self) -> None:
        self.assertEqual(ALLOWED_HOMEPAGE_COUNTS, {8, 10})
        self.assertNotIn(9, ALLOWED_HOMEPAGE_COUNTS)
        self.assertNotIn(11, ALLOWED_HOMEPAGE_COUNTS)


if __name__ == "__main__":
    unittest.main()

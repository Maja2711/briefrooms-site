from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from protect_home_feed import merge, selected_files, validate_current


def item(number: int) -> dict:
    return {
        "title": f"Story {number}",
        "link": f"https://example.com/{number}",
        "source": "Example",
    }


class ExactHomepageCountTests(unittest.TestCase):
    def merge_count(self, count: int) -> int:
        current = {"latest": [item(i) for i in range(count)], "radar": []}
        with patch("protect_home_feed.cards", side_effect=lambda data, section, lang: data.get(section, [])):
            result = merge(current, {}, "en")
        return result["count"]

    def test_eight_stays_eight(self) -> None:
        self.assertEqual(self.merge_count(8), 8)

    def test_nine_is_normalized_to_eight(self) -> None:
        self.assertEqual(self.merge_count(9), 8)

    def test_ten_stays_ten(self) -> None:
        self.assertEqual(self.merge_count(10), 10)

    def test_surplus_is_normalized_to_ten(self) -> None:
        self.assertEqual(self.merge_count(14), 10)

    def test_language_scoped_validation_does_not_touch_other_language(self) -> None:
        self.assertEqual([lang for lang, _ in selected_files("en")], ["en"])

    def test_incomplete_refresh_is_completed_from_last_good_before_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_path = root / "current.json"
            backup_path = root / "backup.json"
            current_path.write_text(
                json.dumps({"latest": [item(i) for i in range(7)], "radar": []}),
                encoding="utf-8",
            )
            backup_path.write_text(
                json.dumps({"latest": [item(i) for i in range(6, 14)], "radar": []}),
                encoding="utf-8",
            )

            with (
                patch(
                    "protect_home_feed.FILES",
                    {"en": (current_path, backup_path)},
                ),
                patch(
                    "protect_home_feed.cards",
                    side_effect=lambda data, section, lang: data.get(section, []),
                ),
            ):
                self.assertTrue(validate_current(lang="en"))

            result = json.loads(current_path.read_text(encoding="utf-8"))
            self.assertEqual(8, result["count"])
            self.assertEqual(8, len(result["latest"]))
            self.assertEqual(8, len({entry["link"] for entry in result["latest"]}))


if __name__ == "__main__":
    unittest.main()

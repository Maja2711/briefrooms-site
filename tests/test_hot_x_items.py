from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import hot_x_items as hot  # noqa: E402
import update_hot_x_topics as generator  # noqa: E402
import validate_hot_x_comments as comments  # noqa: E402


LONG_PL = (
    "Komentarz wyjaśnia konkretny kontekst wydarzenia, najważniejsze fakty oraz możliwe konsekwencje dla odbiorców. "
    "Zawiera pełne zdania i wystarczająco dużo informacji, aby karta była czytelna bez otwierania źródła."
)
LONG_EN = (
    "This comment explains the event context, the most important facts and the possible consequences for readers. "
    "It uses complete sentences and enough information to make the card useful before opening the source."
)


def make_item(index: int, category: str | None = None) -> dict:
    category = category or f"category-{index}"
    return {
        "category": category,
        "label_pl": category.upper(),
        "label_en": category.upper(),
        "title_pl": f"Unikalny polski temat numer {index}",
        "title_en": f"Unique English topic number {index}",
        "comment_pl": LONG_PL,
        "comment_en": LONG_EN,
        "summary_pl": LONG_PL,
        "summary_en": LONG_EN,
        "tweet_url": f"https://x.com/briefrooms/status/{100 + index}?utm_source=test",
        "search_url": f"https://x.com/search?q=unique%20topic%20{index}&src=typed_query&f=top",
        "image": "/assets/hot-x/topic-news.svg",
    }


class HotXSelectionTests(unittest.TestCase):
    def test_selects_eight_unique_items_and_strips_tracking(self) -> None:
        selected = hot.select_unique([[make_item(i) for i in range(10)]])
        self.assertEqual(len(selected), 8)
        self.assertTrue(hot.duplicate_free(selected))
        self.assertNotIn("utm_source", selected[0]["tweet_url"])
        self.assertNotIn("src=", selected[1]["search_url"])

    def test_three_repeated_topics_collapse_to_one(self) -> None:
        repeated = [make_item(i) for i in range(3)]
        for index, item in enumerate(repeated):
            item["title_pl"] = "Ten sam temat"
            item["title_en"] = "The same topic"
            item["search_url"] = f"https://x.com/search?q=same%20topic&src=run-{index}&f=top"
        self.assertEqual(len(hot.select_unique([repeated])), 1)

    def test_category_limit_is_two(self) -> None:
        selected = hot.select_unique([[make_item(i, "crypto") for i in range(6)]])
        self.assertEqual(len(selected), 2)

    def test_missing_language_is_rejected(self) -> None:
        missing_pl = make_item(1)
        missing_pl["comment_pl"] = missing_pl["summary_pl"] = ""
        missing_en = make_item(2)
        missing_en["comment_en"] = missing_en["summary_en"] = ""
        self.assertFalse(hot.valid_item(missing_pl))
        self.assertFalse(hot.valid_item(missing_en))

    def test_search_link_without_concrete_post_is_rejected(self) -> None:
        item = make_item(20)
        item["tweet_url"] = ""
        self.assertFalse(hot.valid_item(item))
        self.assertEqual(hot.item_url(item), "")

    def test_generator_walks_from_current_into_following_slots(self) -> None:
        original_slots = generator.TOPIC_SLOTS
        original_builder = generator.build_item
        try:
            generator.TOPIC_SLOTS = [
                [{"query": f"slot-{slot}-topic-{index}", "category": f"c-{slot}-{index}"} for index in range(3)]
                for slot in range(3)
            ]

            def build(topic: dict, slot: int) -> dict:
                index = int(topic["query"].rsplit("-", 1)[-1]) + slot * 3
                item = make_item(index, topic["category"])
                return item

            generator.build_item = build
            selected = generator.build_current_items(1)
            self.assertEqual(len(selected), 8)
            self.assertEqual(selected[0]["category"], "c-1-0")
            self.assertEqual(selected[3]["category"], "c-2-0")
            self.assertEqual(selected[6]["category"], "c-0-0")
        finally:
            generator.TOPIC_SLOTS = original_slots
            generator.build_item = original_builder

    def test_search_only_emergency_feed_is_not_publishable_as_x_posts(self) -> None:
        emergency = json.loads((ROOT / "data" / "hot_x_emergency.json").read_text(encoding="utf-8"))
        selected = hot.select_unique([emergency["items"]], substantive=True)
        self.assertEqual(len(selected), 0)

    def test_failed_update_keeps_last_good_and_fills_to_eight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            original_paths = comments.DATA, comments.BACKUP, comments.EMERGENCY
            comments.DATA = base / "hot_tweets.json"
            comments.BACKUP = base / "last_good.json"
            comments.EMERGENCY = base / "emergency.json"
            try:
                comments.DATA.write_text('{"items": []}', encoding="utf-8")
                comments.BACKUP.write_text(
                    json.dumps({"items": [make_item(0), make_item(1)]}, ensure_ascii=False),
                    encoding="utf-8",
                )
                comments.EMERGENCY.write_text(
                    json.dumps({"items": [make_item(i) for i in range(2, 10)]}, ensure_ascii=False),
                    encoding="utf-8",
                )
                comments.validate()
                output = json.loads(comments.DATA.read_text(encoding="utf-8"))
                self.assertEqual(len(output["items"]), 8)
                self.assertEqual(output["last_good_protection"]["status"], "generic_or_partial_update_completed_from_last_good")
                self.assertTrue(hot.duplicate_free(output["items"]))
            finally:
                comments.DATA, comments.BACKUP, comments.EMERGENCY = original_paths

    def test_two_new_cards_are_publishable_and_not_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            original_paths = comments.DATA, comments.BACKUP, comments.EMERGENCY
            comments.DATA = base / "hot_tweets.json"
            comments.BACKUP = base / "last_good.json"
            comments.EMERGENCY = base / "emergency.json"
            try:
                comments.DATA.write_text(json.dumps({"items": [make_item(0), make_item(1)]}), encoding="utf-8")
                comments.BACKUP.write_text('{"items": []}', encoding="utf-8")
                comments.EMERGENCY.write_text(
                    json.dumps({"items": [make_item(i) for i in range(2, 10)]}),
                    encoding="utf-8",
                )
                comments.validate()
                output = json.loads(comments.DATA.read_text(encoding="utf-8"))
                self.assertGreaterEqual(len(output["items"]), 2)
                self.assertEqual(output["last_good_protection"]["status"], "new_comments_validated")
            finally:
                comments.DATA, comments.BACKUP, comments.EMERGENCY = original_paths

    def test_duplicate_new_cards_do_not_satisfy_visible_minimum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            original_paths = comments.DATA, comments.BACKUP, comments.EMERGENCY
            comments.DATA = base / "hot_tweets.json"
            comments.BACKUP = base / "last_good.json"
            comments.EMERGENCY = base / "emergency.json"
            try:
                duplicate_a = make_item(0)
                duplicate_b = dict(duplicate_a)
                duplicate_b["search_url"] += "&src=another-run"
                comments.DATA.write_text(json.dumps({"items": [duplicate_a, duplicate_b]}), encoding="utf-8")
                comments.BACKUP.write_text(json.dumps({"items": [make_item(3), make_item(4)]}), encoding="utf-8")
                comments.EMERGENCY.write_text(
                    json.dumps({"items": [make_item(i) for i in range(5, 13)]}),
                    encoding="utf-8",
                )
                comments.validate()
                output = json.loads(comments.DATA.read_text(encoding="utf-8"))
                self.assertEqual(
                    output["last_good_protection"]["status"],
                    "generic_or_partial_update_completed_from_last_good",
                )
            finally:
                comments.DATA, comments.BACKUP, comments.EMERGENCY = original_paths

    def test_daily_gate_counts_only_fresh_direct_posts(self) -> None:
        now = comments.datetime.now(comments.timezone.utc)
        values = [make_item(i) for i in range(4)]
        for index, item in enumerate(values):
            item["tweet_url"] = f"https://x.com/briefrooms/status/{900 + index}"
            item["x_post_created_at"] = now.isoformat()
        self.assertEqual(len(comments.fresh_direct_posts(values, now)), 4)
        values[0]["tweet_url"] = ""
        values[1]["x_post_created_at"] = "2020-01-01T00:00:00+00:00"
        self.assertEqual(len(comments.fresh_direct_posts(values, now)), 2)


if __name__ == "__main__":
    unittest.main()

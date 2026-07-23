import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from hot_x_items import (  # noqa: E402
    INITIAL_VISIBLE_ITEMS,
    clean_x_url,
    duplicate_free,
    is_direct_post,
    is_editorial_search,
    item_url,
    select_unique,
    valid_item,
)


def direct(url="https://x.com/example/status/123456789"):
    return {
        "category": "markets",
        "title_pl": "Polski tytuł rynkowy",
        "title_en": "English market title",
        "comment_pl": "To jest wystarczająco długi, konkretny komentarz redakcyjny dotyczący bezpośredniego postu na platformie X.",
        "comment_en": "This is a sufficiently long and substantive editorial comment concerning a direct public post on the X platform.",
        "tweet_url": url,
        "search_url": "",
    }


def pin(url="https://x.com/search?q=AI%20sovereignty%20Europe&f=live"):
    return {
        "category": "ai",
        "title_pl": "Suwerenność AI w Europie",
        "title_en": "AI sovereignty in Europe",
        "comment_pl": "Ręcznie przypięty temat redakcyjny prowadzi do bieżącej dyskusji i nie może zostać utworzony automatycznie bez jawnej flagi.",
        "comment_en": "A manually pinned editorial topic leads to the current discussion and cannot be generated automatically without an explicit flag.",
        "tweet_url": "",
        "search_url": url,
        "editorial_pin": True,
        "link_kind": "x_search",
    }


class HotXItemsTests(unittest.TestCase):
    def test_four_cards_are_visible_initially(self):
        self.assertEqual(INITIAL_VISIBLE_ITEMS, 4)

    def test_direct_post_is_accepted(self):
        item = direct()
        self.assertTrue(is_direct_post(item["tweet_url"]))
        self.assertTrue(valid_item(item, substantive=True))
        self.assertEqual(item_url(item), "https://x.com/example/status/123456789")

    def test_generic_search_link_is_rejected(self):
        item = pin()
        item.pop("editorial_pin")
        self.assertFalse(is_editorial_search(item))
        self.assertFalse(valid_item(item))

    def test_explicit_editorial_search_is_accepted(self):
        item = pin()
        self.assertTrue(is_editorial_search(item))
        self.assertTrue(valid_item(item, substantive=True))
        self.assertIn("q=AI+sovereignty+Europe", item_url(item))

    def test_tracking_parameters_are_removed(self):
        cleaned = clean_x_url("https://twitter.com/example/status/123456789?ref_src=x&utm_source=test")
        self.assertEqual(cleaned, "https://x.com/example/status/123456789")

    def test_pins_are_ordered_before_direct_posts(self):
        selected = select_unique([[direct(), pin()]], target=2)
        self.assertTrue(selected[0].get("editorial_pin"))
        self.assertTrue(is_direct_post(selected[1].get("tweet_url")))

    def test_duplicate_search_is_rejected(self):
        first = pin()
        second = pin()
        second["title_pl"] = "Inny tytuł"
        second["title_en"] = "Different title"
        selected = select_unique([[first, second]], target=2)
        self.assertEqual(len(selected), 1)
        self.assertTrue(duplicate_free(selected))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import enforce_homepage_photo_only as photo_only


def marker_cards(source: str) -> list[str]:
    match = re.search(
        rf"{re.escape(photo_only.START)}(.*?){re.escape(photo_only.END)}",
        source,
        re.S,
    )
    if not match:
        raise AssertionError("homepage markers missing")
    return re.findall(r'<a class="brief-card" href="[^"]+">.*?</a>', match.group(1), re.S)


class HomepagePhotoOnlyTests(unittest.TestCase):
    def test_repository_homepages_have_only_source_linked_photo_cards(self) -> None:
        for lang in ("pl", "en"):
            source = (ROOT / lang / "index.html").read_text(encoding="utf-8")
            cards = marker_cards(source)
            self.assertGreaterEqual(len(cards), 1, lang)
            self.assertIn('data-home-photo-only="true"', source, lang)
            self.assertIn('/scripts/homepage-photo-only.js?v=1', source, lang)
            for card in cards:
                self.assertTrue(photo_only.photo_card(card), f"{lang}: {card[:120]}")
                self.assertIn('class="thumb has-image"', card)
                self.assertIn('data-br-external-media="source-linked"', card)
                self.assertNotIn("media-fallback-active", card)

    def test_filter_fails_closed_instead_of_reducing_the_card_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.html"
            cards = [
                (
                    '<a class="brief-card" href="/pl/briefy/photo-'
                    f'{index:012x}.html"><div class="thumb has-image">'
                    '<img src="https://example.com/a.jpg" '
                    'data-br-external-media="source-linked"></div><div>Text</div></a>'
                )
                for index in range(7)
            ]
            cards.append(
                '<a class="brief-card" href="/pl/briefy/no-photo-ffffffffffff.html">'
                '<div class="thumb media-fallback-active"></div><div>Text</div></a>'
            )
            original = (
                '<!doctype html><html><body><div id="latest-briefs" class="brief-grid">'
                f'{photo_only.START}' + ''.join(cards) +
                f'{photo_only.END}</div></body></html>'
            )
            path.write_text(original, encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "refusing to create an invalid"):
                photo_only.process(path)
            self.assertEqual(path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()

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
    return photo_only.CARD_RE.findall(match.group(1))


class HomepagePhotoOnlyTests(unittest.TestCase):
    def test_ai_outlook_names_probability_event_and_direction(self) -> None:
        script = (ROOT / "scripts" / "homepage-photo-only.js").read_text(encoding="utf-8")
        self.assertIn("probability_event", script)
        self.assertIn("Prawdopodobieństwo", script)
        self.assertIn("Perspektywa", script)
        self.assertIn("Pewność oceny", script)
        self.assertIn("assessment_perspective", script)
        self.assertIn("assessment_confidence", script)
        self.assertIn("Wniosek AI", script)
        self.assertIn("Ocena kierunku", script)
        self.assertIn("direction.scenarios", script)
        for lang in ("pl", "en"):
            source = (ROOT / lang / "index.html").read_text(encoding="utf-8")
            self.assertIn("homepage-photo-only.js?v=ai-outlook-direction-2", source)

    def test_repository_homepages_have_only_source_linked_photo_cards(self) -> None:
        for lang in ("pl", "en"):
            source = (ROOT / lang / "index.html").read_text(encoding="utf-8")
            cards = marker_cards(source)
            self.assertGreaterEqual(len(cards), 1, lang)
            self.assertIn('data-home-photo-only="true"', source, lang)
            self.assertIn(photo_only.FRESHNESS_SCRIPT, source, lang)
            self.assertIn(photo_only.GUARD_SCRIPT, source, lang)
            self.assertIn(photo_only.SCRIPT, source, lang)
            self.assertEqual(len(photo_only.FRESHNESS_RE.findall(source)), 1, lang)
            self.assertEqual(len(photo_only.GUARD_RE.findall(source)), 1, lang)
            self.assertEqual(len(photo_only.SCRIPT_RE.findall(source)), 1, lang)
            self.assertLess(source.index(photo_only.FRESHNESS_SCRIPT), source.index(photo_only.GUARD_SCRIPT), lang)
            self.assertLess(source.index(photo_only.GUARD_SCRIPT), source.index(photo_only.SCRIPT), lang)
            for card in cards:
                self.assertTrue(photo_only.photo_card(card), f"{lang}: {card[:120]}")
                self.assertIn('data-br-external-media="source-linked"', card)
                self.assertNotIn("media-fallback-active", card)

    def test_production_card_attributes_are_recognized(self) -> None:
        card = (
            '<a class="brief-card" href="https://example.com/story" '
            'target="_blank" rel="noopener noreferrer external">'
            '<div class="thumb has-image">'
            '<img src="https://example.com/image.jpg" '
            'data-br-external-media="source-linked">'
            '</div></a>'
        )
        self.assertEqual(photo_only.CARD_RE.findall(card), [card])
        self.assertTrue(photo_only.photo_card(card))

    def test_runtime_replaces_older_asset_versions_and_orders_freshness_first(self) -> None:
        source = (
            '<!doctype html><html><body>'
            '<div id="latest-briefs" class="brief-grid"></div>'
            '<script src="/scripts/ai-outlook-freshness-guard.js?v=old" defer></script>'
            '<script src="/scripts/ai-outlook-governance-guard.js?v=old" defer></script>'
            '<script src="/scripts/homepage-photo-only.js?v=1" defer></script>'
            '</body></html>'
        )
        updated = photo_only.ensure_runtime(source)
        self.assertIn(photo_only.FRESHNESS_SCRIPT, updated)
        self.assertIn(photo_only.GUARD_SCRIPT, updated)
        self.assertIn(photo_only.SCRIPT, updated)
        self.assertNotIn('homepage-photo-only.js?v=1', updated)
        self.assertNotIn('ai-outlook-governance-guard.js?v=old', updated)
        self.assertNotIn('ai-outlook-freshness-guard.js?v=old', updated)
        self.assertEqual(len(photo_only.FRESHNESS_RE.findall(updated)), 1)
        self.assertEqual(len(photo_only.GUARD_RE.findall(updated)), 1)
        self.assertEqual(len(photo_only.SCRIPT_RE.findall(updated)), 1)
        self.assertLess(updated.index(photo_only.FRESHNESS_SCRIPT), updated.index(photo_only.GUARD_SCRIPT))
        self.assertLess(updated.index(photo_only.GUARD_SCRIPT), updated.index(photo_only.SCRIPT))

    def test_filter_fails_closed_instead_of_reducing_the_card_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.html"
            cards = [
                (
                    '<a class="brief-card" href="/pl/briefy/photo-'
                    f'{index:012x}.html" target="_blank" rel="noopener">'
                    '<div class="thumb has-image">'
                    '<img src="https://example.com/a.jpg" '
                    'data-br-external-media="source-linked"></div><div>Text</div></a>'
                )
                for index in range(7)
            ]
            cards.append(
                '<a class="brief-card" href="/pl/briefy/no-photo-ffffffffffff.html" '
                'target="_blank" rel="noopener">'
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

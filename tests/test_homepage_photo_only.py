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
    def test_ai_outlook_renderer_is_retained_but_not_mounted_on_redesigned_homepage(self) -> None:
        script = (ROOT / "scripts" / "homepage-photo-only.js").read_text(encoding="utf-8")
        self.assertIn("probability_event", script)
        self.assertIn("Prawdopodobieństwo", script)
        self.assertIn("assessment_confidence", script)
        for lang in ("pl", "en"):
            source = (ROOT / lang / "index.html").read_text(encoding="utf-8")
            self.assertIn('class="br-home"', source)
            self.assertNotIn("homepage-photo-only.js?v=ai-outlook-direction-2", source)
            self.assertNotIn("ai-outlook-governance-guard.js", source)
            self.assertNotIn("ai-outlook-freshness-guard.js", source)

    def test_repository_homepages_remain_photo_first(self) -> None:
        for lang in ("pl", "en"):
            source = (ROOT / lang / "index.html").read_text(encoding="utf-8")
            cards = marker_cards(source)
            self.assertGreaterEqual(len(cards), 1, lang)
            self.assertIn('data-home-photo-only="true"', source, lang)
            for card in cards:
                self.assertTrue(photo_only.visual_card(card), f"{lang}: {card[:120]}")
                self.assertNotIn("media-fallback-active", card)

    def test_legacy_source_linked_card_contract_is_still_recognized(self) -> None:
        card = (
            '<a class="brief-card" href="https://example.com/story" '
            'target="_blank" rel="noopener noreferrer external">'
            '<div class="thumb has-image">'
            '<img src="https://example.com/image.jpg" '
            'data-br-external-media="source-linked">'
            '</div></a>'
        )
        self.assertEqual(photo_only.CARD_RE.findall(card), [card])
        self.assertTrue(photo_only.visual_card(card))
        self.assertTrue(photo_only.photo_card(card))

    def test_redesigned_runtime_does_not_restore_legacy_ai_outlook_scripts(self) -> None:
        source = (
            '<!doctype html><html><body class="br-home">'
            '<div id="latest-briefs" class="brief-grid br-news-grid"></div>'
            '<script src="/scripts/ai-outlook-freshness-guard.js?v=old" defer></script>'
            '<script src="/scripts/ai-outlook-governance-guard.js?v=old" defer></script>'
            '<script src="/scripts/homepage-photo-only.js?v=1" defer></script>'
            '</body></html>'
        )
        updated = photo_only.ensure_runtime(source)
        self.assertIn('data-home-photo-only="true"', updated)
        self.assertNotIn('homepage-photo-only.js', updated)
        self.assertNotIn('ai-outlook-governance-guard.js', updated)
        self.assertNotIn('ai-outlook-freshness-guard.js', updated)

    def test_redesigned_filter_accepts_static_photo_fallback_without_legacy_source_attribute(self) -> None:
        source = (
            '<!doctype html><html><body class="br-home">'
            '<div id="latest-briefs" class="brief-grid">'
            f'{photo_only.START}'
            '<a class="brief-card" href="/pl/briefy/photo-aaaaaaaaaaaa.html">'
            '<div class="thumb has-image"><img src="https://example.com/a.jpg"></div>'
            '<div>Text</div></a>'
            f'{photo_only.END}</div></body></html>'
        )
        self.assertEqual(photo_only.filter_marker_block(source, "test"), source)

    def test_legacy_filter_still_fails_closed_instead_of_reducing_card_count(self) -> None:
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

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGES = (
    ("pl", ROOT / "pl/inwestycje/portfel-10k.html", ROOT / "data/investments/portfolio_10k.json"),
    ("en", ROOT / "en/investing/portfolio-10k.html", ROOT / "data/investments/portfolio_10k_usd.json"),
)


class InvestmentRoomStaticSeoTests(unittest.TestCase):
    def test_pages_are_indexable_and_have_one_static_snapshot(self) -> None:
        for lang, page, _ in PAGES:
            source = page.read_text(encoding="utf-8")
            self.assertIn('name="robots" content="index,follow,max-image-preview:large,max-snippet:-1"', source, lang)
            self.assertEqual(source.count("portfolio-static-snapshot:start"), 1, lang)
            self.assertEqual(source.count("portfolio-static-snapshot:end"), 1, lang)
            self.assertEqual(source.count("portfolio-static-schema:start"), 1, lang)
            self.assertEqual(source.count("portfolio-static-schema:end"), 1, lang)

    def test_initial_html_contains_every_active_holding_and_current_metrics(self) -> None:
        for lang, page, data_path in PAGES:
            source = page.read_text(encoding="utf-8")
            payload = json.loads(data_path.read_text(encoding="utf-8"))
            block = re.search(
                r"portfolio-static-snapshot:start -->(.*?)<!-- portfolio-static-snapshot:end",
                source,
                flags=re.DOTALL,
            )
            self.assertIsNotNone(block, lang)
            snapshot = block.group(1)
            active = [position for position in payload["positions"] if position.get("status") == "active"]
            self.assertGreaterEqual(len(active), 1, lang)
            for position in active:
                self.assertIn(position["broker_symbol"], snapshot, f"{lang}: {position['broker_symbol']}")
            self.assertIn(str(len(active)), snapshot, lang)
            self.assertNotIn("Ładowanie", snapshot, lang)
            self.assertNotIn("Loading", snapshot, lang)

    def test_structured_data_points_to_the_public_json_dataset(self) -> None:
        for lang, page, _ in PAGES:
            source = page.read_text(encoding="utf-8")
            match = re.search(
                r'portfolio-static-schema:start --><script type="application/ld\+json">(.*?)</script>',
                source,
            )
            self.assertIsNotNone(match, lang)
            schema = json.loads(match.group(1))
            self.assertEqual(schema["@type"], "WebPage")
            self.assertEqual(schema["mainEntity"]["@type"], "Dataset")
            self.assertTrue(schema["mainEntity"]["distribution"]["contentUrl"].endswith(".json"))


if __name__ == "__main__":
    unittest.main()


from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GeopoliticsLibraryOrderTest(unittest.TestCase):
    def test_static_library_is_newest_first(self):
        html = (ROOT / "pl" / "geopolityka.html").read_text(encoding="utf-8")
        block = re.search(r'<ul class="tiles">(.*?)</ul>', html, re.S)
        self.assertIsNotNone(block)
        dates = re.findall(r'<time class="tile-date" datetime="([^"]+)"', block.group(1))
        normalized = [d + "-01-01" if re.fullmatch(r"\d{4}", d) else d for d in dates]
        self.assertEqual(normalized, sorted(normalized, reverse=True))

    def test_gse_runtime_does_not_mutate_editorial_tiles(self):
        js = (ROOT / "scripts" / "gse-lab-entry.js").read_text(encoding="utf-8")
        forbidden = (
            ".library .tiles",
            "list.prepend(",
            "list.appendChild(",
            "insertBefore(",
            "ensureFeaturedArticles",
        )
        for token in forbidden:
            self.assertNotIn(token, js, msg=f"GSE runtime must not mutate Geopolitics library: {token}")


if __name__ == "__main__":
    unittest.main()

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

    def test_gse_runtime_reference_is_unique(self):
        for relative in ("pl/geopolityka.html", "en/geopolitics.html"):
            html = (ROOT / relative).read_text(encoding="utf-8")
            refs = re.findall(r'/scripts/gse-lab-entry\.js\?v=(\d+)', html)
            self.assertEqual(refs, ["5"], msg=f"{relative} must load exactly one current GSE runtime")

        sync = (ROOT / "scripts" / "sync_gse_lab_geopolitics_pages.py").read_text(encoding="utf-8")
        self.assertIn('gse-lab-entry.js?v=5', sync)
        self.assertIn('SCRIPT_RE.sub("", cleaned)', sync)

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

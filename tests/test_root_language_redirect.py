from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"


class RootLanguageRedirectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_redirect_runs_before_visible_document(self) -> None:
        script_position = self.html.index("function languageTarget()")
        body_position = self.html.index("<body>")
        self.assertLess(script_position, body_position)

    def test_document_is_hidden_during_redirect(self) -> None:
        self.assertIn('data-language-redirect="pending"', self.html)
        self.assertIn(
            'html[data-language-redirect="pending"] body{ visibility:hidden; }',
            self.html,
        )

    def test_supported_languages_and_default(self) -> None:
        self.assertIn('return "/pl/";', self.html)
        self.assertIn('return "/en/";', self.html)
        self.assertIn("navigator.languages", self.html)

    def test_redirect_preserves_query_and_hash(self) -> None:
        self.assertIn('window.location.search || ""', self.html)
        self.assertIn('window.location.hash || ""', self.html)
        self.assertIn("window.location.replace(destination)", self.html)

    def test_no_javascript_fallback_remains_available(self) -> None:
        self.assertIn('<noscript><meta http-equiv="refresh" content="0; url=/pl/"></noscript>', self.html)
        self.assertIn('href="/pl/"', self.html)
        self.assertIn('href="/en/"', self.html)


if __name__ == "__main__":
    unittest.main()

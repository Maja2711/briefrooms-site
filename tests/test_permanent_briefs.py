from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import generate_permanent_briefs as permanent


PL_COMMENT = (
    "Władze potwierdziły najważniejsze fakty dotyczące wydarzenia i wskazały jego bezpośrednie "
    "konsekwencje. Decyzja wpłynie na dalsze działania instytucji oraz sytuację osób, których "
    "dotyczy. Kolejne szczegóły mają zostać przedstawione po zakończeniu oficjalnych rozmów."
)
EN_COMMENT = (
    "Officials confirmed the central facts of the event and described its immediate consequences. "
    "The decision will affect the next steps taken by institutions and the people involved. Further "
    "details are expected after the official talks have concluded."
)


def approved_item(
    lang: str = "pl",
    *,
    url: str | None = None,
    title: str | None = None,
    full_brief: str | None = None,
    image: str = "https://images.example.com/news.jpg",
) -> dict:
    return {
        "title": title or ("Żółć i Łódź: ważna decyzja" if lang == "pl" else "A major decision is confirmed"),
        "link": url or f"https://news.example.com/{lang}/article?utm_source=test",
        "image": image,
        "category": "Polityka" if lang == "pl" else "World",
        "source": "Example News",
        "published_at": "2026-07-18T14:18:00+00:00",
        "full_brief": full_brief or (PL_COMMENT if lang == "pl" else EN_COMMENT),
        "comment_quality_status": "passed_strict_v7",
        "comment_quality_version": 7,
        "summary_basis": "article_text_ai_reviewed",
        "comment_generation_status": "ai_review_approved",
    }


class PermanentBriefTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "pl").mkdir()
        (self.root / "en").mkdir()
        (self.root / "data").mkdir()
        (self.root / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            "  <url><loc>https://briefrooms.com/pl/</loc></url>\n"
            "  <url><loc>https://briefrooms.com/pl/brief.html?u=legacy</loc></url>\n"
            "</urlset>\n",
            encoding="utf-8",
        )
        self.now = datetime(2026, 7, 18, 16, 30, tzinfo=timezone.utc)
        self.write_home("pl", [approved_item("pl")])
        self.write_home("en", [approved_item("en")])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_home(self, lang: str, latest: list[dict], radar: list[dict] | None = None) -> None:
        payload = {
            "updated_at": "2026-07-18T14:20:00+00:00",
            "latest": latest,
            "radar": radar or [],
        }
        (self.root / lang / "home_brief.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def run_generator(self, now: datetime | None = None) -> dict:
        return permanent.generate_all(self.root, now or self.now)

    def home(self, lang: str) -> dict:
        return json.loads((self.root / lang / "home_brief.json").read_text(encoding="utf-8"))

    def archive(self, lang: str) -> list[dict]:
        suffix = "pl" if lang == "pl" else "en"
        payload = json.loads(
            (self.root / "data" / f"permanent_briefs_{suffix}.json").read_text(encoding="utf-8")
        )
        return payload["items"]

    def page(self, lang: str) -> tuple[Path, str, dict]:
        record = self.archive(lang)[0]
        path = self.root / record["permalink"].lstrip("/")
        return path, path.read_text(encoding="utf-8"), record

    def json_ld(self, page_html: str) -> dict:
        match = re.search(
            r'<script type="application/ld\+json">(.*?)</script>', page_html, flags=re.DOTALL
        )
        self.assertIsNotNone(match)
        return json.loads(match.group(1))

    def test_same_canonical_url_has_stable_id(self) -> None:
        first = permanent.brief_id_for_url("https://Example.com/story?utm_source=a&id=7#part")
        second = permanent.brief_id_for_url("https://example.com/story?id=7&utm_medium=b")
        self.assertEqual(first, second)

    def test_brief_id_is_twelve_hex_characters(self) -> None:
        self.assertRegex(permanent.brief_id_for_url("https://example.com/a"), r"^[0-9a-f]{12}$")

    def test_slug_removes_polish_characters(self) -> None:
        self.assertEqual(
            permanent.slugify("Żółć, Łódź i źdźbło — ĄĘĆŃÓŚŹŻ!"),
            "zolc-lodz-i-zdzblo-aecnoszz",
        )

    def test_slug_is_bounded_and_path_safe(self) -> None:
        slug = permanent.slugify("../" + "Bardzo długi tytuł " * 20)
        self.assertLessEqual(len(slug), 80)
        self.assertRegex(slug, r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
        self.assertNotIn("..", slug)

    def test_similar_titles_with_different_urls_have_different_ids(self) -> None:
        self.assertNotEqual(
            permanent.brief_id_for_url("https://example.com/story-one"),
            permanent.brief_id_for_url("https://example.com/story-two"),
        )

    def test_pl_and_en_use_language_specific_paths(self) -> None:
        self.run_generator()
        self.assertTrue(self.archive("pl")[0]["permalink"].startswith("/pl/briefy/"))
        self.assertTrue(self.archive("en")[0]["permalink"].startswith("/en/briefs/"))

    def test_approved_home_items_receive_all_stable_fields(self) -> None:
        self.run_generator()
        for lang in ("pl", "en"):
            item = self.home(lang)["latest"][0]
            self.assertTrue(item["brief_id"])
            self.assertTrue(item["slug"])
            self.assertTrue(item["permalink"])

    def test_old_brief_survives_source_removal(self) -> None:
        self.run_generator()
        path, original_html, record = self.page("pl")
        self.write_home("pl", [])
        self.run_generator(datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc))
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), original_html)
        self.assertIn(record["permalink"], {item["permalink"] for item in self.archive("pl")})

    def test_content_change_updates_date_modified(self) -> None:
        self.run_generator()
        old = self.archive("pl")[0]
        changed = approved_item("pl", full_brief=PL_COMMENT + " Nowe dane zmieniają ocenę sytuacji.")
        self.write_home("pl", [changed])
        later = datetime(2026, 7, 19, 8, 15, tzinfo=timezone.utc)
        self.run_generator(later)
        new = self.archive("pl")[0]
        self.assertNotEqual(old["content_hash"], new["content_hash"])
        self.assertEqual(new["date_modified"], "2026-07-19T08:15:00+00:00")

    def test_unchanged_content_keeps_hash_modified_date_and_html(self) -> None:
        self.run_generator()
        path, page_before, old = self.page("pl")
        self.run_generator(datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc))
        new = self.archive("pl")[0]
        self.assertEqual(old["content_hash"], new["content_hash"])
        self.assertEqual(old["date_modified"], new["date_modified"])
        self.assertEqual(page_before, path.read_text(encoding="utf-8"))

    def test_original_date_published_is_preserved_after_update(self) -> None:
        self.run_generator()
        original = self.archive("pl")[0]["date_published"]
        changed = approved_item("pl", full_brief=PL_COMMENT + " Dodano potwierdzoną informację.")
        changed["published_at"] = "2026-07-20T12:00:00+00:00"
        self.write_home("pl", [changed])
        self.run_generator(datetime(2026, 7, 20, 13, 0, tzinfo=timezone.utc))
        self.assertEqual(self.archive("pl")[0]["date_published"], original)

    def test_page_has_individual_title(self) -> None:
        self.run_generator()
        _, page_html, record = self.page("pl")
        self.assertIn(f"<title>{record['title']} | BriefRooms</title>", page_html)

    def test_page_has_bounded_individual_description(self) -> None:
        self.run_generator()
        _, page_html, record = self.page("pl")
        self.assertTrue(1 <= len(record["description"]) <= 158)
        self.assertIn(f'<meta name="description" content="{record["description"]}"', page_html)

    def test_description_does_not_cut_a_word(self) -> None:
        description = permanent.meta_description("słowo " * 100)
        self.assertLessEqual(len(description), 158)
        self.assertTrue(description.endswith("…"))
        self.assertNotIn("słow…", description)

    def test_page_has_full_canonical(self) -> None:
        self.run_generator()
        _, page_html, record = self.page("pl")
        self.assertIn(
            f'<link rel="canonical" href="https://briefrooms.com{record["permalink"]}"', page_html
        )

    def test_page_has_complete_open_graph(self) -> None:
        self.run_generator()
        _, page_html, record = self.page("pl")
        for name in ("og:type", "og:title", "og:description", "og:image", "og:url", "og:site_name"):
            self.assertIn(f'property="{name}"', page_html)
        self.assertIn(f'https://briefrooms.com{record["permalink"]}', page_html)

    def test_page_has_complete_twitter_card(self) -> None:
        self.run_generator()
        _, page_html, _ = self.page("en")
        for name in ("twitter:card", "twitter:title", "twitter:description", "twitter:image"):
            self.assertIn(f'name="{name}"', page_html)
        self.assertIn('content="summary_large_image"', page_html)

    def test_page_has_news_article_json_ld(self) -> None:
        self.run_generator()
        _, page_html, record = self.page("en")
        data = self.json_ld(page_html)
        self.assertEqual(data["@type"], "NewsArticle")
        self.assertEqual(data["headline"], record["title"])
        self.assertEqual(data["mainEntityOfPage"]["@id"], f"https://briefrooms.com{record['permalink']}")

    def test_json_ld_uses_organizational_authors(self) -> None:
        self.run_generator()
        _, pl_html, _ = self.page("pl")
        _, en_html, _ = self.page("en")
        self.assertEqual(self.json_ld(pl_html)["author"]["name"], "Redakcja BriefRooms")
        self.assertEqual(self.json_ld(en_html)["author"]["name"], "BriefRooms Editorial Team")

    def test_page_has_published_and_modified_dates(self) -> None:
        self.run_generator()
        _, page_html, record = self.page("pl")
        self.assertIn(f'content="{record["date_published"]}"', page_html)
        self.assertIn(f'content="{record["date_modified"]}"', page_html)
        self.assertIn("Opublikowano: 18.07.2026, 16:18", page_html)

    def test_en_page_has_exact_visible_date(self) -> None:
        self.run_generator()
        _, page_html, _ = self.page("en")
        self.assertIn("Published: 18 Jul 2026, 16:18", page_html)

    def test_sitemap_contains_both_permalinks(self) -> None:
        self.run_generator()
        sitemap = (self.root / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn(self.archive("pl")[0]["permalink"], sitemap)
        self.assertIn(self.archive("en")[0]["permalink"], sitemap)

    def test_sitemap_removes_legacy_query_urls_and_duplicates(self) -> None:
        self.run_generator()
        sitemap_path = self.root / "sitemap.xml"
        sitemap = sitemap_path.read_text(encoding="utf-8")
        self.assertNotIn("brief.html?u=", sitemap)
        root = ET.fromstring(sitemap_path.read_bytes())
        namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locations = [node.text for node in root.findall("sm:url/sm:loc", namespace)]
        self.assertEqual(len(locations), len(set(locations)))

    def test_unapproved_comment_blocks_generation(self) -> None:
        for field, bad_value in (
            ("comment_quality_status", "rejected"),
            ("comment_quality_version", 6),
            ("summary_basis", "rss"),
            ("comment_generation_status", "pending"),
            ("full_brief", ""),
        ):
            with self.subTest(field=field):
                item = approved_item("pl")
                item[field] = bad_value
                self.write_home("pl", [item])
                self.run_generator()
                self.assertEqual(self.archive("pl"), [])
                self.assertNotIn("permalink", self.home("pl")["latest"][0])

    def test_unapproved_reappearance_cannot_overwrite_archived_brief(self) -> None:
        self.run_generator()
        path, old_html, old_record = self.page("pl")
        rejected = approved_item("pl", full_brief="Gorsza, niezatwierdzona wersja.")
        rejected["comment_quality_status"] = "rejected"
        self.write_home("pl", [rejected])
        self.run_generator(datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(path.read_text(encoding="utf-8"), old_html)
        self.assertEqual(self.archive("pl")[0], old_record)

    def test_html_escaping_blocks_script_injection(self) -> None:
        item = approved_item(
            "pl",
            title='Tytuł </title><script>alert("x")</script>',
            full_brief='Opis <script>alert("x")</script> i bezpieczna dalsza treść.',
        )
        self.write_home("pl", [item])
        self.run_generator()
        _, page_html, _ = self.page("pl")
        self.assertNotIn('<script>alert("x")</script>', page_html)
        self.assertIn("&lt;script&gt;", page_html)
        self.assertEqual(self.json_ld(page_html)["headline"], item["title"])

    def test_invalid_source_url_is_skipped_without_blocking_valid_item(self) -> None:
        invalid = approved_item("pl", url="javascript:alert(1)", title="Niebezpieczny URL")
        valid = approved_item("pl", url="https://example.com/valid", title="Poprawny URL")
        self.write_home("pl", [invalid, valid])
        stats = self.run_generator()
        self.assertEqual(len(self.archive("pl")), 1)
        self.assertEqual(self.archive("pl")[0]["title"], "Poprawny URL")
        self.assertTrue(stats["errors"])

    def test_missing_or_data_image_uses_existing_default(self) -> None:
        item = approved_item("en", image="data:image/svg+xml,unsafe")
        self.write_home("en", [item])
        self.run_generator()
        _, page_html, record = self.page("en")
        self.assertEqual(record["image"], "https://briefrooms.com/assets/logo.svg")
        self.assertIn(record["image"], page_html)

    def test_languages_render_matching_content_and_labels(self) -> None:
        self.run_generator()
        _, pl_html, _ = self.page("pl")
        _, en_html, _ = self.page("en")
        self.assertIn('<html lang="pl">', pl_html)
        self.assertIn("Sedno sprawy", pl_html)
        self.assertIn('<html lang="en">', en_html)
        self.assertIn("Core point", en_html)

    def test_urgent_technical_categories_are_not_exposed(self) -> None:
        item = approved_item("en")
        item["category"] = "Breaking"
        self.write_home("en", [item])
        self.run_generator()
        _, page_html, record = self.page("en")
        self.assertEqual(record["category"], "World / news")
        self.assertIn('<span class="tag">World / news</span>', page_html)

    def test_missing_published_at_falls_back_to_home_updated_at(self) -> None:
        item = approved_item("pl")
        item.pop("published_at")
        self.write_home("pl", [item])
        self.run_generator()
        self.assertEqual(self.archive("pl")[0]["date_published"], "2026-07-18T14:20:00+00:00")

    def test_title_change_keeps_original_permalink_for_same_source(self) -> None:
        self.run_generator()
        old = self.archive("pl")[0]
        changed = approved_item("pl", title="Całkowicie zmieniony tytuł", full_brief=PL_COMMENT + " Uzupełnienie.")
        self.write_home("pl", [changed])
        self.run_generator(datetime(2026, 7, 19, 9, 0, tzinfo=timezone.utc))
        new = self.archive("pl")[0]
        self.assertEqual(new["permalink"], old["permalink"])
        self.assertEqual(new["slug"], old["slug"])

    def test_duplicate_source_url_creates_one_archive_record(self) -> None:
        first = approved_item("pl", url="https://example.com/one?utm_source=a")
        second = approved_item("pl", url="https://example.com/one?utm_medium=b", title="Inny tytuł")
        self.write_home("pl", [first], [second])
        self.run_generator()
        self.assertEqual(len(self.archive("pl")), 1)
        items = [*self.home("pl")["latest"], *self.home("pl")["radar"]]
        self.assertEqual(items[0]["permalink"], items[1]["permalink"])

    def test_legacy_pages_check_archive_before_current_feed(self) -> None:
        for lang, archive_name in (("pl", "permanent_briefs_pl.json"), ("en", "permanent_briefs_en.json")):
            source = (ROOT / lang / "brief.html").read_text(encoding="utf-8")
            archive_fetch = source.index(f"/data/{archive_name}")
            home_fetch = source.index(f"/{lang}/home_brief.json", archive_fetch)
            self.assertLess(archive_fetch, home_fetch)
            self.assertIn("location.replace(found.permalink)", source)

    def test_legacy_pages_show_clear_missing_archive_message(self) -> None:
        pl = (ROOT / "pl/brief.html").read_text(encoding="utf-8")
        en = (ROOT / "en/brief.html").read_text(encoding="utf-8")
        self.assertIn(
            "Ten brief nie został jeszcze zapisany w archiwum BriefRooms. Możesz otworzyć artykuł źródłowy.",
            pl,
        )
        self.assertIn(
            "This brief has not yet been saved in the BriefRooms archive. You can open the source article.",
            en,
        )


if __name__ == "__main__":
    unittest.main()

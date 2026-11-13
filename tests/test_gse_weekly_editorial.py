"""Offline GSE weekly editorial tests: provenance, licensing, immutability, PL/EN."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from scripts import gse_weekly_editorial as g

DAY = date(2026, 11, 15)
WEEK = "2026-W46"


def source(name, sid, text, primary=False):
    return {
        "id": sid, "organization": name,
        "url": "https://news.un.org/en/story/2026/11/12345" if primary else (
            "https://www.bbc.com/news/world-12345" if name == "BBC News" else "https://www.theguardian.com/world/2026/nov/14/ukraine"),
        "title": "Ukraine security developments discussed by governments",
        "summary": text, "published_at": "2026-11-14T14:00:00+00:00",
        "primary": primary,
    }


SOURCES = [
    source("UN News", "S1", "Ukraine security discussions continued during a multilateral diplomatic meeting.", True),
    source("BBC News", "S2", "Ukraine officials and foreign representatives discussed security issues during the week."),
    source("The Guardian", "S3", "Ukraine security and diplomacy remained a focus of multiple public statements."),
]
PHOTO = {
    "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/aa/Ships.jpg/1200px-Ships.jpg",
    "page": "https://commons.wikimedia.org/wiki/File:Ships.jpg",
    "author": "Example Photographer", "license": "cc by-sa 4.0",
    "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
    "alt": "Cargo vessel", "caption_pl": "Fotografia ilustracyjna, nie dokumentacja omawianego zdarzenia.",
    "caption_en": "Illustrative photograph, not documentation of the reported event.",
}


def draft():
    result = {}
    for lang in ("pl", "en"):
        sections = []
        for idx, angle in enumerate(g.ANGLES):
            sid = SOURCES[idx % 3]["id"]
            quote = SOURCES[idx % 3]["title"][:35]
            sections.append({
                "id": angle, "heading": "Ocena sytuacji geopolitycznej" if lang == "pl" else "Geopolitical situation assessment",
                "text": (("Sytuacja wokół Ukrainy zmienia priorytety strategiczne. "
                          "Nie należy utożsamiać wydarzeń dyplomatycznych z pewnością eskalacji. "
                          "Kolejne oficjalne decyzje i deklaracje pomogą odróżnić trwały zwrot od krótkiego epizodu. ")
                         if lang == "pl" else
                         ("Developments around Ukraine affect strategic priorities. "
                          "Diplomatic developments must not be confused with certain escalation. "
                          "Subsequent official decisions and statements will help distinguish a lasting change from a short episode. ")) * 2,
                "source_ids": [sid, SOURCES[(idx + 1) % 3]["id"]],
                "anchors": [{"source_id": sid, "quote": quote}],
            })
        result[lang] = {
            "title": ("Ukraina i bezpieczeństwo Europy: co zmieniła dyplomacja w tym tygodniu?"
                      if lang == "pl" else "Ukraine and European security: what this week changed for diplomacy"),
            "dek": ("Analiza potwierdzonych doniesień dyplomatycznych i warunków, które mogłyby zmienić ocenę ryzyka w regionie."
                    if lang == "pl" else "Analysis of sourced diplomatic reporting and the conditions that could change the regional risk outlook."),
            "lead": (("Dostępne źródła wskazują na kontynuację rozmów dotyczących Ukrainy. "
                      "Nie ma podstaw do przypisywania pojedynczej wypowiedzi przewidywanego wyniku konfliktu. "
                      "Kluczowe są dalsze decyzje i fakty.")
                     if lang == "pl" else
                     ("Available sources indicate continuing discussions about Ukraine. "
                      "A single statement cannot establish the likely outcome of the conflict. "
                      "Subsequent decisions and verified events are essential.")),
            "lead_source_ids": ["S1", "S2"],
            "sections": sections,
        }
    return result


def setup(root: Path):
    for path in ("pl/geopolityka.html", "en/geopolitics.html"):
        dest = root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text('<html><head></head><body><main><section class="library"><ul class="tiles"><li>EXISTING BOOK</li></ul></section></main></body></html>', encoding="utf-8")
    gse = root / "data/gse/gse_v2_lab_public.json"
    gse.parent.mkdir(parents=True, exist_ok=True)
    gse.write_text(json.dumps({
        "schema_version": "gse-v2-public-lab-v3",
        "generated_at": "2026-11-15T06:00:00Z",
        "public_boundary": {"read_only_projection": True},
        "engine": {"mode": "shadow", "decision_influence": False},
        "featured_thesis": {
            "freshness": "fresh", "research_only": True,
            "scenario_types": ["russia_ukraine_black_sea_escalation"]
        }
    }), encoding="utf-8")
    return gse


class GSEWeeklyEditorialTests(unittest.TestCase):
    def test_gse_public_contract_and_topic(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            original = setup(root)
            _, digest, topics = g.load_projection(DAY, root)
            self.assertEqual(topics, ["russia_ukraine_black_sea_escalation"])
            self.assertEqual(len(digest), 64)
            self.assertIn("generated_at", original.read_text())
            topic, selected = g.select_topic(topics, [dict(x) for x in SOURCES], [], DAY)
            self.assertEqual(topic, topics[0])
            self.assertEqual(len(selected), 3)
            self.assertTrue(any(s["primary"] for s in selected))
            self.assertEqual({s["id"] for s in selected}, {"S1", "S2", "S3"})

    def test_freshness_and_authority_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = setup(root)
            value = json.loads(p.read_text())
            value["generated_at"] = "2026-10-01T00:00:00Z"
            p.write_text(json.dumps(value))
            with self.assertRaises(g.EditorialBlocked):
                g.load_projection(DAY, root)
            value["generated_at"] = "2026-11-15T06:00:00Z"
            value["engine"]["decision_influence"] = True
            p.write_text(json.dumps(value))
            with self.assertRaises(g.EditorialBlocked):
                g.load_projection(DAY, root)

    def test_requires_multiple_credible_sources_and_avoids_repetition(self):
        topics = ["russia_ukraine_black_sea_escalation"]
        with self.assertRaises(g.EditorialBlocked):
            g.select_topic(topics, [dict(SOURCES[0])], [], DAY)
        with self.assertRaises(g.EditorialBlocked):
            g.select_topic(topics, [dict(x) for x in SOURCES], [{"week": "2026-W45", "topic": topics[0]}], DAY)
        with self.assertRaises(g.EditorialBlocked):
            g.select_topic(topics, [dict(x) for x in SOURCES if not x["primary"]], [], DAY)
        # An old issue must not block a recurring conflict forever.
        topic, _ = g.select_topic(topics, [dict(x) for x in SOURCES],
                                  [{"week": "2026-W40", "topic": topics[0]}], DAY)
        self.assertEqual(topic, topics[0])

    def test_commons_license_and_photo_attribution_gate(self):
        metadata = {
            "query": {"pages": {"13": {
                "title": "File:Ships.jpg", "index": 1,
                "imageinfo": [{
                    "thumburl": PHOTO["url"], "width": 2200, "height": 1400, "mime": "image/jpeg",
                    "extmetadata": {
                        "Artist": {"value": "Example Photographer"},
                        "LicenseShortName": {"value": "CC BY-SA 4.0"},
                        "LicenseUrl": {"value": PHOTO["license_url"]},
                    },
                }]
            }}}
        }
        with patch.object(g, "http_json", return_value=metadata):
            image = g.pick_commons_photo("russia_ukraine_black_sea_escalation", DAY)
        self.assertEqual(image["license"], "cc by-sa 4.0")
        self.assertIn("ilustracyjna", image["caption_pl"])
        metadata["query"]["pages"]["13"]["imageinfo"][0]["extmetadata"]["LicenseShortName"]["value"] = "CC BY-NC 4.0"
        with patch.object(g, "http_json", return_value=metadata):
            with self.assertRaises(g.EditorialBlocked):
                g.pick_commons_photo("russia_ukraine_black_sea_escalation", DAY)

    def test_forged_evidence_anchor_blocks_article(self):
        copied = draft()
        copied["pl"]["sections"][0]["anchors"][0]["quote"] = "NOT FOUND IN ANY SOURCE"
        with self.assertRaises(g.EditorialBlocked):
            g.validate_draft(copied, SOURCES)
        copied = draft()
        copied["en"]["lead_source_ids"] = ["S2"]
        with self.assertRaises(g.EditorialBlocked):
            g.validate_draft(copied, SOURCES)

    def test_end_to_end_no_engine_mutation_and_frozen_archive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gse = setup(root)
            original_gse = gse.read_bytes()
            for lang in ("pl", "en"):
                self.assertNotIn(g.START, (root / ("pl/geopolityka.html" if lang == "pl" else "en/geopolitics.html")).read_text())
            with patch.object(g, "collect_evidence", return_value=[dict(x) for x in SOURCES]), \
                 patch.object(g, "pick_commons_photo", return_value=dict(PHOTO)), \
                 patch.object(g, "gemini_generate", return_value=(draft(), {"review_approved": True, "provider": "gemini"})):
                paths = g.run(DAY, root)
                self.assertEqual(len(paths), 6)
                self.assertEqual(g.run(DAY, root), [f"SKIPPED: {WEEK} already frozen"])
            self.assertEqual(gse.read_bytes(), original_gse)
            manifest = json.loads((root / f"data/geo_weekly/issues/{WEEK}.json").read_text())
            self.assertEqual(manifest["gse_public_sha256"], g.hashlib.sha256(original_gse).hexdigest())
            self.assertEqual(manifest["photo"]["author"], PHOTO["author"])
            for lang in ("pl", "en"):
                room = (root / ("pl/geopolityka.html" if lang == "pl" else "en/geopolitics.html")).read_text()
                article = (root / lang / "geo" / f"briefing-{WEEK}.html").read_text()
                self.assertEqual(room.count(g.START), 1)
                self.assertEqual(room.count(g.ARCHIVE_START), 1)
                self.assertIn("EXISTING BOOK", room)
                self.assertIn(f"/{lang}/geo/briefing-{WEEK}.html", room)
                self.assertIn(PHOTO["url"], article)
                self.assertIn(PHOTO["license_url"], article)
                self.assertIn(PHOTO["author"], article)
                self.assertIn('id="evidence-S1"', article)
                self.assertIn("Fotografia ilustracyjna" if lang == "pl" else "Illustrative photograph", article)

    def test_failed_source_or_model_does_not_change_any_publication_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            setup(root)
            before = {lang: (root / ("pl/geopolityka.html" if lang == "pl" else "en/geopolitics.html")).read_bytes()
                      for lang in ("pl", "en")}
            with patch.object(g, "collect_evidence", return_value=[dict(SOURCES[0])]):
                with self.assertRaises(g.EditorialBlocked):
                    g.run(DAY, root)
            self.assertFalse((root / "data/geo_weekly/index.json").exists())
            self.assertFalse((root / f"data/geo_weekly/issues/{WEEK}.json").exists())
            for lang in ("pl", "en"):
                self.assertEqual((root / ("pl/geopolityka.html" if lang == "pl" else "en/geopolitics.html")).read_bytes(), before[lang])

    def test_real_geopolitics_room_preserves_books_and_gse_lab(self):
        for lang, path in (("pl", g.ROOT / "pl/geopolityka.html"),
                           ("en", g.ROOT / "en/geopolitics.html")):
            original = path.read_text(encoding="utf-8")
            updated = g.patch_room(
                original, g.weekly_block(draft()[lang], lang, WEEK, PHOTO),
                g.weekly_archive_tiles(lang, [{
                    "week": WEEK, "title_pl": draft()["pl"]["title"],
                    "title_en": draft()["en"]["title"],
                    "dek_pl": draft()["pl"]["dek"], "dek_en": draft()["en"]["dek"],
                    "photo_url": PHOTO["url"], "published_at": "2026-11-15T09:17:00+01:00",
                }])
            )
            self.assertIn("BR_GSE_LAB_ENTRY_START", updated)
            self.assertEqual(updated.count("BR_GSE_LAB_ENTRY_START"), original.count("BR_GSE_LAB_ENTRY_START"))
            self.assertIn("GSE Lab", updated)
            self.assertEqual(updated.count('<section class="library"'), original.count('<section class="library"'))
            self.assertEqual(updated.count('class="tile"'), original.count('class="tile"'))
            self.assertIn("geo/briefing-"+WEEK+".html", updated)
            self.assertEqual(updated.count(g.ARCHIVE_START), 1)
            self.assertIn("Wikimedia Commons", updated)

    def test_gemini_failure_leaves_all_public_artifacts_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            original = setup(root)
            pages_before = {
                lang: (root / ("pl/geopolityka.html" if lang == "pl" else "en/geopolitics.html")).read_bytes()
                for lang in ("pl", "en")
            }
            with patch.object(g, "collect_evidence", return_value=[dict(x) for x in SOURCES]), \
                 patch.object(g, "pick_commons_photo", return_value=dict(PHOTO)), \
                 patch.object(g, "gemini_generate", side_effect=g.EditorialBlocked("Gemini review failed")):
                with self.assertRaisesRegex(g.EditorialBlocked, "Gemini review failed"):
                    g.run(DAY, root)
            self.assertFalse((root / "data/geo_weekly/index.json").exists())
            self.assertFalse((root / f"data/geo_weekly/issues/{WEEK}.json").exists())
            self.assertEqual(original.read_bytes(), (root / "data/gse/gse_v2_lab_public.json").read_bytes())
            for lang in ("pl", "en"):
                page = root / ("pl/geopolityka.html" if lang == "pl" else "en/geopolitics.html")
                self.assertEqual(page.read_bytes(), pages_before[lang])

    def test_html_escaping_and_safe_domains(self):
        self.assertFalse(g.safe_https("http://upload.wikimedia.org/a.jpg", ("upload.wikimedia.org",)))
        self.assertFalse(g.safe_https('https://upload.wikimedia.org/a.jpg"onerror="x', ("upload.wikimedia.org",)))
        self.assertFalse(g.safe_https("https://upload.wikimedia.org.evil.test/image.jpg", ("upload.wikimedia.org",)))
        edition = draft()["pl"]
        edition["sections"][0]["text"] = "<script>alert(1)</script> foo " + edition["sections"][0]["text"]
        with self.assertRaises(g.EditorialBlocked):
            g.validate_draft({"pl": edition, "en": draft()["en"]}, SOURCES)


if __name__ == "__main__":
    unittest.main()

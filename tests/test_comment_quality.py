from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import comment_quality as quality
import protect_home_feed as protect
import content_update_watchdog as watchdog
import remove_urgent_badge_categories as category_cleanup
sys.modules.setdefault("requests", mock.Mock())
sys.modules.setdefault("feedparser", mock.Mock())
if "dateutil" not in sys.modules:
    dateutil_stub = types.ModuleType("dateutil")
    dateutil_stub.tz = mock.Mock()
    dateutil_stub.tz.gettz.return_value = timezone.utc
    sys.modules["dateutil"] = dateutil_stub
import read_and_summarize_articles as reader
import enforce_brief_length as methodology
import build_home_brief_en as home_en
import build_home_brief_pl as home_pl
import fetch_news_en as news_en
import fetch_news_pl as news_pl
import news_comment_batch as news_batch
import validate_brief_quality as gate


VALID_PL = (
    "Władze stanu Nowy Jork wprowadziły dwuletnie moratorium na budowę największych centrów danych wykorzystywanych przez systemy sztucznej inteligencji. "
    "Decyzja ma ograniczyć ryzyko przeciążenia sieci energetycznej oraz wzrostu rachunków ponoszonych przez mieszkańców. "
    "Moratorium poparły organizacje ekologiczne i część polityków, którzy domagają się jasnych gwarancji dotyczących kosztów oraz bezpieczeństwa dostaw energii."
)

BROKEN_PRODUCTION_PL = (
    'Jak podkrelia, centra danych AI "zuywaj ogromne iloci energii, realnie gro c przeci eniem sieci" i podnosz rachunki za pr d. '
    "Zadeklarowaa take, e nie dopuci do przerzucania tych kosztw na mieszkańcw. "
    '"To rzeczywicie budzi obawy o bezpieczeństwo" Tech Hochul: spoeczeństwo potrzebuje elaznych gwarancji Decyzj Hochul popary organizacje ekologiczne i cz politykw Partii Demokratycznej. '
    "Senatorka Kirsten Gillibrand ocenia, e w moratorium chodzi przede wszystkim o zaufanie spoeczne."
)

VALID_EN = (
    "New York state introduced a two-year moratorium on construction of the largest data centers used for artificial intelligence. "
    "The measure is intended to reduce the risk of overloading the power grid and raising electricity bills for residents. "
    "Environmental groups and several elected officials supported the decision while calling for firm guarantees on costs and energy security."
)


def approved_item(text: str, lang: str = "pl") -> dict:
    return {
        "title": "Testowy artykuł" if lang == "pl" else "Test article",
        "source": "Test Source",
        "link": "https://example.com/article",
        "full_brief": text,
        "summary_basis": "article_text_ai_reviewed",
        "comment_generation_status": "ai_review_approved",
        "comment_review_digest": quality.review_digest(text),
        "comment_quality_status": quality.QUALITY_STATUS,
        "comment_quality_version": quality.QUALITY_VERSION,
    }


class CommentQualityTests(unittest.TestCase):
    class FakeHttpResponse:
        def __init__(self, content: bytes, encoding: str, apparent_encoding: str):
            self.content = content
            self.encoding = encoding
            self.apparent_encoding = apparent_encoding
            self.text = content.decode(encoding, errors="replace")

    def test_valid_polish_comment_passes(self):
        result = quality.validate_comment(VALID_PL, "pl")
        self.assertTrue(result.valid, result.reasons)
        self.assertEqual(3, len(result.sentences))

    def test_exact_production_regression_is_rejected(self):
        result = quality.validate_comment(BROKEN_PRODUCTION_PL, "pl")
        self.assertFalse(result.valid)
        self.assertTrue({"broken_polish_word", "orphan_polish_letters"} & set(result.reasons))

    def test_one_bad_sentence_rejects_the_entire_comment(self):
        broken = VALID_PL.replace(
            "Decyzja ma ograniczyć ryzyko przeciążenia sieci energetycznej oraz wzrostu rachunków ponoszonych przez mieszkańców.",
            "Decyzj popary organizacje, a koszty pr d mają ponieść mieszkańcy.",
        )
        result = quality.validate_comment(broken, "pl")
        self.assertFalse(result.valid)
        self.assertIn("broken_polish_word", result.reasons)

    def test_truncated_lowercase_fragment_is_rejected(self):
        broken = VALID_PL.replace(
            ".",
            ", m. Kolejne zdanie opisuje potwierdzone ustalenia sprawy.",
            1,
        )
        result = quality.validate_comment(broken, "pl")
        self.assertFalse(result.valid)
        self.assertIn("truncated_lowercase_fragment", result.reasons)

    def test_invalid_polish_case_after_przez_is_rejected(self):
        broken = (
            "Prezydent Litwy zosta\u0142 zacytowany przez telewizj\u0105 LRT. "
            + " ".join(quality.split_sentences(VALID_PL)[1:])
        )
        result = quality.validate_comment(broken, "pl")
        self.assertFalse(result.valid)
        self.assertIn("invalid_case_after_przez", result.reasons)

    def test_inconsistent_polish_lottery_tense_is_rejected(self):
        broken = (
            "W\u0142a\u015bnie odby\u0142o si\u0119 losowanie fazy grupowej, kt\u00f3re okre\u015bli, "
            "z kim polskie zespo\u0142y zagraj\u0105 w nadchodz\u0105cych rozgrywkach."
        )
        result = quality.validate_news_comment(broken, "pl")
        self.assertFalse(result.valid)
        self.assertIn("inconsistent_polish_tense", result.reasons)

    def test_known_current_officeholder_is_not_labelled_former(self):
        broken = (
            "Former President Trump threatened strikes on Iranian infrastructure unless negotiations resume, "
            "while military exchanges continued."
        )
        result = quality.validate_news_comment(broken, "en")
        self.assertFalse(result.valid)
        self.assertIn("known_officeholder_status_conflict", result.reasons)

    def test_malformed_english_quote_spacing_is_rejected(self):
        broken = VALID_EN.replace(
            ".",
            ", calling them 'ratepayers. ' Officials provided no further details.",
            1,
        )
        result = quality.validate_comment(broken, "en")
        self.assertFalse(result.valid)
        self.assertIn("malformed_quote_spacing", result.reasons)

    def test_rephrased_duplicate_information_is_rejected(self):
        broken = VALID_EN + (
            " Environmental groups and several elected officials backed the decision and demanded firm guarantees "
            "on costs and energy security."
        )
        result = quality.validate_comment(broken, "en")
        self.assertFalse(result.valid)
        self.assertIn("near_duplicate_sentence", result.reasons)

    def test_source_clipping_keeps_only_complete_sentences(self):
        first = "Officials published a complete and verified first sentence for the report."
        source = first + " The second sentence is intentionally much longer and must never be clipped halfway through."
        self.assertEqual(first, quality.clip_complete_text(source, len(first) + 35))
        self.assertEqual("", quality.clip_complete_text("A" * 200, 100))

    def test_mojibake_and_publisher_fragments_are_rejected(self):
        broken = VALID_PL.replace("Władze", "Åadze").replace("Moratorium poparły", "FOTONEWS Moratorium poparły")
        result = quality.validate_comment(broken, "pl")
        self.assertFalse(result.valid)
        self.assertTrue({"mojibake", "publisher_or_ui_fragment"} & set(result.reasons))

    def test_valid_english_comment_passes(self):
        result = quality.validate_comment(VALID_EN, "en")
        self.assertTrue(result.valid, result.reasons)
        self.assertEqual(3, len(result.sentences))

    def test_broken_english_comment_is_rejected(self):
        broken = VALID_EN.replace(
            "The measure is intended",
            "The measure isn t complete and t s intended",
        )
        result = quality.validate_comment(broken, "en")
        self.assertFalse(result.valid)
        self.assertTrue({"broken_english_contraction", "orphan_english_letters"} & set(result.reasons))

    def test_sentence_count_is_fail_closed_in_both_languages(self):
        self.assertFalse(quality.validate_comment(" ".join(quality.split_sentences(VALID_PL)[:2]), "pl").valid)
        self.assertFalse(quality.validate_comment(" ".join(quality.split_sentences(VALID_EN)[:2]), "en").valid)

    def test_homepage_comment_hard_limit_is_four_sentences(self):
        five_sentences = VALID_EN + (
            " Officials published the implementation timetable after the vote."
            " Regulators will monitor the first phase and publish the results."
        )
        result = quality.validate_comment(five_sentences, "en")
        self.assertFalse(result.valid)
        self.assertIn("sentence_count", result.reasons)

    def test_short_news_comments_use_the_same_language_checks(self):
        valid_pl = "Rząd opublikował szczegółowe zasady programu, które zaczną obowiązywać po zakończeniu konsultacji społecznych."
        broken_pl = "Rz d opublikowa zasady, a cz kosztw ponios mieszkańcy bez dodatkowych wyjaśnień."
        valid_en = "The government published detailed programme rules that will take effect after the consultation period ends."
        broken_en = "The government isn t ready and t s delaying the detailed programme rules again."
        self.assertTrue(quality.validate_news_comment(valid_pl, "pl").valid)
        self.assertFalse(quality.validate_news_comment(broken_pl, "pl").valid)
        self.assertTrue(quality.validate_news_comment(valid_en, "en").valid)
        self.assertFalse(quality.validate_news_comment(broken_en, "en").valid)

    def test_initials_and_market_abbreviations_are_not_treated_as_damage(self):
        pl = "Indeks S&P 500 wzrósł m.in. po publikacji danych, a analityczka E. Nowak wskazała na poprawę nastrojów inwestorów o 15 tys. zł."
        en = "The U.S. S&P 500 rose after the 5 p.m. data release, while analyst E. Smith pointed to stronger investor sentiment."
        pl_result = quality.validate_news_comment(pl, "pl")
        en_result = quality.validate_news_comment(en, "en")
        self.assertTrue(pl_result.valid, pl_result.reasons)
        self.assertTrue(en_result.valid, en_result.reasons)
        self.assertEqual(1, len(pl_result.sentences))
        self.assertEqual(1, len(en_result.sentences))

    def test_rank_ordinal_does_not_create_a_false_sentence_boundary(self):
        text = "Zajmująca 142. miejsce zawodniczka wygrała spotkanie i awansowała do kolejnej rundy międzynarodowego turnieju."
        result = quality.validate_news_comment(text, "pl")
        self.assertTrue(result.valid, result.reasons)
        self.assertEqual(1, len(result.sentences))

    def test_http_decoder_prefers_valid_utf8_over_wrong_header(self):
        original = "Jak podkreśliła, centra danych zużywają energię i podnoszą rachunki za prąd."
        response = self.FakeHttpResponse(original.encode("utf-8"), "iso-8859-1", "Windows-1252")
        self.assertEqual(original, quality.decode_http_response(response))

    def test_http_decoder_supports_polish_legacy_encoding(self):
        original = "Zażółć gęślą jaźń, ponieważ źródło używa starszego kodowania."
        response = self.FakeHttpResponse(original.encode("windows-1250"), "windows-1250", "Windows-1250")
        self.assertEqual(original, quality.decode_http_response(response))

    def test_legitimate_non_polish_names_are_not_mojibake(self):
        text = "Officials in Åland, Łódź and Älmhult published a detailed joint report on regional energy security."
        result = quality.validate_news_comment(text, "en")
        self.assertTrue(result.valid, result.reasons)

    def test_self_contained_english_sentence_may_start_with_that(self):
        text = "That decision will change the timetable for the programme and requires formal approval from parliament."
        result = quality.validate_news_comment(text, "en")
        self.assertTrue(result.valid, result.reasons)


class PipelineContractTests(unittest.TestCase):
    class FakeResponse:
        def __init__(self, payload: dict):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": json.dumps(self.payload, ensure_ascii=False)}}]
            }

    class FakeArticleResponse:
        ok = True
        status_code = 200
        encoding = "iso-8859-1"
        apparent_encoding = "Windows-1252"

        def __init__(self, content: bytes):
            self.content = content
            self.text = content.decode(self.encoding, errors="replace")

    def test_final_gate_keeps_only_fully_reviewed_valid_comments(self):
        payload = {
            "latest": [approved_item(VALID_PL), approved_item(BROKEN_PRODUCTION_PL)],
            "radar": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "home_brief.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            gate.process(path, "pl")
            result = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(1, len(result["latest"]))
        self.assertEqual(quality.QUALITY_STATUS, result["latest"][0]["comment_quality_status"])
        self.assertEqual(1, result["comment_quality_gate"]["rejected_count"])

    def test_final_gate_never_promotes_a_stale_review_contract(self):
        stale = approved_item(VALID_PL)
        stale["comment_quality_status"] = f"passed_strict_v{quality.QUALITY_VERSION - 1}"
        stale["comment_quality_version"] = quality.QUALITY_VERSION - 1
        comment, reasons = gate.publishable_comment(stale, "pl")
        self.assertEqual("", comment)
        self.assertEqual(("stale_quality_contract",), reasons)

    def test_post_review_methodology_never_appends_unreviewed_fields(self):
        item = {
            "full_brief": VALID_PL,
            "details": "Ten tekst nie przeszedł niezależnej recenzji i nie może zostać dopisany.",
            "summary": "Kolejne niezatwierdzone zdanie nie może zmienić opublikowanego komentarza.",
            "why": "Pole pomocnicze nie jest częścią komentarza zatwierdzonego przez model recenzujący.",
        }
        self.assertEqual(VALID_PL, methodology.build_full_brief(item, "pl"))

    def test_category_cleanup_never_changes_an_ai_reviewed_comment(self):
        reviewed = (
            "Sąd uznał Stanisława G. za winnego umyślnego ataku nożem na pokrzywdzonego. "
            "Oskarżony działał pod wpływem alkoholu, co sąd uznał za lekceważenie prawa. "
            "Drugi zarzut dotyczył gróźb pozbawienia życia wobec pokrzywdzonego. "
            "Sąd umorzył postępowanie wobec Kamila T., który odpowiadał za naruszenie nietykalności."
        )
        payload = {"latest": [approved_item(reviewed)], "radar": []}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "home.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            category_cleanup.process(path, "pl")
            gate.process(path, "pl")
            result = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(reviewed, result["latest"][0]["full_brief"])

    def test_any_post_review_text_change_breaks_the_digest_contract(self):
        item = approved_item(VALID_PL)
        item["full_brief"] = VALID_PL.replace("dwuletnie", "trzyletnie")
        comment, reasons = gate.publishable_comment(item, "pl")
        self.assertEqual("", comment)
        self.assertEqual(("reviewed_comment_digest_mismatch",), reasons)
        self.assertFalse(protect.valid_card(item, "pl"))

    def test_article_reader_attaches_digest_to_the_exact_reviewed_comment(self):
        payload = {"latest": [approved_item(VALID_EN, "en")], "radar": []}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "home.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with (
                mock.patch.object(reader, "fetch_article_text", return_value=("Source article " * 100, "article_read")),
                mock.patch.object(reader, "ai_summarize_batch", return_value={"en-0": VALID_EN}),
            ):
                reader.process(path, "en", {})
            result = json.loads(path.read_text(encoding="utf-8"))["latest"][0]
        self.assertEqual(VALID_EN, result["full_brief"])
        self.assertEqual(quality.review_digest(VALID_EN), result["comment_review_digest"])

    def test_english_homepage_rejects_live_roundups_before_article_fetch(self):
        entry = {
            "title": "Australia news live: politics debate; workplace drug use rises",
            "link": "https://example.com/australia-news/live/2026/jul/16/updates",
            "summary": "Rolling coverage of several unrelated stories across the day.",
        }
        with mock.patch.object(home_en, "article_excerpt") as article_excerpt:
            self.assertIsNone(home_en.make_item(entry, "Breaking"))
        article_excerpt.assert_not_called()

    def test_homepage_contract_requires_eight_fresh_items_in_both_languages(self):
        self.assertEqual({"pl": 8, "en": 8}, protect.MIN_VISIBLE_ITEMS)
        contract = json.loads((ROOT / "data/content_update_contract.json").read_text(encoding="utf-8"))
        self.assertEqual(8, contract["homepage_and_news"]["minimum_visible_cards"])
        self.assertGreaterEqual(home_pl.MAX_ITEMS, 16)
        self.assertGreaterEqual(home_en.MAX_ITEMS, 16)
        now = 1_800_000_000.0
        self.assertTrue(home_pl.is_fresh_timestamp(now - 23 * 3600, now))
        self.assertTrue(home_en.is_fresh_timestamp(now - 23 * 3600, now))
        self.assertFalse(home_pl.is_fresh_timestamp(now - 25 * 3600, now))
        self.assertFalse(home_en.is_fresh_timestamp(now - 25 * 3600, now))

    def test_homepage_images_have_no_visible_category_labels(self):
        for relative in ("pl/index.html", "en/index.html"):
            page = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn('<span class="tag">', page)
            self.assertNotIn("brief-card .tag", page)

    def test_stale_homepage_feed_is_not_a_last_good_source(self):
        stale = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        payload = {"updated_at": stale, "latest": [approved_item(VALID_PL)], "radar": []}
        self.assertEqual(0, protect.total_valid(payload, "pl"))

    def test_foreign_source_polish_comment_must_be_longer(self):
        one_sentence = (
            "Światowa Organizacja Zdrowia opublikowała nowe zalecenia dotyczące monitorowania zakażeń w szpitalach."
        )
        two_sentences = (
            "Światowa Organizacja Zdrowia opublikowała nowe zalecenia dotyczące monitorowania zakażeń w szpitalach. "
            "Dokument opisuje grupy ryzyka oraz działania, które placówki powinny wdrożyć podczas kolejnych kontroli."
        )
        self.assertFalse(news_batch._meets_comment_contract(one_sentence, "pl", True))
        self.assertTrue(news_batch._meets_comment_contract(two_sentences, "pl", True))

    def test_last_good_protection_revalidates_text_and_version(self):
        self.assertTrue(protect.valid_card(approved_item(VALID_PL), "pl"))
        self.assertTrue(protect.valid_card(approved_item(VALID_EN, "en"), "en"))

        legacy = approved_item(VALID_PL)
        legacy["comment_quality_status"] = "passed_3_to_6_sentences"
        self.assertFalse(protect.valid_card(legacy, "pl"))
        self.assertFalse(protect.valid_card(approved_item(BROKEN_PRODUCTION_PL), "pl"))

    def test_failed_update_restores_only_strict_last_good_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            current_path = Path(tmp) / "current.json"
            backup_path = Path(tmp) / "backup.json"
            good = []
            for index in range(6):
                item = approved_item(VALID_PL)
                item["title"] = f"Poprawny materiał {index + 1}"
                item["link"] = f"https://example.com/good-{index + 1}"
                good.append(item)
            backup_path.write_text(
                json.dumps({"latest": good, "radar": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            current_path.write_text(
                json.dumps({"latest": [approved_item(BROKEN_PRODUCTION_PL)], "radar": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.object(protect, "FILES", {"pl": (current_path, backup_path)}):
                protect.validate_current()
            restored = json.loads(current_path.read_text(encoding="utf-8"))
        self.assertEqual(6, protect.total_valid(restored, "pl"))
        self.assertEqual("restored_or_completed_from_last_good", restored["homepage_last_good_protection"]["status"])

    def test_english_feed_keeps_a_five_card_last_good_backup(self):
        good = []
        for index in range(5):
            item = approved_item(VALID_EN, "en")
            item["title"] = f"Reviewed English article {index + 1}"
            item["link"] = f"https://example.com/en-{index + 1}"
            good.append(item)
        with tempfile.TemporaryDirectory() as tmp:
            current_path = Path(tmp) / "current.json"
            backup_path = Path(tmp) / "backup.json"
            current_path.write_text(
                json.dumps({"latest": good, "radar": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.object(protect, "FILES", {"en": (current_path, backup_path)}):
                protect.backup_current()
                current_path.write_text(
                    json.dumps({"latest": [], "radar": []}, ensure_ascii=False),
                    encoding="utf-8",
                )
                protect.validate_current()
            restored = json.loads(current_path.read_text(encoding="utf-8"))
        self.assertEqual(5, protect.total_valid(restored, "en"))
        self.assertEqual("restored_or_completed_from_last_good", restored["homepage_last_good_protection"]["status"])

    def test_passive_home_check_does_not_rewrite_an_unchanged_empty_feed(self):
        payload = {
            "latest": [],
            "radar": [],
            "count": 0,
            "last_update_attempt_at": "2026-07-15T12:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as tmp:
            current_path = Path(tmp) / "current.json"
            backup_path = Path(tmp) / "backup.json"
            current_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            before = current_path.read_bytes()
            with mock.patch.object(protect, "FILES", {"pl": (current_path, backup_path)}):
                protect.validate_current(passive=True)
            after = current_path.read_bytes()
        self.assertEqual(before, after)

    def test_passive_home_check_saves_an_unchanged_valid_feed_as_last_good(self):
        good = []
        for index in range(8):
            item = approved_item(VALID_EN, "en")
            item["title"] = f"Reviewed English article {index + 1}"
            item["link"] = f"https://example.com/passive-en-{index + 1}"
            good.append(item)
        payload = {"latest": good, "radar": [], "count": 8}
        with tempfile.TemporaryDirectory() as tmp:
            current_path = Path(tmp) / "current.json"
            backup_path = Path(tmp) / "backup.json"
            current_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            before = current_path.read_bytes()
            with mock.patch.object(protect, "FILES", {"en": (current_path, backup_path)}):
                protect.validate_current(passive=True)
            after = current_path.read_bytes()
            backup = json.loads(backup_path.read_text(encoding="utf-8"))
        self.assertEqual(before, after)
        self.assertEqual(8, protect.total_valid(backup, "en"))

    def test_passive_home_check_never_erases_a_feed_without_a_current_backup(self):
        item = approved_item(VALID_PL)
        item["comment_quality_status"] = "passed_strict_v6"
        item["comment_quality_version"] = 6
        payload = {
            "latest": [item],
            "radar": [],
            "count": 1,
            "last_update_attempt_at": "2026-07-15T12:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as tmp:
            current_path = Path(tmp) / "current.json"
            backup_path = Path(tmp) / "backup.json"
            current_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            before = current_path.read_bytes()
            with mock.patch.object(protect, "FILES", {"pl": (current_path, backup_path)}):
                protect.validate_current(passive=True)
            after = current_path.read_bytes()
        self.assertEqual(before, after)

    def test_watchdog_requires_current_quality_contract_in_both_feed_and_cards(self):
        items = []
        for index in range(8):
            item = approved_item(VALID_EN, "en")
            item["title"] = f"Reviewed English article {index + 1}"
            item["link"] = f"https://example.com/watchdog-en-{index + 1}"
            items.append(item)
        payload = {
            "latest": items,
            "radar": [],
            "count": 8,
            "comment_quality_gate": {
                "status": quality.QUALITY_STATUS,
                "version": quality.QUALITY_VERSION,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "home.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            self.assertTrue(watchdog.home_contract_current(path, "en"))
            payload["latest"][0]["comment_quality_version"] = quality.QUALITY_VERSION - 1
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            self.assertFalse(watchdog.home_contract_current(path, "en"))
            payload["latest"][0]["comment_quality_version"] = quality.QUALITY_VERSION
            payload["latest"] = payload["latest"][:4]
            payload["count"] = 4
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            self.assertFalse(watchdog.home_contract_current(path, "en"))

    def test_bad_reviewed_cache_entry_cannot_bypass_validator(self):
        title = "Test title"
        link = "https://example.com/cache-test"
        article = "A" * 900
        key = hashlib.sha256(
            f"{reader.CACHE_VERSION}|pl|{link}|{title}|{article[:1600]}".encode("utf-8")
        ).hexdigest()[:48]
        cache = {
            key: {
                "summary": BROKEN_PRODUCTION_PL,
                "reviewed": True,
                "quality_version": quality.QUALITY_VERSION,
            }
        }
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual("", reader.ai_summarize(title, "pl", article, link, cache))

    def test_generation_requires_independent_ai_approval(self):
        responses = [
            self.FakeResponse({"full_brief": VALID_PL}),
            self.FakeResponse({"approved": True, "reason": "Poprawny i zgodny z tekstem."}),
        ]
        with (
            mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            mock.patch.object(reader.requests, "post", side_effect=responses) as post,
        ):
            result = reader.ai_summarize(
                "Moratorium na centra danych",
                "pl",
                "Pełny tekst artykułu " * 80,
                "https://example.com/approved",
                {},
            )
        self.assertEqual(VALID_PL, result)
        self.assertEqual(2, post.call_count)

    def test_github_models_is_used_when_openai_secret_is_missing(self):
        with mock.patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "",
                "GITHUB_MODELS_TOKEN": "github-test-token",
                "GITHUB_MODELS_MODEL": "openai/gpt-4o",
                "GITHUB_MODELS_REVIEW_MODEL": "openai/gpt-4.1",
            },
        ):
            runtime = quality.get_ai_runtime()
        self.assertTrue(runtime.available)
        self.assertEqual("github-models", runtime.provider)
        self.assertEqual("https://models.github.ai/inference/chat/completions", runtime.endpoint)
        self.assertEqual("openai/gpt-4o", runtime.generation_model)
        self.assertEqual("openai/gpt-4.1", runtime.review_model)

    def test_news_batch_uses_two_models_and_rejects_missing_review(self):
        first = "The government published detailed programme rules that will take effect after public consultation ends."
        second = "Researchers reported a measurable result and said the full study will be published after peer review."
        responses = [
            self.FakeResponse({
                "items": [
                    {"id": "en-0", "summary": first, "title_pl": ""},
                    {"id": "en-1", "summary": second, "title_pl": ""},
                ]
            }),
            self.FakeResponse({
                "reviews": [
                    {"id": "en-0", "approved": True, "reason": "supported and clear"},
                ]
            }),
        ]
        items = [
            {
                "title": "Government publishes programme rules",
                "summary_raw": first,
                "link": "https://example.com/one",
            },
            {
                "title": "Researchers report study result",
                "summary_raw": second,
                "link": "https://example.com/two",
            },
        ]
        post = mock.Mock(side_effect=responses)
        with mock.patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "",
                "GITHUB_MODELS_TOKEN": "github-test-token",
                "GITHUB_MODELS_MODEL": "openai/gpt-4o",
                "GITHUB_MODELS_REVIEW_MODEL": "openai/gpt-4.1",
            },
        ):
            result = news_batch.summarize_news_items(
                items=items,
                lang="en",
                cache={},
                post=post,
            )
        self.assertEqual({"en-0"}, set(result))
        self.assertEqual(2, post.call_count)
        self.assertEqual("openai/gpt-4o", post.call_args_list[0].kwargs["json"]["model"])
        self.assertEqual("openai/gpt-4.1", post.call_args_list[1].kwargs["json"]["model"])

    def test_batch_review_splits_large_sets_into_small_requests(self):
        entries = [
            {
                "id": f"pl-{index}",
                "title": f"Testowy materiał {index}",
                "source_text": "Źródło opisuje konkretne i potwierdzone ustalenia sprawy.",
                "summary": "Rząd opublikował szczegółowe zasady programu po zakończeniu konsultacji społecznych.",
            }
            for index in range(9)
        ]
        responses = [
            self.FakeResponse({
                "reviews": [
                    {"id": f"pl-{index}", "approved": True, "reason": "approved"}
                    for index in range(3)
                ]
            }),
            self.FakeResponse({
                "reviews": [
                    {"id": f"pl-{index}", "approved": True, "reason": "approved"}
                    for index in range(3, 6)
                ]
            }),
            self.FakeResponse({
                "reviews": [
                    {"id": f"pl-{index}", "approved": True, "reason": "approved"}
                    for index in range(6, 9)
                ]
            }),
        ]
        runtime = quality.AiRuntime(
            "github-models",
            "token",
            "https://models.github.ai/inference/chat/completions",
            "openai/gpt-4o",
            "openai/gpt-4.1",
        )
        post = mock.Mock(side_effect=responses)
        result = quality.independent_ai_review_batch(
            post=post,
            runtime=runtime,
            entries=entries,
            lang="pl",
        )
        self.assertEqual(3, post.call_count)
        self.assertTrue(all(approved for approved, _reason in result.values()))

    def test_read_timeouts_are_bounded_to_two_attempts(self):
        class ReadTimeout(Exception):
            pass

        runtime = quality.AiRuntime(
            "github-models",
            "token",
            "https://models.github.ai/inference/chat/completions",
            "openai/gpt-4o",
            "openai/gpt-4.1",
        )
        post = mock.Mock(side_effect=ReadTimeout("timed out"))
        with (
            mock.patch.object(quality.time, "sleep"),
            self.assertRaisesRegex(RuntimeError, "after retries"),
        ):
            quality.request_json_completion(
                post=post,
                runtime=runtime,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=100,
                temperature=0,
            )
        self.assertEqual(2, post.call_count)

    def test_production_workflows_grant_and_pass_github_models_access(self):
        for relative in (
            ".github/workflows/build-home-brief.yml",
            ".github/workflows/news-pl.yml",
            ".github/workflows/news-en.yml",
            ".github/workflows/content-update-watchdog.yml",
            "config/workflow_templates/build-home-brief.yml",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("models: read", source, relative)
            self.assertIn("GITHUB_MODELS_TOKEN: ${{ secrets.GITHUB_TOKEN }}", source, relative)
            self.assertIn("NEWS_AI_MODEL: gpt-4o", source, relative)
            self.assertIn("GITHUB_MODELS_MODEL: openai/gpt-4o", source, relative)
            self.assertIn("GITHUB_MODELS_REVIEW_MODEL: openai/gpt-4.1", source, relative)
        workflow_groups = {
            ".github/workflows/news-pl.yml": "group: news-pl-publishing",
            ".github/workflows/news-en.yml": "group: news-en-publishing",
        }
        for relative, marker in workflow_groups.items():
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(marker, source, relative)
        watchdog = (ROOT / "scripts/content_update_watchdog.py").read_text(encoding="utf-8")
        self.assertIn('"--validate-passive"', watchdog)
        for relative in (
            ".github/workflows/build-home-brief.yml",
            ".github/workflows/news-pl.yml",
            ".github/workflows/news-en.yml",
            "config/workflow_templates/build-home-brief.yml",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("git pull --ff-only origin main", source, relative)
            self.assertIn("git diff --quiet HEAD..origin/main --", source, relative)
            self.assertIn("Skip stale publish because generator code changed on main.", source, relative)
            self.assertIn("git pull --rebase -X theirs origin main", source, relative)
        watchdog_workflow = (ROOT / ".github/workflows/content-update-watchdog.yml").read_text(encoding="utf-8")
        self.assertIn("git pull --ff-only origin main", watchdog_workflow)
        self.assertIn("git diff --quiet HEAD..origin/main --", watchdog_workflow)
        self.assertIn("Skip stale publish because generator code changed on main.", watchdog_workflow)
        self.assertIn("git pull --rebase -X ours origin main", watchdog_workflow)

    def test_article_reader_preserves_polish_characters_from_realistic_http_bytes(self):
        paragraph = (
            "Jak podkreśliła gubernator, centra danych zużywają ogromne ilości energii, "
            "grożą przeciążeniem sieci i podnoszą rachunki za prąd ponoszone przez mieszkańców."
        )
        html = (
            '<html><head><meta charset="utf-8"></head><body><article>'
            + "".join(f"<p>{paragraph} Akapit numer {index} zawiera dodatkowe informacje.</p>" for index in range(8))
            + "</article></body></html>"
        )
        response = self.FakeArticleResponse(html.encode("utf-8"))
        with mock.patch.object(reader.requests, "get", return_value=response):
            text, status = reader.fetch_article_text("https://example.com/polish-encoding")
        self.assertEqual("article_read", status)
        self.assertIn("podkreśliła", text)
        self.assertIn("zużywają", text)
        self.assertNotIn("podkrelia", text)

    def test_pl_and_en_news_comments_require_generation_and_review(self):
        valid_pl = "Rząd opublikował szczegółowe zasady programu, które zaczną obowiązywać po zakończeniu konsultacji społecznych."
        valid_en = "The government published detailed programme rules that will take effect after the consultation period ends."
        cases = (
            (news_pl, "ai_summarize_pl", "pl", valid_pl),
            (news_en, "ai_summarize_en", "en", valid_en),
        )
        for module, function_name, lang, summary in cases:
            with self.subTest(lang=lang):
                responses = [
                    self.FakeResponse({"summary": summary}),
                    self.FakeResponse({"approved": True, "reason": "approved"}),
                ]
                with (
                    mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
                    mock.patch.object(module, "CACHE", {}),
                    mock.patch.object(module, "save_cache"),
                    mock.patch.object(module.requests, "post", side_effect=responses) as post,
                ):
                    function = getattr(module, function_name)
                    args = (
                        ("Testowy tytuł", valid_pl, "https://example.com/pl", "polityka")
                        if lang == "pl"
                        else ("Test title", valid_en, "https://example.com/en")
                    )
                    result = function(*args)
                self.assertEqual(summary, result["summary"])
                self.assertTrue(result["reviewed"])
                self.assertEqual(quality.QUALITY_STATUS, result["quality_status"])
                self.assertEqual(2, post.call_count)

    def test_en_news_has_no_rss_fallback_without_ai(self):
        with (
            mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}),
            mock.patch.object(news_en, "CACHE", {}),
            mock.patch.object(news_en, "save_cache") as save_cache,
        ):
            result = news_en.ai_summarize_en(
                "Test title",
                "A detailed RSS description that used to be published directly as a fake AI comment.",
                "https://example.com/no-ai",
            )
        self.assertEqual("", result["summary"])
        self.assertFalse(result["reviewed"])
        save_cache.assert_not_called()

    def test_deterministic_rejection_runs_before_ai_review(self):
        response = self.FakeResponse({"full_brief": BROKEN_PRODUCTION_PL})
        with (
            mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            mock.patch.object(reader.requests, "post", return_value=response) as post,
        ):
            result = reader.ai_summarize(
                "Moratorium na centra danych",
                "pl",
                "Pełny tekst artykułu " * 80,
                "https://example.com/rejected-before-review",
                {},
            )
        self.assertEqual("", result)
        self.assertEqual(1, post.call_count)

    def test_ai_review_rejection_blocks_deterministically_valid_text(self):
        responses = [
            self.FakeResponse({"full_brief": VALID_EN}),
            self.FakeResponse({"approved": False, "reason": "One claim is not supported."}),
        ]
        with (
            mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            mock.patch.object(reader.requests, "post", side_effect=responses),
        ):
            result = reader.ai_summarize(
                "Data center moratorium",
                "en",
                "Full source article text " * 80,
                "https://example.com/review-rejected",
                {},
            )
        self.assertEqual("", result)

    def test_raw_article_fallback_is_removed(self):
        source = (SCRIPTS / "read_and_summarize_articles.py").read_text(encoding="utf-8")
        self.assertNotIn("fallback_summary(article_text)", source)
        self.assertNotIn("def fallback_summary", source)

    def test_browser_renderers_require_current_strict_approval(self):
        for relative in ("pl/index.html", "en/index.html", "pl/brief.html", "en/brief.html"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(quality.QUALITY_STATUS, source, relative)
            self.assertIn("article_text_ai_reviewed", source, relative)
            self.assertNotIn("startsWith('passed_')", source, relative)


if __name__ == "__main__":
    unittest.main()

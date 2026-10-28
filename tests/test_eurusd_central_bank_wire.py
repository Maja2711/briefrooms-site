"""Deterministic mocked HTTP. Every event/document is a synthetic fixture."""
import copy
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eurusd_central_bank_wire as wire

NOW = datetime(2026, 10, 28, 18, 1, tzinfo=timezone.utc)
URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary-fixture.htm"
ECB_URL = "https://www.ecb.europa.eu/press/pr/date/fixture.html"
DOC = b"<p>SYNTHETIC TEST FIXTURE. The Committee decided to maintain the target range for the federal funds rate at 3.5 to 3.75 percent.</p>"
EMPTY = b"<rss><channel/></rss>"
CALENDAR = [{"uid": "fixture:fomc", "bank": "FED", "title": "FOMC meeting decision window", "event_at": "2026-10-28T18:00:00Z",
             "source_ref": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", "calendar_checked_at": "2026-10-28T17:59:00Z", "time_precision": "date_only"}]


def feed(title="Federal Reserve issues FOMC statement", url=URL, *, published="Wed, 28 Oct 2026 18:00:00 GMT", guid="fixture-guid", summary="Synthetic fixture", publisher=""):
    source = f"<source>{publisher}</source>" if publisher else ""
    return (f"<rss><channel><item><title>{title}</title><link>{url}</link><guid>{guid}</guid><pubDate>{published}</pubDate><description>{summary}</description>{source}</item></channel></rss>").encode()


class Client:
    def __init__(self, bodies=None):
        self.bodies = bodies or {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.bodies.get(url, EMPTY)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, wire.HttpResult):
            return result
        return wire.HttpResult(200, result, url, {"ETag": "fixture-v1"})


def collected(client=None, previous=None, now=NOW, calendar=CALENDAR):
    client = client or Client({wire.OFFICIAL_RSS["fed_monetary"]: feed(), URL: DOC})
    return wire.collect(now=now, previous=previous, client=client, calendar_events=calendar, calendar_coverage={"complete": True, "checked_at": wire.iso(now),
        "sources": {"FOMC": {"status": "ok", "source_ref": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"},
                    "ECB": {"status": "ok", "source_ref": "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"}}})


class WireTests(unittest.TestCase):
    def test_one_canonical_snapshot_for_both_consumers_and_official_actuals(self):
        result = collected()
        self.assertTrue(wire.validate_snapshot(result, NOW))
        self.assertEqual(result["overall_status"], "FRESH")
        self.assertEqual(len(result["events"]), 1)
        event = result["events"][0]
        self.assertTrue(event["statement_verified"])
        self.assertEqual(event["related_calendar_event_id"], "fixture:fomc")
        self.assertEqual(result["event_window"]["phase"], "RELEASED")
        self.assertEqual(result["event_window"]["conference_status"], "UNKNOWN")
        actual = wire.official_actuals(result, CALENDAR[0], NOW)[0]
        self.assertEqual(actual["value"], {"lower": 3.5, "upper": 3.75})
        self.assertEqual(actual["published_at"], "2026-10-28T18:00:00Z")
        self.assertEqual(actual["source_ref"], URL)
        self.assertEqual(result["consumers"], {"daily": {"last_seen_at": None}, "weekly": {"last_seen_at": None}})

    def test_empty_first_snapshot_has_no_invented_publications(self):
        result = collected(Client())
        self.assertEqual(result["events"], [])
        self.assertEqual(wire.official_actuals(result, CALENDAR[0], NOW), [])

    def test_duplicate_feeds_and_different_guid_cannot_multiply_one_document(self):
        client = Client({wire.OFFICIAL_RSS["fed_monetary"]: feed(), wire.OFFICIAL_RSS["fed_speeches"]: feed(guid="other-guid"), URL: DOC})
        result = collected(client)
        self.assertEqual(len(result["events"]), 1)
        repeat = collected(client, result, now=NOW + timedelta(minutes=1))
        self.assertEqual(repeat["events"], result["events"])

    def test_correction_preserves_original_event_and_first_receipt(self):
        original = collected()
        corrected_doc = DOC.replace(b"3.5 to 3.75", b"3.25 to 3.5")
        next_result = collected(Client({wire.OFFICIAL_RSS["fed_monetary"]: feed(), URL: corrected_doc}), original, NOW + timedelta(minutes=2))
        self.assertEqual(len(next_result["events"]), 2)
        self.assertIn(original["events"][0], next_result["events"])
        correction = next_result["events"][-1]
        self.assertEqual(correction["first_seen_at"], original["events"][0]["first_seen_at"])
        self.assertEqual(correction["supersedes_event_id"], original["events"][0]["event_id"])
        self.assertEqual(wire.official_actuals(next_result, CALENDAR[0], NOW + timedelta(minutes=2))[0]["value"], {"lower": 3.25, "upper": 3.5})

    def test_outage_does_not_erase_verified_history(self):
        original = collected()
        client = Client({url: urllib.error.URLError("fixture outage") for url in wire.OFFICIAL_RSS.values()})
        result = collected(client, original, NOW + timedelta(minutes=1))
        self.assertEqual(result["events"], original["events"])
        self.assertEqual(result["overall_status"], "UNAVAILABLE")

    def test_document_failure_does_not_downgrade_same_publication(self):
        original = collected()
        result = collected(Client({wire.OFFICIAL_RSS["fed_monetary"]: feed(), URL: urllib.error.URLError("fixture")}), original, NOW + timedelta(minutes=1))
        self.assertEqual(result["events"], original["events"])

    def test_etag_304_is_fresh_check_not_new_news(self):
        original = collected()
        client = Client({url: wire.HttpResult(304, b"", url, {}) for url in wire.OFFICIAL_RSS.values()})
        result = collected(client, original, NOW + timedelta(minutes=1))
        self.assertEqual(result["events"], original["events"])
        self.assertTrue(all(row["not_modified"] for row in result["sources"]))
        self.assertEqual(client.calls[0][1]["headers"]["If-None-Match"], "fixture-v1")
        self.assertEqual(result["events"][0]["ingested_at"], "2026-10-28T18:01:00Z")

    def test_304_without_prior_proof_fails(self):
        result = collected(Client({url: wire.HttpResult(304, b"", url, {}) for url in wire.OFFICIAL_RSS.values()}))
        self.assertEqual(result["overall_status"], "UNAVAILABLE")

    def test_google_headline_has_no_official_or_speaker_authority(self):
        client = Client({wire.SECONDARY_RSS: feed(title="Fed Chair says rates unchanged - Reuters", url="https://news.google.com/rss/articles/fixture", publisher="Reuters")})
        result = wire.collect(now=NOW, client=client, include_secondary=True)
        self.assertEqual(len(result["events"]), 1)
        event = result["events"][0]
        self.assertEqual(event["source_class"], "SECONDARY_HEADLINE")
        self.assertFalse(event["speaker_verified"])
        self.assertFalse(event["statement_verified"])
        self.assertEqual(event["content_coverage"], "HEADLINE_ONLY")
        self.assertEqual(len(client.calls), 5)  # No publisher/Google article fetch.

    def test_malicious_xml_and_ssrf_are_rejected_before_fetch(self):
        for payload in (b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///secret">]><rss/>', b"<rss>\x00</rss>", b"x" * (wire.MAX_XML_BYTES + 1)):
            with self.assertRaises(ValueError):
                wire.parse_feed(payload, source_id="fed_monetary", now=NOW)
        for url in ("http://www.federalreserve.gov/a", "https://www.federalreserve.gov.evil.test/a", "https://127.0.0.1/", "https://user@www.federalreserve.gov/a", "https://www.federalreserve.gov:444/a"):
            with self.assertRaises(ValueError):
                wire.canonical_url(url)
        self.assertEqual(wire.parse_feed(feed(url="https://127.0.0.1/a"), source_id="fed_monetary", now=NOW), [])

    def test_redirect_is_checked_before_request(self):
        handler = wire._Redirects()
        request = urllib.request.Request(URL)
        with self.assertRaises(ValueError):
            handler.redirect_request(request, None, 302, "redirect", {}, "https://127.0.0.1/private")
        with self.assertRaises(ValueError):
            handler.redirect_request(request, None, 302, "redirect", {}, ECB_URL)

    def test_future_missing_or_naive_publication_time_not_synthesized(self):
        for published in ("Wed, 28 Oct 2026 19:00:00 GMT", "", "2026-10-28T18:00:00"):
            self.assertEqual(wire.parse_feed(feed(published=published), source_id="fed_monetary", now=NOW), [])
        with self.assertRaises(ValueError):
            wire.collect(now=NOW.replace(tzinfo=None), client=Client())

    def test_atom_timezone_and_content_caps(self):
        atom = f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>fixture</id><title>FOMC statement</title><link href="{URL}"/><published>2026-10-28T14:00:00-04:00</published></entry></feed>'.encode()
        rows = wire.parse_feed(atom, source_id="fed_monetary", now=NOW)
        self.assertEqual(rows[0]["published_at"], "2026-10-28T18:00:00Z")
        self.assertLessEqual(len(wire.parse_feed(feed() .replace(b"</channel>", feed().split(b"<channel>")[1].split(b"</channel>")[0] * 100 + b"</channel>"), source_id="fed_monetary", now=NOW)), wire.MAX_ITEMS_PER_FEED)

    def test_delayed_transcript_does_not_become_live_knowledge(self):
        result = collected(Client({wire.OFFICIAL_RSS["fed_speeches"]: feed(title="Press conference transcript", published="Wed, 28 Oct 2026 17:00:00 GMT"), URL: b"<p>Chair Alex Example. Prepared transcript.</p>"}))
        event = result["events"][0]
        self.assertEqual(event["published_at"], "2026-10-28T17:00:00Z")
        self.assertEqual(event["first_seen_at"], "2026-10-28T18:01:00Z")
        self.assertEqual(event["live_speech_status"], "LIVE_SPEECH_UNVERIFIED")
        self.assertEqual(result["event_window"]["conference_status"], "UNKNOWN")
        self.assertFalse(wire.validate_snapshot(result, NOW - timedelta(minutes=1)))

    def test_no_static_thirty_minute_conference_end(self):
        later = NOW + timedelta(hours=3)
        result = collected(now=later)
        self.assertEqual(result["event_window"]["conference_status"], "UNKNOWN")
        self.assertNotEqual(result["event_window"]["phase"], "ENDED")

    def test_calendar_failure_is_partial_and_stale_calendar_has_no_verified_window(self):
        result = wire.collect(now=NOW, client=Client())
        self.assertEqual(result["overall_status"], "PARTIAL")
        calendar = copy.deepcopy(CALENDAR)
        calendar[0]["calendar_checked_at"] = "2026-10-26T17:59:00Z"
        self.assertFalse(collected(calendar=calendar)["event_window"]["calendar_verified"])

    def test_actuals_do_not_cross_bank_date_or_calendar_identity(self):
        result = collected()
        for event in ({**CALENDAR[0], "uid": "other:fomc"}, {**CALENDAR[0], "bank": "ECB"}, {**CALENDAR[0], "event_at": "2026-10-29T18:00:00Z"}):
            self.assertEqual(wire.official_actuals(result, event, NOW), [])

    def test_integrity_and_append_only_snapshot_journal(self):
        result = collected()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wire.json"
            self.assertTrue(wire.write_snapshot(path, result))
            self.assertFalse(wire.write_snapshot(path, result))
            self.assertEqual(wire.load_wire(path, NOW), result)
            old_bytes = path.read_bytes()
            newer = collected(previous=result, now=NOW + timedelta(minutes=1))
            wire.write_snapshot(path, newer)
            receipts = list((path.parent / "eurusd_cb_wire_journal").glob("*.json"))
            self.assertEqual(len(receipts), 2)
            self.assertIn(old_bytes, [receipt.read_bytes() for receipt in receipts])
            with self.assertRaises(ValueError):
                wire.write_snapshot(path, result)
            altered = copy.deepcopy(newer)
            altered["events"][0]["facts"]["rate_actual"]["lower"] = 99
            self.assertFalse(wire.validate_snapshot(altered))
            path.write_text(json.dumps(altered), encoding="utf-8")
            self.assertEqual(wire.load_wire(path, NOW + timedelta(minutes=1))["overall_status"], "UNAVAILABLE")

    def test_http_retry_429_500_and_byte_cap(self):
        waits = []
        client = wire.HttpClient(sleep=waits.append, jitter=lambda: 0)
        client.opener = Mock()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.headers = {"Content-Length": "7"}
        response.status = 200
        response.geturl.return_value = URL
        response.read.return_value = b"<rss/>"
        client.opener.open.side_effect = [urllib.error.HTTPError(URL, 429, "limited", {"Retry-After": "120"}, None), urllib.error.HTTPError(URL, 500, "fixture", {}, None), response]
        self.assertEqual(client.get(URL).status, 200)
        self.assertEqual(waits, [3, 2])
        self.assertEqual(client.opener.open.call_count, 3)
        client.opener.open.side_effect = None
        client.opener.open.return_value = response
        response.headers["Content-Length"] = "999"
        with self.assertRaises(ValueError):
            client.get(URL, max_bytes=20)

    def test_real_transport_receipt_is_not_backdated_to_start_of_fetch(self):
        client = Client({wire.OFFICIAL_RSS["fed_monetary"]: wire.HttpResult(200, feed(), wire.OFFICIAL_RSS["fed_monetary"], {}, "2026-10-28T18:01:02Z"), URL: wire.HttpResult(200, DOC, URL, {}, "2026-10-28T18:01:04Z")})
        result = collected(client)
        self.assertEqual(result["events"][0]["first_seen_at"], "2026-10-28T18:01:02Z")
        self.assertEqual(result["events"][0]["ingested_at"], "2026-10-28T18:01:04Z")
        self.assertGreaterEqual(result["generated_at"], "2026-10-28T18:01:04Z")
        self.assertTrue(wire.validate_snapshot(result))

    def test_ecb_official_actuals_keep_rate_names_and_source(self):
        calendar = [{**CALENDAR[0], "uid": "fixture:ecb", "bank": "ECB", "title": "ECB monetary policy decisions", "source_ref": "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"}]
        document = b"The Governing Council decided to lower rates. The interest rates on the deposit facility, the main refinancing operations and the marginal lending facility will be decreased to 2.00%, 2.15% and 2.40% respectively."
        result = collected(Client({wire.OFFICIAL_RSS["ecb_press_speeches"]: feed(title="Monetary policy decisions", url=ECB_URL), ECB_URL: document}), calendar=calendar)
        actual = wire.official_actuals(result, calendar[0], NOW)[0]
        self.assertEqual(actual["bank"], "ECB")
        self.assertEqual(actual["value"]["deposit_facility"], 2.0)
        self.assertEqual(actual["source_ref"], ECB_URL)

    def test_fresh_requires_all_unique_official_sources_and_calendar_receipts(self):
        for mutation in (lambda s: s.update(sources=[]),
                         lambda s: s["sources"][0].update(checked_at="2026-10-28T19:00:00Z"),
                         lambda s: s["sources"][0].update(status="ERROR"),
                         lambda s: s.update(calendar_coverage={"complete": True}),
                         lambda s: s["sources"][1].update(source_id="fed_monetary")):
            result = collected()
            mutation(result)
            result["integrity"]["snapshot_sha256"] = wire.snapshot_hash(result)
            self.assertFalse(wire.validate_snapshot(result))

    def test_html_error_page_is_not_a_successful_rss_check(self):
        result = collected(Client({wire.OFFICIAL_RSS["fed_monetary"]: b"<html><body>Access denied</body></html>"}))
        self.assertEqual(result["sources"][0]["status"], "ERROR")
        self.assertEqual(result["overall_status"], "PARTIAL")

    def test_calendar_reuses_canonical_parsers_and_only_two_bounded_sources(self):
        from belief_macro_calendar_adapter import FOMC_CALENDAR, ECB_CALENDAR
        fed = b"2026 FOMC Meetings October 27-28 2027 FOMC Meetings"
        ecb = b"29/10/2026 Monetary policy meeting of the Governing Council of the ECB"
        client = Client({FOMC_CALENDAR: fed, ECB_CALENDAR: ecb})
        calendar, coverage = wire.collect_calendars(now=NOW, client=client)
        self.assertTrue(coverage["complete"])
        self.assertEqual({row["bank"] for row in calendar}, {"FED", "ECB"})
        self.assertEqual(len(client.calls), 2)
        for _, options in client.calls:
            self.assertEqual(options["max_bytes"], wire.MAX_XML_BYTES)
        repeated, _ = wire.collect_calendars(now=NOW + timedelta(minutes=1), client=client, previous={"calendar_events": calendar})
        self.assertEqual(repeated[0]["first_seen_at"], calendar[0]["first_seen_at"])
        self.assertNotEqual(repeated[0]["calendar_checked_at"], calendar[0]["calendar_checked_at"])
        window = wire.event_window(repeated, (), NOW + timedelta(minutes=1))
        self.assertEqual(window["time_precision"], "date_only")
        self.assertEqual(window["first_seen_at"], calendar[0]["first_seen_at"])

    def test_conflicting_writer_lock_never_replaces_snapshot(self):
        result = collected()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wire.json"
            wire.write_snapshot(path, result)
            original = path.read_bytes()
            lock = path.with_name("wire.json.lock")
            lock.touch()
            with self.assertRaises(ValueError):
                wire.write_snapshot(path, collected(previous=result, now=NOW + timedelta(minutes=1)))
            self.assertEqual(path.read_bytes(), original)
            self.assertTrue(lock.exists())

    def test_only_explicit_verified_conference_status_survives_projection(self):
        calendar = copy.deepcopy(CALENDAR)
        calendar[0].update(conference_status="ENDED", conference_ended_at="2026-10-28T18:00:30Z",
            conference_status_verified_at="2026-10-28T18:00:45Z")
        self.assertEqual(wire.event_window(calendar, (), NOW)["conference_status"], "UNKNOWN")
        calendar[0].update(conference_status_source_url=URL, conference_end_evidence={"fixture": "explicit proof is validated independently by risk consumer"})
        window = wire.event_window(calendar, (), NOW)
        self.assertEqual(window["conference_status"], "ENDED")
        self.assertEqual(window["conference_end_evidence"], calendar[0]["conference_end_evidence"])


if __name__ == "__main__":
    unittest.main()

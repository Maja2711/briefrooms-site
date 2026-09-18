from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import belief_company_primary_sources as primary
import belief_news_event_adapter as news


UTC = timezone.utc


class FakeClient:
    def __init__(self, mapping):
        self.mapping = dict(mapping)

    def text(self, url):
        value = self.mapping[url]
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    def bytes(self, url, *, accept="*/*"):
        value = self.mapping[url]
        return value if isinstance(value, bytes) else str(value).encode("utf-8")

    def sec_text(self, url):
        return self.text(url)


class CompanyPrimarySourceTest(unittest.TestCase):
    def test_official_rss_keeps_provenance_and_verifies_named_ceo(self):
        now = datetime(2026, 9, 18, 18, 0, tzinfo=UTC)
        feed = """<?xml version="1.0"?>
        <rss><channel><item>
          <title>Jensen Huang says NVIDIA sees strong AI infrastructure demand</title>
          <link>https://nvidianews.nvidia.com/news/ai-demand</link>
          <pubDate>Fri, 18 Sep 2026 17:30:00 GMT</pubDate>
          <description>NVIDIA CEO Jensen Huang said demand remains strong.</description>
        </item></channel></rss>"""
        detail = """<html><head><meta property="article:published_time" content="2026-09-18T17:30:00Z"></head>
        <body>Jensen Huang said NVIDIA sees strong AI infrastructure demand.</body></html>"""
        client = FakeClient({
            "https://nvidianews.nvidia.com/cats/press_release.xml": feed,
            "https://nvidianews.nvidia.com/news/ai-demand": detail,
        })
        spec = next(row for row in primary.SOURCE_REGISTRY if row["key"] == "nvidia")
        source = spec["sources"][0]
        rows = primary._feed_documents(client, spec, source, now=now, cutoff=datetime(2026, 9, 17, 6, 0, tzinfo=UTC))
        self.assertEqual(1, len(rows))
        self.assertEqual("Jensen Huang", rows[0].metadata["verified_actor"])
        self.assertTrue(rows[0].metadata["official_host_verified"])
        self.assertEqual("verified_executive_statement", rows[0].category_hint)

    def test_official_ir_index_can_promote_transcript_from_document_cdn(self):
        now = datetime(2026, 9, 18, 18, 0, tzinfo=UTC)
        index_url = "https://investor.coinbase.com/home/default.aspx"
        transcript_url = "https://cdn.example.test/coinbase-q3-transcript.html"
        index = f"""<html><body><a href="{transcript_url}">Q3 2026 Earnings Call Transcript</a></body></html>"""
        detail = """<html><head>
          <meta property="article:published_time" content="2026-09-18T16:00:00Z">
        </head><body>Q3 2026 Earnings Call Transcript. Brian Armstrong said crypto product demand increased.</body></html>"""
        client = FakeClient({index_url: index, transcript_url: detail})
        spec = next(row for row in primary.SOURCE_REGISTRY if row["key"] == "coinbase")
        source = spec["sources"][0]
        rows = primary._index_documents(client, spec, source, now=now, cutoff=datetime(2026, 9, 17, 6, 0, tzinfo=UTC))
        self.assertEqual(1, len(rows))
        self.assertEqual("official_earnings_transcript", rows[0].category_hint)
        self.assertFalse(rows[0].metadata["official_host_verified"])
        self.assertTrue(rows[0].metadata["official_lineage_verified"])
        self.assertEqual("Brian Armstrong", rows[0].metadata["verified_actor"])

    def test_undated_official_index_document_fails_closed(self):
        now = datetime(2026, 9, 18, 18, 0, tzinfo=UTC)
        index_url = "https://abc.xyz/investor/earnings/"
        detail_url = "https://abc.xyz/investor/events/q3"
        index = f'<a href="{detail_url}">Q3 Earnings Call Transcript</a>'
        detail = "<html><body>Alphabet earnings call transcript with no publication date.</body></html>"
        client = FakeClient({index_url: index, detail_url: detail})
        spec = next(row for row in primary.SOURCE_REGISTRY if row["key"] == "alphabet")
        source = spec["sources"][0]
        rows = primary._index_documents(client, spec, source, now=now, cutoff=datetime(2026, 9, 17, 6, 0, tzinfo=UTC))
        self.assertEqual([], rows)

    def test_structured_publish_date_outranks_future_visible_event_date(self):
        payload = """<html><head>
          <meta property="article:published_time" content="2026-09-18T15:00:00Z">
        </head><body>
          Published September 18, 2026. Investor day scheduled October 20, 2026.
        </body></html>"""
        value = primary._extract_document_date(payload)
        self.assertIsNotNone(value)
        self.assertEqual("2026-09-18T15:00:00+00:00", value.isoformat())

    def test_low_value_ir_link_is_not_collected(self):
        html = '<a href="/investor/stock-price">Historical Price Lookup</a>'
        self.assertEqual([], primary._anchor_candidates(html, "https://investor.example.com/"))

    def test_systemic_private_and_crypto_sources_are_always_selected(self):
        keys = {row["key"] for row in primary.selected_specs(["NVDA"])}
        self.assertIn("nvidia", keys)
        self.assertIn("openai", keys)
        self.assertIn("anthropic", keys)
        self.assertIn("ethereum", keys)
        self.assertNotIn("microsoft", keys)


class SecPrimarySourceTest(unittest.TestCase):
    def test_sec_earnings_exhibit_is_detected(self):
        published = datetime(2026, 9, 18, 17, 0, tzinfo=UTC)
        accession = "0001045810-26-000123"
        index_url = news._sec_filing_index_url(1045810, accession)
        exhibit_url = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000123/nvda-ex991.htm"
        index = """<table><tr>
          <td>1</td><td>Press Release</td>
          <td><a href="nvda-ex991.htm">nvda-ex991.htm</a></td><td>EX-99.1</td>
        </tr></table>"""
        body = "<html><body>NVIDIA reports quarterly financial results with revenue and net income.</body></html>"
        client = FakeClient({index_url: index, exhibit_url: body})
        rows = news._sec_earnings_exhibits(
            client,
            ticker="NVDA",
            cik=1045810,
            accession=accession,
            published=published,
        )
        self.assertEqual(1, len(rows))
        self.assertEqual("sec_earnings_exhibit", rows[0].category_hint)
        self.assertEqual(exhibit_url, rows[0].source_ref)
        self.assertEqual(.995, rows[0].reliability)

    def test_default_watchlist_contains_systemic_public_issuers(self):
        tickers = set(news._watch_tickers())
        for ticker in ("NVDA", "MSFT", "AAPL", "GOOGL", "META", "AMD", "COIN", "MSTR", "CRCL"):
            self.assertIn(ticker, tickers)

    def test_company_primary_document_becomes_belief_primary_observation(self):
        document = news.SourceDocument(
            source="NVIDIA Investor Relations",
            source_ref="https://investor.nvidia.com/example",
            title="NVIDIA quarterly results",
            published_at="2026-09-18T17:00:00Z",
            entity="NVDA",
            document_text="NVIDIA reported quarterly results.",
            category_hint="official_earnings_release",
            reliability=.995,
            metadata={
                "primary_source_class": "official_earnings_release",
                "official_lineage_verified": True,
            },
        )
        observation = news.document_to_observation(document)
        self.assertEqual("primary", observation.source_type)
        self.assertEqual("NVDA", observation.entity)
        self.assertIn("official_earnings_release", observation.tags)
        self.assertTrue(observation.metadata["official_lineage_verified"])


if __name__ == "__main__":
    unittest.main()

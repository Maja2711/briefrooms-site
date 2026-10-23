import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from scripts import company_logo_resolver as resolver

def png():
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff)
    raw = b''.join(b'\x00' + (b'\x11\x22\x33\xff' * 64) for _ in range(64))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 64, 64, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))

def entity(ticker="NEW", market="Q82059", domain="https://example.org",
           name="Example Widget Company", extra=None):
    return {
        "labels": {"en": {"value": name}},
        "aliases": {},
        "claims": {
            "P414": [{"mainsnak": {"datavalue": {"value": {"id": market}}},
                      "qualifiers": {"P249": [{"datavalue": {"value": ticker}}]}}],
            "P856": [{"mainsnak": {"datavalue": {"value": domain}}}],
            **(extra or {}),
        }
    }

class ResolverTests(unittest.TestCase):
    def test_exchanges_must_match_even_when_ticker_matches(self):
        a = entity()
        self.assertEqual(resolver.verified_identity(a, "US", "NEW", "Example Widget Company"), "example.org")
        self.assertIsNone(resolver.verified_identity(a, "GPW", "NEW", "Example Widget Company"))
        self.assertIsNone(resolver.verified_identity(a, "US", "BAD", "Example Widget Company"))
        self.assertIsNone(resolver.verified_identity(a, "US", "NEW", "Unrelated Issuer"))
        gpw = entity(market="Q849235")
        self.assertEqual(resolver.verified_identity(gpw, "GPW", "NEW", "Example Widget Company"), "example.org")
        self.assertIsNone(resolver.verified_identity(gpw, "US", "NEW", "Example Widget Company"))

    def test_https_and_host_validation(self):
        for bad in ("http://example.org", "https://127.0.0.1", "https://abc.local",
                    "https://evil.com:8443", "https://user:pass@other.com",
                    "https://example..org", "https://localhost"):
            with self.subTest(domain=bad):
                self.assertIsNone(resolver.verified_identity(entity(domain=bad), "US", "NEW", "Example Widget Company"))

    def test_png_bounded_and_complete(self):
        good = png()
        self.assertTrue(resolver.valid_png(good))
        self.assertFalse(resolver.valid_png(good[:-7]))
        self.assertFalse(resolver.valid_png(b'not PNG'))
        bad = bytearray(good)
        bad[-6] = bad[-6] ^ 1
        self.assertFalse(resolver.valid_png(bytes(bad)))

    def test_legacy_domain_map_is_market_scoped(self):
        # Existing BRs logo domains may be cached while new symbols still
        # use issuer evidence. Never allow one exchange's ticker to leak.
        self.assertEqual(resolver.curated_domain("GPW", "LPP"), "lppsa.com")
        self.assertEqual(resolver.curated_domain("GPW", "ALE"), "allegro.eu")
        self.assertEqual(resolver.curated_domain("GPW", "DNP"), "grupadino.pl")
        self.assertEqual(resolver.curated_domain("US", "ASO"), "academy.com")
        self.assertEqual(resolver.curated_domain("US", "WMG"), "wmg.com")
        self.assertEqual(resolver.curated_domain("US", "SAP"), "sap.com")
        self.assertIsNone(resolver.curated_domain("US", "LPP"))
        self.assertIsNone(resolver.curated_domain("GPW", "ASO"))
        self.assertIsNone(resolver.curated_domain("GPW", "SAP"))
        self.assertIsNone(resolver.curated_domain("US", "UNKNOWN2026"))

    def test_open_positions_scope_and_dedup(self):
        rows = resolver.open_positions({"markets": {
            "GPW": {"open_positions": [{"ticker":"ALE","symbol":"ALE.WA","name":"Allegro"},
                                       {"ticker":"ALE","symbol":"ALE.WA","name":"Allegro"},
                                       {"ticker":"CLOSE","name":"Closed","status":"CLOSED"}]},
            "US": {"open_positions": [{"ticker":"ALE","name":"Other American issuer"},
                                      {"ticker":"../BAD","name":"Unsafe path"}]}}})
        self.assertEqual(rows, [("GPW", "ALE", "Allegro"), ("US", "ALE", "Other American issuer")])

    def test_process_cache_and_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            portfolio = folder / "stock_trading_portfolio.json"
            manifest = folder / "company_logo_cache.json"
            assets = folder / "assets"
            portfolio.write_text(json.dumps({"markets": {
                "GPW": {"open_positions": [{"ticker":"NEW","name":"Example Widget Company"}]},
                "US": {"open_positions": [{"ticker":"XYZ","name":"Unresolved issuer"}]}
            }}))
            calls = []
            def discover(market, ticker, name):
                calls.append(("lookup",market,ticker))
                return ("example.org","Q1234") if market == "GPW" else None
            def download(url):
                calls.append(("fetch",url))
                self.assertIn("domain_url=https%3A%2F%2Fexample.org", url)
                return png()
            first = resolver.process(portfolio, manifest, assets, discover, download)
            self.assertEqual((first["created"], first["unresolved"]), (1, 1))
            self.assertTrue((assets / "gpw/new.png").exists())
            self.assertFalse((assets / "us/xyz.png").exists())
            entry = json.loads(manifest.read_text())["entries"]["GPW:NEW"]
            self.assertEqual(entry["path"], "/assets/company-logos/gpw/new.png")
            self.assertEqual(entry["entity_id"], "Q1234")
            before = manifest.read_bytes()
            second = resolver.process(portfolio, manifest, assets, discover, download)
            self.assertEqual(second["cached"],1)
            self.assertEqual(second["created"],0)
            self.assertEqual(manifest.read_bytes(),before)

    def test_corrupt_image_never_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            p = folder/"p.json"
            m = folder/"m.json"
            a = folder/"assets"
            p.write_text(json.dumps({"markets":{"US":{"open_positions":[{"ticker":"NEW","name":"Example Widget Company"}]}}}))
            result = resolver.process(p,m,a,lambda *_:("example.org","Q1234"),lambda *_:b"<svg>bad</svg>")
            self.assertEqual(result["created"],0)
            self.assertFalse((a/"us/new.png").exists())
            self.assertFalse(json.loads(m.read_text())["entries"])

if __name__ == "__main__":
    unittest.main()

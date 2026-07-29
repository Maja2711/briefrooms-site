from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
sys.modules.setdefault("requests", mock.Mock())

from scripts.enforce_external_media_policy import (
    POLICY_DATA_ID,
    RUNTIME,
    ensure_assets,
)


class ExternalMediaBrowserPolicyTests(unittest.TestCase):
    def test_browser_receives_the_authoritative_publisher_cdn_policy(self) -> None:
        rendered = ensure_assets("<html><head></head><body></body></html>")
        match = re.search(
            rf'<script id="{POLICY_DATA_ID}" type="application/json">(.*?)</script>',
            rendered,
        )
        self.assertIsNotNone(match)
        embedded = json.loads(match.group(1))
        authoritative = json.loads(
            (ROOT / "data/external_media_policy.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            authoritative["source_to_image_hosts"],
            embedded["source_to_image_hosts"],
        )
        self.assertIn("i-scmp.com", embedded["source_to_image_hosts"]["scmp.com"])
        self.assertIn(
            "mediacorp.sg",
            embedded["source_to_image_hosts"]["channelnewsasia.com"],
        )
        self.assertIn("365dm.com", embedded["source_to_image_hosts"]["skysports.com"])

    def test_policy_markup_and_runtime_are_updated_idempotently(self) -> None:
        old = (
            '<html><head><script id="br-external-media-policy-data" '
            'type="application/json">{"source_to_image_hosts":{}}</script>'
            '</head><body><script src="/scripts/external-media-guard.js?v=2" '
            "defer></script></body></html>"
        )
        once = ensure_assets(old)
        twice = ensure_assets(once)
        self.assertEqual(once, twice)
        self.assertEqual(1, once.count(f'id="{POLICY_DATA_ID}"'))
        self.assertEqual(1, once.count(RUNTIME))

    def test_runtime_reads_embedded_policy_instead_of_a_second_host_list(self) -> None:
        runtime = (ROOT / "scripts/external-media-guard.js").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "getElementById('br-external-media-policy-data')",
            runtime,
        )
        self.assertNotIn("'scmp.com':", runtime)


if __name__ == "__main__":
    unittest.main()

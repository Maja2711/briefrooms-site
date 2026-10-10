from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import verify_production as verify


class ProductionParityCoverageTests(unittest.TestCase):
    def test_decision_lab_assets_are_protected_by_production_parity(self) -> None:
        required = {
            "pl/inwestycje/decision-lab.html",
            "en/investing/decision-lab.html",
            "scripts/decision-lab.js",
            "scripts/decision-lab-runtime-en.js",
            "assets/decision-lab.css",
        }
        self.assertTrue(required.issubset(set(verify.PARITY_PATHS)))

    def test_axiom_homepage_thought_assets_are_protected_by_parity(self) -> None:
        self.assertIn("scripts/home-intelligence-layout.js", verify.PARITY_PATHS)
        self.assertIn("data/home/axiom-thought.json", verify.PARITY_PATHS)

    def test_pwa_install_badge_script_is_protected_by_production_parity(self) -> None:
        self.assertIn("scripts/pwa.js", verify.PARITY_PATHS)

    def test_long_view_assets_are_protected_by_production_parity(self) -> None:
        required = {
            "pl/inwestycje/long-view.html",
            "en/investing/long-view.html",
            "scripts/long-view-spx.js",
        }
        self.assertTrue(required.issubset(set(verify.PARITY_PATHS)))

    def test_spx_house_view_pages_are_protected_by_production_parity(self) -> None:
        required = {
            "pl/inwestycje/spx-scenariusze-2026.html",
            "en/investing/spx-scenarios-2026.html",
        }
        self.assertTrue(required.issubset(set(verify.PARITY_PATHS)))


class ProductionParityRetryTests(unittest.TestCase):
    def test_exact_file_parity_retries_until_pages_converges(self) -> None:
        calls: list[str] = []

        def fake_fetch(url: str, *, timeout: float) -> verify.FetchResult:
            calls.append(url)
            body = b"old" if "attempt=1" in url else b"current"
            return verify.FetchResult(url=url, status=200, body=body)

        with (
            patch.object(verify, "PARITY_PATHS", ("pl/index.html",)),
            patch.object(verify, "local_bytes", return_value=b"current"),
            patch.object(verify, "fetch", side_effect=fake_fetch),
            patch.object(verify.time, "sleep") as sleep,
        ):
            verify.verify_exact_files(
                "https://briefrooms.com",
                "a" * 40,
                attempts=3,
                interval=10,
                timeout=1,
            )

        self.assertEqual(len(calls), 2)
        self.assertIn("attempt=1", calls[0])
        self.assertIn("attempt=2", calls[1])
        sleep.assert_called_once_with(10)

    def test_exact_file_parity_fails_only_after_retry_budget(self) -> None:
        calls: list[str] = []

        def fake_fetch(url: str, *, timeout: float) -> verify.FetchResult:
            calls.append(url)
            return verify.FetchResult(url=url, status=200, body=b"old")

        with (
            patch.object(verify, "PARITY_PATHS", ("en/index.html",)),
            patch.object(verify, "local_bytes", return_value=b"current"),
            patch.object(verify, "fetch", side_effect=fake_fetch),
            patch.object(verify.time, "sleep") as sleep,
        ):
            with self.assertRaisesRegex(RuntimeError, "after 2 attempts"):
                verify.verify_exact_files(
                    "https://briefrooms.com",
                    "b" * 40,
                    attempts=2,
                    interval=5,
                    timeout=1,
                )

        self.assertEqual(len(calls), 2)
        self.assertIn("attempt=1", calls[0])
        self.assertIn("attempt=2", calls[1])
        sleep.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()

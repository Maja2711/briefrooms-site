from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import verify_production as verify


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

from __future__ import annotations

import unittest
from unittest import mock

from scripts import check_ai_provider as preflight
from scripts import comment_quality as quality


class Response:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload or {
            "choices": [{"message": {"content": '{"ok":true}'}}]
        }

    def json(self):
        return self._payload


class AiProviderPreflightTests(unittest.TestCase):
    def setUp(self):
        self.runtime = quality.AiRuntime(
            "github-models",
            "secret-token",
            "https://models.github.ai/inference/chat/completions",
            "openai/gpt-4o-mini",
            "openai/gpt-4.1-mini",
        )

    def test_preflight_sends_documented_github_headers_once(self):
        post = mock.Mock(return_value=Response(200))
        result = preflight.check_provider(runtime=self.runtime, post=post)
        self.assertEqual("healthy", result["status"])
        self.assertEqual(1, post.call_count)
        headers = post.call_args.kwargs["headers"]
        self.assertEqual("application/vnd.github+json", headers["Accept"])
        self.assertEqual("Bearer secret-token", headers["Authorization"])
        self.assertEqual("2026-03-10", headers["X-GitHub-Api-Version"])

    def test_preflight_classifies_permanent_status_without_retry(self):
        for status in (400, 401, 403, 404, 410, 422):
            with self.subTest(status=status):
                post = mock.Mock(return_value=Response(status))
                with self.assertRaises(preflight.PreflightError) as caught:
                    preflight.check_provider(runtime=self.runtime, post=post)
                self.assertTrue(caught.exception.permanent)
                self.assertEqual(status, caught.exception.status_code)
                self.assertEqual(1, post.call_count)

    def test_preflight_classifies_rate_limit_and_server_errors_as_transient(self):
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                post = mock.Mock(return_value=Response(status))
                with self.assertRaises(preflight.PreflightError) as caught:
                    preflight.check_provider(runtime=self.runtime, post=post)
                self.assertFalse(caught.exception.permanent)
                self.assertEqual(1, post.call_count)

    def test_diagnostics_never_contain_the_token_or_query_string(self):
        runtime = quality.AiRuntime(
            "github-models",
            "do-not-print-me",
            "https://models.github.ai/inference/chat/completions?secret=bad",
            "openai/gpt-4o-mini",
            "openai/gpt-4.1-mini",
        )
        value = preflight.diagnostic(runtime, status="checking")
        rendered = str(value)
        self.assertNotIn("do-not-print-me", rendered)
        self.assertNotIn("secret=bad", rendered)


if __name__ == "__main__":
    unittest.main()

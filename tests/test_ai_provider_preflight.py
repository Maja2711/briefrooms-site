from __future__ import annotations

import sys
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

    def test_gemini_35_preflight_is_minimal_text_health_probe(self):
        runtime = quality.AiRuntime(
            "gemini",
            "secret-token",
            "https://generativelanguage.googleapis.com/v1beta/models",
            "gemini-3.5-flash",
            "gemini-3.5-flash",
        )
        payload = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "OK"}]},
                    "finishReason": "STOP",
                }
            ]
        }
        post = mock.Mock(return_value=Response(200, payload))
        result = preflight.check_provider(runtime=runtime, post=post)
        self.assertEqual("healthy", result["status"])
        request = post.call_args.kwargs["json"]
        config = request["generationConfig"]
        self.assertEqual(128, config["maxOutputTokens"])
        self.assertEqual("minimal", config["thinkingConfig"]["thinkingLevel"])
        self.assertNotIn("temperature", config)
        self.assertNotIn("responseMimeType", config)
        self.assertEqual(45, post.call_args.kwargs["timeout"])

    def test_gemini_empty_200_is_transient_not_permanent(self):
        runtime = quality.AiRuntime(
            "gemini",
            "secret-token",
            "https://generativelanguage.googleapis.com/v1beta/models",
            "gemini-3.5-flash",
            "gemini-3.5-flash",
        )
        payload = {"candidates": [{"content": {}, "finishReason": "MAX_TOKENS"}]}
        post = mock.Mock(return_value=Response(200, payload))
        with self.assertRaises(preflight.PreflightError) as caught:
            preflight.check_provider(runtime=runtime, post=post)
        self.assertFalse(caught.exception.permanent)
        self.assertEqual("provider_output_budget_exhausted", caught.exception.error_class)

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

    def test_missing_provider_secrets_fail_without_calling_retired_provider(self):
        runtime = quality.AiRuntime("unavailable", "", "", "", "")
        post = mock.Mock()
        with self.assertRaises(preflight.PreflightError) as caught:
            preflight.check_provider(runtime=runtime, post=post)
        self.assertEqual("missing_provider_credentials", caught.exception.error_class)
        self.assertTrue(caught.exception.permanent)
        post.assert_not_called()
        details = preflight.diagnostic(runtime, status="failed")
        self.assertEqual("GEMINI_API_KEY or OPENAI_API_KEY", details["required_secret"])
        self.assertIn("Gemini", details["provider_note"])

    def test_cli_allows_explicit_approved_cache_mode_without_credentials(self):
        runtime = quality.AiRuntime("unavailable", "", "", "", "")
        with (
            mock.patch.object(preflight, "get_ai_runtime", return_value=runtime),
            mock.patch.object(
                sys,
                "argv",
                ["check_ai_provider.py", "--allow-approved-cache"],
            ),
        ):
            self.assertEqual(0, preflight.main())


if __name__ == "__main__":
    unittest.main()

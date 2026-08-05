from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import publish_ai_outlook_gemini as publisher
from comment_quality import AiRuntime


class GeminiAiOutlookPublisherTests(unittest.TestCase):
    def runtime(self, provider: str = "gemini", key: str = "secret") -> AiRuntime:
        return AiRuntime(
            provider=provider,
            api_key=key,
            endpoint="https://generativelanguage.googleapis.com/v1beta/models",
            generation_model="gemini-3.5-flash-lite",
            review_model="gemini-3.5-flash-lite",
        )

    def payload(self) -> dict:
        return {
            "schema_version": 2,
            "date": "2026-08-05",
            "generated_at": "2026-08-05T09:00:00+02:00",
            "edition_policy": "independent-per-language",
            "source_policy": {
                "pl": "polish-language-canonical-news-only",
                "en": "english-language-canonical-news-only",
            },
            "generation_mode": "ai_primary",
            "ai_provider": "gemini",
            "ai_model": "gemini-3.5-flash-lite",
            "pl": {
                "source_language": "pl",
                "forecast_id": "2026-08-05-pl-candidate-1",
            },
            "en": {
                "source_language": "en",
                "forecast_id": "2026-08-05-en-candidate-1",
            },
        }

    def test_requires_available_gemini_runtime(self) -> None:
        publisher.require_gemini(self.runtime())
        with self.assertRaisesRegex(RuntimeError, "requires Gemini"):
            publisher.require_gemini(self.runtime(provider="openai"))
        with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY"):
            publisher.require_gemini(self.runtime(key=""))

    def test_rejects_fallback_or_non_gemini_payload(self) -> None:
        payload = self.payload()
        with mock.patch.object(publisher.v3, "validate_payload", return_value=None):
            publisher.validate_payload(payload, today="2026-08-05")
            fallback = dict(payload, generation_mode="deterministic_daily_fallback")
            with self.assertRaisesRegex(RuntimeError, "ai_primary"):
                publisher.validate_payload(fallback, today="2026-08-05")
            openai = dict(payload, ai_provider="openai")
            with self.assertRaisesRegex(RuntimeError, "Gemini-generated"):
                publisher.validate_payload(openai, today="2026-08-05")

    def test_status_records_healthy_gemini_provider(self) -> None:
        payload = self.payload()
        runtime = self.runtime()
        daily = publisher.build_daily_status(payload, runtime)
        provider = publisher.build_provider_status(
            payload,
            runtime,
            {
                "status": "healthy",
                "endpoint": runtime.endpoint,
                "status_code": 200,
            },
        )
        publisher.validate_status(daily, provider, today="2026-08-05")
        self.assertEqual(daily["provider"], "gemini")
        self.assertEqual(provider["publication_mode"], "ai_primary")


if __name__ == "__main__":
    unittest.main()

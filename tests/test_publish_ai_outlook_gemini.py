from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ai_outlook_final_contract_normalizer as final_normalizer
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
        with (
            mock.patch.object(publisher.v3, "validate_payload", return_value=None),
            mock.patch.object(publisher, "validate_pl_edition", return_value=None),
        ):
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

    def test_generation_uses_pl_methodology_and_unchanged_en_path(self) -> None:
        moment = datetime(2026, 8, 5, 9, 0, tzinfo=ZoneInfo("Europe/Warsaw"))
        pl = {"source_language": "pl", "forecast_id": "2026-08-05-pl-real"}
        en = {"source_language": "en", "forecast_id": "2026-08-05-en-existing"}
        runtime = self.runtime()
        with (
            mock.patch.object(
                publisher, "generate_pl_edition", return_value=(pl, {"pl": "audit"})
            ) as pl_generate,
            mock.patch.object(
                publisher.v3,
                "generate_language",
                return_value=(en, {"en": "audit"}),
            ) as en_generate,
            mock.patch.object(publisher.v3, "validate_payload", return_value=None),
            mock.patch.object(publisher, "validate_pl_edition", return_value=None),
        ):
            payload, audit = publisher.generate_verified_payload(moment, runtime)
        pl_generate.assert_called_once_with(moment, runtime)
        en_generate.assert_called_once_with("en", moment, runtime)
        self.assertIs(payload["pl"], pl)
        self.assertIs(payload["en"], en)
        self.assertEqual(audit["editions"]["pl"], {"pl": "audit"})
        self.assertEqual(audit["editions"]["en"], {"en": "audit"})

    def test_probability_event_fallback_for_binary_resolution(self) -> None:
        resolution = {
            "metric": "Wydanie przez TSUE orzeczenia w sprawie pytania NSA",
            "comparison_operator": ">=",
            "threshold": 1.0,
            "unit": "zdarzenie binarne",
            "resolution_date": "2027-12-31",
        }
        text = final_normalizer.probability_event_from_resolution(resolution, "pl")
        self.assertIn("2027-12-31", text)
        self.assertIn("TSUE", text)

    def test_probability_event_fallback_for_numeric_resolution(self) -> None:
        resolution = {
            "metric": "Average Brent crude price",
            "comparison_operator": ">=",
            "threshold": 75.0,
            "unit": "USD per barrel",
            "resolution_date": "2027-08-01",
        }
        text = final_normalizer.probability_event_from_resolution(resolution, "en")
        self.assertEqual(
            text,
            "By 2027-08-01, Average Brent crude price is at least 75 USD per barrel.",
        )

    def test_bilingual_normalizer_overrides_model_probability_wording(self) -> None:
        resolution = {
            "metric": "Average Brent crude price",
            "comparison_operator": ">=",
            "threshold": 75.0,
            "unit": "USD per barrel",
            "resolution_date": "2027-08-01",
        }
        raw = {
            "pl": {"probability_event": "Ogólny wzrost cen energii w przyszłości."},
            "en": {"probability_event": "Energy markets stay strong."},
        }
        normalized = final_normalizer._normalize_bilingual(raw, resolution)
        self.assertIn("Average Brent crude price", normalized["pl"]["probability_event"])
        self.assertIn("75 USD per barrel", normalized["pl"]["probability_event"])
        self.assertEqual(
            normalized["en"]["probability_event"],
            "By 2027-08-01, Average Brent crude price is at least 75 USD per barrel.",
        )


if __name__ == "__main__":
    unittest.main()

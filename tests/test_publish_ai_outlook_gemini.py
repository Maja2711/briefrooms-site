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
    def runtime(
        self,
        provider: str = "gemini",
        key: str = "secret",
        model: str = "gemini-3.5-flash-lite",
    ) -> AiRuntime:
        return AiRuntime(
            provider=provider,
            api_key=key,
            endpoint="https://generativelanguage.googleapis.com/v1beta/models",
            generation_model=model,
            review_model=model,
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
            "ai_model_role": "fallback",
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

    def test_budget_policy_rejects_unapproved_model(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "budget policy"):
            publisher.require_gemini(self.runtime(model="gemini-3.1-pro"))

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
            expensive = dict(payload, ai_model="gemini-3.1-pro")
            with self.assertRaisesRegex(RuntimeError, "budget allowlist"):
                publisher.validate_payload(expensive, today="2026-08-05")

    def test_status_records_healthy_gemini_provider(self) -> None:
        payload = self.payload()
        runtime = self.runtime()
        daily = publisher.build_daily_status(
            payload, runtime, model_role="fallback", primary_error="primary failed"
        )
        provider = publisher.build_provider_status(
            payload,
            runtime,
            {
                "status": "healthy",
                "endpoint": runtime.endpoint,
                "status_code": 200,
            },
            model_role="fallback",
            primary_error="primary failed",
        )
        publisher.validate_status(daily, provider, today="2026-08-05")
        self.assertEqual(daily["provider"], "gemini")
        self.assertEqual(provider["publication_mode"], "ai_primary")
        self.assertTrue(daily["fallback_used"])
        self.assertTrue(provider["fallback_used"])
        self.assertEqual(daily["primary_error"], "primary failed")

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

    def test_final_json_budget_is_expanded_without_inflating_candidate_budget(self) -> None:
        original = mock.Mock(return_value={"ok": True})
        wrapped = publisher._inflate_final_json_budget(original)
        wrapped(
            post=mock.Mock(),
            runtime=self.runtime(),
            messages=[],
            max_tokens=2300,
            temperature=0.1,
        )
        self.assertEqual(
            original.call_args.kwargs["max_tokens"], publisher.FINAL_OUTPUT_TOKEN_FLOOR
        )
        original.reset_mock()
        wrapped(
            post=mock.Mock(),
            runtime=self.runtime(),
            messages=[],
            max_tokens=3600,
            temperature=0.1,
        )
        self.assertEqual(original.call_args.kwargs["max_tokens"], 3600)

    def test_publish_falls_back_to_lite_and_publishes_only_valid_result(self) -> None:
        base = self.runtime(model=publisher.PRIMARY_MODEL)
        payload = self.payload()
        audit = {}
        healthy = {
            "status": "healthy",
            "endpoint": base.endpoint,
            "status_code": 200,
        }
        with (
            mock.patch.object(publisher, "current_is_valid", return_value=False),
            mock.patch.object(publisher, "get_ai_runtime", return_value=base),
            mock.patch.object(
                publisher,
                "check_provider",
                side_effect=[RuntimeError("primary unavailable"), healthy],
            ) as health_check,
            mock.patch.object(
                publisher,
                "_generate_verified_payload_with_budget",
                return_value=(payload, audit),
            ),
            mock.patch.object(publisher, "validate_payload", return_value=None),
            mock.patch.object(publisher, "validate_status", return_value=None),
            mock.patch.object(publisher.v3, "publish") as publish_write,
            mock.patch.object(publisher, "write_json"),
        ):
            result = publisher.publish(force=True)

        checked_models = [
            call.kwargs["runtime"].generation_model for call in health_check.call_args_list
        ]
        self.assertEqual(
            checked_models, [publisher.PRIMARY_MODEL, publisher.FALLBACK_MODEL]
        )
        self.assertEqual(result["ai_model"], publisher.FALLBACK_MODEL)
        self.assertEqual(result["ai_model_role"], "fallback")
        publish_write.assert_called_once()

    def test_publish_writes_nothing_when_both_budget_models_fail(self) -> None:
        base = self.runtime(model=publisher.PRIMARY_MODEL)
        with (
            mock.patch.object(publisher, "current_is_valid", return_value=False),
            mock.patch.object(publisher, "get_ai_runtime", return_value=base),
            mock.patch.object(
                publisher,
                "check_provider",
                side_effect=[RuntimeError("full failed"), RuntimeError("lite failed")],
            ),
            mock.patch.object(publisher.v3, "publish") as publish_write,
            mock.patch.object(publisher, "write_json") as status_write,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "neither approved budget Gemini model"
            ):
                publisher.publish(force=True)
        publish_write.assert_not_called()
        status_write.assert_not_called()

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

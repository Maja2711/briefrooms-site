"""Synthetic as-of fixtures; none are market observations or persisted trades."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import daily_eurusd_belief_decision as belief
import daily_eurusd_fast_lifecycle as fast
import daily_eurusd_macro_fast as macro
import daily_eurusd_spot_v19 as daily
import eurusd_central_bank_wire as wire_module
from belief_adapter_contract import Observation
from belief_market_data_adapter import Bar
from eurusd_central_bank_event_risk import DEFAULT_POLICY
from eurusd_daily_cb_event_adapter import evaluate_daily, safe_evaluate_daily

UTC = timezone.utc
NOW = datetime(2026, 10, 28, 18, 5, tzinfo=UTC)
EVENT_AT = NOW - timedelta(minutes=5)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def synthetic_wire(*, bank="FED", actual=True, now=NOW, scheduled=EVENT_AT):
    payload = wire_module._empty(now)
    payload["overall_status"] = "FRESH"
    payload["sources"] = [{"source_id": key, "url": value, "status": "OK", "checked_at": stamp(now)}
        for key, value in wire_module.OFFICIAL_RSS.items()]
    payload["integrity"].update(source_count=len(payload["sources"]), fresh_source_count=len(payload["sources"]))
    payload["calendar_coverage"] = {"complete": True, "checked_at": stamp(now), "sources": {
        "FOMC": {"status": "ok", "source_ref": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"},
        "ECB": {"status": "ok", "source_ref": "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"}}}
    source = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm" if bank == "FED" else "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"
    payload["event_window"] = {"event_id": "synthetic-" + bank,
        "scheduled_at": stamp(scheduled), "bank": bank, "conference_status": "UNKNOWN",
        "source_url": source, "first_seen_at": stamp(now - timedelta(minutes=10)), "time_precision": "exact"}
    if actual:
        payload["events"] = [{"event_id": "synthetic-statement-" + bank, "bank": bank,
            "event_type": "FOMC_DECISION" if bank == "FED" else "ECB_DECISION",
            "source_class": "OFFICIAL_STATEMENT", "statement_verified": True,
            "speaker_verified": False, "headline": "Synthetic official decision test fixture",
            "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/synthetic-test.htm" if bank == "FED" else "https://www.ecb.europa.eu/press/pr/date/synthetic-test.html",
            "published_at": stamp(scheduled), "first_seen_at": stamp(scheduled + timedelta(seconds=20)),
            "ingested_at": stamp(scheduled + timedelta(seconds=21)), "document_sha256": "synthetic-test",
            "fact_evidence": [{"source_url": source, "text": "Synthetic fixture only"}],
            "facts": {"rate_actual": {"lower": 3.0, "upper": 3.25} if bank == "FED" else {"deposit_facility": 2.0},
                "rate_previous": None, "rate_expected": None, "decision": "HOLD", "expectations_status": "MISSING"}}]
    payload["integrity"]["snapshot_sha256"] = wire_module.snapshot_hash(payload)
    assert wire_module.validate_snapshot(payload, now)
    return payload


class StaticAdapter:
    def __init__(self, rows=()):
        self.rows = rows

    def run(self, now):
        return SimpleNamespace(observations=self.rows, evidence=())

    def source_status(self):
        return {"complete": True, "sources": {}}


class NoInterpreter:
    available = False


def calendar(bank="FED", scheduled=EVENT_AT):
    return Observation.make(adapter="macro_event_calendar", metric="scheduled_macro_event", entity="EURUSD",
        observed_at=stamp(NOW - timedelta(hours=1)), value=0, unit="hours_until_event", source=bank,
        source_type="primary", source_ref="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm" if bank == "FED" else "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html",
        reliability=1, independence_cluster="synthetic-calendar", metadata={"uid": "synthetic-" + bank,
            "title": "FOMC decision" if bank == "FED" else "ECB monetary policy decisions",
            "event_at": stamp(scheduled), "importance": "high", "time_precision": "exact"})


def position():
    return {"trade_id": "synthetic-daily-position", "status": "OPEN", "direction": "SHORT",
        "entry": 1.11869, "stop": 1.12487, "target": 1.10220, "opened_at": stamp(NOW - timedelta(hours=2)),
        "expires_at": stamp(NOW + timedelta(hours=25)), "decision_manifest": {"immutable": "original"}}


class DailyCBIntegrationTests(unittest.TestCase):
    def build(self, facts, bank="FED", scheduled=EVENT_AT):
        with mock.patch.object(macro.MacroDataAdapter, "run", side_effect=AssertionError("CB must not use BLS")):
            return macro.build_context(NOW, calendar=StaticAdapter((calendar(bank, scheduled),)),
                macro_data=macro.MacroDataAdapter(), expectations=StaticAdapter(), interpreter=NoInterpreter(), wire=facts)

    def test_fomc_after_19_warsaw_is_ready_without_llm_and_no_direction(self):
        context = self.build(synthetic_wire())
        self.assertEqual(context["status"], "POST_RELEASE")
        self.assertEqual(context["actuals_status"], "READY")
        self.assertTrue(context["post_release_ready"])
        self.assertEqual(context["actuals"][0]["metadata"]["published_at"], stamp(EVENT_AT))
        self.assertEqual(context["actuals"][0]["source_ref"], synthetic_wire()["events"][0]["source_url"])
        self.assertFalse(context["decision_influence"])
        self.assertEqual(context["eurusd_score"], 0)
        self.assertEqual(context["expectations_status"], "MISSING")
        self.assertEqual(context["rules"]["official_actual_period_contract"], "OFFICIAL_CENTRAL_BANK_DOCUMENT")

    def test_ecb_actuals_use_same_wire_and_do_not_require_bls_period(self):
        context = self.build(synthetic_wire(bank="ECB"), bank="ECB")
        self.assertEqual(context["status"], "POST_RELEASE")
        self.assertEqual(context["actuals"][0]["metric"], "ecb_policy_rates")
        self.assertNotIn("data_period", context["actuals"][0]["metadata"])

    def test_missing_official_statement_is_explicit_awaiting_not_pre_release(self):
        context = self.build(synthetic_wire(actual=False))
        self.assertEqual(context["status"], "AWAITING_OFFICIAL_DECISION")
        self.assertEqual(context["phase"], "POST_RELEASE")
        self.assertEqual(context["actuals"], [])
        self.assertFalse(context["post_release_ready"])

    def test_unscheduled_official_decision_is_ready_without_calendar_or_direction(self):
        for bank in ("FED", "ECB"):
            with self.subTest(bank=bank):
                facts = synthetic_wire(bank=bank)
                facts["event_window"] = wire_module.event_window((), facts["events"], NOW)
                facts["integrity"]["snapshot_sha256"] = wire_module.snapshot_hash(facts)
                with mock.patch.object(macro.MacroDataAdapter, "run", side_effect=AssertionError("CB must not fetch BLS")), mock.patch.object(wire_module.HttpClient, "get", side_effect=AssertionError("consumer must not fetch wire")):
                    context = macro.build_context(NOW, calendar=StaticAdapter(), macro_data=macro.MacroDataAdapter(),
                        expectations=StaticAdapter(), interpreter=NoInterpreter(), wire=facts)
                self.assertEqual(context["status"], "POST_RELEASE")
                self.assertTrue(context["post_release_ready"])
                self.assertEqual(context["actuals_status"], "READY")
                self.assertEqual(context["event"]["schedule_kind"], "UNSCHEDULED_RELEASE")
                self.assertEqual(context["event"]["event_at"], facts["events"][0]["published_at"])
                self.assertEqual(context["actuals"][0]["source_ref"], facts["events"][0]["source_url"])
                self.assertFalse(context["decision_influence"])
                self.assertEqual(context["eurusd_score"], 0)

    def test_unscheduled_statement_at_exact_publication_time_is_post_release(self):
        facts = synthetic_wire()
        for field in ("published_at", "first_seen_at", "ingested_at"):
            facts["events"][0][field] = stamp(NOW)
        facts["event_window"] = wire_module.event_window((), facts["events"], NOW)
        facts["integrity"]["snapshot_sha256"] = wire_module.snapshot_hash(facts)
        context = macro.build_context(NOW, calendar=StaticAdapter(), macro_data=StaticAdapter(),
            expectations=StaticAdapter(), interpreter=NoInterpreter(), wire=facts)
        self.assertEqual(context["status"], "POST_RELEASE")
        self.assertTrue(context["post_release_ready"])
        self.assertEqual(context["event"]["hours_until_at_check"], 0)

    def test_stale_unverified_or_corrupt_wire_cannot_create_unscheduled_event(self):
        original = synthetic_wire()
        original["event_window"] = wire_module.event_window((), original["events"], NOW)
        for failure in ("stale_asof", "unverified_document", "corrupt_hash", "unavailable"):
            with self.subTest(failure=failure):
                facts = copy.deepcopy(original)
                if failure == "stale_asof":
                    facts["as_of"] = stamp(NOW - timedelta(minutes=30))
                elif failure == "unverified_document":
                    facts["events"][0]["statement_verified"] = False
                elif failure == "unavailable":
                    facts["overall_status"] = "UNAVAILABLE"
                facts["integrity"]["snapshot_sha256"] = wire_module.snapshot_hash(facts)
                if failure == "corrupt_hash":
                    facts["integrity"]["snapshot_sha256"] = "0" * 64
                context = macro.build_context(NOW, calendar=StaticAdapter(), macro_data=StaticAdapter(),
                    expectations=StaticAdapter(), interpreter=NoInterpreter(), wire=facts)
                self.assertIsNone(context["event"])
                self.assertEqual(context["actuals"], [])
                self.assertFalse(context["decision_influence"])

    def test_time_proximity_cannot_attach_unrelated_consensus_to_fomc(self):
        expectation = Observation.make(adapter="macro_expectations", metric="macro_expectation_distribution",
            entity="EURUSD", observed_at=stamp(NOW - timedelta(hours=1)), value={"consensus": 3.25},
            unit="percent", source="Synthetic sourced forecast", source_type="secondary",
            source_ref="https://example.test/synthetic", reliability=.8, independence_cluster="synthetic-expectation",
            metadata={"event_at": stamp(EVENT_AT), "event_uid": "payroll-not-fomc"})
        self.assertIsNone(macro._expectation_for_event((expectation,), {"title": "FOMC decision",
            "event_at": stamp(EVENT_AT), "uid": "synthetic-FED"}, NOW))

    def test_future_ingestion_cannot_be_used_as_live_actual(self):
        facts = synthetic_wire()
        facts["events"][0]["ingested_at"] = stamp(NOW + timedelta(seconds=1))
        facts["integrity"]["snapshot_sha256"] = wire_module.snapshot_hash(facts)
        self.assertEqual(self.build(facts)["actuals_status"], "AWAITING_OFFICIAL_DECISION")

    def test_conference_still_visible_two_hours_after_statement(self):
        scheduled = NOW - timedelta(hours=2)
        context = self.build(synthetic_wire(actual=False, scheduled=scheduled), scheduled=scheduled)
        self.assertEqual(context["status"], "AWAITING_OFFICIAL_DECISION")
        self.assertEqual(context["central_bank_wire"]["event_window"]["conference_status"], "UNKNOWN")

    def test_daily_geometry_and_immutable_manifest_are_preserved(self):
        p = position()
        before = copy.deepcopy(p)
        policy = copy.deepcopy(DEFAULT_POLICY)
        quote = {"bid": 1.11367, "ask": 1.11369, "observed_at": stamp(NOW), "source": "synthetic-asof", "verified": True, "cost_pips": 0}
        result = evaluate_daily(p, (), NOW, wire=synthetic_wire(), policy=policy, quote=quote)
        self.assertEqual(p, before)
        self.assertEqual(result["mode"], "SHADOW")
        self.assertFalse(result["execution_authority"])
        self.assertEqual(result["position_id"], p["trade_id"])
        self.assertAlmostEqual(result["current_net_R"], 50 / 61.8, places=6)
        self.assertGreater(result["diagnostics"]["profit_at_risk_pips"], 110)

    def test_stale_mark_has_no_close_price_or_execution(self):
        bars = [Bar(timestamp=NOW - timedelta(minutes=20), open=1.11369, high=1.1137, low=1.1136, close=1.11369)]
        result = evaluate_daily(position(), bars, NOW, wire=synthetic_wire(), policy=DEFAULT_POLICY)
        self.assertFalse(result["quote_verified"])
        self.assertIsNone(result["close_now_net_R"])
        self.assertEqual(result["action"], "NO_ACTION_UNVERIFIED")

    def test_future_bars_cannot_change_shadow_economics(self):
        present = Bar(timestamp=NOW, open=1.11369, high=1.1137, low=1.1136, close=1.11369)
        future = Bar(timestamp=NOW + timedelta(minutes=1), open=1.0, high=1.0, low=.9, close=1.0)
        first = evaluate_daily(position(), (present,), NOW, wire=synthetic_wire(), policy=DEFAULT_POLICY)
        second = evaluate_daily(position(), (present, future), NOW, wire=synthetic_wire(), policy=DEFAULT_POLICY)
        self.assertEqual(first, second)

    def test_wire_failure_is_observable_and_does_not_raise_into_exit_path(self):
        with mock.patch("eurusd_central_bank_wire.load_wire", side_effect=RuntimeError("feed down")):
            result = safe_evaluate_daily(position(), (), NOW)
        self.assertEqual(result["risk_protection_status"], "PROTECTION_PENDING")
        self.assertEqual(result["mode"], "SHADOW")

    def test_fast_probe_records_shadow_close_without_writing_position(self):
        p = position()
        payload = {"metadata": {"position": p}}
        bars = [Bar(timestamp=NOW, open=1.11369, high=1.1137, low=1.1136, close=1.11369)]
        client = StaticAdapter()
        client.bars = lambda *args: bars
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "output.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            before = path.read_bytes()
            with mock.patch.object(fast.v18, "_evaluate_position", return_value=None), mock.patch.object(fast.macro_event_risk, "assess_central_bank_position", return_value={"action": "CLOSE_BEFORE_EVENT", "mode": "SHADOW"}):
                probe = fast.probe(path, client)
                applied = fast.apply_if_triggered(path, Path(td) / "history.json", client)
            self.assertFalse(probe["exit_triggered"])
            self.assertFalse(applied["changed"])
            self.assertEqual(probe["central_bank_event_risk"]["action"], "CLOSE_BEFORE_EVENT")
            self.assertEqual(before, path.read_bytes())
            self.assertFalse((Path(td) / "history.json").exists())

    def test_fast_audit_receipt_is_immutable_and_idempotent(self):
        assessment = {"scope": "EURUSD_DAILY", "action": "CLOSE_BEFORE_EVENT", "mode": "SHADOW", "executed_at": None}
        with tempfile.TemporaryDirectory() as td:
            receipt = fast.save_cb_audit(Path(td), assessment, position=position())
            first = receipt.read_bytes()
            self.assertEqual(receipt, fast.save_cb_audit(Path(td), assessment, position=position()))
            self.assertEqual(first, receipt.read_bytes())
            self.assertEqual(len(list(Path(td).glob("*.json"))), 1)
            receipt.write_text("corrupt receipt", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "immutable"):
                fast.save_cb_audit(Path(td), assessment, position=position())
            self.assertEqual(receipt.read_text(encoding="utf-8"), "corrupt receipt")

    def test_private_audit_destination_is_guarded_before_write(self):
        private = position()
        private["execution_price_engine"] = {"private_saxo_primary": True}
        with tempfile.TemporaryDirectory() as td, mock.patch.object(fast, "assert_private_state_destination", side_effect=ValueError("private destination rejected")) as guard:
            with self.assertRaises(ValueError):
                fast.save_cb_audit(Path(td) / "must-not-exist", {"action": "HOLD"}, position=private)
            guard.assert_called_once()
            self.assertFalse((Path(td) / "must-not-exist").exists())

    def test_audit_storage_failure_does_not_hide_existing_hard_exit(self):
        payload = {"metadata": {"position": position()}}
        result = {"position_open": True, "exit_triggered": True, "exit_reason": "STOP_LOSS",
            "central_bank_event_risk": {"action": "NO_ACTION_UNVERIFIED", "mode": "SHADOW"}}
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "output.json"
            receipt = Path(td) / "probe.json"
            output.write_text(json.dumps(payload), encoding="utf-8")
            argv = ["daily_eurusd_fast_lifecycle.py", "--output", str(output), "--probe-json", str(receipt),
                    "--cb-audit-dir", str(Path(td) / "audit")]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(fast, "probe", return_value=result), mock.patch.object(fast, "save_cb_audit", side_effect=OSError("storage unavailable")):
                self.assertEqual(fast.main(), 0)
            persisted = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertTrue(persisted["exit_triggered"])
            self.assertEqual(persisted["central_bank_audit_status"], "RECOVERY_REQUIRED")

    def test_activated_entry_guard_retains_unknown_conference_beyond_30m(self):
        scheduled = NOW - timedelta(hours=2)
        facts = synthetic_wire(scheduled=scheduled)
        policy = copy.deepcopy(DEFAULT_POLICY)
        policy["entry_guard_enabled"] = True
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            path.write_text("{}", encoding="utf-8")
            coverage = {"adapter": "macro_event_calendar", "metric": "calendar_coverage",
                "observed_at": stamp(NOW), "metadata": {"complete": True}}
            (path.parent / "observations.jsonl").write_text(json.dumps(coverage) + "\n", encoding="utf-8")
            with mock.patch("eurusd_central_bank_wire.load_wire", return_value=facts), mock.patch("eurusd_central_bank_event_risk.load_policy", return_value=policy):
                gate = belief.calendar_safety(observed_at=NOW, state_path=path)
        self.assertTrue(gate["blocked"])
        self.assertEqual(gate["reason"], "central_bank_event_risk_entry_guard")
        self.assertEqual(gate["central_bank_event_risk"]["diagnostics"]["conference_status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()

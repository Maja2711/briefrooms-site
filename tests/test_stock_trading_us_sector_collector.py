"""US classification transport fallback under the unchanged risk contract."""
from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

from scripts import stock_trading_portfolio as portfolio
from scripts import stock_trading_sourced_metadata as sourced
from scripts import stock_trading_us_sector_collector as collector


class USSectorCollectorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
        self.policy = portfolio.load_policy()
        self.state = portfolio.empty_state(self.now, self.policy)
        self.names = {
            "SAP": ("SAP SE ADS", "SAP SE", "NYSE", "Technology"),
            "NXPI": ("NXP Semiconductors N.V. - Common Stock", "NXP Semiconductors N.V.", "NASDAQ-GS", "Technology"),
            "MCK": ("McKesson Corporation Common Stock", "McKesson Corporation", "NYSE", "Health Care"),
        }
        self.universe = {"instruments": []}
        self.profiles, self.summaries = {}, {}
        for symbol, (listed_name, profile_name, exchange, sector) in self.names.items():
            self.state["markets"]["US"]["open_positions"].append({
                "position_id": f"original-{symbol}", "market": "US", "symbol": symbol,
                "name": listed_name, "status": "OPEN", "entry": 100, "last_mark": 100,
                "stop": 95, "quantity": 25, "sector": None,
            })
            self.universe["instruments"].append({
                "market": "US", "listing_symbol": symbol, "market_data_symbol": symbol,
                "name": listed_name, "exchange": "NASDAQ" if exchange == "NASDAQ-GS" else exchange,
                "source": {"provider": "Nasdaq Trader"},
            })
            # Only fields actually present in the captured Nasdaq responses are
            # necessary: classification and independent symbol/exchange proof.
            self.profiles[symbol] = {
                "data": {"Symbol": {"value": symbol}, "CompanyName": {"value": profile_name},
                         "Sector": {"value": sector}, "Industry": {"value": "Source industry"}},
                "message": None, "status": {"rCode": 200},
            }
            self.summaries[symbol] = {
                "data": {"symbol": symbol, "assetClass": "STOCKS", "summaryData": {
                    "Exchange": {"value": exchange}, "Sector": {"value": sector},
                    "Industry": {"value": "Source industry"}}},
                "message": None, "status": {"rCode": 200},
            }
        self.calls = []

    def fetch(self, url):
        parsed = urlsplit(url)
        self.assertEqual(("https", "api.nasdaq.com"), (parsed.scheme, parsed.hostname))
        parts = parsed.path.strip("/").split("/")
        self.assertEqual("api", parts[0])
        symbol = parts[2]
        self.calls.append(url)
        payload = self.profiles[symbol] if parts[1] == "company" else self.summaries[symbol]
        return json.dumps(payload, separators=(",", ":")).encode()

    def collect(self, ledger=None, **kwargs):
        ledger = ledger if ledger is not None else sourced.empty_ledger()
        report = collector.collect(self.state, self.universe, ledger, now=self.now,
                                   fetcher=self.fetch, **kwargs)
        return report, ledger

    def only_sap(self):
        self.state["markets"]["US"]["open_positions"] = [self.state["markets"]["US"]["open_positions"][0]]

    def candidate(self, symbol, *, utility=60, eligible=True):
        name = f"Example {symbol} Technology Corporation"
        self.universe["instruments"].append({
            "market": "US", "listing_symbol": symbol, "market_data_symbol": symbol,
            "name": name, "exchange": "NYSE", "source": {"provider": "Nasdaq Trader"}})
        self.profiles[symbol] = deepcopy(self.profiles["SAP"])
        self.profiles[symbol]["data"]["Symbol"]["value"] = symbol
        self.profiles[symbol]["data"]["CompanyName"]["value"] = name
        self.summaries[symbol] = deepcopy(self.summaries["SAP"])
        self.summaries[symbol]["data"]["symbol"] = symbol
        return {"market_data_symbol": symbol, "symbol": symbol, "name": name,
                "eligible": eligible, "utility": utility, "deep_rank": 1}

    def test_three_actual_issuer_shapes_resolve_without_bulk_or_history_writes(self):
        before = json.dumps(self.state, sort_keys=True)
        report, ledger = self.collect()
        self.assertEqual("OBSERVED", report["status"])
        self.assertEqual(3, report["counts"]["verified"])
        self.assertEqual(6, len(self.calls))
        self.assertEqual({"SAP": "Technology", "NXPI": "Technology", "MCK": "Health Care"},
                         {row["symbol"]: row["sector"] for row in ledger["observations"]})
        for position in self.state["markets"]["US"]["open_positions"]:
            row = sourced.sector_observation(position, "US", ledger, now=self.now)
            self.assertIsNotNone(row)
            self.assertEqual("Nasdaq", row["source"]["provider"])
            self.assertEqual("api.nasdaq.com", urlsplit(row["source"]["url"]).hostname)
        self.assertEqual(before, json.dumps(self.state, sort_keys=True))

    def test_cache_reuses_records_and_preserves_existing_ledger_and_portfolio_bytes(self):
        ledger = sourced.empty_ledger()
        old = sourced.append_observation(ledger, {
            "kind": "SECTOR", "market": "GPW", "symbol": "LPP.WA", "sector": "odzież i obuwie",
            "source": {"provider": "GPW", "url": "https://www.gpw.pl/spolka?isin=PLLPP0000011"},
            "identity": {"market": "GPW", "symbol": "LPP.WA", "market_data_symbol": "LPP.WA",
                         "name": "LPP", "exchange": "GPW_MAIN_MARKET", "isin": "PLLPP0000011"},
            "observed_at": self.now.isoformat(), "received_at": self.now.isoformat(),
        })
        original_record = deepcopy(old)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "portfolio.json"
            state_bytes = json.dumps(self.state, indent=4).encode()
            state_path.write_bytes(state_bytes)
            self.collect(ledger)
            sourced._atomic(root / "metadata.json", ledger)
            first_bytes = (root / "metadata.json").read_bytes()
            def unexpected_fetch(url):
                raise AssertionError("fresh verified records must skip every transport")
            report = collector.collect(self.state, self.universe, ledger, now=self.now + timedelta(hours=1),
                                       fetcher=unexpected_fetch)
            sourced._atomic(root / "metadata.json", ledger)
            self.assertEqual(3, report["counts"]["reused"])
            self.assertEqual(first_bytes, (root / "metadata.json").read_bytes())
            self.assertEqual(state_bytes, state_path.read_bytes())
            self.assertEqual(original_record, ledger["observations"][0])

    def test_actual_fetch_completion_time_is_used_and_future_receipts_are_not_available_early(self):
        self.only_sap()
        finished = self.now + timedelta(seconds=17)
        _, ledger = self.collect(clock=lambda: finished)
        row = ledger["observations"][0]
        self.assertEqual(finished, sourced._dt(row["received_at"]))
        position = self.state["markets"]["US"]["open_positions"][0]
        self.assertIsNone(sourced.sector_observation(position, "US", ledger, now=self.now))
        self.assertIsNotNone(sourced.sector_observation(position, "US", ledger, now=finished))

    def test_wrong_symbol_name_exchange_sector_or_incomplete_payload_never_appends(self):
        self.only_sap()
        original_profile, original_summary = deepcopy(self.profiles["SAP"]), deepcopy(self.summaries["SAP"])
        changes = (
            ("profile_symbol", lambda p, s: p["data"]["Symbol"].update(value="OTHER")),
            ("summary_symbol", lambda p, s: s["data"].update(symbol="OTHER")),
            ("issuer", lambda p, s: p["data"]["CompanyName"].update(value="Unrelated Mining Company")),
            ("exchange", lambda p, s: s["data"]["summaryData"]["Exchange"].update(value="NASDAQ-GS")),
            ("sector_disagreement", lambda p, s: s["data"]["summaryData"]["Sector"].update(value="Finance")),
            ("unknown_sector", lambda p, s: (p["data"]["Sector"].update(value="Invented Sector"), s["data"]["summaryData"]["Sector"].update(value="Invented Sector"))),
            ("missing_name", lambda p, s: p["data"].pop("CompanyName")),
            ("missing_exchange", lambda p, s: s["data"]["summaryData"].pop("Exchange")),
            ("wrong_asset", lambda p, s: s["data"].update(assetClass="ETF")),
            ("provider_error", lambda p, s: s["status"].update(rCode=500)),
            ("empty_data", lambda p, s: p.update(data=None)),
        )
        for name, mutate in changes:
            with self.subTest(name=name):
                self.profiles["SAP"], self.summaries["SAP"] = deepcopy(original_profile), deepcopy(original_summary)
                mutate(self.profiles["SAP"], self.summaries["SAP"])
                report, ledger = self.collect()
                self.assertEqual("PARTIAL", report["status"])
                self.assertEqual([], ledger["observations"])
                self.assertEqual(1, report["counts"]["unavailable"])
                self.assertTrue(report["errors"])

    def test_malformed_response_is_reported_and_does_not_break_bulk_fallback(self):
        self.only_sap()
        ledger = sourced.empty_ledger()
        failed = collector.collect(self.state, self.universe, ledger, now=self.now,
                                   fetcher=lambda url: b'{"data":')
        self.assertEqual("PARTIAL", failed["status"])
        self.assertEqual([], ledger["observations"])
        resolved = sourced.collect(self.state, {"US": self.universe}, ledger, now=self.now,
                                   nasdaq_fetcher=lambda **kwargs: ([{
                                       "symbol": "SAP", "name": "SAP SE ADS", "sector": "Technology"}], {}))
        self.assertEqual("Technology", resolved["markets"]["US"][0]["sector"])
        self.assertEqual(1, len(ledger["observations"]))

    def test_failure_retains_valid_old_classification_but_stale_or_future_is_not_reused(self):
        self.only_sap()
        _, ledger = self.collect()
        original = deepcopy(ledger["observations"][0])
        def unavailable(url):
            raise TimeoutError("provider unavailable")
        report = collector.collect(self.state, self.universe, ledger, now=self.now + timedelta(days=2), fetcher=unavailable)
        self.assertEqual("PARTIAL", report["status"])
        self.assertEqual(1, report["counts"]["verified"])
        self.assertEqual(original, ledger["observations"][0])
        self.assertEqual(1, len(ledger["observations"]))
        position = self.state["markets"]["US"]["open_positions"][0]
        self.assertIsNone(sourced.sector_observation(position, "US", ledger, now=self.now - timedelta(seconds=1)))
        self.assertIsNone(sourced.sector_observation(position, "US", ledger, now=self.now + timedelta(days=31)))
        stale = collector.collect(self.state, self.universe, ledger, now=self.now + timedelta(days=31), fetcher=unavailable)
        self.assertEqual(1, stale["counts"]["unavailable"])
        self.assertEqual(original, ledger["observations"][0])

    def test_health_care_aliases_keep_the_published_nasdaq_sector_label(self):
        self.state["markets"]["US"]["open_positions"] = [self.state["markets"]["US"]["open_positions"][2]]
        self.profiles["MCK"]["data"]["Sector"]["value"] = "HealthCare"
        self.summaries["MCK"]["data"]["summaryData"]["Sector"]["value"] = "Health Care"
        _, ledger = self.collect()
        self.assertEqual("Health Care", ledger["observations"][0]["sector"])
        position = self.state["markets"]["US"]["open_positions"][0]
        risk_sector = sourced.risk_sector(position, "US", ledger, now=self.now)
        self.assertEqual("health care", portfolio.sector_identity({**position, **risk_sector}, now=self.now))

    def test_sourced_current_positions_expose_concentration_without_mutating_history(self):
        original = json.dumps(self.state, sort_keys=True)
        _, ledger = self.collect()
        candidate = {"symbol": "NEW", "name": "New Technology", "sector": "Technology",
                     "sector_provenance": {"source": "prospectively frozen candidate evidence", "observed_at": self.now.isoformat()},
                     "market_snapshot": {"last": 100}, "stop": 99}
        risk = portfolio.portfolio_admission_risk(self.state, "US", candidate, policy=self.policy,
                                                 now=self.now, risk_metadata=ledger)
        self.assertEqual({"technology": 2, "health care": 1}, risk["sector_positions"])
        self.assertEqual(0, risk["unknown_sector_positions"])
        self.assertEqual("portfolio_sector_concentration_exceeded", risk["reason"])
        self.assertEqual(3, len(risk["current_sector_observation_ids"]))
        self.assertEqual(original, json.dumps(self.state, sort_keys=True))

    def test_unknown_candidate_is_resolved_by_ledger_and_third_technology_is_rejected(self):
        candidate = self.candidate("NEW")
        opportunity = {"market": "US", "generated_at": self.now.isoformat(),
                       "decision": {"candidate": candidate}, "evaluated_candidates": [candidate]}
        original_state, original_opportunity = json.dumps(self.state, sort_keys=True), json.dumps(opportunity, sort_keys=True)
        _, ledger = self.collect(opportunity=opportunity)
        # The selection has no inline classification to accidentally supply
        # the answer. The same ledger-aware production risk function must find
        # the newly collected candidate receipt as well as both actual holdings.
        selection = {"symbol": "NEW", "name": candidate["name"],
                     "market_snapshot": {"last": 100}, "stop": 99}
        risk = portfolio.portfolio_admission_risk(self.state, "US", selection, policy=self.policy,
                                                 now=self.now, risk_metadata=ledger)
        self.assertEqual("technology", risk["candidate_sector"])
        self.assertEqual({"technology": 2, "health care": 1}, risk["sector_positions"])
        self.assertEqual("portfolio_sector_concentration_exceeded", risk["reason"])
        self.assertEqual(4, len(risk["current_sector_observation_ids"]))
        self.assertEqual(4, len(ledger["observations"]))
        self.assertEqual(original_state, json.dumps(self.state, sort_keys=True))
        self.assertEqual(original_opportunity, json.dumps(opportunity, sort_keys=True))

    def test_holdings_and_ranked_candidates_share_one_six_issuer_budget(self):
        candidates = [self.candidate(f"CAND{index}", utility=utility)
                      for index, utility in enumerate((61, 63, 62, 60))]
        ignored = self.candidate("IGNORED", utility=100, eligible=False)
        held_duplicate = {"market_data_symbol": "SAP", "symbol": "SAP", "name": self.names["SAP"][0],
                          "eligible": True, "utility": 100, "deep_rank": 1}
        opportunity = {"market": "US", "decision": {"candidate": candidates[0]},
                       "evaluated_candidates": [candidates[0], candidates[3], candidates[2], ignored,
                                                candidates[1], held_duplicate]}
        original = json.dumps(self.state, sort_keys=True)
        report, ledger = self.collect(opportunity=opportunity)
        self.assertEqual("OBSERVED", report["status"])
        self.assertEqual(3, report["counts"]["current_positions"])
        self.assertEqual(3, report["counts"]["candidate_targets"])
        self.assertEqual(12, len(self.calls))
        self.assertEqual({"SAP", "NXPI", "MCK", "CAND0", "CAND1", "CAND2"},
                         {row["symbol"] for row in ledger["observations"]})
        self.assertEqual(["CAND3"], report["candidate_symbols_skipped_by_limit"])
        self.assertEqual(3, sum(row["input_role"] == "HOLDING" for row in report["positions"]))
        self.assertEqual(3, sum(row["input_role"] == "CANDIDATE" for row in report["positions"]))
        self.assertEqual(original, json.dumps(self.state, sort_keys=True))
        cached = collector.collect(self.state, self.universe, ledger, now=self.now + timedelta(hours=1),
                                   opportunity=opportunity,
                                   fetcher=lambda url: self.fail("fresh held/candidate receipts must be reused"))
        self.assertEqual(6, cached["counts"]["reused"])
        self.assertEqual(6, len(ledger["observations"]))

    def test_untrusted_or_ambiguous_universe_does_not_authorize_metadata(self):
        self.only_sap()
        for bad_universe in (
            {"instruments": [{**self.universe["instruments"][0], "source": {"provider": "Unverified"}}]},
            {"instruments": [self.universe["instruments"][0], self.universe["instruments"][0]]},
        ):
            with self.subTest(universe=bad_universe):
                ledger = sourced.empty_ledger()
                report = collector.collect(self.state, bad_universe, ledger, now=self.now, fetcher=self.fetch)
                self.assertEqual("PARTIAL", report["status"])
                self.assertEqual([], ledger["observations"])

    def test_request_budget_is_bounded_and_uncovered_issuers_are_explicit(self):
        for index in range(4):
            symbol = f"EX{index}"
            name = f"Example{index} Corporation"
            self.state["markets"]["US"]["open_positions"].append({
                "position_id": symbol, "market": "US", "symbol": symbol, "name": name, "status": "OPEN"})
            self.universe["instruments"].append({
                "market": "US", "listing_symbol": symbol, "market_data_symbol": symbol,
                "name": name, "exchange": "NYSE", "source": {"provider": "Nasdaq Trader"}})
            self.profiles[symbol] = deepcopy(self.profiles["SAP"])
            self.profiles[symbol]["data"]["Symbol"]["value"] = symbol
            self.profiles[symbol]["data"]["CompanyName"]["value"] = name
            self.summaries[symbol] = deepcopy(self.summaries["SAP"])
            self.summaries[symbol]["data"]["symbol"] = symbol
        report, ledger = self.collect()
        self.assertEqual("PARTIAL", report["status"])
        self.assertEqual(12, len(self.calls))
        self.assertEqual(6, len(ledger["observations"]))
        self.assertEqual(1, report["counts"]["unavailable"])
        self.assertTrue(any(item["symbol"] == "EX3" for item in report["errors"]))

    def test_listing_class_alias_uses_the_official_ticker_in_both_endpoints(self):
        self.only_sap()
        position = self.state["markets"]["US"]["open_positions"][0]
        position.update(symbol="BRK-B", name="Berkshire Hathaway Inc. Class B")
        self.universe = {"instruments": [{
            "market": "US", "listing_symbol": "BRK.B", "market_data_symbol": "BRK-B",
            "name": position["name"], "exchange": "NYSE", "source": {"provider": "Nasdaq Trader"}}]}
        self.profiles["BRK.B"] = deepcopy(self.profiles["SAP"])
        self.profiles["BRK.B"]["data"]["Symbol"]["value"] = "BRK.B"
        self.profiles["BRK.B"]["data"]["CompanyName"]["value"] = "Berkshire Hathaway Inc."
        self.profiles["BRK.B"]["data"]["Sector"]["value"] = "Finance"
        self.summaries["BRK.B"] = deepcopy(self.summaries["SAP"])
        self.summaries["BRK.B"]["data"]["symbol"] = "BRK.B"
        self.summaries["BRK.B"]["data"]["summaryData"]["Sector"]["value"] = "Finance"
        report, ledger = self.collect()
        self.assertEqual("OBSERVED", report["status"])
        self.assertTrue(all("/BRK.B/" in url for url in self.calls))
        self.assertEqual("BRK-B", ledger["observations"][0]["symbol"])
        self.assertEqual("Finance", sourced.sector_observation(position, "US", ledger, now=self.now)["sector"])

    def test_cli_partial_failure_allows_bulk_fallback_but_corrupt_history_fails_before_fetch(self):
        self.only_sap()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, universe, ledger, report = (root / name for name in ("state.json", "universe.json", "ledger.json", "report.json"))
            state.write_text(json.dumps(self.state), encoding="utf-8")
            universe.write_text(json.dumps(self.universe), encoding="utf-8")
            sourced._atomic(ledger, sourced.empty_ledger())
            args = ["us-sectors", "--state", str(state), "--universe", str(universe),
                    "--ledger", str(ledger), "--report", str(report)]
            with patch("sys.argv", args), patch.object(collector, "fetch_json_bytes", side_effect=TimeoutError("source unavailable")), redirect_stdout(StringIO()):
                self.assertEqual(0, collector.main())
            self.assertEqual("PARTIAL", json.loads(report.read_text())["status"])
            self.assertEqual([], sourced.load_ledger(ledger)["observations"])
            invalid = {"schema_version": sourced.SCHEMA, "observations": [{"kind": "SECTOR", "observation_id": "tampered"}]}
            ledger.write_text(json.dumps(invalid), encoding="utf-8")
            old_bytes = ledger.read_bytes()
            with patch("sys.argv", args), patch.object(collector, "fetch_json_bytes", side_effect=AssertionError("invalid history must fail before networking")):
                with self.assertRaisesRegex(ValueError, "receipt"):
                    collector.main()
            self.assertEqual(old_bytes, ledger.read_bytes())


if __name__ == "__main__":
    unittest.main()

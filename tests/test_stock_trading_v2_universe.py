from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_universe as universe


NASDAQ_SAMPLE = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|40|N|N
AARD|Aardvark Therapeutics, Inc. - Common Stock|Q|N|N|100|N|N
AETF|Example Daily ETF|G|N|N|100|Y|N
BADQ|Broken Corp - Common Stock|S|N|Q|100|N|N
SPACU|Example Acquisition Corp - Units|G|N|N|100|N|N
SPACW|Example Acquisition Corp - Warrant|G|N|N|100|N|N
TEST|Nasdaq Test Issue - Common Stock|Q|Y|N|100|N|N
ADSX|Example International - American Depositary Shares|Q|N|N|100|N|N
File Creation Time: 0916202617:15|||||||
"""

OTHER_SAMPLE = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
A|Agilent Technologies, Inc. Common Stock|N|A|N|100|N|A
BRK.B|Berkshire Hathaway Inc. Class B Common Stock|N|BRK.B|N|1|N|BRK-B
AAA|Alternative Access ETF|P|AAA|Y|100|N|AAA
ABR$D|Arbor Realty Trust Preferred Stock|N|ABRpD|N|100|N|ABR-D
AAC.U|Ares Acquisition Corporation Units|N|AAC.U|N|100|N|AAC=
BOND|Example 5.25% Senior Notes due 2030|N|BOND|N|100|N|BOND
TESTX|Other Exchange Test Common Stock|N|TESTX|N|100|Y|TESTX
File Creation Time: 0916202617:15|||||||
"""


class StockTradingV2UniverseTests(unittest.TestCase):
    def test_nasdaq_parser_keeps_common_and_adr_but_excludes_non_equity(self):
        rows, stats = universe.parse_nasdaq_listed(NASDAQ_SAMPLE)
        symbols = {row["listing_symbol"] for row in rows}
        self.assertEqual(symbols, {"AAPL", "AARD", "ADSX"})
        self.assertEqual(stats["accepted"], 3)
        self.assertEqual(stats["excluded_etf"], 1)
        self.assertEqual(stats["excluded_test"], 1)
        self.assertEqual(stats["excluded_financial_status"], 1)
        self.assertEqual(stats["excluded_security_type"], 2)
        adr = next(row for row in rows if row["listing_symbol"] == "ADSX")
        self.assertEqual(adr["security_type"], "ADR")
        self.assertFalse(adr["eligibility"]["production_admission"])

    def test_other_listed_parser_normalises_class_symbol_and_excludes_non_equity(self):
        rows, stats = universe.parse_other_listed(OTHER_SAMPLE)
        symbols = {row["listing_symbol"] for row in rows}
        self.assertEqual(symbols, {"A", "BRK.B"})
        brk = next(row for row in rows if row["listing_symbol"] == "BRK.B")
        self.assertEqual(brk["market_data_symbol"], "BRK-B")
        self.assertEqual(brk["exchange"], "NYSE")
        self.assertEqual(stats["accepted"], 2)
        self.assertEqual(stats["excluded_etf"], 1)
        self.assertEqual(stats["excluded_test"], 1)
        self.assertEqual(stats["excluded_symbol"], 1)  # preferred symbol with $
        self.assertEqual(stats["excluded_security_type"], 2)  # unit + note

    def test_combined_snapshot_is_hashed_deduplicated_and_shadow_only(self):
        snapshot = universe.build_us_snapshot(
            NASDAQ_SAMPLE,
            OTHER_SAMPLE,
            generated_at="2026-09-16T16:00:00Z",
        )
        universe.validate_snapshot(snapshot)
        self.assertEqual(snapshot["instrument_count"], 5)
        self.assertFalse(snapshot["governance"]["production_decision_influence"])
        self.assertTrue(all(not row["eligibility"]["production_admission"] for row in snapshot["instruments"]))

    def test_snapshot_tamper_is_detected(self):
        snapshot = universe.build_us_snapshot(NASDAQ_SAMPLE, OTHER_SAMPLE)
        snapshot["instruments"][0]["name"] = "changed after freeze"
        with self.assertRaises(contracts.ContractError):
            universe.validate_snapshot(snapshot)

    def test_write_and_verify_root(self):
        snapshot = universe.build_us_snapshot(NASDAQ_SAMPLE, OTHER_SAMPLE)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = universe.write_snapshot(snapshot, root=root)
            self.assertTrue(path.exists())
            result = universe.verify_root(root)
            self.assertTrue(result["ok"])
            self.assertEqual(result["markets"]["US"]["instrument_count"], 5)

    def test_gpw_seed_is_explicitly_non_dynamic_and_non_production(self):
        snapshot = universe.build_legacy_gpw_seed_snapshot(generated_at="2026-09-16T16:00:00Z")
        universe.validate_snapshot(snapshot)
        self.assertEqual(snapshot["market"], "GPW")
        self.assertEqual(snapshot["source"]["provider"], "legacy_seed")
        self.assertFalse(snapshot["governance"]["dynamic_provider_ready"])
        self.assertFalse(snapshot["governance"]["production_decision_influence"])
        self.assertGreaterEqual(snapshot["instrument_count"], 40)


if __name__ == "__main__":
    unittest.main()

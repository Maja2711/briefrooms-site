import json
import tempfile
import unittest
from pathlib import Path

from brace_entity_sec_source_aliases import (
    ALIAS_CONTRACT_VERSION,
    SEC_MARKET_SYMBOL_ALIASES,
    annotate_alias_provenance,
    apply_reviewed_aliases,
)


class ReviewedSecAliasTests(unittest.TestCase):
    def test_novo_and_sap_aliases_resolve_only_with_expected_cik(self):
        index = {
            "NVO": {"ticker": "NVO", "cik": 353278, "title": "NOVO NORDISK A S"},
            "SAP": {"ticker": "SAP", "cik": 1000184, "title": "SAP SE"},
        }
        resolved, diagnostics = apply_reviewed_aliases(index)
        self.assertEqual(resolved["NOVO-B.CO"]["cik"], 353278)
        self.assertEqual(resolved["NOVO-B.CO"]["sec_reporting_symbol"], "NVO")
        self.assertEqual(resolved["SAP.DE"]["cik"], 1000184)
        self.assertEqual(resolved["SAP.DE"]["sec_reporting_symbol"], "SAP")
        self.assertEqual({d["status"] for d in diagnostics}, {"resolved"})

    def test_cik_mismatch_fails_closed(self):
        resolved, diagnostics = apply_reviewed_aliases({
            "NVO": {"ticker": "NVO", "cik": 999999, "title": "wrong"},
            "SAP": {"ticker": "SAP", "cik": 1000184, "title": "SAP SE"},
        })
        self.assertNotIn("NOVO-B.CO", resolved)
        novo = next(d for d in diagnostics if d["market_symbol"] == "NOVO-B.CO")
        self.assertEqual(novo["status"], "cik_mismatch_fail_closed")
        self.assertEqual(novo["expected_cik"], 353278)

    def test_no_exchange_suffix_heuristic(self):
        resolved, _ = apply_reviewed_aliases({
            "BMW": {"ticker": "BMW", "cik": 123, "title": "example"},
        })
        self.assertNotIn("BMW.DE", resolved)
        self.assertEqual(set(SEC_MARKET_SYMBOL_ALIASES), {"NOVO-B.CO", "SAP.DE"})

    def test_alias_provenance_does_not_change_collection_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = {
                "entities": {
                    "novo": {
                        "market_symbol": "NOVO-B.CO",
                        "current_window_opened_at": "2026-08-19T21:04:38Z",
                        "collection_windows": [{"opened_at": "2026-08-19T21:04:38Z", "closed_at": None}],
                        "sec_source": {"status": "resolved", "ticker": "NOVO-B.CO", "cik": 353278},
                    }
                }
            }
            report = {"source_contract": {}, "sample": {}}
            (root / "ENTITY_PRIMARY_SOURCE_EVIDENCE_STATE.json").write_text(json.dumps(state))
            (root / "BRACE_ENTITY_PRIMARY_SOURCE_EVIDENCE_REPORT.json").write_text(json.dumps(report))
            annotate_alias_provenance(root, ({"market_symbol": "NOVO-B.CO", "status": "resolved"},))
            after = json.loads((root / "ENTITY_PRIMARY_SOURCE_EVIDENCE_STATE.json").read_text())
            entity = after["entities"]["novo"]
            self.assertEqual(entity["current_window_opened_at"], "2026-08-19T21:04:38Z")
            self.assertEqual(entity["collection_windows"][0]["opened_at"], "2026-08-19T21:04:38Z")
            self.assertEqual(entity["sec_source"]["resolution_method"], "reviewed_explicit_market_symbol_alias")
            self.assertEqual(entity["sec_source"]["alias_contract_version"], ALIAS_CONTRACT_VERSION)


if __name__ == "__main__":
    unittest.main()

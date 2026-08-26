from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from instrument_registry import (  # noqa: E402
    AssetClass,
    DEFAULT_METHODOLOGY_PATH,
    DEFAULT_POLICY_PATH,
    DEFAULT_REGISTRY,
    InstrumentRegistry,
    InstrumentRegistryError,
    InstrumentSpec,
    InstrumentType,
    UnknownInstrumentError,
    VendorSymbol,
    validate_wes_configuration,
)


class InstrumentRegistryTest(unittest.TestCase):
    def spec(self, instrument_id: str, *, aliases: tuple[str, ...] = ()) -> InstrumentSpec:
        return InstrumentSpec(
            instrument_id=instrument_id,
            display_name=instrument_id,
            asset_class=AssetClass.FX,
            instrument_type=InstrumentType.SPOT,
            market_timezone="UTC",
            calendar="FX_24X5",
            venue="OTC",
            base_currency="EUR",
            quote_currency="USD",
            aliases=aliases,
            vendor_symbols=(VendorSymbol("test", f"{instrument_id}=X"),),
        )

    def test_resolves_canonical_id_and_alias_case_insensitively(self):
        self.assertEqual(DEFAULT_REGISTRY.resolve_instrument_id(" EUR/USD "), "eurusd")
        self.assertEqual(DEFAULT_REGISTRY.resolve_instrument_id("BTC-USD"), "btcusd")
        self.assertEqual(DEFAULT_REGISTRY.resolve_instrument_id("es=f"), "sp500_futures")

    def test_known_wes_vendor_symbols_are_canonical(self):
        self.assertEqual(DEFAULT_REGISTRY.get_vendor_symbol("eurusd", "yahoo"), "EURUSD=X")
        self.assertEqual(DEFAULT_REGISTRY.get_vendor_symbol("sp500_futures", "YAHOO"), "ES=F")
        self.assertEqual(DEFAULT_REGISTRY.get_vendor_symbol("btcusd", "yahoo"), "BTC-USD")

    def test_macro_reference_symbols_are_registered(self):
        self.assertEqual(DEFAULT_REGISTRY.find_by_vendor_symbol("yahoo", "CL=F").instrument_id, "wti_futures")
        self.assertEqual(DEFAULT_REGISTRY.find_by_vendor_symbol("yahoo", "^TNX").instrument_id, "us10y_yield")

    def test_unknown_instrument_fails_closed(self):
        with self.assertRaises(UnknownInstrumentError):
            DEFAULT_REGISTRY.get("not-a-real-instrument")
        self.assertIsNone(DEFAULT_REGISTRY.try_get("not-a-real-instrument"))

    def test_unknown_vendor_symbol_fails_closed(self):
        with self.assertRaises(UnknownInstrumentError):
            DEFAULT_REGISTRY.find_by_vendor_symbol("yahoo", "UNKNOWN")

    def test_duplicate_alias_across_instruments_is_rejected(self):
        with self.assertRaises(InstrumentRegistryError):
            InstrumentRegistry((self.spec("one", aliases=("shared",)), self.spec("two", aliases=("SHARED",))))

    def test_duplicate_alias_inside_instrument_is_rejected(self):
        with self.assertRaises(InstrumentRegistryError):
            InstrumentRegistry((self.spec("one", aliases=("Alias", " alias ")),))

    def test_duplicate_vendor_symbol_is_rejected(self):
        a = self.spec("one")
        b = InstrumentSpec(
            instrument_id="two",
            display_name="two",
            asset_class=AssetClass.FX,
            instrument_type=InstrumentType.SPOT,
            market_timezone="UTC",
            calendar="FX_24X5",
            venue="OTC",
            vendor_symbols=(VendorSymbol("TEST", "one=X"),),
        )
        with self.assertRaises(InstrumentRegistryError):
            InstrumentRegistry((a, b))

    def test_invalid_timezone_is_rejected(self):
        with self.assertRaises(InstrumentRegistryError):
            InstrumentSpec(
                instrument_id="bad_tz",
                display_name="Bad timezone",
                asset_class=AssetClass.FX,
                instrument_type=InstrumentType.SPOT,
                market_timezone="Mars/Olympus",
                calendar="FX_24X5",
                venue="OTC",
            )

    def test_invalid_currency_is_rejected(self):
        with self.assertRaises(InstrumentRegistryError):
            InstrumentSpec(
                instrument_id="bad_ccy",
                display_name="Bad currency",
                asset_class=AssetClass.FX,
                instrument_type=InstrumentType.SPOT,
                market_timezone="UTC",
                calendar="FX_24X5",
                venue="OTC",
                base_currency="eur",
            )

    def test_specs_are_immutable(self):
        spec = DEFAULT_REGISTRY.get("eurusd")
        with self.assertRaises(FrozenInstanceError):
            spec.display_name = "changed"  # type: ignore[misc]

    def test_current_wes_configuration_matches_registry(self):
        self.assertEqual(validate_wes_configuration(), [])

    def test_wes_validator_detects_symbol_drift(self):
        methodology = json.loads(DEFAULT_METHODOLOGY_PATH.read_text(encoding="utf-8"))
        policy = json.loads(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
        methodology["instruments"][0]["symbol"] = "WRONG=X"

        with tempfile.TemporaryDirectory() as tmp:
            method_path = Path(tmp) / "methodology.json"
            policy_path = Path(tmp) / "policy.json"
            method_path.write_text(json.dumps(methodology), encoding="utf-8")
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            errors = validate_wes_configuration(method_path, policy_path)

        self.assertTrue(any("Yahoo symbol" in error for error in errors))

    def test_wes_validator_detects_unknown_policy_instrument(self):
        methodology = json.loads(DEFAULT_METHODOLOGY_PATH.read_text(encoding="utf-8"))
        policy = json.loads(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
        policy["instruments"].append({"instrument_id": "unknown_market"})

        with tempfile.TemporaryDirectory() as tmp:
            method_path = Path(tmp) / "methodology.json"
            policy_path = Path(tmp) / "policy.json"
            method_path.write_text(json.dumps(methodology), encoding="utf-8")
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            errors = validate_wes_configuration(method_path, policy_path)

        self.assertTrue(any("unknown instrument" in error for error in errors))


if __name__ == "__main__":
    unittest.main()

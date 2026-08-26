#!/usr/bin/env python3
"""Canonical instrument metadata for BriefRooms investment engines.

P0.1 introduces a single fail-closed registry for stable instrument identity,
market metadata and provider symbols. The registry is intentionally additive:
it does not change signal generation, ranking, risk plans or execution logic.

Existing engines can migrate to this module incrementally. During migration the
CLI validator prevents the governed WES configuration from silently drifting
away from canonical IDs and Yahoo symbols.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_METHODOLOGY_PATH = ROOT / "data" / "investments" / "methodology.json"
DEFAULT_POLICY_PATH = ROOT / "data" / "investments" / "multi_instrument_exposure_policy.json"

_INSTRUMENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class AssetClass(str, Enum):
    FX = "fx"
    EQUITY_INDEX_FUTURES = "equity_index_futures"
    CRYPTO = "crypto"
    COMMODITY_FUTURES = "commodity_futures"
    RATES = "rates"


class InstrumentType(str, Enum):
    SPOT = "spot"
    FUTURE = "future"
    CRYPTO_SPOT = "crypto_spot"
    REFERENCE_INDEX = "reference_index"


class InstrumentRegistryError(ValueError):
    """Base error for an invalid or ambiguous registry."""


class UnknownInstrumentError(InstrumentRegistryError):
    """Raised when an instrument cannot be resolved; callers must fail closed."""


@dataclass(frozen=True)
class VendorSymbol:
    provider: str
    symbol: str

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise InstrumentRegistryError("vendor provider must not be empty")
        if not self.symbol.strip():
            raise InstrumentRegistryError("vendor symbol must not be empty")


@dataclass(frozen=True)
class InstrumentSpec:
    instrument_id: str
    display_name: str
    asset_class: AssetClass
    instrument_type: InstrumentType
    market_timezone: str
    calendar: str
    venue: str
    base_currency: Optional[str] = None
    quote_currency: Optional[str] = None
    aliases: tuple[str, ...] = ()
    vendor_symbols: tuple[VendorSymbol, ...] = ()
    tick_size: Optional[float] = None
    pip_size: Optional[float] = None
    underlying_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not _INSTRUMENT_ID_RE.fullmatch(self.instrument_id):
            raise InstrumentRegistryError(
                f"invalid canonical instrument_id {self.instrument_id!r}; use lower-case stable IDs"
            )
        if not self.display_name.strip():
            raise InstrumentRegistryError(f"{self.instrument_id}: display_name must not be empty")
        if not self.calendar.strip():
            raise InstrumentRegistryError(f"{self.instrument_id}: calendar must not be empty")
        if not self.venue.strip():
            raise InstrumentRegistryError(f"{self.instrument_id}: venue must not be empty")
        try:
            ZoneInfo(self.market_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise InstrumentRegistryError(
                f"{self.instrument_id}: invalid IANA timezone {self.market_timezone!r}"
            ) from exc
        for field_name, currency in (
            ("base_currency", self.base_currency),
            ("quote_currency", self.quote_currency),
        ):
            if currency is not None and not _CURRENCY_RE.fullmatch(currency):
                raise InstrumentRegistryError(
                    f"{self.instrument_id}: {field_name} must be a 3-letter uppercase currency code"
                )
        for field_name, value in (("tick_size", self.tick_size), ("pip_size", self.pip_size)):
            if value is not None and value <= 0:
                raise InstrumentRegistryError(f"{self.instrument_id}: {field_name} must be positive")
        if self.underlying_id is not None and not _INSTRUMENT_ID_RE.fullmatch(self.underlying_id):
            raise InstrumentRegistryError(
                f"{self.instrument_id}: underlying_id must use canonical instrument ID syntax"
            )

    def vendor_symbol(self, provider: str) -> str:
        normalized = _normalize(provider)
        matches = [item.symbol for item in self.vendor_symbols if _normalize(item.provider) == normalized]
        if not matches:
            raise UnknownInstrumentError(
                f"{self.instrument_id}: no symbol registered for provider {provider!r}"
            )
        if len(matches) != 1:
            raise InstrumentRegistryError(
                f"{self.instrument_id}: multiple symbols registered for provider {provider!r}"
            )
        return matches[0]


def _normalize(value: str) -> str:
    return value.strip().casefold()


class InstrumentRegistry:
    """Immutable lookup indexes built from canonical InstrumentSpec objects."""

    def __init__(self, specs: Iterable[InstrumentSpec]):
        ordered = tuple(specs)
        by_id: dict[str, InstrumentSpec] = {}
        alias_to_id: dict[str, str] = {}
        vendor_to_id: dict[tuple[str, str], str] = {}

        for spec in ordered:
            if spec.instrument_id in by_id:
                raise InstrumentRegistryError(f"duplicate instrument_id: {spec.instrument_id}")
            by_id[spec.instrument_id] = spec

            local_aliases: set[str] = set()
            for raw_alias in (spec.instrument_id, *spec.aliases):
                alias = _normalize(raw_alias)
                if not alias:
                    raise InstrumentRegistryError(f"{spec.instrument_id}: aliases must not be empty")
                if alias in local_aliases:
                    raise InstrumentRegistryError(
                        f"{spec.instrument_id}: duplicate alias after normalization: {raw_alias!r}"
                    )
                local_aliases.add(alias)
                owner = alias_to_id.get(alias)
                if owner is not None and owner != spec.instrument_id:
                    raise InstrumentRegistryError(
                        f"alias {raw_alias!r} is ambiguous between {owner!r} and {spec.instrument_id!r}"
                    )
                alias_to_id[alias] = spec.instrument_id

            local_vendor_keys: set[tuple[str, str]] = set()
            for item in spec.vendor_symbols:
                key = (_normalize(item.provider), _normalize(item.symbol))
                if key in local_vendor_keys:
                    raise InstrumentRegistryError(
                        f"{spec.instrument_id}: duplicate vendor symbol {item.provider}:{item.symbol}"
                    )
                local_vendor_keys.add(key)
                owner = vendor_to_id.get(key)
                if owner is not None:
                    raise InstrumentRegistryError(
                        f"vendor symbol {item.provider}:{item.symbol} is ambiguous between "
                        f"{owner!r} and {spec.instrument_id!r}"
                    )
                vendor_to_id[key] = spec.instrument_id

        self._specs = ordered
        self._by_id: Mapping[str, InstrumentSpec] = MappingProxyType(by_id)
        self._alias_to_id: Mapping[str, str] = MappingProxyType(alias_to_id)
        self._vendor_to_id: Mapping[tuple[str, str], str] = MappingProxyType(vendor_to_id)

    def all(self) -> tuple[InstrumentSpec, ...]:
        return self._specs

    def resolve_instrument_id(self, instrument_id_or_alias: str) -> str:
        canonical = self._alias_to_id.get(_normalize(instrument_id_or_alias))
        if canonical is None:
            raise UnknownInstrumentError(f"unknown instrument: {instrument_id_or_alias!r}")
        return canonical

    def get(self, instrument_id_or_alias: str) -> InstrumentSpec:
        return self._by_id[self.resolve_instrument_id(instrument_id_or_alias)]

    def try_get(self, instrument_id_or_alias: str) -> Optional[InstrumentSpec]:
        try:
            return self.get(instrument_id_or_alias)
        except UnknownInstrumentError:
            return None

    def find_by_vendor_symbol(self, provider: str, symbol: str) -> InstrumentSpec:
        canonical = self._vendor_to_id.get((_normalize(provider), _normalize(symbol)))
        if canonical is None:
            raise UnknownInstrumentError(f"unknown vendor symbol: {provider}:{symbol}")
        return self._by_id[canonical]

    def get_vendor_symbol(self, instrument_id_or_alias: str, provider: str) -> str:
        return self.get(instrument_id_or_alias).vendor_symbol(provider)


DEFAULT_INSTRUMENTS: tuple[InstrumentSpec, ...] = (
    InstrumentSpec(
        instrument_id="eurusd",
        display_name="EUR/USD",
        asset_class=AssetClass.FX,
        instrument_type=InstrumentType.SPOT,
        market_timezone="UTC",
        calendar="FX_24X5",
        venue="OTC",
        base_currency="EUR",
        quote_currency="USD",
        aliases=("EUR/USD", "EURUSD=X"),
        vendor_symbols=(VendorSymbol("yahoo", "EURUSD=X"),),
        pip_size=0.0001,
    ),
    InstrumentSpec(
        instrument_id="sp500_futures",
        display_name="S&P 500 futures",
        asset_class=AssetClass.EQUITY_INDEX_FUTURES,
        instrument_type=InstrumentType.FUTURE,
        market_timezone="America/Chicago",
        calendar="CME_GLOBEX",
        venue="CME Globex",
        quote_currency="USD",
        aliases=("S&P 500 futures", "ES", "ES=F"),
        vendor_symbols=(VendorSymbol("yahoo", "ES=F"),),
        tick_size=0.25,
    ),
    InstrumentSpec(
        instrument_id="btcusd",
        display_name="BTC/USD",
        asset_class=AssetClass.CRYPTO,
        instrument_type=InstrumentType.CRYPTO_SPOT,
        market_timezone="UTC",
        calendar="CRYPTO_24X7",
        venue="GLOBAL",
        base_currency="BTC",
        quote_currency="USD",
        aliases=("BTC/USD", "BTC-USD"),
        vendor_symbols=(VendorSymbol("yahoo", "BTC-USD"),),
    ),
    InstrumentSpec(
        instrument_id="wti_futures",
        display_name="WTI crude oil futures",
        asset_class=AssetClass.COMMODITY_FUTURES,
        instrument_type=InstrumentType.FUTURE,
        market_timezone="America/Chicago",
        calendar="CME_GLOBEX",
        venue="NYMEX",
        quote_currency="USD",
        aliases=("WTI", "CL", "CL=F"),
        vendor_symbols=(VendorSymbol("yahoo", "CL=F"),),
        tick_size=0.01,
    ),
    InstrumentSpec(
        instrument_id="us10y_yield",
        display_name="CBOE 10-Year Treasury Note Yield Index",
        asset_class=AssetClass.RATES,
        instrument_type=InstrumentType.REFERENCE_INDEX,
        market_timezone="America/New_York",
        calendar="CBOE_US",
        venue="CBOE",
        quote_currency="USD",
        aliases=("US10Y", "TNX", "^TNX"),
        vendor_symbols=(VendorSymbol("yahoo", "^TNX"),),
    ),
)

DEFAULT_REGISTRY = InstrumentRegistry(DEFAULT_INSTRUMENTS)


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstrumentRegistryError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise InstrumentRegistryError(f"expected JSON object in {path}")
    return payload


def validate_wes_configuration(
    methodology_path: Path = DEFAULT_METHODOLOGY_PATH,
    policy_path: Path = DEFAULT_POLICY_PATH,
    registry: InstrumentRegistry = DEFAULT_REGISTRY,
) -> list[str]:
    """Return configuration drift errors without changing runtime decisions."""
    errors: list[str] = []
    methodology = _read_json(methodology_path)
    policy = _read_json(policy_path)

    method_rows = methodology.get("instruments") or []
    if not isinstance(method_rows, list):
        return ["methodology.instruments must be a list"]

    for row in method_rows:
        if not isinstance(row, dict):
            errors.append("methodology.instruments contains a non-object row")
            continue
        instrument_id = str(row.get("id") or "")
        try:
            spec = registry.get(instrument_id)
        except UnknownInstrumentError as exc:
            errors.append(f"methodology: {exc}")
            continue

        symbol = str(row.get("symbol") or "")
        try:
            expected_symbol = spec.vendor_symbol("yahoo")
        except UnknownInstrumentError as exc:
            errors.append(f"methodology {instrument_id}: {exc}")
        else:
            if symbol != expected_symbol:
                errors.append(
                    f"methodology {instrument_id}: Yahoo symbol {symbol!r} != canonical {expected_symbol!r}"
                )

        asset_class = str(row.get("asset_class") or "")
        if asset_class != spec.asset_class.value:
            errors.append(
                f"methodology {instrument_id}: asset_class {asset_class!r} != canonical "
                f"{spec.asset_class.value!r}"
            )

    policy_rows = policy.get("instruments") or []
    if not isinstance(policy_rows, list):
        errors.append("policy.instruments must be a list")
    else:
        for row in policy_rows:
            if not isinstance(row, dict):
                errors.append("policy.instruments contains a non-object row")
                continue
            instrument_id = str(row.get("instrument_id") or "")
            try:
                registry.get(instrument_id)
            except UnknownInstrumentError as exc:
                errors.append(f"policy: {exc}")

    macro = policy.get("macro_context") or {}
    if isinstance(macro, dict) and macro.get("enabled") is True:
        for field_name in ("oil_symbol", "us10y_symbol"):
            symbol = str(macro.get(field_name) or "")
            if not symbol:
                errors.append(f"policy.macro_context.{field_name} is empty")
                continue
            try:
                registry.find_by_vendor_symbol("yahoo", symbol)
            except UnknownInstrumentError as exc:
                errors.append(f"policy.macro_context.{field_name}: {exc}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="BriefRooms canonical instrument registry")
    parser.add_argument(
        "--validate-wes",
        action="store_true",
        help="validate current governed WES methodology/policy against canonical metadata",
    )
    args = parser.parse_args()

    if not args.validate_wes:
        parser.print_help()
        return 0

    errors = validate_wes_configuration()
    if errors:
        print("Canonical Instrument Registry validation FAILED:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(
        "Canonical Instrument Registry validation passed: "
        f"{len(DEFAULT_REGISTRY.all())} canonical instruments; WES configuration is aligned."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

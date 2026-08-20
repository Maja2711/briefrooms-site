#!/usr/bin/env python3
"""Reviewed market-symbol -> SEC filer aliases for PR13.

This module deliberately supports only an explicit, reviewed allowlist. It does
not strip exchange suffixes or infer ADR mappings heuristically. Each alias must
resolve through the live SEC ticker map and match a frozen expected CIK before it
is exposed to the PR13 primary-source collector.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

import brace_entity_primary_source_evidence as pr13

ALIAS_CONTRACT_VERSION = "sec-market-symbol-alias-v1"

# Reviewed against current SEC EDGAR filer records. The market symbol remains the
# canonical BRACE/portfolio symbol; the SEC reporting symbol is source-specific.
SEC_MARKET_SYMBOL_ALIASES: Mapping[str, Mapping[str, Any]] = {
    "NOVO-B.CO": {
        "sec_ticker": "NVO",
        "expected_cik": 353278,
        "issuer_name": "NOVO NORDISK A S",
    },
    "SAP.DE": {
        "sec_ticker": "SAP",
        "expected_cik": 1000184,
        "issuer_name": "SAP SE",
    },
}


def apply_reviewed_aliases(
    ticker_index: Mapping[str, Mapping[str, Any]],
) -> Tuple[Dict[str, Mapping[str, Any]], Tuple[Dict[str, Any], ...]]:
    """Inject only aliases whose live SEC row matches the frozen expected CIK."""
    out: Dict[str, Mapping[str, Any]] = {str(k): dict(v) for k, v in ticker_index.items()}
    diagnostics = []
    for raw_market_symbol, spec in SEC_MARKET_SYMBOL_ALIASES.items():
        market_symbol = pr13.normalize_ticker(raw_market_symbol)
        sec_ticker = pr13.normalize_ticker(str(spec["sec_ticker"]))
        expected_cik = int(spec["expected_cik"])
        target = out.get(sec_ticker)
        if not target:
            diagnostics.append({
                "market_symbol": market_symbol,
                "sec_ticker": sec_ticker,
                "expected_cik": expected_cik,
                "status": "target_sec_ticker_missing",
            })
            continue
        try:
            actual_cik = int(target.get("cik"))
        except (TypeError, ValueError):
            actual_cik = 0
        if actual_cik != expected_cik:
            diagnostics.append({
                "market_symbol": market_symbol,
                "sec_ticker": sec_ticker,
                "expected_cik": expected_cik,
                "actual_cik": actual_cik or None,
                "status": "cik_mismatch_fail_closed",
            })
            continue
        alias_row = dict(target)
        alias_row.update({
            "market_symbol_alias": market_symbol,
            "sec_reporting_symbol": sec_ticker,
            "alias_contract_version": ALIAS_CONTRACT_VERSION,
            "expected_cik": expected_cik,
        })
        out[market_symbol] = alias_row
        diagnostics.append({
            "market_symbol": market_symbol,
            "sec_ticker": sec_ticker,
            "expected_cik": expected_cik,
            "actual_cik": actual_cik,
            "status": "resolved",
        })
    return out, tuple(diagnostics)


class ReviewedAliasSecEdgarClient(pr13.SecEdgarClient):
    """SEC client exposing reviewed local-market aliases as exact ticker keys."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.alias_diagnostics: Tuple[Dict[str, Any], ...] = ()

    def ticker_index(self) -> Mapping[str, Mapping[str, Any]]:
        raw = super().ticker_index()
        resolved, diagnostics = apply_reviewed_aliases(raw)
        self.alias_diagnostics = diagnostics
        return resolved


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def annotate_alias_provenance(
    state_dir: Path,
    diagnostics: Tuple[Dict[str, Any], ...],
) -> Dict[str, Any]:
    """Annotate resolved aliases without changing PR13 collection-window lineage."""
    state_dir = Path(state_dir)
    state_path = state_dir / pr13.STATE_FILENAME
    report_path = state_dir / pr13.REPORT_FILENAME
    state = json.loads(state_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))

    for entity_state in (state.get("entities") or {}).values():
        market_symbol = pr13.normalize_ticker(str(entity_state.get("market_symbol") or ""))
        spec = SEC_MARKET_SYMBOL_ALIASES.get(market_symbol)
        if not spec:
            continue
        source = dict(entity_state.get("sec_source") or {})
        expected_cik = int(spec["expected_cik"])
        if int(source.get("cik") or 0) != expected_cik:
            continue
        source.update({
            "resolution_method": "reviewed_explicit_market_symbol_alias",
            "market_symbol": market_symbol,
            "sec_reporting_symbol": pr13.normalize_ticker(str(spec["sec_ticker"])),
            "expected_cik": expected_cik,
            "alias_contract_version": ALIAS_CONTRACT_VERSION,
        })
        entity_state["sec_source"] = source

    source_contract = dict(report.get("source_contract") or {})
    source_contract.update({
        "market_symbol_alias_contract_version": ALIAS_CONTRACT_VERSION,
        "market_symbol_aliases_are_explicit_allowlist_only": True,
        "exchange_suffix_heuristic_enabled": False,
    })
    report["source_contract"] = source_contract
    report["sec_market_symbol_alias_diagnostics"] = list(diagnostics)

    _write_json(state_path, state)
    _write_json(report_path, report)
    return report


def run(
    state_dir: Path,
    *,
    portfolio_path: Path = pr13.DEFAULT_PORTFOLIO,
    analysis_path: Path = pr13.DEFAULT_ANALYSIS,
    universe_path: Path = pr13.DEFAULT_UNIVERSE,
    as_of=None,
    sec_client: Optional[pr13.SecClientProtocol] = None,
) -> Dict[str, Any]:
    client = sec_client or ReviewedAliasSecEdgarClient()
    report = pr13.run(
        state_dir,
        portfolio_path=portfolio_path,
        analysis_path=analysis_path,
        universe_path=universe_path,
        as_of=as_of,
        sec_client=client,
    )
    diagnostics = tuple(getattr(client, "alias_diagnostics", ()))
    return annotate_alias_provenance(Path(state_dir), diagnostics)


def main() -> int:
    parser = argparse.ArgumentParser(description="PR13 primary-source collector with reviewed SEC market-symbol aliases")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--portfolio", default=str(pr13.DEFAULT_PORTFOLIO))
    parser.add_argument("--analysis", default=str(pr13.DEFAULT_ANALYSIS))
    parser.add_argument("--universe", default=str(pr13.DEFAULT_UNIVERSE))
    args = parser.parse_args()
    report = run(
        Path(args.state_dir),
        portfolio_path=Path(args.portfolio),
        analysis_path=Path(args.analysis),
        universe_path=Path(args.universe),
    )
    print(json.dumps({
        "mode": report.get("mode"),
        "sample": report.get("sample"),
        "reporting_regimes": report.get("reporting_regimes"),
        "sec_market_symbol_alias_diagnostics": report.get("sec_market_symbol_alias_diagnostics"),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

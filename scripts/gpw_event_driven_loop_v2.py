#!/usr/bin/env python3
"""Canonical GPW Daily v2 wrapper.

Installs the existing event/learning layer, then replaces the faulty v1.3
market-completeness generator and prefetch chain with the audited v2 versions.
Ranking is strictly based on the latest completed GPW session; the current
session is used only later for executable entry/SL/TP repricing.
"""
from __future__ import annotations

import json

try:
    from scripts import gpw_event_driven_loop as base
    from scripts import gpw_pipeline_v2 as pipeline
    from scripts import gpw_provider_v2 as provider
except ModuleNotFoundError:
    import gpw_event_driven_loop as base
    import gpw_pipeline_v2 as pipeline
    import gpw_provider_v2 as provider

_INSTALLED = False
_ORIGINAL_BUILD_QUANT_CANDIDATE = base.gpw.build_quant_candidate


def _completed_session_candidate(company, bars, expected_day, config, history):
    completed = [bar for bar in (bars or []) if bar.day <= expected_day]
    if not completed or completed[-1].day != expected_day:
        return None
    return _ORIGINAL_BUILD_QUANT_CANDIDATE(
        company, completed, expected_day, config, history
    )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    # _event_generate calls this captured function; replace it before base.install.
    base._ORIGINAL_GENERATE = pipeline.generate
    # Enforce completed-session-only ranking even when Yahoo already exposes a
    # partial candle for today's session.
    base.gpw.build_quant_candidate = _completed_session_candidate
    base.install()
    base.gpw.cutoff_for = pipeline.cutoff_for
    base.loop.prefetch_market = provider.prefetch_market
    _INSTALLED = True


def main() -> int:
    install()
    result = base.loop.main()
    if provider.LAST_AUDIT:
        print("GPW_PROVIDER_AUDIT=" + json.dumps(provider.LAST_AUDIT, ensure_ascii=False))
    return result


if __name__ == "__main__":
    raise SystemExit(main())

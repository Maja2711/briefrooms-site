#!/usr/bin/env python3
"""Canonical GPW Daily v2 wrapper.

Installs the existing event/learning layer, then replaces the faulty v1.3
market-completeness generator and prefetch chain with the audited v2 versions.
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


def install() -> None:
    # _event_generate calls this captured function; replace it before base.install.
    base._ORIGINAL_GENERATE = pipeline.generate
    base.install()
    base.gpw.cutoff_for = pipeline.cutoff_for
    base.loop.prefetch_market = provider.prefetch_market


def main() -> int:
    install()
    result = base.loop.main()
    if provider.LAST_AUDIT:
        print("GPW_PROVIDER_AUDIT=" + json.dumps(provider.LAST_AUDIT, ensure_ascii=False))
    return result


if __name__ == "__main__":
    raise SystemExit(main())

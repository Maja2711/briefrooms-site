#!/usr/bin/env python3
"""Architecture guard for BriefRooms trading workflows.

# NO RETROACTIVE EXECUTION

Every workflow that can persist canonical trading/execution state must establish
an immutable run clock and invoke verify_no_retroactive_execution.py before
publication. Research/shadow workflows may model historical trades but are not
allowed to write canonical execution books.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

# These are the currently known canonical execution/state writers. Keeping the
# list explicit makes deletion/renaming itself a reviewed architecture change.
REQUIRED_AIRLOCK_WORKFLOWS = {
    "investments-wes.yml",
    "investments-exposure-watch.yml",
    "stock-trading-portfolio.yml",
    "stock-trading-v2-production.yml",
    "gpw-daily-pick-pl.yml",
    "us-daily-stock-en.yml",
    "us-daily-stock-position-monitor.yml",
    "daily-eurusd-monitor.yml",
    "portfolio-10k-live-entry.yml",
    "portfolio-10k-hourly-prices.yml",
    "portfolio-10k-guardian.yml",
    "portfolio-10k-weekly.yml",
    "portfolio-10k-brace.yml",
    "brace-portfolio-monitor.yml",
    "brace-portfolio-daily.yml",
}

# Canonical execution/state locations. Any other workflow that gains write
# authority and commits one of these locations is automatically pulled into the
# invariant even before this explicit registry is updated.
CANONICAL_TOKENS = (
    "data/investments/weekly",
    "multi_instrument_exposure_state_v5.json",
    "multi_instrument_exposure_report_v5.json",
    "stock_trading_portfolio.json",
    "stock_trading_v2_production_state.json",
    "us_daily_stock_position.json",
    "eurusd_daily_spot.json",
    "eurusd_daily_history.json",
    "data/investments/portfolio_10k.json",
    "data/portfolio10k",
)

SHADOW_WORKFLOWS = {
    "daily-eurusd-abc-live-shadow.yml",
    "brace-spx-architecture-v2-shadow.yml",
    "brace-spx-generation6.yml",
    "stock-trading-v1-shadow.yml",
    "stock-trading-v2-shadow-ingest.yml",
}

FORBIDDEN_SHADOW_CANONICAL_WRITES = (
    "git add data/investments/stock_trading_portfolio.json",
    "git add -- data/investments/stock_trading_portfolio.json",
    "git add data/investments/eurusd_daily_spot.json",
    "git add -- data/investments/eurusd_daily_spot.json",
    "git add data/investments/portfolio_10k.json",
    "git add -- data/investments/portfolio_10k.json",
    "git add data/investments/weekly",
    "git add -- data/investments/weekly",
    "git add data/portfolio10k/paper_portfolio.json",
    "git add -- data/portfolio10k/paper_portfolio.json",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def canonical_writer(text: str) -> bool:
    lowered = text.lower()
    if "contents: write" not in lowered:
        return False
    if "git commit" not in lowered and "git push" not in lowered:
        return False
    return any(token.lower() in lowered for token in CANONICAL_TOKENS)


def validate_airlock(path: Path, text: str) -> list[str]:
    problems: list[str] = []
    if "NO RETROACTIVE EXECUTION" not in text:
        problems.append("missing architecture marker '# NO RETROACTIVE EXECUTION'")
    if "NO_RETROACTIVE_RUN_STARTED_AT" not in text:
        problems.append("missing immutable run clock NO_RETROACTIVE_RUN_STARTED_AT")
    if "verify_no_retroactive_execution.py" not in text:
        problems.append("missing pre-publication execution airlock")
    if "git commit" in text and "--baseline-ref HEAD" not in text:
        problems.append("airlock is not run against pre-commit HEAD")
    if "git pull --rebase" in text and "--baseline-ref HEAD^" not in text:
        problems.append("missing post-rebase revalidation against HEAD^")
    return [f"{path.name}: {problem}" for problem in problems]


def main() -> int:
    violations: list[str] = []
    missing = sorted(name for name in REQUIRED_AIRLOCK_WORKFLOWS if not (WORKFLOWS / name).exists())
    violations.extend(f"registry: required workflow missing: {name}" for name in missing)

    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = read(path)
        must_guard = path.name in REQUIRED_AIRLOCK_WORKFLOWS or canonical_writer(text)
        if must_guard:
            violations.extend(validate_airlock(path, text))

        if path.name in SHADOW_WORKFLOWS:
            lowered = text.lower()
            for token in FORBIDDEN_SHADOW_CANONICAL_WRITES:
                if token.lower() in lowered:
                    violations.append(f"{path.name}: SHADOW workflow writes canonical state via {token!r}")
            if "contents: read" in lowered and "git push" not in lowered:
                continue
            # Shadow workflows may persist research artifacts or sanitized public
            # status. They may not claim production execution authority.
            if "production_execution" in lowered and "!= 'production_execution'" not in lowered:
                violations.append(f"{path.name}: SHADOW workflow references production execution authority")

    if violations:
        print("TRADING WORKFLOW AIRLOCK: BLOCKED", file=sys.stderr)
        for violation in violations:
            print(f" - {violation}", file=sys.stderr)
        return 1

    print(
        "TRADING WORKFLOW AIRLOCK: PASS - canonical writers guarded, shadow writers isolated "
        f"({len(REQUIRED_AIRLOCK_WORKFLOWS)} registered production/paper-live workflows)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

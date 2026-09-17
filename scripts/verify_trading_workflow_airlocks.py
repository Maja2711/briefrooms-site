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

REQUIRED_AIRLOCK_WORKFLOWS = {
    "investments-wes.yml",
    "investments-exposure-watch.yml",
    "investments-weekly.yml",
    "investments-weekly-freshness-watchdog.yml",
    "investment-event-intelligence-production.yml",
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
    "portfolio-10k-material-hotfix.yml",
    "portfolio-10k-analysis-news.yml",
    "brace-portfolio-monitor.yml",
    "brace-portfolio-daily.yml",
    "brace-portfolio-learning-loop-v1.yml",
}

CANONICAL_TOKENS = (
    "data/investments/weekly/",
    "multi_instrument_exposure_state_v5.json",
    "multi_instrument_exposure_report_v5.json",
    "stock_trading_portfolio.json",
    "stock_trading_v2_production_state.json",
    "us_daily_stock_position.json",
    "eurusd_daily_spot.json",
    "eurusd_daily_history.json",
    "data/investments/portfolio_10k.json",
    "data/portfolio10k/paper_portfolio.json",
    "data/portfolio10k/paper_orders.json",
)

# Historical/counterfactual research is allowed here, but it may never mutate
# canonical LIVE/PAPER_LIVE execution books. These workflows can persist only
# research state on their isolated branches/artifact surfaces.
SHADOW_WORKFLOWS = {
    "daily-eurusd-abc-live-shadow.yml",
    "brace-spx-architecture-v2-shadow.yml",
    "brace-spx-generation6.yml",
    "brace-spx-recovery-engine.yml",
    "stock-trading-v1-shadow.yml",
    "stock-trading-v2-shadow-ingest.yml",
    "stock-trading-v2-continuous-discovery.yml",
    "stock-trading-v2-learning-loop.yml",
}

FORBIDDEN_SHADOW_CANONICAL_WRITES = (
    "git add data/investments/stock_trading_portfolio.json",
    "git add -- data/investments/stock_trading_portfolio.json",
    "git add data/investments/eurusd_daily_spot.json",
    "git add -- data/investments/eurusd_daily_spot.json",
    "git add data/investments/portfolio_10k.json",
    "git add -- data/investments/portfolio_10k.json",
    "git add data/investments/weekly/",
    "git add -- data/investments/weekly/",
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
    # Either the human-readable invariant or the machine flag is accepted as
    # the workflow-level marker. New/edited workflows should use both.
    if "NO RETROACTIVE EXECUTION" not in text and "NO_RETROACTIVE_EXECUTION=1" not in text:
        problems.append("missing NO RETROACTIVE EXECUTION architecture marker")
    if "NO_RETROACTIVE_RUN_STARTED_AT" not in text:
        problems.append("missing immutable run clock NO_RETROACTIVE_RUN_STARTED_AT")
    if "verify_no_retroactive_execution.py" not in text:
        problems.append("missing pre-publication execution airlock")
    if "git commit" in text and "--baseline-ref HEAD" not in text:
        problems.append("airlock is not run against pre-commit HEAD")
    if "git pull --rebase" in text and "--baseline-ref HEAD^" not in text:
        problems.append("missing post-rebase revalidation against HEAD^")
    return [f"{path.name}: {problem}" for problem in problems]


def validate_shadow(path: Path, text: str) -> list[str]:
    violations: list[str] = []
    lowered = text.lower()
    for token in FORBIDDEN_SHADOW_CANONICAL_WRITES:
        if token.lower() in lowered:
            violations.append(f"{path.name}: SHADOW workflow writes canonical state via {token!r}")
    # Read-only production snapshots are fine. Actual canonical publication is not.
    if "production_execution" in lowered and "!= 'production_execution'" not in lowered:
        violations.append(f"{path.name}: SHADOW workflow references production execution authority")
    return violations


def main() -> int:
    violations: list[str] = []
    missing = sorted(name for name in REQUIRED_AIRLOCK_WORKFLOWS if not (WORKFLOWS / name).exists())
    violations.extend(f"registry: required workflow missing: {name}" for name in missing)

    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = read(path)
        is_shadow = path.name in SHADOW_WORKFLOWS
        must_guard = path.name in REQUIRED_AIRLOCK_WORKFLOWS or (canonical_writer(text) and not is_shadow)
        if must_guard:
            violations.extend(validate_airlock(path, text))
        if is_shadow:
            violations.extend(validate_shadow(path, text))

    if violations:
        print("TRADING WORKFLOW AIRLOCK: BLOCKED", file=sys.stderr)
        for violation in violations:
            print(f" - {violation}", file=sys.stderr)
        return 1

    print(
        "TRADING WORKFLOW AIRLOCK: PASS - canonical writers guarded, shadow writers isolated "
        f"({len(REQUIRED_AIRLOCK_WORKFLOWS)} registered production/paper-live workflows; "
        f"{len(SHADOW_WORKFLOWS)} shadow/research workflows isolated)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

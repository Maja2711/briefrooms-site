#!/usr/bin/env python3
"""One-shot integration patch for Portfolio 10K cash yield and active UI."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"Missing integration anchor: {label}")
    return text.replace(old, new, 1)


def patch_workflow() -> None:
    rel = ".github/workflows/portfolio-10k-hourly-prices.yml"
    text = read(rel)
    text = replace_once(
        text,
        '      - "scripts/portfolio_10k_hourly_prices.py"\n',
        '      - "scripts/portfolio_10k_hourly_prices.py"\n'
        '      - "scripts/portfolio_10k_cash_yield.py"\n'
        '      - "data/portfolio10k/cash_yield_policy.json"\n',
        "hourly workflow paths",
    )
    text = replace_once(
        text,
        '      - "tests/test_portfolio_10k_guardian.py"\n',
        '      - "tests/test_portfolio_10k_guardian.py"\n'
        '      - "tests/test_portfolio_10k_cash_yield.py"\n',
        "cash-yield test path",
    )
    text = replace_once(
        text,
        '      - name: Mark portfolio to market\n'
        '        run: python scripts/portfolio_10k_hourly_prices.py\n',
        '      - name: Accrue PLN cash at the NBP reference rate\n'
        '        run: python scripts/portfolio_10k_cash_yield.py\n\n'
        '      - name: Mark portfolio to market\n'
        '        run: python scripts/portfolio_10k_hourly_prices.py\n',
        "cash accrual step",
    )
    text = replace_once(
        text,
        '            scripts/portfolio_10k_hourly_prices.py \\\n',
        '            scripts/portfolio_10k_hourly_prices.py \\\n'
        '            scripts/portfolio_10k_cash_yield.py \\\n',
        "cash-yield compile",
    )
    text = replace_once(
        text,
        '          python -m unittest tests.test_portfolio_10k_guardian -v\n',
        '          python -m unittest tests.test_portfolio_10k_guardian -v\n'
        '          python -m unittest tests.test_portfolio_10k_cash_yield -v\n',
        "cash-yield unit tests",
    )
    write(rel, text)


def patch_dashboard(rel: str) -> None:
    text = read(rel)
    cash_anchor = "    setText('#cash-value', money(cash));\n"
    cash_block = cash_anchor + (
        "    const cashYield = portfolio?.cash_yield || {};\n"
        "    const cashLabel = $('#cash-value')?.parentElement?.querySelector('small');\n"
        "    if (cashLabel && Number.isFinite(Number(cashYield.rate_percent))) {\n"
        "      cashLabel.textContent = isEn\n"
        "        ? `Cash · ${cashYield.label_en || `Fed ${num(cashYield.rate_percent).toFixed(3)}%`}`\n"
        "        : `Gotówka · ${cashYield.label_pl || `NBP ${num(cashYield.rate_percent).toFixed(2).replace('.', ',')}%`}`;\n"
        "      cashLabel.title = isEn\n"
        "        ? 'Model cash earns the Federal Reserve target-range midpoint on an ACT/365 basis.'\n"
        "        : 'Gotówka modelowa jest oprocentowana stopą referencyjną NBP w konwencji ACT/365.';\n"
        "    }\n"
    )
    text = replace_once(text, cash_anchor, cash_block, f"cash-yield label in {rel}")
    status_anchor = "    setText('#data-status', `${T.active} · ${currency}`);\n"
    status_block = status_anchor + (
        "    const dataStatus = $('#data-status');\n"
        "    if (dataStatus) dataStatus.classList.add('is-active');\n"
    )
    text = replace_once(text, status_anchor, status_block, f"active status in {rel}")
    unavailable_anchor = "    setText('#data-status', T.unavailable);\n"
    unavailable_block = unavailable_anchor + (
        "    const dataStatus = $('#data-status');\n"
        "    if (dataStatus) dataStatus.classList.remove('is-active');\n"
    )
    if unavailable_anchor in text and unavailable_block not in text:
        text = text.replace(unavailable_anchor, unavailable_block, 1)
    write(rel, text)


def patch_css() -> None:
    rel = "assets/portfolio-10k-dashboard.css"
    text = read(rel)
    marker = ".top-meta #data-status.is-active{color:var(--dash-green);font-weight:900}"
    if marker not in text:
        text += (
            "\n/* Portfolio 10K operational status */\n"
            ".top-meta #data-status.is-active{color:var(--dash-green);font-weight:900}\n"
            ".live-badge[data-automation-status=\"healthy\"]{color:var(--dash-green);font-weight:900}\n"
            ".live-badge[data-automation-status=\"healthy\"] i{background:var(--dash-green);box-shadow:0 0 0 3px rgba(21,150,77,.14)}\n"
        )
    write(rel, text)


def patch_renderer() -> None:
    rel = "scripts/render_portfolio_10k_static.py"
    text = read(rel)
    text = text.replace(
        'lambda match: f"/scripts/portfolio-10k-dashboard{match.group(1) or \'\'}.js?v=9",',
        'lambda match: f"/scripts/portfolio-10k-dashboard{match.group(1) or \'\'}.js?v=10",',
    )
    css_anchor = '    stylesheet = \'<link rel="stylesheet" href="/assets/portfolio-10k-static.css?v=1">\'\n'
    css_block = css_anchor + (
        '    source = re.sub(r\'/assets/portfolio-10k-dashboard\\.css\\?v=\\d+\', \'/assets/portfolio-10k-dashboard.css?v=5\', source)\n'
    )
    text = replace_once(text, css_anchor, css_block, "dashboard CSS cache bust")
    write(rel, text)


def patch_version_expectations() -> None:
    for rel in (
        "scripts/patch_portfolio_10k_hourly_ui.py",
        "tests/test_investment_room_resilience.py",
        "tests/test_automation_workflow_ownership.py",
        ".github/workflows/investment-room-production-audit.yml",
    ):
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        text = text.replace("portfolio-10k-dashboard.js?v=9", "portfolio-10k-dashboard.js?v=10")
        text = text.replace("portfolio-10k-dashboard-en.js?v=9", "portfolio-10k-dashboard-en.js?v=10")
        path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_workflow()
    patch_dashboard("scripts/portfolio-10k-dashboard.js")
    patch_dashboard("scripts/portfolio-10k-dashboard-en.js")
    patch_css()
    patch_renderer()
    patch_version_expectations()
    print("Portfolio 10K cash-yield integration patched")


if __name__ == "__main__":
    main()

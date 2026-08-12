"""Render an indexable Portfolio 10K snapshot into the public PL and EN pages.

The interactive dashboard still hydrates from JSON in the browser.  This script
adds the same essential information to the initial HTML response so search
engines, link preview services and visitors without JavaScript can understand
the page.  It is intentionally deterministic and safe to run after every data
refresh.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_START = "<!-- portfolio-static-snapshot:start -->"
SNAPSHOT_END = "<!-- portfolio-static-snapshot:end -->"
SCHEMA_START = "<!-- portfolio-static-schema:start -->"
SCHEMA_END = "<!-- portfolio-static-schema:end -->"


@dataclass(frozen=True)
class PageConfig:
    lang: str
    page: str
    data: str
    url: str
    title: str
    heading: str
    description: str
    market_date: str
    starting_capital: str
    portfolio_value: str
    portfolio_return: str
    benchmark_return: str
    cash: str
    positions: str
    holdings: str
    methodology: str
    status_hold: str
    disclaimer: str
    locale: str
    currency: str


PAGES = (
    PageConfig(
        lang="pl",
        page="pl/inwestycje/portfel-10k.html",
        data="data/investments/portfolio_10k.json",
        url="https://briefrooms.com/pl/inwestycje/portfel-10k.html",
        title="Inwestycje 10K — publiczny portfel modelowy BriefRooms",
        heading="Publiczny snapshot portfela Inwestycje 10K",
        description=(
            "Aktualny, statyczny zapis wartości, składu, wyników i zasad portfela. "
            "Interaktywny dashboard poniżej rozszerza te dane po uruchomieniu JavaScriptu."
        ),
        market_date="Dane rynkowe",
        starting_capital="Kapitał startowy",
        portfolio_value="Wartość portfela",
        portfolio_return="Wynik portfela",
        benchmark_return="Wynik benchmarku",
        cash="Gotówka",
        positions="Aktywne pozycje",
        holdings="Skład portfela i tezy",
        methodology="Zasady i cel modelu",
        status_hold="UTRZYMAJ",
        disclaimer="Portfel modelowy i materiał edukacyjny — nie rekomendacja inwestycyjna.",
        locale="pl-PL",
        currency="PLN",
    ),
    PageConfig(
        lang="en",
        page="en/investing/portfolio-10k.html",
        data="data/investments/portfolio_10k_usd.json",
        url="https://briefrooms.com/en/investing/portfolio-10k.html",
        title="10K Investing — BriefRooms public model portfolio",
        heading="Public 10K Investing portfolio snapshot",
        description=(
            "A current static record of the portfolio value, holdings, performance and rules. "
            "The interactive dashboard below enhances these data when JavaScript runs."
        ),
        market_date="Market data",
        starting_capital="Starting capital",
        portfolio_value="Portfolio value",
        portfolio_return="Portfolio return",
        benchmark_return="Benchmark return",
        cash="Cash",
        positions="Active positions",
        holdings="Portfolio holdings and theses",
        methodology="Model objective and rules",
        status_hold="HOLD",
        disclaimer="Model portfolio and educational material — not investment advice.",
        locale="en-US",
        currency="USD",
    ),
)


PL_RULES = {
    "No leverage and no CFDs": "Bez dźwigni i bez kontraktów CFD.",
    "Weekly review of price trend, drawdown, volatility, earnings calendar and material news": (
        "Cotygodniowy przegląd trendu cenowego, obsunięcia, zmienności, kalendarza wyników i istotnych informacji."
    ),
    "Target weights are guides, not automatic trading instructions": (
        "Wagi docelowe są wytycznymi, a nie automatycznymi dyspozycjami transakcyjnymi."
    ),
    "A single stock should normally remain below 18% of portfolio value": (
        "Udział pojedynczej spółki powinien zwykle pozostawać poniżej 18% wartości portfela."
    ),
    "A broad ETF may remain up to 30% of portfolio value": (
        "Szeroki ETF może stanowić do 30% wartości portfela."
    ),
    "Rebalancing threshold: more than 5 percentage points away from target or more than 1.5 times target weight": (
        "Próg rebalancingu: odchylenie od celu o ponad 5 pkt proc. lub przekroczenie 1,5-krotności wagi docelowej."
    ),
    "Closed history and weekly snapshots are append-only": (
        "Historia zamkniętych pozycji i snapshoty tygodniowe są tylko dopisywane."
    ),
}


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def number(value: object, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def active_positions(payload: dict) -> list[dict]:
    return [position for position in payload.get("positions", []) if position.get("status") == "active"]


def value(payload: dict, config: PageConfig, stem: str) -> float:
    currency_key = "usd" if config.currency == "USD" else "pln"
    return number(payload.get(f"{stem}_{currency_key}", payload.get(f"{stem}_pln")))


def position_value(position: dict, config: PageConfig) -> float:
    key = "current_value_usd" if config.currency == "USD" else "current_value_pln"
    return number(position.get(key, position.get("current_value_pln")))


def money(amount: float, config: PageConfig) -> str:
    if config.lang == "pl":
        formatted = f"{amount:,.2f}".replace(",", "\u00a0").replace(".", ",")
        return f"{formatted}\u00a0zł"
    return f"${amount:,.2f}"


def percentage(value_: object) -> str:
    result = number(value_) * 100
    return f"{result:+.2f}%"


def market_date(payload: dict) -> str:
    raw = str(payload.get("last_market_session") or payload.get("last_updated_at") or "").strip()
    if not raw:
        return "—"
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return raw[:10]


def translated_rules(payload: dict, config: PageConfig) -> list[str]:
    rules = [str(rule) for rule in payload.get("methodology", {}).get("rules", [])]
    if config.lang == "pl":
        return [PL_RULES.get(rule, rule) for rule in rules]
    return rules


def render_snapshot(payload: dict, config: PageConfig) -> str:
    positions = active_positions(payload)
    starting = value(payload, config, "starting_capital")
    cash = value(payload, config, "cash")
    total = value(payload, config, "total_value")
    result = number(payload.get("total_return_percent"))
    benchmark = number(payload.get("benchmark_return_percent"))
    objective_key = "objective_pl" if config.lang == "pl" else "objective_en"
    objective = payload.get("methodology", {}).get(objective_key, "")
    cash_yield = payload.get("cash_yield") or {}
    yield_label = cash_yield.get("label_pl" if config.lang == "pl" else "label_en")
    cash_caption = f"{config.cash} · {yield_label}" if yield_label else config.cash

    rows = []
    for position in positions:
        thesis_key = "thesis_pl" if config.lang == "pl" else "thesis_en"
        status = str(position.get("review_flag") or config.status_hold)
        rows.append(
            "<tr>"
            f"<td><strong>{esc(position.get('label') or position.get('broker_symbol'))}</strong> "
            f"<small>{esc(position.get('broker_symbol'))}</small></td>"
            f"<td>{number(position.get('current_weight', position.get('target_weight'))) * 100:.1f}%</td>"
            f"<td>{esc(money(position_value(position, config), config))}</td>"
            f"<td>{esc(percentage(position.get('pnl_percent')))}</td>"
            f"<td>{esc(status)}</td>"
            f"<td>{esc(position.get(thesis_key) or '')}</td>"
            "</tr>"
        )

    rules = "".join(f"<li>{esc(rule)}</li>" for rule in translated_rules(payload, config))
    return (
        f"{SNAPSHOT_START}"
        '<article class="dash-card page-card i10k-static-summary" aria-labelledby="portfolio-static-title">'
        '<div class="card-head"><div>'
        f'<h2 id="portfolio-static-title">{esc(config.heading)}</h2>'
        f"<p>{esc(config.description)}</p>"
        "</div>"
        f'<span class="source-pill">{esc(config.market_date)}: <time datetime="{esc(market_date(payload))}">{esc(market_date(payload))}</time></span>'
        "</div>"
        '<div class="metric-strip">'
        f"<div><small>{esc(config.starting_capital)}</small><b>{esc(money(starting, config))}</b></div>"
        f"<div><small>{esc(config.portfolio_value)}</small><b>{esc(money(total, config))}</b></div>"
        f"<div><small>{esc(config.portfolio_return)}</small><b>{esc(percentage(result))}</b></div>"
        f"<div><small>{esc(config.benchmark_return)}</small><b>{esc(percentage(benchmark))}</b></div>"
        f"<div><small>{esc(cash_caption)}</small><b>{esc(money(cash, config))}</b></div>"
        f"<div><small>{esc(config.positions)}</small><b>{len(positions)}</b></div>"
        "</div>"
        f"<h3>{esc(config.holdings)}</h3>"
        '<div class="table-wrap"><table class="clean-table">'
        "<thead><tr>"
        + (
            "<th>Instrument</th><th>Waga</th><th>Wartość</th><th>Wynik</th><th>Status</th><th>Teza</th>"
            if config.lang == "pl"
            else "<th>Instrument</th><th>Weight</th><th>Value</th><th>Return</th><th>Status</th><th>Thesis</th>"
        )
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
        f"<h3>{esc(config.methodology)}</h3>"
        f'<p class="method-note">{esc(objective)}</p><ol>{rules}</ol>'
        f'<p class="legal">{esc(config.disclaimer)}</p>'
        "</article>"
        f"{SNAPSHOT_END}"
    )


def render_schema(payload: dict, config: PageConfig) -> str:
    modified = payload.get("generated_at") or payload.get("last_updated_at") or market_date(payload)
    data_url = "portfolio_10k_usd.json" if config.lang == "en" else "portfolio_10k.json"
    schema = {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": config.title,
        "description": config.description,
        "url": config.url,
        "inLanguage": config.locale,
        "dateModified": modified,
        "isPartOf": {"@type": "WebSite", "name": "BriefRooms", "url": "https://briefrooms.com/"},
        "mainEntity": {
            "@type": "Dataset",
            "name": config.title,
            "description": config.description,
            "creator": {"@type": "Organization", "name": "BriefRooms"},
            "dateModified": modified,
            "distribution": {
                "@type": "DataDownload",
                "encodingFormat": "application/json",
                "contentUrl": f"https://briefrooms.com/data/investments/{data_url}",
            },
        },
    }
    encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f'{SCHEMA_START}<script type="application/ld+json">{encoded}</script>{SCHEMA_END}'


def replace_marked(source: str, start: str, end: str, replacement: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), flags=re.DOTALL)
    if pattern.search(source):
        return pattern.sub(replacement, source, count=1)
    raise ValueError(f"Missing insertion marker for {start}")


def render_page(source: str, payload: dict, config: PageConfig) -> str:
    stylesheet = '<link rel="stylesheet" href="/assets/portfolio-10k-static.css?v=1">'
    if "/assets/portfolio-10k-static.css" not in source:
        source = source.replace("</head>", stylesheet + "</head>", 1)
    source = re.sub(
        r'/scripts/portfolio-10k-dashboard(-en)?\.js\?v=\d+',
        lambda match: f"/scripts/portfolio-10k-dashboard{match.group(1) or ''}.js?v=9",
        source,
    )
    source = re.sub(
        r'/scripts/portfolio-10k-navigation-guard\.js\?v=\d+',
        '/scripts/portfolio-10k-navigation-guard.js?v=5',
        source,
    )
    snapshot = render_snapshot(payload, config)
    if SNAPSHOT_START in source:
        source = replace_marked(source, SNAPSHOT_START, SNAPSHOT_END, snapshot)
    else:
        anchor = '<div class="dashboard-foot">'
        if anchor not in source:
            raise ValueError(f"Dashboard insertion point is missing in {config.page}")
        source = source.replace(anchor, snapshot + anchor, 1)

    schema = render_schema(payload, config)
    if SCHEMA_START in source:
        source = replace_marked(source, SCHEMA_START, SCHEMA_END, schema)
    else:
        source = source.replace("</head>", schema + "</head>", 1)

    robots = '<meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1">'
    if re.search(r'<meta\s+name=["\']robots["\']', source, flags=re.IGNORECASE):
        source = re.sub(
            r'<meta\s+name=["\']robots["\'][^>]*>', robots, source, count=1, flags=re.IGNORECASE
        )
    else:
        source = source.replace("</head>", robots + "</head>", 1)
    return source


def render_all(root: Path = ROOT, check: bool = False) -> list[str]:
    changed = []
    for config in PAGES:
        page = root / config.page
        data = root / config.data
        payload = json.loads(data.read_text(encoding="utf-8"))
        before = page.read_text(encoding="utf-8")
        after = render_page(before, payload, config)
        if before != after:
            changed.append(config.page)
            if not check:
                page.write_text(after, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="Fail if snapshots are not current")
    args = parser.parse_args()
    changed = render_all(args.root.resolve(), check=args.check)
    if args.check and changed:
        print("Static portfolio snapshots are stale: " + ", ".join(changed))
        return 1
    print("Static portfolio snapshots: " + (", ".join(changed) if changed else "current"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

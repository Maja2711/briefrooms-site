#!/usr/bin/env python3
"""One-time UI patch for hourly Portfolio 10K valuations."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "scripts" / "portfolio-10k-public.js"
PAGES = [
    ROOT / "pl" / "inwestycje" / "portfel-10k.html",
    ROOT / "en" / "investing" / "portfolio-10k.html",
]

text = JS.read_text(encoding="utf-8")
replacements = {
    "activeText: 'Ceny wejścia są zamrożone. Cotygodniowy przegląd aktualizuje wycenę, wyniki, trendy, kalendarz wyników i istotne nagłówki.',":
        "activeText: 'Ceny wejścia są zamrożone. Bieżące ceny i wycena są aktualizowane co godzinę podczas handlu instrumentem; przegląd tez pozostaje cotygodniowy.',",
    "activeText: 'Entry prices are frozen. The weekly review updates valuation, performance, trends, earnings calendar and material headlines.',":
        "activeText: 'Entry prices are frozen. Current prices and valuation are refreshed hourly while each instrument is trading; thesis review remains weekly.',",
    "target: 'Cel', currentWeight: 'Udział teraz', entry: 'Cena wejścia', current: 'Cena teraz', quantity:":
        "target: 'Cel', currentWeight: 'Udział teraz', entry: 'Cena wejścia', current: 'Cena teraz', quoteTime: 'Notowanie', quantity:",
    "target: 'Target', currentWeight: 'Current weight', entry: 'Entry price', current: 'Current price', quantity:":
        "target: 'Target', currentWeight: 'Current weight', entry: 'Entry price', current: 'Current price', quoteTime: 'Quote time', quantity:",
    "      ['1', 'Wycena', 'Aktualizacja cen wszystkich instrumentów i kursów USD/PLN, EUR/PLN oraz DKK/PLN.'],":
        "      ['1', 'Wycena', 'Aktualizacja cen instrumentów co godzinę podczas ich sesji oraz kursów USD/PLN, EUR/PLN i DKK/PLN.'],",
    "      ['1', 'Valuation', 'Update instrument prices and the FX rates required to express non-USD holdings in USD.'],":
        "      ['1', 'Valuation', 'Refresh instrument prices hourly during their trading sessions and update the FX rates required for reporting.'],",
    "  const text = (obj, key) => obj?.[`${key}_${lang}`] || obj?.[key] || '';":
        "  const dateTimeFmt = value => { if (!value) return T.noData; const d = new Date(value); return Number.isNaN(d.valueOf()) ? esc(value) : d.toLocaleString(locale, {year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}); };\n  const text = (obj, key) => obj?.[`${key}_${lang}`] || obj?.[key] || '';",
    "<div class=\"metric\"><small>${esc(T.current)}</small><b>${price(p.current_price,p.currency)}</b></div><div class=\"metric\"><small>${esc(T.quantity)}</small>":
        "<div class=\"metric\"><small>${esc(T.current)}</small><b>${price(p.current_price,p.currency)}</b><span class=\"news-meta\">${esc(T.quoteTime)}: ${dateTimeFmt(p.current_price_updated_at)}</span></div><div class=\"metric\"><small>${esc(T.quantity)}</small>",
    "document.getElementById('updated-meta').textContent=`${T.lastUpdate}: ${dateFmt(data.last_updated_at)} · ${T.marketSession}: ${dateFmt(data.last_market_session)}`;":
        "document.getElementById('updated-meta').textContent=`${T.lastUpdate}: ${dateTimeFmt(data.last_updated_at)} · ${T.marketSession}: ${dateFmt(data.last_market_session)}`;",
    "  load();\n})();":
        "  load();\n  setInterval(load, 60 * 60 * 1000);\n  let hiddenAt = Date.now();\n  document.addEventListener('visibilitychange', () => { if (document.hidden) hiddenAt = Date.now(); else if (Date.now() - hiddenAt >= 15 * 60 * 1000) load(); });\n})();",
}

for old, new in replacements.items():
    if old not in text:
        raise SystemExit(f"Expected UI fragment not found: {old[:90]}")
    text = text.replace(old, new, 1)
JS.write_text(text, encoding="utf-8", newline="\n")

for page in PAGES:
    html = page.read_text(encoding="utf-8")
    if "/scripts/portfolio-10k-public.js?v=3" not in html:
        raise SystemExit(f"Expected script version not found in {page}")
    html = html.replace("/scripts/portfolio-10k-public.js?v=3", "/scripts/portfolio-10k-public.js?v=4", 1)
    page.write_text(html, encoding="utf-8", newline="\n")

Path(__file__).unlink()
print("Portfolio 10K hourly UI applied to PL and EN")

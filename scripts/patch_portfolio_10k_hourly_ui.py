from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = [
    ROOT / "pl/inwestycje/portfel-10k.html",
    ROOT / "en/investing/portfolio-10k.html",
]
CSS = '<link rel="stylesheet" href="/assets/portfolio-10k-clarity.css?v=1">'
JS = '<script src="/scripts/portfolio-10k-analytics-enhanced.js?v=1" defer></script>'

for path in PAGES:
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8")
    if "/assets/portfolio-10k-clarity.css" not in text:
        text = text.replace("</head>", CSS + "</head>")
    if "/scripts/portfolio-10k-analytics-enhanced.js" not in text:
        text = text.replace("</body>", JS + "</body>")
    text = text.replace(
        '/scripts/portfolio-10k-dashboard.js?v=4',
        '/scripts/portfolio-10k-dashboard.js?v=5',
    )
    text = text.replace(
        '/scripts/portfolio-10k-dashboard-en.js?v=4',
        '/scripts/portfolio-10k-dashboard-en.js?v=5',
    )
    if path.parts[-3] == "pl":
        text = text.replace(
            '<span class="live-badge"><i></i> LIVE</span>',
            '<span class="live-badge" aria-live="polite"><i></i> SPRAWDZANIE</span>',
        )
    else:
        text = text.replace(
            '<span class="live-badge"><i></i> LIVE</span>',
            '<span class="live-badge" aria-live="polite"><i></i> CHECKING</span>',
        )
    path.write_text(text, encoding="utf-8")

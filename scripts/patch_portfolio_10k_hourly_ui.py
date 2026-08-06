from pathlib import Path
import json
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PAGES = [ROOT / "pl/inwestycje/portfel-10k.html", ROOT / "en/investing/portfolio-10k.html"]
CLARITY = '<link rel="stylesheet" href="/assets/portfolio-10k-clarity.css?v=2">'
ACCESSIBILITY = '<link rel="stylesheet" href="/assets/portfolio-10k-accessibility.css?v=2">'
DECISION_CSS = '<link rel="stylesheet" href="/assets/portfolio-10k-decision-overlay.css?v=1">'
ANALYTICS_JS = '<script src="/scripts/portfolio-10k-analytics-enhanced.js?v=2" defer></script>'
CAPITAL_JS = '<script src="/scripts/portfolio-10k-capital-summary.js?v=1" defer></script>'
EXPLAINERS_JS = '<script src="/scripts/portfolio-10k-explainers.js?v=4" defer></script>'
VERIFIED_REPORTS_JS = '<script src="/scripts/portfolio-10k-verified-material-loader.js?v=2" defer></script>'
DECISION_JS = '<script src="/scripts/portfolio-10k-decision-overlay.js?v=2" defer></script>'
EXECUTION_FINALIZER_JS = '<script src="/scripts/portfolio-10k-execution-finalizer.js?v=3" defer></script>'
AUDIT_PATH = ROOT / "data/portfolio10k/investment_room_full_audit.json"


def ensure_asset(text: str, marker: str, html: str, where: str) -> str:
    return text if marker in text else text.replace(where, html + where)


for path in PAGES:
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8")
    text = ensure_asset(text, "/assets/portfolio-10k-clarity.css", CLARITY, "</head>")
    text = re.sub(r'/assets/portfolio-10k-clarity\.css\?v=\d+', '/assets/portfolio-10k-clarity.css?v=2', text)
    text = ensure_asset(text, "/assets/portfolio-10k-accessibility.css", ACCESSIBILITY, "</head>")
    text = re.sub(r'/assets/portfolio-10k-accessibility\.css\?v=\d+', '/assets/portfolio-10k-accessibility.css?v=2', text)
    text = ensure_asset(text, "/assets/portfolio-10k-decision-overlay.css", DECISION_CSS, "</head>")
    text = re.sub(r'/assets/portfolio-10k-decision-overlay\.css\?v=\d+', '/assets/portfolio-10k-decision-overlay.css?v=1', text)
    text = ensure_asset(text, "/scripts/portfolio-10k-analytics-enhanced.js", ANALYTICS_JS, "</body>")
    text = ensure_asset(text, "/scripts/portfolio-10k-capital-summary.js", CAPITAL_JS, "</body>")
    text = ensure_asset(text, "/scripts/portfolio-10k-explainers.js", EXPLAINERS_JS, "</body>")
    text = re.sub(r'/scripts/portfolio-10k-explainers\.js\?v=\d+', '/scripts/portfolio-10k-explainers.js?v=4', text)
    text = ensure_asset(text, "/scripts/portfolio-10k-verified-material-loader.js", VERIFIED_REPORTS_JS, "</body>")
    text = re.sub(r'/scripts/portfolio-10k-verified-material-loader\.js\?v=\d+', '/scripts/portfolio-10k-verified-material-loader.js?v=2', text)
    text = ensure_asset(text, "/scripts/portfolio-10k-decision-overlay.js", DECISION_JS, "</body>")
    text = re.sub(r'/scripts/portfolio-10k-decision-overlay\.js\?v=\d+', '/scripts/portfolio-10k-decision-overlay.js?v=2', text)
    text = ensure_asset(text, "/scripts/portfolio-10k-execution-finalizer.js", EXECUTION_FINALIZER_JS, "</body>")
    text = re.sub(r'/scripts/portfolio-10k-execution-finalizer\.js\?v=\d+', '/scripts/portfolio-10k-execution-finalizer.js?v=3', text)
    text = re.sub(r'/scripts/portfolio-10k-control-public\.js\?v=\d+', '/scripts/portfolio-10k-control-public.js?v=3', text)
    text = text.replace('/scripts/portfolio-10k-dashboard.js?v=4', '/scripts/portfolio-10k-dashboard.js?v=5')
    text = text.replace('/scripts/portfolio-10k-dashboard-en.js?v=4', '/scripts/portfolio-10k-dashboard-en.js?v=5')

    if path.parts[-3] == "pl":
        text = text.replace('<span class="live-badge"><i></i> LIVE</span>', '<span class="live-badge" aria-live="polite"><i></i> SPRAWDZANIE</span>')
        text = text.replace('<div><small>Zainwestowane</small><b id="invested-value">—</b></div>', '<div><small>Kapitał startowy</small><b id="invested-value">—</b></div>')
        if 'id="portfolio-launch-label"' not in text:
            text = text.replace('<small>Od początku</small><div id="mini-chart" class="mini-chart"></div>', '<small>Od początku · <span id="portfolio-launch-label">Start: lipiec 2026</span></small><div id="mini-chart" class="mini-chart"></div>')
        if 'id="portfolio-launch-note"' not in text:
            text = text.replace('<p>Bieżące pozycje, wagi, wyniki, tezy i sygnały przeglądu.</p></div></div>', '<p>Bieżące pozycje, wagi, wyniki, tezy i sygnały przeglądu.</p><p id="portfolio-launch-note">Start: lipiec 2026</p></div></div>')
        text = text.replace('<h2 id="brace-control-title">Kto steruje Portfelem 10K</h2>', '<h2 id="brace-control-title">BRACE steruje Portfelem 10K</h2>')
        text = text.replace('Jawny stan baseline, challengera, bramek awansu i automatycznych zabezpieczeń modelowego portfela.', 'BRACE prowadzi oddzielny portfel modelowy w trybie próbnym. Cotygodniowo aktualizuje dane, raporty istotne i oceny pozycji; wykonuje tylko transakcje paper, z aktywnymi limitami ryzyka i fallbackiem do baseline.')
    else:
        text = text.replace('<span class="live-badge"><i></i> LIVE</span>', '<span class="live-badge" aria-live="polite"><i></i> CHECKING</span>')
        text = text.replace('<div><small>Invested</small><b id="invested-value">—</b></div>', '<div><small>Starting capital</small><b id="invested-value">—</b></div>')
        if 'id="portfolio-launch-label"' not in text:
            text = text.replace('<small>Since launch</small><div id="mini-chart" class="mini-chart"></div>', '<small>Since launch · <span id="portfolio-launch-label">Started: July 2026</span></small><div id="mini-chart" class="mini-chart"></div>')
        if 'id="portfolio-launch-note"' not in text:
            text = text.replace('<p>Current positions, weights, results, theses and review signals.</p></div></div>', '<p>Current positions, weights, results, theses and review signals.</p><p id="portfolio-launch-note">Started: July 2026</p></div></div>')
        text = text.replace('<h2 id="brace-control-title">Who controls the 10K Portfolio</h2>', '<h2 id="brace-control-title">BRACE controls the 10K Portfolio</h2>')
        text = text.replace('A transparent view of the baseline, challenger, promotion gates and automatic model-portfolio safeguards.', 'BRACE runs a separate model portfolio in probationary mode. It refreshes data, material reports and position assessments weekly; it executes paper trades only, with risk limits and baseline fallback kept active.')
    path.write_text(text, encoding="utf-8")


def run_room_audit() -> None:
    """Run the real-browser audit after the final page patch without blocking publication."""
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", "8000", "--bind", "127.0.0.1"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(1.5)
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/audit_investment_rooms_fast.py")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=360,
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr)
        print(f"Investment room audit exit code: {result.returncode}")
    except Exception as exc:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        AUDIT_PATH.write_text(
            json.dumps(
                {
                    "schema_version": "investment-room-full-audit-v2",
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "passed": False,
                    "fatal_error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        print(f"Investment room audit failed to execute: {exc}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


run_room_audit()

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "en/investing/portfolio-10k.html"


def main() -> None:
    html = PAGE.read_text(encoding="utf-8")
    html = html.replace(
        "AI-Powered Investment Benchmark · 10,000 PLN model portfolio",
        "AI-Powered Investment Benchmark · native 10,000 USD model portfolio",
    )
    source = '<script src="/scripts/portfolio-10k-usd-source.js?v=1"></script>'
    marker = '<script src="/scripts/portfolio-10k-material-reports-public.js?v=1" defer></script>'
    if source not in html:
        html = html.replace(marker, source + marker, 1)
    html = html.replace(
        '/scripts/portfolio-10k-dashboard-en.js?v=2',
        '/scripts/portfolio-10k-dashboard-en.js?v=5',
    )
    html = html.replace(
        '/scripts/portfolio-10k-dashboard-en.js?v=4',
        '/scripts/portfolio-10k-dashboard-en.js?v=5',
    )
    PAGE.write_text(html, encoding="utf-8")
    print("Patched English 10K page for native USD portfolio")


if __name__ == "__main__":
    main()

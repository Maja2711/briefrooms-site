#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHANGES = {
    ROOT / "pl" / "o-projekcie.html": (
        '        <p><strong>Przykład:</strong> system może porównać, czy strategia łącząca trend, momentum i zmienność daje stabilniejsze wyniki niż strategia oparta wyłącznie na trendzie — osobno podczas silnego trendu, konsolidacji i podwyższonej zmienności. Nowa metoda jest uwzględniana dopiero wtedy, gdy poprawę potwierdzają testy historyczne i kolejne obserwacje, a pełne wyniki pozostają możliwe do oceny.</p>\n',
        '',
    ),
    ROOT / "en" / "about.html": (
        '        <p><strong>Example:</strong> the system can compare whether a strategy combining trend, momentum and volatility produces more stable results than a trend-only strategy — separately during strong trends, consolidation and periods of elevated volatility. A new method is included only when historical tests and subsequent observations confirm an improvement, while the complete results remain available for evaluation.</p>\n',
        '',
    ),
}

changed = False
for path, (old, new) in CHANGES.items():
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
        print(f"updated {path.relative_to(ROOT)}")
        changed = True
    elif new == "" and "<strong>Przykład:</strong>" not in text and "<strong>Example:</strong>" not in text:
        print(f"already updated {path.relative_to(ROOT)}")
    else:
        raise SystemExit(f"Expected paragraph not found in {path.relative_to(ROOT)}")

print("done" if changed else "no changes")

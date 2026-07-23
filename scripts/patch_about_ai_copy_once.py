#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHANGES = {
    ROOT / "pl" / "o-projekcie.html": (
        '        <p>Sztuczna inteligencja jest wykorzystywana przede wszystkim jako narzędzie badawcze do testowania nowych algorytmów, porównywania metod oceny sygnałów i rozwijania zasad zarządzania ryzykiem. Nie podejmuje samodzielnie decyzji inwestycyjnych i nie zastępuje danych rynkowych, jawnej metodologii ani kontroli wyników.</p>',
        '        <p>Sztuczna inteligencja jest wykorzystywana przede wszystkim jako narzędzie badawcze do testowania nowych algorytmów, porównywania metod oceny sygnałów i rozwijania zasad zarządzania ryzykiem.</p>',
    ),
    ROOT / "en" / "about.html": (
        '        <p>Artificial intelligence is used primarily as a research tool for testing new algorithms, comparing signal-evaluation methods and developing risk-management rules. It does not make investment decisions independently and does not replace market data, a transparent methodology or performance controls.</p>',
        '        <p>Artificial intelligence is used primarily as a research tool for testing new algorithms, comparing signal-evaluation methods and developing risk-management rules.</p>',
    ),
}

changed = False
for path, (old, new) in CHANGES.items():
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
        print(f"updated {path.relative_to(ROOT)}")
        changed = True
    elif new in text:
        print(f"already updated {path.relative_to(ROOT)}")
    else:
        raise SystemExit(f"Expected AI paragraph not found in {path.relative_to(ROOT)}")

print("done" if changed else "no changes")

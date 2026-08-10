#!/usr/bin/env python3
from pathlib import Path
import re

PAGES = (
    Path('pl/inwestycje/portfel-10k.html'),
    Path('en/investing/portfolio-10k.html'),
)
NAV_GUARD = Path('scripts/portfolio-10k-navigation-guard.js')
FINALIZER = Path('scripts/portfolio-10k-execution-finalizer.js')


def remove_balanced_container(text: str, marker: str, tag: str) -> tuple[str, bool]:
    idx = text.find(marker)
    if idx < 0:
        return text, False
    start = text.rfind(f'<{tag}', 0, idx)
    if start < 0:
        raise SystemExit(f'Cannot find opening <{tag}> for {marker}')
    token = re.compile(rf'<{tag}\b[^>]*>|</{tag}\s*>', re.I)
    depth = 0
    end = None
    for match in token.finditer(text, start):
        if match.group(0).lower().startswith(f'</{tag}'):
            depth -= 1
            if depth == 0:
                end = match.end()
                break
        else:
            depth += 1
    if end is None:
        raise SystemExit(f'Cannot find closing </{tag}> for {marker}')
    return text[:start] + text[end:], True


def patch_page(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    original = text

    # Remove sidebar/top navigation controls and any other direct triggers.
    text = re.sub(
        r'<button\b[^>]*\bdata-tab=["\']projections["\'][^>]*>.*?</button>',
        '', text, flags=re.I | re.S,
    )
    text = re.sub(
        r'<a\b[^>]*\bhref=["\']#projections["\'][^>]*>.*?</a>',
        '', text, flags=re.I | re.S,
    )

    # Remove the dashboard preview card and the full projections panel.
    for marker in ('id="projection-overview"', "id='projection-overview'"):
        if marker in text:
            text, _ = remove_balanced_container(text, marker, 'article')
            break
    for marker in ('data-panel="projections"', "data-panel='projections'"):
        if marker in text:
            text, _ = remove_balanced_container(text, marker, 'section')
            break

    forbidden = ('data-tab="projections"', "data-tab='projections'",
                 'data-panel="projections"', "data-panel='projections'",
                 'id="projection-overview"', "id='projection-overview'",
                 'href="#projections"', "href='#projections'")
    leftovers = [token for token in forbidden if token in text]
    if leftovers:
        raise SystemExit(f'{path}: projections leftovers: {leftovers}')
    if text == original:
        raise SystemExit(f'{path}: no projections UI removed')
    path.write_text(text, encoding='utf-8')


def patch_navigation_guard() -> None:
    text = NAV_GUARD.read_text(encoding='utf-8')
    old = "const VALID_TABS = new Set(['overview','portfolio','benchmark','agents','projections','rules','brace','analytics','history']);"
    new = "const VALID_TABS = new Set(['overview','portfolio','benchmark','agents','rules','brace','analytics','history']);"
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise SystemExit('Navigation guard VALID_TABS shape changed unexpectedly')
    if "'projections'" in re.search(r'const VALID_TABS = new Set\([^\n]+', text).group(0):
        raise SystemExit('projections remains a valid navigation tab')
    NAV_GUARD.write_text(text, encoding='utf-8')


def patch_finalizer() -> None:
    text = FINALIZER.read_text(encoding='utf-8')
    start_marker = "    const projectionOverview = document.getElementById('projection-overview');"
    end_marker = "    const braceImpact = document.getElementById('brace-impact');"
    if start_marker in text:
        start = text.index(start_marker)
        end = text.index(end_marker, start)
        text = text[:start] + text[end:]
    if 'projection-overview' in text or 'data-panel="projections"' in text:
        raise SystemExit('Dead projections placeholder code remains in finalizer')
    FINALIZER.write_text(text, encoding='utf-8')


def main() -> None:
    for page in PAGES:
        patch_page(page)
    patch_navigation_guard()
    patch_finalizer()
    print('Removed Portfolio 10K projections tab/panel from PL and EN.')


if __name__ == '__main__':
    main()

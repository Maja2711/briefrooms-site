from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PAGES = [
    ROOT / 'pl/inwestycje/portfel-10k.html',
    ROOT / 'en/investing/portfolio-10k.html',
]
SCRIPT = '<script src="/scripts/portfolio-10k-executed-decisions-history-sync.js?v=1" defer></script>'

for path in PAGES:
    text = path.read_text(encoding='utf-8')
    text = re.sub(
        r'<script src="/scripts/portfolio-10k-executed-decisions-history-sync\.js\?v=\d+" defer></script>',
        SCRIPT,
        text,
    )
    if '/scripts/portfolio-10k-executed-decisions-history-sync.js' not in text:
        marker = '<script src="/scripts/portfolio-10k-execution-finalizer.js?v=3" defer></script>'
        if marker in text:
            text = text.replace(marker, marker + SCRIPT)
        else:
            text = text.replace('</body>', SCRIPT + '</body>')
    path.write_text(text, encoding='utf-8')

for path in PAGES:
    text = path.read_text(encoding='utf-8')
    assert text.count('/scripts/portfolio-10k-executed-decisions-history-sync.js?v=1') == 1, path

print('BRACE execution history sync installed on PL and EN.')

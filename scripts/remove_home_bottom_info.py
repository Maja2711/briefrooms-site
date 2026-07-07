#!/usr/bin/env python3
from __future__ import annotations
import re
from pathlib import Path

FILES = [Path('pl/index.html'), Path('en/index.html')]

WHY_RE = re.compile(r'<section class="why">[\s\S]*?</section>', re.I)
CSS_RE = re.compile(r'\.why\{[^}]*\}', re.I)

for path in FILES:
    html = path.read_text(encoding='utf-8')
    before = html
    html = WHY_RE.sub('', html)
    # Defensive cleanup: if a minified variant leaves only CSS, hide it too.
    if '.why{display:none!important}' not in html:
        html = html.replace('</style>', '.why{display:none!important}\n  </style>', 1)
    if html != before:
        path.write_text(html, encoding='utf-8')
        print(f'updated {path}')
    else:
        print(f'no visible block found in {path}; CSS guard ensured')

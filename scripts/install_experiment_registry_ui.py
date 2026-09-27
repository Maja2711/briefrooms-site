#!/usr/bin/env python3
"""Remove the retired Portfolio10K Lab frontend after consolidation into BriefRooms LAB."""
from pathlib import Path
import re
PAGES=(Path("pl/inwestycje/portfel-10k.html"),Path("en/investing/portfolio-10k.html"))
LEGACY=(r'<script\s+src="/scripts/portfolio-10k-experience-store\.js\?v=\d+"\s+defer></script>',r'<script\s+src="/scripts/portfolio-10k-lab-health\.js\?v=\d+"\s+defer></script>')
def update_page(path):
    text=path.read_text(encoding="utf-8"); updated=text
    for pattern in LEGACY: updated=re.sub(pattern,"",updated)
    if updated!=text: path.write_text(updated,encoding="utf-8"); return True
    return False
def main():
    changed=[str(p) for p in PAGES if update_page(p)]
    print("Portfolio10K legacy Lab removed:",", ".join(changed) if changed else "no changes")
    return 0
if __name__=="__main__": raise SystemExit(main())

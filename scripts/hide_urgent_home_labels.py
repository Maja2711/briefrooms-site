#!/usr/bin/env python3
"""Remove every category badge from homepage brief images."""

from pathlib import Path
import re


FILES = [Path("pl/index.html"), Path("en/index.html")]
OLD_RUNTIME_PATCH = re.compile(
    r"\n?<script>\s*\(function\(\)\{\s*function hide\(\).*?"
    r"setInterval\(hide,250\);\s*\}\)\(\);\s*</script>",
    re.S,
)


for path in FILES:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'<span class="tag">.*?</span>', "", text)
    text = OLD_RUNTIME_PATCH.sub("", text)
    if '<span class="tag">' in text:
        raise SystemExit(f"homepage image label still present in {path}")
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"{path}: homepage image labels removed")

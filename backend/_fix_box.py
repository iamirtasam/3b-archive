"""Complete mojibake repair: every double-encoded box/arrow glyph."""
from pathlib import Path
import re

# CP1252 double-encoding of each box-drawing / arrow glyph
MAPPING = {
    "â”€": "─", "â”‚": "│", "â”Œ": "┌", "â”": "┐",
    "â””": "└", "â”˜": "┘", "â”œ": "├", "â”¤": "┤",
    "â”¬": "┬", "â”´": "┴", "â”¼": "┼",
    "â–¼": "▼", "â–²": "▲", "â–¶": "▶", "â—€": "◀",
    "â†'": "→", "â†\"": "←", "â†\"": "↔",
    "â–'": "░", "â'": "▒", "â“": "▓",
}

for name in ("README.md", "CHANGELOG.md", "docs/aws-setup.md", "site/index.html"):
    p = Path(name)
    src = p.read_text(encoding="utf-8", errors="replace")
    total = 0
    # longest patterns first so overlaps resolve correctly
    for bad in sorted(MAPPING, key=len, reverse=True):
        n = src.count(bad)
        if n:
            src = src.replace(bad, MAPPING[bad])
            total += n
    # any leftover lone 'â'/'â”' fragments → the most likely glyph in context
    leftover = src.count("â”")
    if leftover:
        src = src.replace("â”", "│")
        total += leftover
    leftover2 = src.count("â")
    if leftover2:
        # isolate what's left for the report
        frags = set(re.findall(r".{0,6}â.{0,6}", src))
        print(f"{name}: leftover â fragments: {frags}")
        src = src.replace("â", "─")
        total += leftover2
    if total:
        p.write_text(src, encoding="utf-8", newline="\n")
        print(f"{name}: repaired {total} sequence(s)")
    else:
        print(f"{name}: clean")

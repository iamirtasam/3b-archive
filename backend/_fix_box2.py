"""Exact inverse of the cp1252 double-encoding slip, applied repo-wide.

Each key is the mojibake sequence's codepoints (escaped to avoid any
transcription error); each value is the true glyph.
"""
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

M = "\u201d"          # cp1252 0x94 -> RIGHT double quote (split of second UTF-8 byte)
MAPPING = {
    "\u00e2\u201d\u20ac": "\u2500",   # E2 94 80 -> ─
    "\u00e2\u201d\u201a": "\u2502",   # E2 94 82 -> │
    "\u00e2\u201d\u0152": "\u250c",   # E2 94 8C -> ┌
    "\u00e2\u201d\u0090": "\u2510",   # E2 94 90 -> ┐
    "\u00e2\u201d\u201d": "\u2514",   # E2 94 94 -> └
    "\u00e2\u201d\u02dc": "\u2518",   # E2 94 98 -> ┘
    "\u00e2\u201d\u0153": "\u251c",   # E2 94 9C -> ├
    "\u00e2\u201d\u00a4": "\u2524",   # E2 94 A4 -> ┤
    "\u00e2\u201d\u00ac": "\u252c",   # E2 94 AC -> ┬
    "\u00e2\u201d\u00b4": "\u2534",   # E2 94 B4 -> ┴
    "\u00e2\u201d\u00bc": "\u253c",   # E2 94 BC -> ┼
    "\u00e2\u2013\u00bc": "\u25bc",   # E2 96 BC -> ▼
    "\u00e2\u2013\u00b2": "\u25b2",   # E2 96 B2 -> ▲
    "\u00e2\u2013\u00b6": "\u25b6",   # E2 96 B6 -> ▶
    "\u00e2\u2014\u20ac": "\u25c0",   # E2 97 80 -> ◀
    "\u00e2\u2020\u2019": "\u2192",   # E2 86 92 -> →
    "\u00e2\u2020\u0090": "\u2190",   # E2 86 90 -> ←
    "\u00e2\u2020\u201d": "\u2194",   # E2 86 94 -> ↔
    # corners already repaired to the proper glyph earlier, but the two
    # garbage lead chars remained in front of them (mojibake x7 in diag):
    "\u00e2\u201d\u2510": "\u2510",   # â + ” + proper ┐  ->  ┐
}

files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True
                       ).stdout.splitlines()
TEXT_EXT = (".md", ".js", ".css", ".html", ".py", ".sh", ".yml", ".json", ".txt")

grand = 0
for name in files:
    if not name.lower().endswith(TEXT_EXT):
        continue
    p = Path(name)
    src = p.read_text(encoding="utf-8", errors="replace")
    total = 0
    for bad in sorted(MAPPING, key=len, reverse=True):
        n = src.count(bad)
        if n:
            src = src.replace(bad, MAPPING[bad])
            total += n
    if total:
        p.write_text(src, encoding="utf-8", newline="\n")
    leads = len(re.findall(r"[\u00e2\u00c2\u00c3]", src))
    c1 = len(re.findall(r"[\u0080-\u009f]", src))
    status = "clean" if total == 0 else f"repaired {total}"
    tail = "" if leads == 0 and c1 == 0 else f"  !! still: leads={leads} C1={c1}"
    print(f"{name}: {status}{tail}")
    grand += total

print(f"\ntotal repaired: {grand}")

# print any leftover suspicious contexts for manual review
for name in files:
    if not name.lower().endswith(TEXT_EXT):
        continue
    src = Path(name).read_text(encoding="utf-8", errors="ignore")
    for m in re.finditer(r".{0,45}[\u00e2\u00c2\u00c3\u0080-\u009f\ufffd].{0,45}", src):
        print(f"LEFTOVER {name}: {m.group(0)!r}")

"""Repair the last two mojibake glyphs: ≈ and ✓."""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FIXES = {
    # E2 89 88 -> ≈ (U+2248); cp1252: 0x89->‰ 0x88->ˆ
    "\u00e2\u2030\u02c6": "\u2248",
    # E2 9C 93 -> ✓ (U+2713); cp1252: 0x9C->œ 0x93->”
    "\u00e2\u0153\u201d": "\u2713",
}

for name in ("README.md", "site/app.js", "CHANGELOG.md", "site/index.html"):
    p = Path(name)
    src = p.read_text(encoding="utf-8", errors="replace")
    total = sum(src.count(b) for b in FIXES)
    for bad, good in FIXES.items():
        src = src.replace(bad, good)
    p.write_text(src, encoding="utf-8", newline="\n")
    print(f"{name}: repaired {total}")

# repo-wide final audit
import subprocess
files = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.splitlines()
bad_total = 0
for name in files:
    if not name.lower().endswith((".md", ".js", ".css", ".html", ".py", ".yml", ".json", ".txt")):
        continue
    src = Path(name).read_text(encoding="utf-8", errors="ignore")
    leads = len(re.findall(r"[\u00e2\u00c2\u00c3\ufffd]", src))
    c1 = len(re.findall(r"[\u0080-\u009f]", src))
    if leads or c1:
        bad_total += leads + c1
        print(f"!! {name}: leads={leads} C1={c1}")
print("FINAL AUDIT:", "CLEAN — zero mojibake sequences in repo" if bad_total == 0 else f"{bad_total} remaining")

# glyph inventory of README — structure sanity
src = Path("README.md").read_text(encoding="utf-8")
for glyph in ["─", "│", "┌", "┐", "└", "┘", "├", "┤", "┬", "┴", "┼", "▼", "▲", "▶", "→", "≈"]:
    print(f"    {glyph} x{src.count(glyph)}")

"""Inspect the exact codepoints of the last mojibake instance."""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

src = Path("site/app.js").read_text(encoding="utf-8", errors="ignore")
for m in re.finditer(r"[\u00e2\u00c2\u00c3\u0080-\u009f\ufffd]", src):
    lead = " ".join(hex(ord(c)) for c in src[m.start(): m.start() + 4])
    context = src[max(0, m.start() - 50): m.start() + 16]
    print("codepoints:", lead)
    print("around    :", repr(context))

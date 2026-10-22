"""Definitive diagnostic: compare local README bytes vs what GitHub serves."""
import hashlib
import re
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

local_raw = Path("README.md").read_bytes()
local = local_raw.decode("utf-8", errors="replace")
lsha = hashlib.sha256(local_raw).hexdigest()[:16]
print(f"local : {len(local_raw)} bytes  sha {lsha}")

url = "https://raw.githubusercontent.com/iamirtasam/3b-archive/main/README.md"
remote_raw = urllib.request.urlopen(url, timeout=30).read()
remote = remote_raw.decode("utf-8", errors="replace")
gsha = hashlib.sha256(remote_raw).hexdigest()[:16]
print(f"github: {len(remote_raw)} bytes  sha {gsha}")
print(f"identical: {lsha == gsha}")
print()


def stats(label, text):
    # proper UTF-8 box/arrow glyphs
    box = len(re.findall(r"[\u2500-\u257f]", text))
    arrows = text.count("\u2192") + text.count("\u2190") + text.count("\u2194")
    trips = text.count("\u25b2") + text.count("\u25bc") + text.count("\u25b6") + text.count("\u25c0")
    # mojibake lead characters (double-encoded UTF-8, cp1252-decoded bytes)
    leads = len(re.findall(r"[\u00e2\u00c2\u00c3]", text))
    c1 = len(re.findall(r"[\u0080-\u009f]", text))
    fffd = text.count("\ufffd")
    print(f"{label}  proper-box={box}  arrows={arrows}  triangles={trips}  "
          f"moji-leads={leads}  C1={c1}  FFFD={fffd}")


stats("local ", local)
stats("github", remote)
print()

for label, text in (("local", local), ("github", remote)):
    lines = [ln for ln in text.splitlines() if re.search(r"[\u00e2\u00c2\u00c3]", ln)]
    print(f"--- {label}: {len(lines)} damaged-looking lines, first 3:")
    for ln in lines[:3]:
        print("   ", repr(ln[:110]))
    print()

# what mojibake sequences actually exist?
seqs = {}
for label, text in (("local", local), ("github", remote)):
    for m in re.findall(r"[\u00e2\u00c2\u00c3].{0,2}", text):
        seqs.setdefault(label, {})
        seqs[label][m] = seqs[label].get(m, 0) + 1

for label in seqs:
    common = sorted(seqs[label].items(), key=lambda kv: -kv[1])[:14]
    print(f"{label} top mojibake pairs:")
    for s, n in common:
        print(f"    {s!r} x{n}  codepoints={[hex(ord(c)) for c in s]}")

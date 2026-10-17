"""Verify the two recovered GTA V videos are in the catalog correctly."""
import json
from pathlib import Path

import boto3

CFG = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
r = boto3.client("dynamodb", region_name=CFG["aws"]["region"]).scan(
    TableName="svg_videos",
    ProjectionExpression="slug,folder,duration_seconds,width,height")
found = 0
for it in r.get("Items", []):
    slug = it["slug"]
    if "grand-theft" in slug and ("ec0fcb38" in slug or "d342c6c5" in slug):
        found += 1
        d = int(it.get("duration_seconds", 0))
        w = int(it.get("width", 0))
        h = int(it.get("height", 0))
        print(f"{slug}")
        print(f"  folder={it.get('folder')}  duration={d // 3600}h{(d % 3600) // 60:02d}m  {w}x{h}")
if not found:
    print("NONE FOUND - packager may still be running")

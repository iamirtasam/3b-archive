"""Find catalog entries whose title appears more than once (duplicate uploads)."""
import json
from pathlib import Path

import boto3
from boto3.dynamodb.types import TypeDeserializer

CFG = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
raw = boto3.client("dynamodb", region_name=CFG["aws"]["region"]).scan(
    TableName="svg_videos")
des = TypeDeserializer()
items = [{k: des.deserialize(v) for k, v in it.items()} for it in raw.get("Items", [])]

by_title = {}
for it in items:
    t = str(it.get("title", ""))
    by_title.setdefault(t, []).append((it["slug"], it.get("folder", ""),
                                       int(it.get("duration_seconds", 0))))
dups = {t: v for t, v in by_title.items() if len(v) > 1}
if not dups:
    print("no duplicates")
for t, v in dups.items():
    print(f'"{t}" x{len(v)}:')
    for slug, folder, dur in v:
        print(f"   {slug}  folder={folder}  {dur // 60} min")

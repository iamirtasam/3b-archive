"""Dedupe YouTube uploads: parse Lambda logs for every push, keep the first
video per slug, delete duplicates, fix svg_youtube ledger entries."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3

CFG = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
REGION = CFG["aws"]["region"]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "local" / "youtube"))

logs = boto3.client("logs", region_name=REGION)
yt_table = boto3.resource("dynamodb", region_name=REGION).Table("svg_youtube")


def youtube_client():
    from push_to_youtube import youtube_client
    return youtube_client()


start = int((datetime.now(timezone.utc) - timedelta(days=3)).timestamp() * 1000)
by_slug = {}
token = None
while True:
    kw = dict(logGroupName="/aws/lambda/s3-streaming-api", startTime=start,
              filterPattern="\"YT pushed\"")
    if token:
        kw["nextToken"] = token
    resp = logs.filter_log_events(**kw)
    for e in resp.get("events", []):
        msg = e["message"].strip()
        try:
            body = msg.split("YT pushed ", 1)[1]
            slug, vid = body.split(" -> ")
            vid = vid.split(" ")[0]
            by_slug.setdefault(slug, []).append(
                {"vid": vid, "ts": e["timestamp"]})
        except (ValueError, IndexError):
            pass
    token = resp.get("nextToken")
    if not token:
        break

yt = youtube_client()
dupes = 0
for slug, pushes in by_slug.items():
    pushes.sort(key=lambda p: p["ts"])
    keep = pushes[0]["vid"]
    extras = [p["vid"] for p in pushes[1:]]
    if extras:
        print(f"{slug}: keeping {keep}, deleting {len(extras)} duplicate(s)")
        for vid in extras:
            try:
                yt.videos().delete(id=vid).execute()
                print(f"  deleted {vid}")
                dupes += 1
            except Exception as err:  # noqa: BLE001
                print(f"  delete failed for {vid}: {err}")
    entry = yt_table.get_item(Key={"slug": slug}).get("Item", {})
    if entry.get("youtube_id") != keep:
        entry["youtube_id"] = keep
        entry["slug"] = slug
        yt_table.put_item(Item=entry)
print(f"done: {dupes} duplicate(s) deleted")

"""Sweep staged originals that never packaged (corrupt-assembled objects
from the sync-complete era), verify them locally, and repackage the valid
ones. Broken beyond repair -> deleted (they were junk assemblies)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text())
BUCKET = CFG["s3"]["bucket"]
REGION = CFG["aws"]["region"]
WORK = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("H:/tmp/repkg")
FFMPEG = str(Path(sys.argv[2])) if len(sys.argv) > 2 else "ffmpeg"

s3 = boto3.client("s3", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)
ddb = boto3.resource("dynamodb", region_name=REGION).Table("svg_videos")

staged = {o["Key"] for o in s3.list_objects_v2(
    Bucket=BUCKET, Prefix="uploads/irtasam/").get("Contents", [])}
cat = {i["slug"] for i in ddb.scan(ProjectionExpression="slug").get("Items", [])}

print(f"{len(staged)} staged original(s) that failed packaging")
ok = bad = 0
WORK.mkdir(parents=True, exist_ok=True)
for key in sorted(staged):
    name = key.split("/")[-1]
    slug_base = name.rsplit(".", 1)[0]
    print(f"checking {name[:60]}…", flush=True)
    local = WORK / name
    tmp = WORK / (name + ".part")
    s3.download_file(BUCKET, key, str(tmp))
    tmp.replace(local)
    # integrity probe
    probe = subprocess.run(
        [FFMPEG, "-v", "error", "-nostdin", "-i", str(local), "-f", "null", "-"],
        capture_output=True, timeout=900)
    if probe.returncode != 0:
        err = probe.stderr.decode(errors="replace")[-200:]
        print(f"  CORRUPT -> deleting staged object ({err[-80:]})")
        s3.delete_object(Bucket=BUCKET, Key=key)
        local.unlink(missing_ok=True)
        bad += 1
        continue
    print("  valid - firing packager")
    import hashlib
    import re
    stem = name.rsplit(".", 1)[0]
    base = re.sub(r"[^a-z0-9-_]+", "-", stem.lower()).strip("-") or "video"
    slug = f"{base}-{hashlib.md5(key.encode()).hexdigest()[:8]}"
    lam.invoke(FunctionName="s3-streaming-api", InvocationType="Event",
               Payload=json.dumps({"action": "package", "key": key,
                                   "slug": slug, "title": stem}))
    local.unlink(missing_ok=True)
    ok += 1

shutil.rmtree(WORK, ignore_errors=True)
print(f"done: {ok} valid re-fired, {bad} corrupt deleted")

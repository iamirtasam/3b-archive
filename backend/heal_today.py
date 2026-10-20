"""Heal the two Oct-20 Cfx.re sessions (8.77 GB + 5.7 GB): complete from
S3's part list and fire the packager. Same logic as finish_uploads.main
but filtered to today's sessions."""
import json
from pathlib import Path

import boto3

CFG = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
s3 = boto3.client("s3", region_name=CFG["aws"]["region"])
lam = boto3.client("lambda", region_name=CFG["aws"]["region"])

ups = [u for u in s3.list_multipart_uploads(
    Bucket=CFG["s3"]["bucket"]).get("Uploads", [])
    if u["Initiated"].isoformat()[:16] >= "2026-10-20T02:38"]

for u in ups:
    parts, token = [], None
    while True:
        kw = dict(Bucket=CFG["s3"]["bucket"], Key=u["Key"],
                  UploadId=u["UploadId"])
        if token:
            kw["PartNumberMarker"] = token
        r = s3.list_parts(**kw)
        parts.extend(r.get("Parts", []))
        token = r.get("NextPartNumberMarker")
        if not r.get("IsTruncated"):
            break
    parts.sort(key=lambda p: p["PartNumber"])
    nums = [p["PartNumber"] for p in parts]
    gaps = [n for n in range(1, max(nums) + 1) if n not in set(nums)]
    name = u["Key"].split("/")[-1][:55]
    if gaps:
        print(f"SKIP {name}: {len(gaps)} gaps - needs re-upload")
        continue
    s3.complete_multipart_upload(
        Bucket=CFG["s3"]["bucket"], Key=u["Key"], UploadId=u["UploadId"],
        MultipartUpload={"Parts": [
            {"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in parts]})
    total = sum(p["Size"] for p in parts)
    print(f"completed {name}: {len(parts)} parts, {total/1e9:.2f} GB")
    import hashlib
    import re
    stem = u["Key"].split("/")[-1].rsplit(".", 1)[0]
    base = re.sub(r"[^a-z0-9-_]+", "-", stem.lower()).strip("-") or "video"
    slug = f"{base}-{hashlib.md5(u['Key'].encode()).hexdigest()[:8]}"
    lam.invoke(FunctionName="s3-streaming-api", InvocationType="Event",
               Payload=json.dumps({"action": "package", "key": u["Key"],
                                   "slug": slug, "title": stem}))
    print(f"  packager fired for {slug}")

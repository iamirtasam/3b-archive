"""Check the state of the four PAUSED/403 files: live session? parts? archived?"""
import json
from pathlib import Path

import boto3

CFG = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
s3 = boto3.client("s3", region_name=CFG["aws"]["region"])
ddb = boto3.resource("dynamodb", region_name=CFG["aws"]["region"]).Table("svg_videos")

FILES = ["Pacify 2025.03.25 - 23.16.05.04.DVR.mp4",
         "Desktop 2025.06.11 - 15.06.42.01.mp4",
         "Desktop 2025.06.08 - 06.10.45.04.mp4",
         "Desktop 2025.06.08 - 02.37.37.03.mp4"]

ups = {u["Key"]: u for u in s3.list_multipart_uploads(
    Bucket=CFG["s3"]["bucket"]).get("Uploads", [])}
catalog = {i["slug"] for i in ddb.scan(ProjectionExpression="slug").get("Items", [])}
staged = {o["Key"] for o in s3.list_objects_v2(
    Bucket=CFG["s3"]["bucket"], Prefix="uploads/irtasam/").get("Contents", [])}

for f in FILES:
    key = f"uploads/irtasam/Desktop OLD/{f}"
    session = ups.get(key)
    state = []
    if session:
        parts = []
        token = None
        while True:
            kw = dict(Bucket=CFG["s3"]["bucket"], Key=key,
                      UploadId=session["UploadId"])
            if token:
                kw["PartNumberMarker"] = token
            r = s3.list_parts(**kw)
            parts.extend(r.get("Parts", []))
            token = r.get("NextPartNumberMarker")
            if not r.get("IsTruncated"):
                break
        gb = sum(p["Size"] for p in parts) / 1e9
        state.append(f"live session, {len(parts)} parts, {gb:.2f} GB uploaded")
    else:
        state.append("NO live session")
    if key in staged:
        state.append("staged object exists (fully assembled!)")
    # is it archived under either slug formula (old key without folder / new)?
    for k in (key, f"uploads/irtasam/{f}"):
        import hashlib, re
        stem = f.rsplit(".", 1)[0]
        base = re.sub(r"[^a-z0-9-_]+", "-", stem.lower()).strip("-") or "video"
        slug = f"{base}-{hashlib.md5(k.encode()).hexdigest()[:8]}"
        if slug in catalog:
            state.append(f"ALREADY ARCHIVED as {slug}")
    print(f"{f[:45]}: " + "; ".join(state))

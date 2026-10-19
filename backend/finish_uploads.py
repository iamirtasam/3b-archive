"""Heal every orphaned multipart session after the async-finish migration.

For each session: list parts from S3, verify no gaps, complete, fire the
packager. Sessions with gaps (truly partial) are aborted to stop the pile
from growing. Idempotent - safe to run anytime."""
import json
from pathlib import Path

import boto3

CFG = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
s3 = boto3.client("s3", region_name=CFG["aws"]["region"])
lam = boto3.client("lambda", region_name=CFG["aws"]["region"])


def list_all_parts(bucket, key, upload_id):
    parts, token = [], None
    while True:
        kw = dict(Bucket=bucket, Key=key, UploadId=upload_id)
        if token:
            kw["PartNumberMarker"] = token
        r = s3.list_parts(**kw)
        parts.extend(r.get("Parts", []))
        token = r.get("NextPartNumberMarker")
        if not r.get("IsTruncated"):
            break
    return parts


def slug_for(key: str, filename: str) -> str:
    import hashlib
    import re
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    base = re.sub(r"[^a-z0-9-_]+", "-", stem.lower()).strip("-") or "video"
    return f"{base}-{hashlib.md5(key.encode()).hexdigest()[:8]}"


def main() -> None:
    ups = s3.list_multipart_uploads(Bucket=CFG["s3"]["bucket"]).get("Uploads", [])
    print(f"{len(ups)} orphaned session(s)")
    completed = aborted = 0
    for u in ups:
        parts = list_all_parts(CFG["s3"]["bucket"], u["Key"], u["UploadId"])
        parts.sort(key=lambda p: p["PartNumber"])
        nums = [p["PartNumber"] for p in parts]
        gaps = [n for n in range(1, max(nums, default=0) + 1)
                if n not in set(nums)]
        name = u["Key"].split("/")[-1][:50]
        if not parts or gaps:
            s3.abort_multipart_upload(Bucket=CFG["s3"]["bucket"],
                                      Key=u["Key"], UploadId=u["UploadId"])
            aborted += 1
            reason = "empty" if not parts else f"{len(gaps)} gaps"
            print(f"  aborted {name} ({reason})")
            continue
        s3.complete_multipart_upload(
            Bucket=CFG["s3"]["bucket"], Key=u["Key"], UploadId=u["UploadId"],
            MultipartUpload={"Parts": [
                {"PartNumber": p["PartNumber"], "ETag": p["ETag"]}
                for p in parts]})
        slug = slug_for(u["Key"], u["Key"].split("/")[-1])
        stem = u["Key"].split("/")[-1].rsplit(".", 1)[0]
        lam.invoke(FunctionName="s3-streaming-api", InvocationType="Event",
                   Payload=json.dumps({"action": "package", "key": u["Key"],
                                       "slug": slug, "title": stem}))
        completed += 1
        print(f"  completed {name} ({len(parts)} parts, "
              f"{sum(p['Size'] for p in parts)/1e9:.2f} GB) -> packager fired")
    print(f"done: {completed} completed, {aborted} aborted")


if __name__ == "__main__":
    main()

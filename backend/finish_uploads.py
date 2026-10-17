"""Complete orphaned multipart uploads that are fully uploaded but were
never finalized (browser gateway timeout on the finish call), then fire
the Lambda packager for each. Safe: refuses anything with part gaps or a
suspicious full-size last part (possible truncation)."""
import hashlib
import json
import re
import time
from pathlib import Path

import boto3

CFG = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
s3 = boto3.client("s3", region_name=CFG["aws"]["region"])
lam = boto3.client("lambda", region_name=CFG["aws"]["region"])
ddb = boto3.resource("dynamodb", region_name=CFG["aws"]["region"]).Table("svg_videos")
FULL_PART = 16 * 1024 * 1024


def slug_for(key: str, filename: str) -> str:
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    base = re.sub(r"[^a-z0-9-_]+", "-", stem.lower()).strip("-") or "video"
    return f"{base}-{hashlib.md5(key.encode()).hexdigest()[:8]}"


def catalog_slugs() -> set:
    r = ddb.scan(ProjectionExpression="slug")
    return {i["slug"] for i in r.get("Items", [])}


def main() -> None:
    known = catalog_slugs()
    uploads = s3.list_multipart_uploads(Bucket=CFG["s3"]["bucket"]).get("Uploads", [])
    for u in uploads:
        if "/test/" in u["Key"]:                      # my old test session
            s3.abort_multipart_upload(Bucket=CFG["s3"]["bucket"],
                                      Key=u["Key"], UploadId=u["UploadId"])
            print(f"aborted stale test session: {u['Key']}")
            continue

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
        last_size = parts[-1]["Size"]
        total = sum(p["Size"] for p in parts)
        name = u["Key"].split("/")[-1]

        if gaps:
            print(f"SKIP {name}: {len(gaps)} missing parts (truncated upload)")
            continue
        if last_size >= FULL_PART:
            print(f"SKIP {name}: last part is a full 16 MB chunk - the upload "
                  f"may be truncated; not auto-completing")
            continue

        print(f"completing {name}: {len(parts)} parts, {total/1e9:.2f} GB, "
              f"last part {last_size/1e6:.1f} MB")
        s3.complete_multipart_upload(
            Bucket=CFG["s3"]["bucket"], Key=u["Key"], UploadId=u["UploadId"],
            MultipartUpload={"Parts": [
                {"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in parts
            ]})
        head = s3.head_object(Bucket=CFG["s3"]["bucket"], Key=u["Key"])
        print(f"  assembled object: {head['ContentLength']/1e9:.2f} GB")

        filename = u["Key"].split("/")[-1]
        stem = filename.rsplit(".", 1)[0]
        slug = slug_for(u["Key"], filename)
        title = stem
        lam.invoke(FunctionName="s3-streaming-api", InvocationType="Event",
                   Payload=json.dumps({"action": "package", "key": u["Key"],
                                       "slug": slug, "title": title}))
        print(f"  packager fired for {slug}")

    # wait for the catalog entries to appear
    deadline = time.time() + 900
    while time.time() < deadline:
        now = catalog_slugs()
        done = [s for s in (slug_for(u["Key"], u["Key"].split("/")[-1])
                            for u in uploads if "/test/" not in u["Key"])
                if s in now]
        print(f"  catalog: {len(done)} of the recovered videos live…")
        if done:
            for s in done:
                print(f"    LIVE: {s}")
            if len(done) >= len([u for u in uploads if "/test/" not in u["Key"]]):
                return
        time.sleep(20)


if __name__ == "__main__":
    main()

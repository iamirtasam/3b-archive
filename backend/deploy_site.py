"""Upload the website files to S3 and invalidate the CloudFront cache.

Usage:
    python backend/deploy_site.py

Uploads site/* to the bucket root (index.html at /), with correct
content types and cache headers, then creates a CloudFront invalidation
so the new files are visible immediately.
"""
from __future__ import annotations

import json
import mimetypes
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text())
SITE_DIR = ROOT / "site"

BUCKET = CONFIG["s3"]["bucket"]
DISTRIBUTION_ID = CONFIG.get("cloudfront", {}).get("distribution_id")

# Cache policy: short TTL for app code (redeploys appear within 5 min),
# long TTL for the vendored hls.js (content-addressed by filename).
CACHE_RULES = {
    "index.html": ("text/html; charset=utf-8", "max-age=60"),
    "styles.css": ("text/css; charset=utf-8", "max-age=300"),
    "app.js": ("text/javascript; charset=utf-8", "max-age=300"),
    "hls.min.js": ("text/javascript; charset=utf-8", "max-age=31536000"),
}


def main() -> None:
    s3 = boto3.client("s3")

    files = sorted(p for p in SITE_DIR.iterdir() if p.is_file())
    if not files:
        raise SystemExit(f"No files found in {SITE_DIR}")

    for path in files:
        ctype, cache = CACHE_RULES.get(
            path.name,
            (mimetypes.guess_type(path.name)[0] or "application/octet-stream",
             "max-age=300"),
        )
        s3.upload_file(
            str(path), BUCKET, path.name,
            ExtraArgs={"ContentType": ctype, "CacheControl": cache},
        )
        print(f"Uploaded {path.name} ({path.stat().st_size:,} bytes, {ctype})")

    if not DISTRIBUTION_ID:
        print("No cloudfront.distribution_id in config.json - skipping invalidation.")
        return

    cf = boto3.client("cloudfront")
    paths = [f"/{p.name}" for p in files]
    resp = cf.create_invalidation(
        DistributionId=DISTRIBUTION_ID,
        InvalidationBatch={"Paths": {"Quantity": len(paths), "Items": paths},
                           "CallerReference": f"site-{int(__import__('time').time())}"},
    )
    print(f"Invalidation created: {resp['Invalidation']['Id']} ({len(paths)} paths)")


if __name__ == "__main__":
    main()

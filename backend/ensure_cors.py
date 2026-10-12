"""Ensure the bucket's CORS rules allow browser direct-to-S3 uploads.

Usage:
    python backend/ensure_cors.py

The web uploader PUTs 16 MB parts straight to S3 with presigned URLs.
That only works when the bucket's CORS configuration allows the site
origin, exposes the ETag header (needed to complete multipart uploads),
and permits the request headers browsers send preflight.
"""
from __future__ import annotations

import json
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text())
BUCKET = CONFIG["s3"]["bucket"]
DOMAIN = CONFIG["cloudfront"]["domain"]

WANTED = {
    "CORSRules": [{
        "AllowedOrigins": [f"https://{DOMAIN}"],
        "AllowedMethods": ["PUT"],
        "AllowedHeaders": ["*"],
        "ExposeHeaders": ["ETag"],
        "MaxAgeSeconds": 3600,
    }]
}


def main() -> None:
    s3 = boto3.client("s3")
    try:
        current = s3.get_bucket_cors(Bucket=BUCKET).get("CORSRules", [])
    except s3.exceptions.NoSuchCORS:
        current = []

    if current == WANTED["CORSRules"]:
        print("CORS already correct.")
        return

    s3.put_bucket_cors(Bucket=BUCKET, CORSConfiguration=WANTED)
    print(f"CORS configured on {BUCKET}: PUT from https://{DOMAIN}, ETag exposed.")


if __name__ == "__main__":
    main()

"""Update the CloudFront distribution to serve site + API + videos from one domain.

Usage:
    python backend/sync_cloudfront.py [--no-wait]

Changes applied (idempotent):
    1. New origin: the API Gateway HTTP API (for /api/*)
    2. New behavior /api/*   -> API origin, caching disabled, CORS-friendly methods
    3. New behavior /videos/* -> S3 origin, trusted key groups preserved (signed wall)
    4. Default behavior (*)   -> S3 origin, public site files, no key groups
    5. DefaultRootObject      -> index.html

Run time: the distribution redeploys globally, taking several minutes.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text())
STATE = Path(__file__).resolve().parent / "api_endpoint.json"

DISTRIBUTION_ID = CONFIG.get("cloudfront", {}).get("distribution_id")
BUCKET = CONFIG["s3"]["bucket"]
REGION = CONFIG["aws"]["region"]

MANAGED_CACHING_OPTIMIZED = "658327ea-f89d-4fab-a63d-7f02bd39ecda"
MANAGED_CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-wait", action="store_true",
                        help="Do not block until the distribution is Deployed")
    args = parser.parse_args()

    if not DISTRIBUTION_ID:
        raise SystemExit("Add cloudfront.distribution_id to config.json first.")
    if not STATE.exists():
        raise SystemExit("backend/api_endpoint.json not found - deploy the API first.")
    api_host = json.loads(STATE.read_text())["endpoint"].split("//")[1].rstrip("/")

    cf = boto3.client("cloudfront")
    resp = cf.get_distribution_config(Id=DISTRIBUTION_ID)
    config = resp["DistributionConfig"]
    etag = resp["ETag"]

    s3_origin_id = config["DefaultCacheBehavior"]["TargetOriginId"]
    api_origin_id = "api-gateway-origin"

    origins = config["Origins"].get("Items", [])

    # Normalize fields the UpdateDistribution API requires on every origin
    # (creates tolerate defaults; updates demand them explicitly).
    for origin in origins:
        origin.setdefault("OriginPath", "")
        origin.setdefault("CustomHeaders", {"Quantity": 0, "Items": []})
        origin.setdefault("ConnectionAttempts", 3)
        origin.setdefault("ConnectionTimeout", 10)
        origin.setdefault("OriginShield", {"Enabled": False})
        if "S3OriginConfig" in origin:
            origin["S3OriginConfig"].setdefault("OriginAccessIdentity", "")
        if "CustomOriginConfig" in origin:
            origin["CustomOriginConfig"].setdefault("OriginReadTimeout", 30)
            origin["CustomOriginConfig"].setdefault("OriginKeepaliveTimeout", 5)

    if not any(o["Id"] == api_origin_id for o in origins):
        origins.append({
            "Id": api_origin_id,
            "DomainName": api_host,
            "OriginPath": "",
            "CustomOriginConfig": {
                "HTTPPort": 80,
                "HTTPSPort": 443,
                "OriginProtocolPolicy": "https-only",
                "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
            },
        })
        print(f"Origin added: {api_origin_id} -> {api_host}")
    else:
        print(f"Origin exists: {api_origin_id}")
    config["Origins"] = {"Quantity": len(origins), "Items": origins}

    # 2 + 3. /api/* and /videos/* behaviors ----------------------------------
    default = config["DefaultCacheBehavior"]
    key_groups = default.pop("TrustedKeyGroups", None)  # moved to /videos/*

    def behavior(path: str, target: str, cache_policy: str,
                 with_key_groups: bool) -> dict:
        b = {
            "PathPattern": path,
            "TargetOriginId": target,
            "ViewerProtocolPolicy": "https-only",
            "CachePolicyId": cache_policy,
            "MinTTL": 0,
        }
        if with_key_groups and key_groups:
            b["TrustedKeyGroups"] = key_groups
        return b

    wanted = {
        "/api/*": behavior("/api/*", api_origin_id, MANAGED_CACHING_DISABLED, False),
        "/videos/*": behavior("/videos/*", s3_origin_id, MANAGED_CACHING_OPTIMIZED, True),
    }

    existing = config["CacheBehaviors"].get("Items", [])
    existing = [b for b in existing if b["PathPattern"] not in wanted]
    for b in wanted.values():
        existing.append(b)
        print(f"Behavior set: {b['PathPattern']} -> {b['TargetOriginId']}")
    config["CacheBehaviors"] = {"Quantity": len(existing), "Items": existing}

    # 4. Default behavior becomes the public site ----------------------------
    print("Default behavior: public site files (key groups removed)")

    # 5. Root object ----------------------------------------------------------
    config["DefaultRootObject"] = "index.html"

    print("Updating distribution (this triggers a global redeploy)...")
    cf.update_distribution(Id=DISTRIBUTION_ID, IfMatch=etag, DistributionConfig=config)

    if args.no_wait:
        print("Update submitted. Status will reach 'Deployed' in a few minutes.")
        return

    print("Waiting for Deployed status (typically 3-6 minutes)...")
    waiter = cf.get_waiter("distribution_deployed")
    waiter.wait(Id=DISTRIBUTION_ID,
                WaiterConfig={"Delay": 20, "MaxAttempts": 60})
    print("Distribution Deployed.")


if __name__ == "__main__":
    main()

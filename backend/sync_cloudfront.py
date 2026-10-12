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

MANAGED_CACHING_OPTIMIZED = "658327ea-f89d-4fab-a63d-7e88639e58f6"
MANAGED_CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
# Forwards every viewer header (cookies, content-type) to the origin except
# Host, which must stay on the origin domain for API Gateway to accept it.
# Free-plan distributions cannot use custom policies, so the signed CloudFront-*
# cookies on /videos/* are forwarded (and edge-validated) via this managed
# origin request policy instead of a cookie-whitelisting cache policy.
MANAGED_ALL_VIEWER_EXCEPT_HOST = "b689b0a8-53d0-40ab-baf2-68738e2966ac"


def ensure_video_cache_policy(cf) -> str:
    """Cache policy for /videos/*.

    Signed CloudFront-* cookies must reach the edge validator for
    /videos/*. Custom cache policies are a paid-plan feature, so we use
    managed CachingOptimized + Managed-AllViewerExceptHostHeader (cookies
    forwarded, not part of the cache key).
    """
    return MANAGED_CACHING_OPTIMIZED


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
    video_cache_policy = ensure_video_cache_policy(cf)

    origins = config["Origins"].get("Items", [])

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

    # Normalize fields the UpdateDistribution API requires on every origin
    # (creates tolerate defaults; updates demand them explicitly). Must run
    # after any new origin is appended above.
    for origin in origins:
        origin.setdefault("OriginPath", "")
        origin.setdefault("CustomHeaders", {"Quantity": 0, "Items": []})
        origin.setdefault("ConnectionAttempts", 3)
        origin.setdefault("ConnectionTimeout", 10)
        origin.setdefault("OriginShield", {"Enabled": False})
        if "S3OriginConfig" in origin:
            origin["S3OriginConfig"].setdefault("OriginAccessIdentity", "")
            origin["S3OriginConfig"].pop("OriginReadTimeout", None)  # S3 origins reject it
        if "CustomOriginConfig" in origin:
            origin["CustomOriginConfig"].setdefault("OriginReadTimeout", 30)
            origin["CustomOriginConfig"].setdefault("OriginKeepaliveTimeout", 5)

    # 2 + 3. /api/* and /videos/* behaviors ----------------------------------
    # Clone the existing default behavior as a complete template: UpdateDistribution
    # rejects sparse behaviors (SmoothStreaming, Compress, field-level TTLs, ...),
    # so every new behavior must carry the full field set.
    default = config["DefaultCacheBehavior"]

    def find_key_groups() -> dict:
        """Locate the signing key group: first in existing behaviors, then in
        the account. Refuses to continue without one - /videos/* must never
        deploy without its privacy wall."""
        candidates = list(config["CacheBehaviors"].get("Items", [])) + [default]
        for b in candidates:
            kg = b.get("TrustedKeyGroups") or {}
            if kg.get("Enabled", True) and kg.get("Quantity", 0) > 0:
                return {"Enabled": True, "Quantity": kg["Quantity"],
                        "Items": kg["Items"]}
        groups = cf.list_key_groups().get("KeyGroupList", {}).get("Items", [])
        if len(groups) == 1:
            gid = groups[0]["KeyGroup"]["Id"]
            print(f"Key group from account: {gid}")
            return {"Enabled": True, "Quantity": 1, "Items": [gid]}
        raise SystemExit(
            "No trusted key groups found - refusing to update, because "
            "/videos/* would be deployed without its privacy wall."
        )

    key_groups = find_key_groups()
    # Omitting TrustedKeyGroups on update makes CloudFront KEEP the old value;
    # it must be explicitly disabled to make the default behavior public.
    default["TrustedKeyGroups"] = {"Enabled": False, "Quantity": 0, "Items": []}

    def behavior(path: str, target: str, cache_policy: str,
                 with_key_groups: bool, allowed: list | None = None,
                 origin_request_policy: str | None = None) -> dict:
        b = json.loads(json.dumps({
            k: v for k, v in default.items()
            if k not in ("TargetOriginId", "PathPattern")
        }))
        b["PathPattern"] = path
        b["TargetOriginId"] = target
        b["ViewerProtocolPolicy"] = "https-only"
        b["CachePolicyId"] = cache_policy
        # Cache policies own the TTLs; legacy per-behavior TTL fields are rejected.
        for ttl in ("MinTTL", "DefaultTTL", "MaxTTL"):
            b.pop(ttl, None)
        b.pop("TrustedKeyGroups", None)
        if with_key_groups and key_groups:
            b["TrustedKeyGroups"] = key_groups
        if origin_request_policy:
            b["OriginRequestPolicyId"] = origin_request_policy
        if allowed:
            # CloudFront only accepts fixed method combinations; POST requires
            # the full set. CachedMethods nests inside AllowedMethods.
            b["AllowedMethods"] = {
                "Quantity": len(allowed),
                "Items": allowed,
                "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
            }
        return b

    wanted = {
        "/api/*": behavior("/api/*", api_origin_id, MANAGED_CACHING_DISABLED,
                           False,
                           allowed=["GET", "HEAD", "OPTIONS", "PUT", "PATCH",
                                    "POST", "DELETE"],
                           origin_request_policy=MANAGED_ALL_VIEWER_EXCEPT_HOST),
        "/videos/*": behavior("/videos/*", s3_origin_id, video_cache_policy, True,
                              origin_request_policy=MANAGED_ALL_VIEWER_EXCEPT_HOST),
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

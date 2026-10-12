"""Create a signed CloudFront URL that plays one video end-to-end.

Because HLS players fetch the playlist *and* every segment separately, a
single-object signed URL is not enough - each segment request would be
rejected. This script signs one custom policy covering the whole video
folder (playlist, segments, thumbnail) with a wildcard resource.

Usage:
    python sign-url.py list                     # show slugs in the catalog
    python sign-url.py <slug>                   # print a signed playback URL
    python sign-url.py <slug> --hours 1         # shorter validity

The output URL works in players that understand HLS: VLC (recommended for
testing), Safari, or any hls.js-based page. Paste it into
Media > Open Network Stream in VLC to verify playback.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.common import load_config, setup_logging


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("slug", nargs="?", help="Video slug from the catalog, or 'list'")
    parser.add_argument("--domain", help="CloudFront domain, e.g. d1234abcd.cloudfront.net")
    parser.add_argument("--key-id", dest="key_id", help="CloudFront key pair ID")
    parser.add_argument("--private-key", help="Path to the private key .pem file")
    parser.add_argument("--hours", type=float, default=4.0, help="URL validity window (default 4h)")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    config = load_config(Path(args.config))
    catalog = _load_catalog(config)

    if not args.slug or args.slug == "list":
        _print_slugs(catalog)
        return

    entry = next((e for e in catalog.values() if e.get("slug") == args.slug), None)
    if entry is None:
        _print_slugs(catalog)
        raise SystemExit(f"Slug not found: {args.slug}")

    cf = config.get("cloudfront", {})
    domain = args.domain or cf.get("domain")
    key_id = args.key_id or cf.get("key_pair_id")
    key_path = Path(args.private_key or cf.get("private_key_path", "cloudfront-private-key.pem"))

    missing = [name for name, value in (("domain", domain), ("key-id", key_id)) if not value]
    if missing:
        print(f"Missing CloudFront setting(s): {', '.join(missing)}", file=sys.stderr)
        print('Add this block to config.json (see docs/aws-setup.md):', file=sys.stderr)
        print(json.dumps({"cloudfront": {"domain": "dXXXX.cloudfront.net",
                                         "key_pair_id": "KXXXXXXXXXXXXX",
                                         "private_key_path": "cloudfront-private-key.pem"}}, indent=2),
              file=sys.stderr)
        raise SystemExit(2)
    if not key_path.exists():
        raise SystemExit(f"Private key not found: {key_path} (created during AWS setup)")

    prefix = config["s3"].get("prefix", "videos").strip("/")
    resource = f"https://{domain}/{prefix}/{args.slug}/*"
    expires = datetime.now(timezone.utc) + timedelta(hours=args.hours)

    url = _signed_url(resource, key_id, key_path.read_text(encoding="utf-8"), expires)
    print(url)
    print(f"\nResource : {resource}")
    print(f"Valid    : {expires.isoformat(timespec='seconds')}")
    print("Test in  : VLC > Media > Open Network Stream (paste the URL above)")


def _signed_url(resource: str, key_id: str, private_key_pem: str, expires: datetime) -> str:
    """Sign a custom policy with a wildcard resource (needed for HLS folders)."""
    policy = json.dumps({
        "Statement": [{
            "Resource": resource,
            "Condition": {"DateLessThan": {"AWS:EpochTime": int(expires.timestamp())}},
        }]
    }, separators=(",", ":"))

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key = load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    signature = key.sign(policy.encode("utf-8"), padding.PKCS1v15(), hashes.SHA1())

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    return (
        f"{resource.rstrip('*')}index.m3u8"
        f"?Policy={b64url(policy.encode('utf-8'))}"
        f"&Signature={b64url(signature)}"
        f"&Key-Pair-Id={key_id}"
    )


def _load_catalog(config: dict) -> dict:
    from lib.common import load_json
    return load_json(Path(config["paths"]["work_dir"]) / "catalog.json", {})


def _print_slugs(catalog: dict) -> None:
    ready = [e["slug"] for e in catalog.values() if e.get("status") == "ready"]
    if not ready:
        print("No prepared videos yet - run prepare.py first.")
        return
    print("Available slugs:")
    for slug in ready:
        print(f"  {slug}")
    print("\nUsage: python scripts/sign-url.py <slug>")


if __name__ == "__main__":
    main()

"""Sync the local prepare-catalog (.data/catalog.json) into DynamoDB.

Usage:
    python backend/seed_catalog.py

Run this after each batch of prepare.py uploads - it copies slug, title,
folder (derived from the source subfolder), duration, and dimensions for
every ready entry into svg_videos, which the /api/videos route reads.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import boto3

REGION = "ap-south-1"
TABLE = "svg_videos"
CATALOG = Path(".data/catalog.json")


def main() -> None:
    if not CATALOG.exists():
        raise SystemExit("No .data/catalog.json - run prepare.py first.")
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))

    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)
    count = 0
    with table.batch_writer() as batch:
        for entry in catalog.values():
            if entry.get("status") != "ready" or not entry.get("uploaded"):
                continue
            media = entry.get("media", {})
            source = Path(entry.get("source_path", ""))
            folder = source.parent.name or "Videos"
            batch.put_item(Item={
                "slug": entry["slug"],
                "title": entry.get("title", entry["slug"]),
                "folder": folder,
                "duration_seconds": int(media.get("duration_seconds", 0)),
                "width": media.get("width") or 0,
                "height": media.get("height") or 0,
                "updated_at": int(time.time()),
            })
            count += 1
    print(f"Seeded {count} video(s) into {TABLE}.")


if __name__ == "__main__":
    sys.exit(main())

"""One command for the full local pipeline: prepare, then upload.

Usage:
    python pipeline.py [--config config.json] [--source DIR] [--limit N]
                       [--force] [--dry-run] [--verbose]

This is the command to run whenever you have a new batch of recordings:
it packages every new file into HLS and uploads the results to S3, then stops.
Nothing here needs to run in the background - streaming is served entirely by
S3 and CloudFront once the files are uploaded.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import prepare
import upload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    parser.add_argument("--source", help="Folder containing recordings (overrides config)")
    parser.add_argument("--limit", type=int, help="Process at most N files this run")
    parser.add_argument("--force", action="store_true", help="Re-prepare even if already done")
    parser.add_argument("--dry-run", action="store_true", help="List work without doing it")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    passthrough: list[str] = ["--config", args.config]
    if args.source:
        passthrough += ["--source", args.source]
    if args.limit:
        passthrough += ["--limit", str(args.limit)]
    if args.force:
        passthrough.append("--force")
    if args.dry_run:
        passthrough.append("--dry-run")
    if args.verbose:
        passthrough.append("--verbose")

    print("=== Step 1/2: prepare (remux to HLS + thumbnails) ===")
    prepare.main(passthrough)
    print()
    print("=== Step 2/2: upload to S3 ===")
    upload.main(["--config", args.config] + (["--dry-run"] if args.dry_run else []))


if __name__ == "__main__":
    main()

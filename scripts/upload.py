"""Upload prepared HLS folders from the catalog to S3.

Usage:
    python upload.py [--config config.json] [--slug SLUG] [--dry-run] [--verbose]

Reads the catalog written by prepare.py, uploads every ready-but-not-uploaded
HLS folder, and marks progress after each video so an interrupted run resumes
exactly where it stopped.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import catalog as cat
from lib.common import human_size, load_config, setup_logging
from lib.uploader import Uploader

log = logging.getLogger("upload")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    parser.add_argument("--slug", help="Upload only this video's slug")
    parser.add_argument("--dry-run", action="store_true", help="List uploads without doing them")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    setup_logging(args.verbose)
    config = load_config(Path(args.config))

    catalog_path = Path(config["paths"]["work_dir"]) / "catalog.json"
    catalog = cat.load(catalog_path)
    if not catalog:
        raise SystemExit("Catalog is empty - run prepare.py first.")

    entries = sorted(
        (key, entry) for key, entry in catalog.items()
        if entry.get("status") == "ready" and not entry.get("uploaded")
        and (args.slug is None or entry.get("slug") == args.slug)
    )
    if not entries:
        log.info("Nothing to upload - everything is already on S3.")
        return

    pending_bytes = sum(entry.get("hls_bytes", 0) for _, entry in entries)
    log.info(
        "%d video(s) to upload, %s of HLS data",
        len(entries), human_size(pending_bytes),
    )

    if args.dry_run:
        for _, entry in entries:
            log.info("would upload: %s (%s)", entry["slug"], human_size(entry.get("hls_bytes", 0)))
        return

    uploader = Uploader(config)
    uploader.check_bucket()

    uploaded = failed = 0
    for index, (key, entry) in enumerate(entries, 1):
        hls_dir = Path(entry["hls_dir"])
        try:
            log.info(
                "[%d/%d] uploading %s (%s)",
                index, len(entries), entry["slug"], human_size(entry.get("hls_bytes", 0)),
            )
            uploader.upload_video(entry["slug"], hls_dir)
            thumb = entry.get("thumbnail")
            if thumb:
                thumbnail_path = Path(config["paths"]["work_dir"]) / "thumbs" / thumb
                if thumbnail_path.exists():
                    uploader.upload_thumbnail(entry["slug"], thumbnail_path)
        except Exception as err:  # keep uploading the rest of the batch
            log.error("Upload failed for %s: %s", entry["slug"], err)
            failed += 1
            continue
        entry["uploaded"] = True
        entry["uploaded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cat.update(catalog, key, entry)
        cat.save(catalog_path, catalog)
        uploaded += 1

    log.info(
        "Upload finished: %d uploaded, %d failed - s3://%s/%s/",
        uploaded, failed, config["s3"]["bucket"], config["s3"].get("prefix", "videos"),
    )


if __name__ == "__main__":
    main()

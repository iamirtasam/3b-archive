"""Promote browser-staged uploads into playable archive videos.

Usage:
    python scripts/publish.py                # package + upload everything staged
    python scripts/publish.py --dry-run      # show what would happen
    python scripts/publish.py --keep-source  # keep the staged original afterwards

The web uploader (site #/upload) streams original recordings straight to
S3 under uploads/<username>/<filename>. This script closes the loop on
your PC:

    1. download each staged original (skipped if already packaged)
    2. remux to HLS (fMP4, lossless) exactly like the local pipeline
    3. upload the HLS package + thumbnail to videos/<slug>/
    4. delete the staged original (unless --keep-source)
    5. sync the catalog into DynamoDB so the site lists the video

Resumable at every step: the shared catalog fingerprints downloads and
HLS folders, so an interrupted publish simply continues where it stopped.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import boto3
from botocore.exceptions import ClientError

from lib import catalog as cat
from lib import hls
from lib.probe import probe, summarize
from lib.thumbs import make_thumbnail

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text())
BUCKET = CONFIG["s3"]["bucket"]
REGION = CONFIG["aws"]["region"]
WORK_DIR = ROOT / CONFIG["paths"]["work_dir"]

UPLOAD_PREFIX = "uploads/"
S3_UPLOADER_PREFIX = "videos/"

log = logging.getLogger("publish")


def staged_uploads(s3) -> list[dict]:
    """Everything currently sitting in uploads/ (finished multipart sessions)."""
    out = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=UPLOAD_PREFIX):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith("/"):
                continue
            name = obj["Key"].split("/")[-1]
            out.append({
                "key": obj["Key"],
                "filename": name,
                "size": obj["Size"],
                "local": WORK_DIR / "staged" / name,
            })
    return out


def download(s3, key: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    s3.download_file(BUCKET, key, str(tmp))
    tmp.replace(dest)
    log.info("downloaded %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)


def publish_one(s3, item: dict, *, keep_source: bool, dry_run: bool) -> bool:
    """Returns True when the item is fully published (or was skipped)."""
    dest: Path = item["local"]
    key = f"staged:{item['filename']}"
    catalog_path = WORK_DIR / "catalog.json"
    catalog = cat.load(catalog_path)

    entry = cat.get(catalog, key)
    if entry:
        hls_dir = WORK_DIR / "hls" / entry["slug"]
        thumb_path = WORK_DIR / "thumbs" / f"{entry['slug']}.jpg"
    else:
        # Same slug the packaging step below will compute.
        base_slug = "".join(
            c if c.isalnum() or c in "-_" else "-"
            for c in item["filename"].rsplit(".", 1)[0].lower()
        ).strip("-") or "staged-upload"
        slug = f"{base_slug}-{item['key'].split('/')[-1][-4:]}"
        hls_dir = WORK_DIR / "hls" / slug
        thumb_path = WORK_DIR / "thumbs" / f"{slug}.jpg"

    ready = (hls_dir / hls.PLAYLIST_NAME).exists() and thumb_path.exists()
    if not ready:
        if dry_run:
            log.info("[dry-run] would package %s", item["filename"])
            return False
        if not dest.exists():
            download(s3, item["key"], dest)
        elif dest.stat().st_size != item["size"]:
            log.warning("%s size mismatch on disk - re-downloading", dest.name)
            download(s3, item["key"], dest)

        summary = summarize(probe(dest, "ffprobe"))
        if summary["duration_seconds"] <= 0:
            raise ValueError(f"{item['filename']} has no readable video/audio")

        # Stable slug from the staged filename (colons from the pseudo key
        # would break make_slug's path handling on Windows).
        base_slug = "".join(
            c if c.isalnum() or c in "-_" else "-"
            for c in item["filename"].rsplit(".", 1)[0].lower()
        ).strip("-") or "staged-upload"
        out_dir = WORK_DIR / "hls" / f"{base_slug}-{item['key'].split('/')[-1][-4:]}"
        hls_dir = out_dir
        playlist, reencoded = hls.prepare(
            dest, out_dir, summary=summary,
            segment_seconds=int(CONFIG["ffmpeg"].get("hls_segment_seconds", 6)),
            ffmpeg=CONFIG["ffmpeg"].get("binary", "ffmpeg"),
        )
        thumb = make_thumbnail(
            dest, thumb_path,
            seek_seconds=float(CONFIG.get("thumbnail", {}).get("seek_seconds", 5)),
            width=int(CONFIG.get("thumbnail", {}).get("width", 640)),
            ffmpeg=CONFIG["ffmpeg"].get("binary", "ffmpeg"),
        )
        entry = {
            "slug": out_dir.name,
            "title": dest.stem,
            "source_path": item["key"],
            "status": "ready",
            "uploaded": True,
            "duration_seconds": int(summary["duration_seconds"]),
            "width": summary.get("width"),
            "height": summary.get("height"),
            "reencoded": reencoded,
            "hls_segment_count": sum(
                1 for p in out_dir.iterdir()
                if p.suffix in (".ts", ".m4s")
            ),
            "thumbnail": "thumbnail.jpg" if thumb else None,
        }
        cat.update(catalog, key, entry)
        cat.save(catalog_path, catalog)
        log.info(
            "packaged %s -> %s (%d segments%s)",
            item["filename"], out_dir.name, entry["hls_segment_count"],
            ", re-encoded" if reencoded else "",
        )

    if dry_run:
        log.info("[dry-run] would upload %s", hls_dir.name)
        return False

    from lib.uploader import Uploader
    uploader = Uploader(CONFIG)
    uploader.check_bucket()
    uploader.upload_video(hls_dir.name, hls_dir)
    if thumb_path.exists():
        uploader.upload_thumbnail(hls_dir.name, thumb_path)
    log.info("uploaded %s", hls_dir.name)

    if not keep_source:
        s3.delete_object(Bucket=BUCKET, Key=item["key"])
        if dest.exists():
            dest.unlink()
        log.info("removed staged original %s", item["filename"])
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-source", action="store_true",
                        help="keep the staged original in S3 after publishing")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )

    session = boto3.session.Session(region_name=REGION)
    s3 = session.client("s3")
    items = staged_uploads(s3)
    if not items:
        log.info("Nothing staged in s3://%s/%s - upload via the website first.",
                 BUCKET, UPLOAD_PREFIX)
        return
    log.info("%d staged upload(s) found", len(items))

    done = 0
    for item in items:
        try:
            if publish_one(s3, item, keep_source=args.keep_source,
                           dry_run=args.dry_run):
                done += 1
        except Exception as err:  # noqa: BLE001 - keep publishing the rest
            log.error("%s failed: %s", item["filename"], err)

    if done and not args.dry_run:
        log.info("Syncing catalog to DynamoDB...")
        import subprocess
        subprocess.run(
            [sys.executable, str(ROOT / "backend" / "seed_catalog.py")],
            check=False,
        )
    log.info("Publish finished: %d/%d published", done, len(items))


if __name__ == "__main__":
    main()

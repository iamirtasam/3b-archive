"""Remux local recordings into HLS folders and build the catalog.

Usage:
    python prepare.py [--config config.json] [--source DIR]
                      [--limit N] [--force] [--dry-run] [--verbose]

Each source file is probed, packaged into HLS (remuxed when possible, so it is
fast and lossless), given a poster thumbnail, and recorded in the catalog.
Already-processed files are skipped, so the run is fully resumable.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import catalog as cat
from lib import hls
from lib.common import human_size, load_config, setup_logging
from lib.probe import probe, summarize
from lib.thumbs import make_thumbnail

log = logging.getLogger("prepare")


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
    setup_logging(args.verbose)
    config = load_config(Path(args.config))

    source_dir = Path(args.source or config["paths"]["source_dir"])
    if not source_dir.exists():
        raise SystemExit(f"Source folder does not exist: {source_dir}")
    work_dir = Path(config["paths"]["work_dir"])
    catalog_path = work_dir / "catalog.json"
    ffmpeg_cfg = config.get("ffmpeg", {})
    extensions = {e.lower() for e in config.get("video_extensions", [".mp4", ".mov", ".m4v"])}

    files = sorted(
        p for p in source_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in extensions
    )
    if args.limit:
        files = files[: args.limit]
    log.info("Found %d video file(s) under %s", len(files), source_dir)

    catalog = cat.load(catalog_path)
    keys_seen: set[str] = set()
    done = skipped = failed = 0

    for index, source in enumerate(files, 1):
        key = cat.fingerprint(source)
        keys_seen.add(key)

        existing = cat.get(catalog, key)
        if existing and existing.get("status") == "ready" and not args.force:
            log.info("[%d/%d] skip (already prepared): %s", index, len(files), source.name)
            skipped += 1
            continue
        if args.dry_run:
            log.info("[%d/%d] would prepare: %s (%s)", index, len(files), source.name,
                     human_size(source.stat().st_size))
            continue

        try:
            entry = _process_one(config, source, key, source_dir, work_dir)
        except Exception as err:  # keep going: one bad file must not kill a 500 GB run
            log.error("[%d/%d] FAILED: %s - %s", index, len(files), source.name, err)
            catalog[key] = {
                "slug": cat.make_slug(source, source_dir, key),
                "source_path": str(source),
                "status": "error",
                "error": str(err)[:300],
                "updated_at": _now(),
            }
            cat.save(catalog_path, catalog)
            failed += 1
            continue

        cat.update(catalog, key, entry)
        cat.save(catalog_path, catalog)
        done += 1
        log.info(
            "[%d/%d] prepared: %s (%s, %d segments%s)",
            index, len(files), source.name, human_size(source.stat().st_size),
            entry["hls_segment_count"],
            ", re-encoded" if entry["reencoded"] else "",
        )

    removed = cat.prune(catalog, keys_seen)
    if removed:
        log.info("Pruned %d stale catalog entrie(s) for deleted sources", removed)
        cat.save(catalog_path, catalog)

    log.info("Prepare finished: %d prepared, %d skipped, %d failed", done, skipped, failed)


def _process_one(
    config: dict[str, Any],
    source: Path,
    key: str,
    source_dir: Path,
    work_dir: Path,
) -> dict[str, Any]:
    """Probe, package, thumbnail, and describe a single video."""
    ffmpeg_cfg = config.get("ffmpeg", {})
    ffmpeg_bin = ffmpeg_cfg.get("binary", "ffmpeg")
    ffprobe_bin = ffmpeg_cfg.get("ffprobe", "ffprobe")
    segment_seconds = int(ffmpeg_cfg.get("hls_segment_seconds", 6))
    thumb_cfg = config.get("thumbnail", {})
    thumb_seek = float(thumb_cfg.get("seek_seconds", 5))
    thumb_width = int(thumb_cfg.get("width", 640))

    slug = cat.make_slug(source, source_dir, key)
    out_dir = work_dir / "hls" / slug

    summary = summarize(probe(source, ffprobe_bin))
    if summary["duration_seconds"] <= 0:
        raise ValueError("File has no readable video duration")

    playlist, reencoded = hls.prepare(
        source,
        out_dir,
        summary=summary,
        segment_seconds=segment_seconds,
        ffmpeg=ffmpeg_bin,
    )
    segment_count = sum(1 for p in out_dir.iterdir() if p.suffix == ".ts")

    thumbnail = make_thumbnail(
        source,
        work_dir / "thumbs" / f"{slug}.jpg",
        seek_seconds=thumb_seek,
        width=thumb_width,
        ffmpeg=ffmpeg_bin,
    )
    if thumbnail is None and thumb_seek > 0:
        thumbnail = make_thumbnail(
            source,
            work_dir / "thumbs" / f"{slug}.jpg",
            seek_seconds=0,
            width=thumb_width,
            ffmpeg=ffmpeg_bin,
        )

    return {
        "slug": slug,
        "title": source.stem,
        "source_path": str(source),
        "source_size": source.stat().st_size,
        "status": "ready",
        "reencoded": reencoded,
        "playlist": playlist.name,
        "hls_dir": str(out_dir),
        "hls_segment_count": segment_count,
        "hls_bytes": sum(p.stat().st_size for p in out_dir.iterdir() if p.is_file()),
        "thumbnail": thumbnail.name if thumbnail else None,
        "media": summary,
        "updated_at": _now(),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    main()

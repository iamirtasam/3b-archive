"""Prepare videos for HTTP streaming by packaging them into HLS.

Remuxing (`-c copy`) splits the existing H.264 stream into segments without
re-encoding, so it is fast and completely lossless - the video quality is
bit-for-bit identical to the source. A slower fallback transcode handles the
occasional file whose codec browsers cannot play directly.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from lib.probe import is_remux_safe

log = logging.getLogger(__name__)

PLAYLIST_NAME = "index.m3u8"


class RemuxError(RuntimeError):
    """Raised when a lossless remux is not possible for a source file."""


def prepare(
    source: Path,
    out_dir: Path,
    *,
    summary: dict[str, Any],
    segment_seconds: int,
    ffmpeg: str = "ffmpeg",
) -> tuple[Path, bool]:
    """Remux when possible, otherwise transcode.

    Returns a tuple of (playlist path, was re-encoded).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if is_remux_safe(summary):
        try:
            return remux_to_hls(
                source, out_dir, segment_seconds=segment_seconds, ffmpeg=ffmpeg
            ), False
        except RemuxError as err:
            log.warning("Remux failed for %s (%s); falling back to transcode", source.name, err)
            _clean_dir(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
    else:
        log.warning(
            "%s uses codec %s/%s which browsers may not play; re-encoding",
            source.name,
            summary.get("video_codec"),
            summary.get("audio_codec"),
        )
    return transcode_to_hls(
        source, out_dir, segment_seconds=segment_seconds, ffmpeg=ffmpeg
    ), True


def remux_to_hls(
    source: Path, out_dir: Path, *, segment_seconds: int, ffmpeg: str = "ffmpeg"
) -> Path:
    """Remux a video into HLS without re-encoding (lossless and fast)."""
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0?", "-sn",
        "-c", "copy",
        "-f", "hls",
        "-hls_time", str(segment_seconds),
        "-hls_list_size", "0",
        "-hls_playlist_type", "vod",
        "-hls_flags", "independent_segments",
        "-hls_segment_filename", str(out_dir / "seg_%05d.ts"),
        str(out_dir / PLAYLIST_NAME),
    ]
    _run(cmd, source)
    _require_playlist(out_dir, source)
    return out_dir / PLAYLIST_NAME


def transcode_to_hls(
    source: Path, out_dir: Path, *, segment_seconds: int, ffmpeg: str = "ffmpeg"
) -> Path:
    """Slow path: re-encode to browser-friendly H.264/AAC HLS."""
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0?", "-sn",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-f", "hls",
        "-hls_time", str(segment_seconds),
        "-hls_list_size", "0",
        "-hls_playlist_type", "vod",
        "-hls_flags", "independent_segments",
        "-hls_segment_filename", str(out_dir / "seg_%05d.ts"),
        str(out_dir / PLAYLIST_NAME),
    ]
    _run(cmd, source)
    _require_playlist(out_dir, source)
    return out_dir / PLAYLIST_NAME


def _run(cmd: list[str], source: Path) -> None:
    log.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RemuxError(f"ffmpeg failed for {source.name}: {result.stderr.strip()[:500]}")


def _require_playlist(out_dir: Path, source: Path) -> None:
    if not (out_dir / PLAYLIST_NAME).exists():
        raise RemuxError(f"ffmpeg produced no playlist for {source.name}")


def _clean_dir(out_dir: Path) -> None:
    """Remove partial outputs from a failed attempt so the retry starts fresh."""
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)

"""Generate one poster thumbnail per video with ffmpeg."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def make_thumbnail(
    source: Path,
    out_path: Path,
    *,
    seek_seconds: float,
    width: int,
    ffmpeg: str = "ffmpeg",
) -> Path | None:
    """Grab a single frame as a JPEG poster, or return None when it fails."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if _grab(source, out_path, seek_seconds=seek_seconds, width=width, ffmpeg=ffmpeg):
        return out_path
    # Seeking past the end produces no output - retry from the very start.
    log.debug("Retrying thumbnail at 0s for %s", source.name)
    if _grab(source, out_path, seek_seconds=0, width=width, ffmpeg=ffmpeg):
        return out_path
    log.warning("Could not generate a thumbnail for %s", source.name)
    return None


def _grab(
    source: Path,
    out_path: Path,
    *,
    seek_seconds: float,
    width: int,
    ffmpeg: str,
) -> bool:
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(seek_seconds),
        "-i", str(source),
        "-frames:v", "1",
        "-vf", f"scale={width}:-2",
        "-q:v", "3",
        str(out_path),
    ]
    log.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0

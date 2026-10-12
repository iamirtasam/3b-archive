"""Read media metadata with ffprobe."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def probe(path: Path, ffprobe: str = "ffprobe") -> dict[str, Any]:
    """Return ffprobe's JSON metadata for a media file."""
    cmd = [
        ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    log.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path.name}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def summarize(info: dict[str, Any]) -> dict[str, Any]:
    """Extract just the fields we care about into a small dictionary."""
    fmt = info.get("format", {})
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {
        "duration_seconds": round(float(fmt.get("duration") or 0.0), 2),
        "bitrate_bps": int(fmt.get("bit_rate") or 0),
        "container": fmt.get("format_name"),
        "video_codec": video.get("codec_name") if video else None,
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        "fps": _fps(video) if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
    }


def _fps(video: dict[str, Any]) -> float | None:
    """Convert ffprobe's rational fps string (e.g. '30000/1001') to a float."""
    raw = video.get("avg_frame_rate") or video.get("r_frame_rate")
    if not raw or raw == "0/0":
        return None
    try:
        num, den = raw.split("/")
        den_f = float(den)
        return round(float(num) / den_f, 3) if den_f else None
    except (ValueError, ZeroDivisionError):
        return None


def is_remux_safe(summary: dict[str, Any]) -> bool:
    """True when HLS remuxing needs no re-encoding (H.264 video, AAC or no audio)."""
    return summary.get("video_codec") == "h264" and summary.get("audio_codec") in {"aac", None}

"""Download a static ffmpeg binary for the Lambda packaging job.

Usage:
    python backend/fetch_ffmpeg.py

The auto-packaging Lambda shells out to ffmpeg for the lossless HLS remux.
A fully static build (johnvansickle.com) runs on Amazon Linux without any
system dependencies. The binary is ~78 MB, so it is downloaded on demand
and gitignored - never committed.

Checked against: https://johnvansickle.com/ffmpeg/ (GPL builds, public).
"""
from __future__ import annotations

import shutil
import tarfile
import tempfile
from pathlib import Path

import urllib.request

URL = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
DEST = Path(__file__).resolve().parent / "app" / "bin" / "ffmpeg"


def main() -> None:
    if DEST.exists() and DEST.stat().st_size > 40 * 1024 * 1024:
        print(f"ffmpeg already present: {DEST}")
        return

    print(f"Downloading {URL} ...")
    DEST.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "ffmpeg.tar.xz"
        urllib.request.urlretrieve(URL, archive)
        print(f"Extracting ffmpeg -> {DEST}")
        with tarfile.open(archive) as tf:
            member = next(m for m in tf.getmembers()
                          if m.isfile() and m.name.endswith("/ffmpeg"))
            src = tf.extractfile(member)
            with open(DEST, "wb") as out:
                shutil.copyfileobj(src, out)
    print(f"ffmpeg ready: {DEST.stat().st_size / 1e6:.0f} MB")


if __name__ == "__main__":
    main()

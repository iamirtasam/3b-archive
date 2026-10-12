"""A tiny JSON catalog that remembers which files were already processed.

The catalog maps a stable per-file fingerprint to metadata about the prepared
HLS output. It is what makes the whole pipeline resumable: interrupted runs
skip everything that is already done, and re-runs after edits only redo the
files whose size or modified time changed.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    """Read the catalog from disk, starting fresh when it does not exist."""
    if not path.exists():
        return {}
    import json

    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def save(path: Path, catalog: dict[str, Any]) -> None:
    """Persist the catalog to disk (called after every file so runs are resumable)."""
    from lib.common import dump_json

    dump_json(path, catalog)


def fingerprint(source: Path) -> str:
    """A stable identity for one version of a file: size + mtime, hashed."""
    stat = source.stat()
    raw = f"{stat.st_size}:{stat.st_mtime_ns}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def get(catalog: dict[str, Any], key: str) -> dict[str, Any] | None:
    return catalog.get(key)


def update(catalog: dict[str, Any], key: str, entry: dict[str, Any]) -> None:
    catalog[key] = entry


def prune(catalog: dict[str, Any], keys_seen: set[str]) -> int:
    """Drop entries whose source files no longer exist. Returns removed count."""
    stale = [key for key in catalog if key not in keys_seen]
    for key in stale:
        del catalog[key]
    return len(stale)


def make_slug(source: Path, source_dir: Path, key: str) -> str:
    """Build a URL-friendly unique id from the relative path plus fingerprint.

    Example: Recordings/Talks/Intro.mp4 -> talks-intro-9f2c41ab
    """
    rel = source.relative_to(source_dir)
    stem = re.sub(r"[^a-z0-9]+", "-", rel.stem.lower()).strip("-") or "video"
    parent = re.sub(r"[^a-z0-9]+", "-", rel.parent.as_posix().lower()).strip("-")
    prefix = f"{parent}-" if parent and parent != "." else ""
    return f"{prefix}{stem}-{key[:8]}"

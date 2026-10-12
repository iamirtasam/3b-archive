"""Shared helpers for the S3 video streaming toolkit.

Everything here is intentionally small and dependency-free (standard library
only) so the prepare/upload scripts stay easy to read and modify.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(message)s"


def load_config(path: Path) -> dict[str, Any]:
    """Load the user's config.json, with a friendly error if it is missing."""
    if not path.exists():
        print(f"Config file not found: {path}", file=sys.stderr)
        print("Copy config.example.json to config.json and edit it first.", file=sys.stderr)
        raise SystemExit(2)
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path: Path, data: Any) -> None:
    """Write pretty JSON to disk (UTF-8, readable diffs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_json(path: Path, default: Any) -> Any:
    """Read JSON from disk, returning `default` when the file does not exist."""
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def human_size(num: float) -> str:
    """Format a byte count as a human-readable string (e.g. 1.4 GB)."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:3.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def setup_logging(verbose: bool = False) -> None:
    """Configure root logging once for the CLI entry points."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=LOG_FORMAT,
        datefmt="%H:%M:%S",
    )

"""Shared test helpers."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path


def copy_layout(src: str | Path, dst: str | Path) -> Path:
    """Copy a layout file, retrying while another process rewrites it.

    A live Kilim instance (or a parallel worker) may be mid-write on
    `layouts/*.json`: a truncated read parses as invalid JSON and the window
    under test would fail with "EOF while parsing". Wait for a parseable
    snapshot instead.
    """
    src, dst = Path(src), Path(dst)
    for _ in range(40):
        try:
            json.loads(src.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.05)
            continue
        shutil.copy(src, dst)
        return dst
    raise AssertionError(f"{src} never settled (still being rewritten?)")

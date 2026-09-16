"""Small shared Qt-surface helpers (paths)."""

from __future__ import annotations




def _same_path(a: str, b: str) -> bool:
    """Case-insensitive path compare (Windows): open/history dedupe."""
    import os

    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _elide_path(path: str, limit: int = 64) -> str:
    """Middle-elide a long path for the status bar (tooltip keeps it whole)."""
    if len(path) <= limit:
        return path
    keep = limit // 2 - 2
    return f"{path[:keep]}\u2026{path[-keep:]}"

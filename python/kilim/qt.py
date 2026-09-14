"""Qt surface (setup B, unified): same layout.json as the TUI, rendered with Lace.

Unified: Qt talks ONLY to kilim._core (Rust stitch-pty crate). The PyPI
`stitch-pty` wheel is a dev reference (its terminal_emulator.py example),
never a runtime dep.

Requires: pip install -e .[qt]
"""

from __future__ import annotations

import asyncio
from pathlib import Path


def load_doc(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


class QtTerminalPane:
    """One Lace dock pane backed by a live core term.

    Poll loop: QTimer/asyncio task calls `term_range()` for the visible
    window (O(window), not O(scrollback)) + `term_cursor()`; keys go out
    via `write_term()`; resizes via `resize_term()`.
    """

    def __init__(self, core, pane_id: str):
        self.core = core
        self.pane_id = pane_id

    async def refresh(self, rows: int, cols: int):
        total = await self.core.term_total_lines(self.pane_id)
        start = max(0, total - rows)
        cells = await self.core.term_range(self.pane_id, start, rows)
        cursor = await self.core.term_cursor(self.pane_id)
        title = await self.core.term_title(self.pane_id)
        return {"cells": cells, "cursor": cursor, "title": title, "start": start}

    async def key(self, data: bytes):
        await self.core.write_term(self.pane_id, data)

    async def resize(self, rows: int, cols: int):
        await self.core.resize_term(self.pane_id, rows, cols)

    async def close(self, grace: float = 2.0):
        await self.core.terminate_term(self.pane_id, grace)


async def demo_headless(layout_path: str = "layouts/default.json"):
    """No Qt needed: proves the unified path works end to end."""
    import json

    from kilim import CoreSession

    core = CoreSession(load_doc(layout_path))
    print("panes:", core.pane_ids())
    await core.ensure_terms()
    for pid in core.pane_ids():
        try:
            total = await core.term_total_lines(pid)
            print(f"{pid}: {total} lines, alive={core.term_alive(pid)}")
        except ValueError:
            rows = core.highlighted_file(pid)
            print(f"{pid}: file, {len(rows)} rows")
    # Qt hosting sketch (needs PySide6 + Lace):
    # from lace import DockManager, DockWidget
    # from PySide6.QtWidgets import QApplication
    # ... each term pane gets QtTerminalPane(core, pid) + QTimer polling refresh()
    # ... each file pane gets core.highlighted_file(pid) painted once


if __name__ == "__main__":
    asyncio.run(demo_headless())

"""Colored blocks (pi-style) keep their background across the whole block.

Windows' ConPTY re-renders child output: trailing whitespace is trimmed and
handed back as `ESC[K` / `ESC[20X` with the block's SGR still active. That
only works if the emulator implements background-color-erase, so this pins
stitch-pty >= 0.8.1 (BCE) through the real ConPTY.
"""

import asyncio
import json
import sys

import pytest

from kilim import CoreSession

DOC = json.dumps(
    {
        "layout": {"root": {"type": "pane", "pane_id": "dummy"}, "active": "dummy"},
        "panes": [{"id": "dummy", "title": "dummy", "kind": "term"}],
    }
)

SCRIPT = (
    "import sys\n"
    "w = sys.stdout.write\n"
    "w('\\x1b[48;2;45;27;61m hello \\x1b[K\\x1b[0m\\r\\n')\n"
    "w('\\x1b[48;2;45;27;61m hello' + ' ' * 20 + '\\x1b[0m\\r\\n')\n"
    "w('\\x1b[38;2;1;2;3mplain default row\\r\\n')\n"
    "sys.stdout.flush()\n"
)


def test_colored_block_keeps_its_background():
    core = CoreSession(DOC)

    async def run():
        await core.spawn_term(
            "probe", "probe", sys.executable, ["-c", SCRIPT], 24, 60, 200
        )
        rows = None
        for _ in range(60):
            await asyncio.sleep(0.1)
            _total, _start, cells, _cursor, _modes, _dirty, _cwd = await core.snapshot_term(
                "probe", 24, None
            )
            if any("plain default row" in "".join(c[0] for c in row) for row in cells):
                rows = cells
                break
        await core.terminate_term("probe", 1.0)
        return rows

    rows = asyncio.run(run())
    assert rows is not None, "probe output never arrived"

    # Row 0: block text, then EL for the trimmed tail -> whole row colored.
    # Row 1: the same via ECH (ConPTY turns the run of spaces into ESC[20X),
    #        so exactly 6 + 20 cells carry the block background.
    assert all(cell[2] == "2d1b3d" for cell in rows[0][:40]), [
        cell[2] for cell in rows[0][:40]
    ]
    assert all(cell[2] == "2d1b3d" for cell in rows[1][:26]), [
        cell[2] for cell in rows[1][:26]
    ]
    assert rows[1][26][2] == "default", "block bled past its own cells"

    # A plain row (no background set) stays default: BCE tints nothing extra.
    plain = next(
        row
        for row in rows
        if "".join(cell[0] for cell in row).startswith("plain default row")
    )
    assert all(cell[2] == "default" for cell in plain), [c[2] for c in plain[:10]]

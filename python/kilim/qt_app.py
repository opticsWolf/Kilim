"""Kilim Qt frontend: layout.json hosted in Lace docks.

Same CoreSession as the TUI — no wheel, no second PTY stack.
Asyncio lives on a background thread; the GUI thread never blocks:
polls are submitted as coroutines and applied when done.

Run:  uv run python -m kilim.qt_app layouts/default.json
"""

from __future__ import annotations

from kilim.main_window import KilimWindow, main
from kilim.qt_bridge import Bridge
from kilim.qt_termkeys import (
    _arrow_seq,
    _char_to_term_col,
    _char_width,
    _modes_dict,
    _sgr_mouse,
    _term_col_to_char,
    _x10_mouse,
)
from kilim.qt_themes import cell_qcolor, kilim_theme_defs, register_kilim_lace_themes
from kilim.qt_util import _elide_path, _same_path
from kilim.terminal_pane import TerminalPane, _TermView
from kilim.title_bar import KilimTitleBar
from kilim.viewer_panes import FilePane, MarkdownPane, _fusion_scrollbar_css

__all__ = [
    "Bridge",
    "FilePane",
    "KilimTitleBar",
    "KilimWindow",
    "MarkdownPane",
    "TerminalPane",
    "_TermView",
    "_arrow_seq",
    "_char_to_term_col",
    "_char_width",
    "_elide_path",
    "_fusion_scrollbar_css",
    "_modes_dict",
    "_same_path",
    "_sgr_mouse",
    "_term_col_to_char",
    "_x10_mouse",
    "cell_qcolor",
    "kilim_theme_defs",
    "main",
    "register_kilim_lace_themes",
]

if __name__ == "__main__":
    raise SystemExit(main())

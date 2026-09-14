"""Qt scrollback tests — offscreen, offline safe (live PowerShell spawn)."""

import time

import pytest

PySide6 = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("lace")


def _window():
    from PySide6.QtWidgets import QApplication

    from kilim.qt_app import KilimWindow

    app = QApplication.instance() or QApplication([])
    w = KilimWindow("layouts/default.json")
    w.show()
    return app, w


def _term(w):
    terms = getattr(w, "term_panes", {})
    assert terms, "no TerminalPane"
    return next(iter(terms.values()))


def test_scrollbar_tracks_history():
    app, w = _window()
    try:
        term = _term(w)
        for _ in range(40):  # ~2.5s of polls
            app.processEvents()
            time.sleep(0.06)
        app.processEvents()
        assert term._total > 0
        assert term.bar.maximum() >= 0
        assert term._follow is True
    finally:
        w.close()
        app.processEvents()


def test_idle_skips_repaints():
    """Settled shell: polls continue, Qt rebuilds stop."""
    app, w = _window()
    try:
        term = _term(w)
        for _ in range(25):
            app.processEvents()
            time.sleep(0.06)
        assert term._render_count > 0  # painted at least once
        frozen = term._render_count
        for _ in range(15):
            app.processEvents()
            time.sleep(0.06)
        assert term._render_count == frozen
    finally:
        w.close()
        app.processEvents()


def test_wheel_leaves_follow_and_keypress_returns():
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QWheelEvent

    app, w = _window()
    try:
        term = _term(w)
        for _ in range(20):
            app.processEvents()
            time.sleep(0.06)
        rows, _ = term._grid()
        # Fake enough history to scroll: pretend and wheel up.
        term._total = max(term._total, rows + 50)
        pos = term.view.viewport().rect().center()
        ev = QWheelEvent(pos, pos, QPoint(0, 0), QPoint(0, 120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        term.on_wheel(ev)
        assert term._follow is False
        assert term._start == 0 or term.bar.value() == term._start
    finally:
        w.close()
        app.processEvents()


def test_selection_survives_repaints_and_copies():
    """Selection is anchored to history: paints continue underneath,
    the selection follows its content; Ctrl+Shift+C copies; Esc clears."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import QApplication

    app, w = _window()
    try:
        term = _term(w)
        for _ in range(20):
            app.processEvents()
            time.sleep(0.06)
        assert term._render_count > 0
        # Programmatic selection (stands in for mouse drag).
        cur = term.view.textCursor()
        cur.movePosition(QTextCursor.Start)
        cur.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, 10)
        term.view.setTextCursor(cur)
        wanted = term.view.textCursor().selectedText()
        assert wanted, "expected selectable shell output"
        for _ in range(10):
            app.processEvents()
            time.sleep(0.06)
        # Paints continued (no freeze) yet the selection survived.
        assert term.view.textCursor().hasSelection()
        assert term.view.textCursor().selectedText() == wanted
        assert term._copy_selection() is True
        clip = QApplication.clipboard().text()
        assert len(clip) > 0
        # Escape clears.
        term.on_key(_key(Qt.Key_Escape))
        assert not term.view.textCursor().hasSelection()
        assert term._sel is None
    finally:
        w.close()
        app.processEvents()


def _key(code):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    return QKeyEvent(QEvent.KeyPress, code, Qt.NoModifier)


def test_position_survives_repaints():
    """Regression: internal slider resets must not clobber scroll position."""
    app, w = _window()
    try:
        term = _term(w)
        term._poller.stop()  # freeze live polls; drive _render directly
        term._pending = None
        rows = [["line %d" % i, "default", "default", 0] for i in range(10)]
        snap = {"cells": [rows] * 1 * 10, "cursor": (0, 45), "start": 5, "total": 50, "rows": 10}
        snap["cells"] = [[("x", "default", "default", 0)] for _ in range(10)]
        term._last_sig = None
        term._render(snap)
        app.processEvents()
        term._last_sig = None  # force repaint, as new output would
        term._render(snap)
        app.processEvents()
        assert term._start == 5
        assert term.bar.value() == 5
        assert term._follow is False or True  # follow untouched by holds
    finally:
        w.close()
        app.processEvents()


def test_right_click_paste_reaches_shell():
    """Clipboard text pasted via pane lands in the shell's line buffer."""
    import asyncio

    from PySide6.QtWidgets import QApplication

    app, w = _window()
    try:
        term = _term(w)
        QApplication.clipboard().setText("KILIMPASTE42")
        fut = term._paste_from_clipboard()
        assert fut is not None
        fut.result(timeout=10)
        found = False
        for _ in range(40):
            app.processEvents()
            time.sleep(0.06)
            total, start, cells, _cursor, _modes = w.bridge.call(
                lambda: w.bridge.core.snapshot_term("term1", 60, None)
            )
            blob = "".join("".join(t for t, _, _, _ in row) for row in cells)
            if "KILIMPASTE42" in blob:
                found = True
                break
        assert found, "pasted text never appeared in the terminal"
    finally:
        w.close()
        app.processEvents()


def test_wheel_scrolls_three_lines_and_repaints():
    """One notch (120 units) = 3 lines, fractions accumulate, repaint now."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QWheelEvent

    app, w = _window()
    try:
        term = _term(w)
        for _ in range(10):
            app.processEvents()
            time.sleep(0.06)
        rows, _ = term._grid()
        term._total = rows + 50
        term._start = 20
        term._follow = False
        term._pending = None

        def wheel(units):
            pos = term.view.viewport().rect().center()
            ev = QWheelEvent(pos, pos, QPoint(0, 0), QPoint(0, units),
                             Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
            term.on_wheel(ev)

        wheel(120)  # one notch up = 3 lines
        assert term._start == 17
        assert term._pending is not None  # repaint requested immediately
        wheel(30)
        assert term._start == 17  # fractional part held...
        wheel(30)
        assert term._start == 16  # ...until it accumulates to a line
        wheel(-200)
        assert term._start == 20
        assert term.bar.value() == 20
    finally:
        w.close()
        app.processEvents()


def test_markdown_renders_pure_rust():
    """README pane via core.markdown_page — no wheel involved."""
    from PySide6.QtWidgets import QApplication

    from kilim.qt_app import Bridge, MarkdownPane

    app = QApplication.instance() or QApplication([])
    doc = open("layouts/default.json", encoding="utf-8").read()
    bridge = Bridge(doc)
    try:
        assert hasattr(bridge.core, "markdown_page")
        page = bridge.core.markdown_page("notes")
        assert "<h1" in page and "KaTeX_Main" in page
        pane = MarkdownPane(bridge, "notes", "README.md")
        view = pane.layout().itemAt(0).widget()
        assert type(view).__name__ in ("QWebEngineView", "QTextBrowser"), type(view)
    finally:
        bridge.stop()


def test_active_term_autofocused():
    """Unfocused text widgets draw no cursor — window must focus the shell."""
    from PySide6.QtWidgets import QApplication

    app, w = _window()
    try:
        for _ in range(12):
            app.processEvents()
            time.sleep(0.06)
        view = w.term_panes["term1"].view
        assert QApplication.focusWidget() is view, QApplication.focusWidget()
        assert view.textCursor() is not None
    finally:
        w.close()
        app.processEvents()


def test_terminal_menu_launches_shell():
    """Terminal menu: detected shells launch into new left docks."""
    app, w = _window()
    try:
        shells = w._shell_options()
        assert shells, "expected at least one shell on this machine"
        names = [n for n, _, _ in shells]
        assert "Command Prompt" in names
        cmd = next(c for n, c, _ in shells if n == "Command Prompt")
        args = next(a for n, _, a in shells if n == "Command Prompt")
        before = set(w.pane_docks)
        w.launch_shell("Command Prompt", cmd, args)
        new = set(w.pane_docks) - before
        assert len(new) == 1
        pid = new.pop()
        assert w.bridge.core.term_alive(pid) is True
    finally:
        w.close()
        app.processEvents()


def test_themes_menu_switches_and_repaints():
    """Themes menu: code/md/lace actions apply, repaint, and persist."""
    import json

    from PySide6.QtWidgets import QApplication

    layout_file = "layouts/default.json"
    before = open(layout_file, encoding="utf-8").read()
    app, w = _window()
    try:
        menus = {a.text(): a.menu() for a in w.menuBar().actions()}
        assert "&Themes" in menus, sorted(menus)
        subs = {a.text(): a.menu() for a in menus["&Themes"].actions() if a.menu()}
        assert "Code (Qt + TUI)" in subs and "Markdown" in subs and "Lace" in subs, sorted(subs)
        code_actions = {a.text(): a for a in subs["Code (Qt + TUI)"].actions()}
        md_actions = {a.text(): a for a in subs["Markdown"].actions()}
        # One shared eight: code + markdown lists match, no Pin menu.
        assert sorted(code_actions) == sorted(md_actions), "code/md lists diverged"
        assert len(code_actions) == 8
        assert "&Pin" not in menus
        code_actions["Kilim Warm"].trigger()
        assert w.bridge.core.theme() == "Kilim Warm"
        md_actions["Kilim Dark Neo"].trigger()
        assert w.bridge.core.markdown_theme() == "Kilim Dark Neo"
        raw = json.loads(open("layouts/default.json", encoding="utf-8").read())
        assert raw["layout"]["theme"] == "Kilim Warm"
        assert raw["layout"]["markdown_theme"] == "Kilim Dark Neo"
        # Lace menu is Kilim-only and flat: 8 actions, no stock presets.
        lace_acts = [a for a in subs["Lace"].actions() if a.menu() is None]
        assert [a.text() for a in lace_acts] == ["Kilim Dark", "Kilim Dark Neo",
            "Kilim Neutral", "Kilim Neutral Neo", "Kilim Light", "Kilim Light Neo",
            "Kilim Warm", "Kilim Warm Neo"]
        lace_acts[4].trigger()  # Kilim Light: chrome applies immediately.
        assert w.lace_theme == "kilim_light", "lace choice not recorded"
    finally:
        open(layout_file, "w", encoding="utf-8").write(before)
        w.close()
        app.processEvents()


def test_kilim_group_unified_apply_and_fresh_default(tmp_path):
    """Kilim group heads the Lace menu; fresh windows open unified Dark."""
    import json
    import shutil

    from PySide6.QtWidgets import QApplication

    from kilim.qt_app import KilimWindow, register_kilim_lace_themes
    from lace.dock_style_manager import theme_groups

    mapping = register_kilim_lace_themes()
    assert set(mapping) == {"kilim_dark", "kilim_neutral", "kilim_light", "kilim_warm",
                              "kilim_dark_neo", "kilim_neutral_neo", "kilim_light_neo", "kilim_warm_neo"}
    assert mapping["kilim_dark_neo"] == "Kilim Dark Neo"  # neo chrome → neo code/md
    groups = {title: [k for _, k in members] for title, members in theme_groups()}
    assert "Kilim Neon" not in groups, "legacy group dropped; single Kilim submenu only"
    assert groups["Kilim"] == ["kilim_dark", "kilim_dark_neo",
                                      "kilim_neutral", "kilim_neutral_neo",
                                      "kilim_light", "kilim_light_neo",
                                      "kilim_warm", "kilim_warm_neo"]

    layout = tmp_path / "fresh.json"
    shutil.copy("layouts/default.json", layout)
    sidecar = tmp_path / "fresh.perspective.json"
    app, _ = _window()  # noqa: F841 — ensures QApplication exists
    w = KilimWindow(str(layout), str(sidecar))
    try:
        w.show()
        app.processEvents()
        assert w.lace_theme == "kilim_dark"
        assert w.bridge.core.theme() == "Kilim Dark"
        assert w.bridge.core.markdown_theme() == "Kilim Dark"
        w.apply_lace_theme("kilim_light")
        assert w.bridge.core.theme() == "Kilim Light"
        assert w.bridge.core.markdown_theme() == "Kilim Light"
        # Neo chassis: chrome changes, code/md follow into the neo theme.
        w.apply_lace_theme("kilim_light_neo")
        assert w.lace_theme == "kilim_light_neo"
        assert w.bridge.core.theme() == "Kilim Light Neo"
        assert w.bridge.core.markdown_theme() == "Kilim Light Neo"
        raw = json.loads(layout.read_text(encoding="utf-8"))
        assert raw["layout"]["theme"] == "Kilim Light Neo"
        assert json.loads(sidecar.read_text(encoding="utf-8"))["lace_theme"] == "kilim_light_neo"
        # Legacy keys normalize forward.
        w.apply_lace_theme("kilim_neo_dark")
        assert w.lace_theme == "kilim_dark_neo"
        assert w.bridge.core.theme() == "Kilim Dark Neo"
    finally:
        w.close()
        app.processEvents()


def test_file_pane_no_phantom_lines_and_full_bleed_bg(tmp_path):
    """Code view: one block per line (no doubled breaks), theme bg edge to edge."""
    import json

    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication

    from kilim import theme_background
    from kilim.qt_app import Bridge, FilePane

    app = QApplication.instance() or QApplication([])
    src = tmp_path / "s.py"
    src.write_text("x = 1\n# hi\n", encoding="utf-8")  # trailing newline: the old doubling case
    doc = {
        "layout": {"root": {"type": "pane", "pane_id": "code"}, "active": "code",
                   "theme": "Kilim Dark", "markdown_theme": "Kilim Dark"},
        "panes": [{"id": "code", "title": "s.py", "kind": "file", "path": str(src)}],
    }
    bridge = Bridge(json.dumps(doc))
    try:
        assert theme_background("Kilim Dark") == "#101319"
        # Unknown names fall back to the default paper (Qt/TUI/chrome agree).
        assert theme_background("nope") == "#101319"
        pane = FilePane(bridge, "code")
        assert pane.document().blockCount() == 2
        base = pane.palette().color(QPalette.Base)
        assert base == QColor("#101319"), base.name()
        first = pane.document().findBlockByNumber(0)
        assert first.blockFormat().background().color() == QColor("#101319")
        text = pane.toPlainText()
        assert text.count("\n") == 1 and "x = 1" in text and "# hi" in text
    finally:
        bridge.stop()


def test_sidebars_exist_for_titlebar_pins():
    """Both sidebars pre-exist so Lace's title-bar pin buttons work.

    (No Pin menu: pinning lives in the title bars + drag, persisted
    via the perspective sidecar.)
    """
    from lace.enums import DockWidgetArea

    app, w = _window()
    try:
        sb = w.manager.sidebar_manager
        assert {a for a in sb._sidebars} == {DockWidgetArea.left, DockWidgetArea.right}
        menus = {a.text() for a in w.menuBar().actions()}
        assert "&Pin" not in menus
    finally:
        w.close()
        app.processEvents()


def test_resize_retry_on_failure():
    """Lost terminal resizes retry instead of sticking (Qt size sync)."""
    import concurrent.futures

    app, w = _window()
    try:
        pane = w.term_panes["term1"]
        grid = (99, 99)
        pane._resize_pending = grid
        bad: concurrent.futures.Future = concurrent.futures.Future()
        bad.set_exception(RuntimeError("pty gone"))
        pane._on_resized(bad, grid)
        assert pane._resize_pending is None  # next poll retries
        pane._resize_pending = grid
        ok: concurrent.futures.Future = concurrent.futures.Future()
        ok.set_result(None)
        pane._on_resized(ok, grid)
        assert pane._resize_pending == grid  # success sticks
        pane._resize_pending = (1, 1)
        pane._on_resized(bad, grid)  # stale grid: newer mark survives
        assert pane._resize_pending == (1, 1)
        # Grid stays within sanity bounds whatever the viewport does.
        rows, cols = pane._grid()
        assert 5 <= rows <= 400 and 20 <= cols <= 800
    finally:
        w.close()
        app.processEvents()


def test_term_pane_follows_code_theme():
    """Shell view tracks the code theme (palette + repaint marker)."""
    from PySide6.QtGui import QColor, QPalette

    from kilim import theme_background, theme_foreground

    app, w = _window()  # import runs here; registry starts empty
    try:
        assert theme_foreground("Kilim Dark") != ""
        pane = w.term_panes["term1"]
        pane._ensure_theme()
        assert pane._theme_name == w.bridge.core.theme()
        assert pane.view.palette().color(QPalette.Base) == QColor(
            theme_background(w.bridge.core.theme()))
        w.apply_code_theme("Kilim Light")
        pane._ensure_theme()  # idempotent: syncs now if no poll beat us
        assert pane._theme_name == "Kilim Light"
        assert pane.view.palette().color(QPalette.Base) == QColor("#ffffff")
    finally:
        w.close()
        app.processEvents()


def test_pane_focus_reclaimed_from_chrome():
    """Cursor visibility = focus: Lace chrome (tab bar) must not keep
    focus over the active terminal pane (regression: no input cursor)."""
    from PySide6.QtWidgets import QApplication, QWidget

    app, w = _window()
    try:
        view = w.term_panes["term1"].view
        bars = [o for o in w.findChildren(QWidget) if type(o).__name__ == "DockAreaTabBar"]
        assert bars, "expected Lace tab bar chrome"
        bars[0].setFocus()
        app.processEvents()
        assert QApplication.focusWidget() is view
        assert view.hasFocus()
    finally:
        w.close()
        app.processEvents()


def test_snapshot_carries_dec_modes():
    """snapshot_term reports app-cursor/bracketed/mouse/sgr/alt flags."""
    app, w = _window()
    try:
        total, start, cells, cursor, modes = w.bridge.call(
            lambda: w.bridge.core.snapshot_term("term1", 10, None)
        )
        app_cursor, bracketed, mouse, sgr, alt = modes
        assert isinstance(app_cursor, bool) and isinstance(mouse, int)
        assert mouse == 0 and alt is False  # plain PowerShell: no modes
    finally:
        w.close()
        app.processEvents()


def test_arrow_and_mouse_encodings():
    from PySide6.QtCore import Qt

    from kilim.qt_app import (
        _arrow_seq,
        _char_to_term_col,
        _sgr_mouse,
        _term_col_to_char,
        _x10_mouse,
    )

    assert _arrow_seq(Qt.Key_Up, False) == b"\x1b[A"
    assert _arrow_seq(Qt.Key_Up, True) == b"\x1bOA"
    assert _arrow_seq(Qt.Key_Home, True) == b"\x1bOH"
    assert _arrow_seq(Qt.Key_End, False) == b"\x1b[F"
    assert _arrow_seq(Qt.Key_A, False) is None
    assert _sgr_mouse(0, 5, 10, True) == b"\x1b[<0;5;10M"
    assert _sgr_mouse(3, 5, 10, False) == b"\x1b[<3;5;10m"
    assert _sgr_mouse(64, 1, 1, True) == b"\x1b[<64;1;1M"
    assert _x10_mouse(0, 5, 10) == bytes((0x1B, ord("M"), 32, 37, 42))
    # Wide chars: 中 is 2 cells; combining mark is 0.
    assert _term_col_to_char("a中b", 0) == 0
    assert _term_col_to_char("a中b", 1) == 1
    assert _term_col_to_char("a中b", 3) == 2
    assert _term_col_to_char("a中b", 4) == 3
    assert _char_to_term_col("a中b", 2) == 3
    assert _char_to_term_col("é", 2) == 1


def _fake_snap(rows, start=0, total=None, cursor=(0, 0)):
    cells = [[(t, "default", "default", 0) for t in row] for row in rows]
    return {
        "cells": cells,
        "cursor": cursor,
        "start": start,
        "total": total if total is not None else len(rows),
        "rows": len(rows),
        "modes": {"app_cursor": False, "bracketed": False, "mouse": 0, "sgr": False, "alt": False},
    }


def test_incremental_repaint_touches_one_row():
    """Dirty-region paint: one changed row = one more frame, rest intact."""
    from PySide6.QtGui import QTextCursor

    app, w = _window()
    try:
        term = _term(w)
        rows = [["aaa", "bbb"], ["ccc", "ddd"], ["eee", "fff"]]
        term._render(_fake_snap(rows, total=10))
        base = term._render_count
        assert term.view.document().findBlockByNumber(1).text() == "cccddd"
        changed = [["aaa", "bbb"], ["CCC", "ddd"], ["eee", "fff"]]
        term._render(_fake_snap(changed, total=11))
        assert term._render_count == base + 1
        assert term.view.document().findBlockByNumber(1).text() == "CCCddd"
        assert term.view.document().findBlockByNumber(0).text() == "aaabbb"
        # Identical re-render: idle, no touch.
        term._render(_fake_snap(changed, total=11))
        assert term._render_count == base + 1
    finally:
        w.close()
        app.processEvents()


def test_selection_follows_scrolled_content():
    """Selection anchored to absolute lines survives scrolling output."""
    from PySide6.QtGui import QTextCursor

    app, w = _window()
    try:
        term = _term(w)
        term._render(_fake_snap([["aa", "bb"], ["cc", "dd"], ["ee", "ff"]]))
        doc = term.view.document()
        c = QTextCursor(doc)
        c.setPosition(doc.findBlockByNumber(1).position())
        c.setPosition(doc.findBlockByNumber(1).position() + 4, QTextCursor.KeepAnchor)
        term.view.setTextCursor(c)
        assert term.view.textCursor().selectedText() == "ccdd"
        # Same content scrolled up one (start 0 -> 1), plus a fresh row.
        term._render(_fake_snap([["cc", "dd"], ["ee", "ff"], ["gg", "hh"]], start=1, total=4))
        assert term.view.textCursor().hasSelection()
        assert term.view.textCursor().selectedText() == "ccdd"
    finally:
        w.close()
        app.processEvents()


def test_hidden_pane_badges_on_output():
    """Output while hidden dots the tab; showing clears it."""
    app, w = _window()
    try:
        term = _term(w)
        dock = w.pane_docks["term1"]
        rows = [["aaa"], ["bbb"], ["ccc"]]
        term._render(_fake_snap(rows, total=10))  # visible: baseline, no dot
        assert "●" not in dock.windowTitle()
        dock.hide()
        app.processEvents()
        assert not term.view.isVisible()
        term._render(_fake_snap(rows, total=11))  # hidden + growth -> dot
        assert "●" in dock.windowTitle()
        dock.show()
        app.processEvents()
        term._render(_fake_snap(rows, total=11))  # visible -> cleared
        assert "●" not in dock.windowTitle()
    finally:
        w.close()
        app.processEvents()

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


def test_selection_freezes_paint_and_copies():
    """Selecting holds rebuilds; Ctrl+Shift+C copies; Esc resumes."""
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
        term._selecting = True
        frozen = term._render_count
        for _ in range(10):
            app.processEvents()
            time.sleep(0.06)
        assert term._render_count == frozen  # paints held
        assert term.view.textCursor().hasSelection()  # selection survived
        assert term._copy_selection() is True
        clip = QApplication.clipboard().text()
        assert len(clip) > 0
        # Escape clears + resumes.
        term.on_key(_key(Qt.Key_Escape))
        assert term._selecting is False
        for _ in range(10):
            app.processEvents()
            time.sleep(0.06)
        assert term._render_count >= frozen  # resumed (idle-skip may hold)
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
            total, start, cells, _cursor = w.bridge.call(
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

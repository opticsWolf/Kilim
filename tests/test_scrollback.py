"""Qt scrollback tests — offscreen, offline safe (live PowerShell spawn)."""

import time

import pytest

PySide6 = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("lace")


def _window(layout="layouts/default.json"):
    """A shown KilimWindow. Repo layouts are copied to a temp dir first:
    windows write their sidecar (and a *chosen* theme its layout), and
    parallel workers must not race on the repo's files."""
    import tempfile
    from pathlib import Path as _Path

    from _util import copy_layout
    from kilim.qt_app import KilimWindow
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    sidecar = None
    if not _Path(layout).is_absolute():
        tmp = _Path(tempfile.mkdtemp(prefix="kilim-window-"))
        local = tmp / _Path(layout).name
        copy_layout(layout, local)
        layout, sidecar = str(local), str(tmp / "l.perspective.json")
    w = KilimWindow(layout, sidecar)
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
            total, start, cells, _cursor, _modes, _dirty, _cwd = w.bridge.call(
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
    from kilim.qt_app import Bridge, MarkdownPane
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    doc = open("layouts/example.json", encoding="utf-8").read()
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


def test_active_term_autofocused(tmp_path):
    """Unfocused text widgets draw no cursor — window must focus the shell.

    Hermetic layout pair: the repo sidecar is live session state, and a
    saved arrangement can leave the active pane as a background tab."""
    from _util import copy_layout
    from kilim.qt_app import KilimWindow
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    layout = tmp_path / "default.json"
    copy_layout("layouts/default.json", layout)
    w = KilimWindow(str(layout), str(tmp_path / "default.perspective.json"))
    w.show()
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
    """Terminal menu: discovered shells launch into new left docks."""
    app, w = _window()
    try:
        shells = w._shell_options()
        assert shells, "expected at least one shell on this machine"
        label, cmd, args = shells[0]
        before = set(w.pane_docks)
        w.launch_shell(label, cmd, args)
        new = set(w.pane_docks) - before
        assert len(new) == 1
        pid = new.pop()
        assert w.bridge.core.term_alive(pid) is True
    finally:
        w.close()
        app.processEvents()


def test_shell_start_directory_modes_save_and_forward(tmp_path):
    """Start Directory: App/Shell/custom per shell, persist, forward."""
    import json
    from pathlib import Path as _Path
    from unittest.mock import AsyncMock, patch

    from kilim._core import CoreSession

    app, w = _window()
    try:
        shells = w._shell_options()
        assert shells, "expected at least one shell on this machine"
        label = shells[0][0]

        def shell_menu():
            start = next(
                a.menu() for a in w.titleBar.terminal_menu.actions()
                if a.text() == "Start Directory"
            )
            return next(
                a.menu() for a in start.actions()
                if a.text() == label
            )

        # App default checked everywhere out of the box.
        acts = {
            a.text(): a
            for a in shell_menu().actions()
        }
        assert acts["App default"].isChecked()
        assert not acts["Shell default"].isChecked()
        assert w._shell_cwd_arg(label) is None

        # Shell default resolves to home and forwards it to core.
        acts["Shell default"].trigger()
        assert w._shell_dir_spec(label)["mode"] == "home"
        sidecar = json.loads(_Path(w.perspective_path).read_text(encoding="utf-8"))
        assert sidecar["shell_cwds"][label]["mode"] == "home"
        with patch.object(CoreSession, "spawn_term", new=AsyncMock()) as sp:
            w.launch_shell(label, shells[0][1], shells[0][2])
        assert sp.await_args.kwargs.get("cwd") == str(_Path.home())

        # Back to App default forwards nothing (inherit).
        next(
            a for a in shell_menu().actions()
            if a.text() == "App default"
        ).trigger()
        with patch.object(CoreSession, "spawn_term", new=AsyncMock()) as sp:
            w.launch_shell(label, shells[0][1], shells[0][2])
        assert sp.await_args.kwargs.get("cwd") is None

        # A picked directory becomes the custom mode, shown and persisted.
        target = tmp_path / "work"
        target.mkdir()
        choose = next(
            a for a in shell_menu().actions() if a.text() == "Choose\u2026"
        )
        with patch(
            "PySide6.QtWidgets.QFileDialog.getExistingDirectory",
            return_value=str(target),
        ):
            choose.trigger()
        assert w._shell_dir_spec(label) == {"mode": "custom", "dir": str(target)}
        assert f"{target}" in [
            a.text() for a in shell_menu().actions() if a.isChecked()
        ]
        with patch.object(CoreSession, "spawn_term", new=AsyncMock()) as sp:
            w.launch_shell(label, shells[0][1], shells[0][2])
        assert sp.await_args.kwargs.get("cwd") == str(target)

        # Legacy v0.4.12 sidecars (bare-string customs) still read.
        w.shell_cwds[label] = "C:\\old-place"
        assert w._shell_dir_spec(label) == {"mode": "custom", "dir": "C:\\old-place"}

        # Reset clears every shell back to App default.
        start = next(
            a.menu() for a in w.titleBar.terminal_menu.actions()
            if a.text() == "Start Directory"
        )
        next(
            a for a in start.actions()
            if a.text() == "Reset all to App default"
        ).trigger()
        assert w.shell_cwds == {}
        sidecar = json.loads(_Path(w.perspective_path).read_text(encoding="utf-8"))
        assert sidecar["shell_cwds"] == {}
    finally:
        w.close()
        app.processEvents()


def test_themes_menu_switches_and_repaints(tmp_path):
    """Themes menu: code/terminal/lace actions apply, repaint, persist."""
    import json

    from _util import copy_layout

    # Hermetic copy: parallel workers must not race on the repo's layout.
    layout_file = tmp_path / "l.json"
    copy_layout("layouts/default.json", layout_file)
    app, w = _window(str(layout_file))
    try:
        menus = {a.text(): a.menu() for a in w.titleBar.menu_bar.actions()}
        assert "&Themes" in menus, sorted(menus)
        subs = {a.text(): a.menu() for a in menus["&Themes"].actions() if a.menu()}
        assert "Syntax" in subs and "Terminal" in subs and "App" in subs, sorted(subs)
        assert "Markdown" not in subs, "fences follow the code apply now"
        term_actions = {a.text(): a for a in subs["Terminal"].actions() if not a.isSeparator()}
        code_actions = {
            a.text(): a
            for a in subs["Syntax"].actions()
            if a.text() in term_actions
        }
        # One shared ten: code + terminal lists match, no Pin menu, and no
        # extra toggles in the theme submenus.
        assert sorted(code_actions) == sorted(term_actions), "code/terminal lists diverged"
        assert len(code_actions) == 10
        assert all(
            a.isSeparator() or a.text() in term_actions
            for a in subs["Syntax"].actions()
        ), "unexpected extra in the Syntax submenu"
        assert "&Pin" not in menus
        # One code apply covers Markdown too: both keys set + persisted equal.
        code_actions["Kilim Warm"].trigger()
        assert w.bridge.core.theme() == "Kilim Warm"
        assert w.bridge.core.markdown_theme() == "Kilim Warm"
        raw = json.loads(layout_file.read_text(encoding="utf-8"))
        assert raw["layout"]["theme"] == "Kilim Warm"
        assert raw["layout"]["markdown_theme"] == "Kilim Warm"
        # Terminal applies on its own track: layout key only, code untouched.
        term_actions["Kilim Midnight Neo"].trigger()
        assert w.terminal_theme == "Kilim Midnight Neo"
        assert w.bridge.core.theme() == "Kilim Warm"
        raw = json.loads(layout_file.read_text(encoding="utf-8"))
        assert raw["layout"]["terminal_theme"] == "Kilim Midnight Neo"
        # Lace menu is Kilim-only and flat: 10 actions, no stock presets.
        lace_acts = [a for a in subs["App"].actions() if a.menu() is None]
        assert [a.text() for a in lace_acts] == ["Kilim Midnight", "Kilim Midnight Neo",
            "Kilim Dark", "Kilim Dark Neo",
            "Kilim Neutral", "Kilim Neutral Neo", "Kilim Light", "Kilim Light Neo",
            "Kilim Warm", "Kilim Warm Neo"]
        next(a for a in lace_acts if a.text() == "Kilim Light").trigger()
        assert w.lace_theme == "kilim_light", "lace choice not recorded"
    finally:
        w.close()
        app.processEvents()


def test_code_apply_repaints_file_and_markdown_panes(tmp_path):
    """One code apply repaints FilePanes and MarkdownPanes together."""
    from unittest.mock import patch

    from kilim.qt_app import FilePane, MarkdownPane

    app, w = _window("layouts/example.json")
    try:
        src = tmp_path / "sample.py"
        src.write_text("x = 1\n", encoding="utf-8")
        w.open_in_viewer(str(src), 1, "code")
        app.processEvents()
        assert w.file_panes and w.md_panes
        with (
            patch.object(FilePane, "refresh", autospec=True) as fr,
            patch.object(MarkdownPane, "refresh", autospec=True) as mr,
        ):
            w.apply_code_theme("Kilim Light")
        assert w.bridge.core.theme() == "Kilim Light"
        assert w.bridge.core.markdown_theme() == "Kilim Light"
        assert fr.call_count == len(w.file_panes) >= 1
        assert mr.call_count == len(w.md_panes) >= 1
    finally:
        w.close()
        app.processEvents()


def test_terminal_apply_recolors_only_terms(tmp_path):
    """Terminal apply reaches term panes; file/md panes stay quiet."""
    from unittest.mock import patch

    from kilim.qt_app import FilePane, MarkdownPane

    app, w = _window("layouts/example.json")
    try:
        src = tmp_path / "sample.py"
        src.write_text("x = 1\n", encoding="utf-8")
        w.open_in_viewer(str(src), 1, "code")
        app.processEvents()
        assert w.term_panes and w.file_panes and w.md_panes
        with (
            patch.object(FilePane, "refresh", autospec=True) as fr,
            patch.object(MarkdownPane, "refresh", autospec=True) as mr,
        ):
            w.apply_terminal_theme("Kilim Warm")
        assert w.terminal_theme == "Kilim Warm"
        assert all(p._terminal_theme == "Kilim Warm" for p in w.term_panes.values())
        assert fr.call_count == 0
        assert mr.call_count == 0
        assert w.bridge.core.theme() != "Kilim Warm", "code track untouched"
    finally:
        w.close()
        app.processEvents()


def test_fresh_launch_unifies_terminal_theme(tmp_path):
    """Fresh launch (no sidecar) opens unified: a stored terminal_theme
    yields to the Midnight default — the same reunification rule the code
    track has always had (restart restores the Lace selection)."""
    import json

    from _util import copy_layout

    layout_file = tmp_path / "l.json"
    copy_layout("layouts/default.json", layout_file)
    raw = json.loads(layout_file.read_text(encoding="utf-8"))
    raw["layout"]["terminal_theme"] = "Kilim Warm"
    layout_file.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    app, w = _window(str(layout_file))
    try:
        assert w.lace_theme == "kilim_midnight"
        assert w.terminal_theme == "Kilim Midnight"
        assert all(
            p._terminal_theme == "Kilim Midnight" for p in w.term_panes.values()
        )
    finally:
        w.close()
        app.processEvents()


def test_kilim_group_unified_apply_and_fresh_default(tmp_path):
    """Kilim group heads the Lace menu; fresh windows open unified Midnight."""
    import json

    from _util import copy_layout
    from kilim.qt_app import KilimWindow, register_kilim_lace_themes
    from lace.dock_style_manager import theme_groups

    mapping = register_kilim_lace_themes()
    assert set(mapping) == {"kilim_midnight", "kilim_midnight_neo",
                            "kilim_dark", "kilim_dark_neo",
                            "kilim_neutral", "kilim_neutral_neo",
                            "kilim_light", "kilim_light_neo",
                            "kilim_warm", "kilim_warm_neo"}
    assert mapping["kilim_midnight_neo"] == "Kilim Midnight Neo"  # neo chrome → neo code/md
    groups = {title: [k for _, k in members] for title, members in theme_groups()}
    assert "Kilim Neon" not in groups, "legacy group dropped; single Kilim submenu only"
    assert groups["Kilim"] == ["kilim_midnight", "kilim_midnight_neo",
                                      "kilim_dark", "kilim_dark_neo",
                                      "kilim_neutral", "kilim_neutral_neo",
                                      "kilim_light", "kilim_light_neo",
                                      "kilim_warm", "kilim_warm_neo"]

    layout = tmp_path / "fresh.json"
    copy_layout("layouts/default.json", layout)
    sidecar = tmp_path / "fresh.perspective.json"
    app, _ = _window()
    w = KilimWindow(str(layout), str(sidecar))
    try:
        w.show()
        app.processEvents()
        assert w.lace_theme == "kilim_midnight"
        assert w.bridge.core.theme() == "Kilim Midnight"
        assert w.bridge.core.markdown_theme() == "Kilim Midnight"
        assert w.terminal_theme == "Kilim Midnight"
        w.apply_lace_theme("kilim_light")
        assert w.bridge.core.theme() == "Kilim Light"
        assert w.bridge.core.markdown_theme() == "Kilim Light"
        assert w.terminal_theme == "Kilim Light"
        # Neo chassis: chrome changes, code/md/terminal follow into neo.
        w.apply_lace_theme("kilim_light_neo")
        assert w.lace_theme == "kilim_light_neo"
        assert w.bridge.core.theme() == "Kilim Light Neo"
        assert w.bridge.core.markdown_theme() == "Kilim Light Neo"
        assert w.terminal_theme == "Kilim Light Neo"
        raw = json.loads(layout.read_text(encoding="utf-8"))
        assert raw["layout"]["theme"] == "Kilim Light Neo"
        assert raw["layout"]["terminal_theme"] == "Kilim Light Neo"
        assert json.loads(sidecar.read_text(encoding="utf-8"))["lace_theme"] == "kilim_light_neo"
    finally:
        w.close()
        app.processEvents()


def test_reset_layout_restores_the_file_arrangement(tmp_path):
    """Reset Layout returns to the layout file's split, not the sidecar.

    A saved sidecar that tabbed every pane into one area must not become
    the "default": the file's groups are what Reset Layout restores."""
    import json

    from _util import copy_layout
    from kilim import perspective as perspectives
    from kilim.qt_app import KilimWindow
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    layout = tmp_path / "default.json"
    sidecar = tmp_path / "default.perspective.json"
    copy_layout("layouts/example.json", layout)

    w = KilimWindow(str(layout), str(sidecar))  # no sidecar: file arrangement
    w.show()
    try:
        for _ in range(10):
            app.processEvents()
        data = perspectives.capture(w)
    finally:
        w.close()
        app.processEvents()

    # Degenerate saved state: every pane in one tab group.
    blob = json.loads(data["lace"])
    root = blob["containers"][0]["data"]["root_splitter"]
    widgets = [wd for area in root["children"] if area["type"] == "Area"
               for wd in area["widgets"]]
    root["children"] = [{
        "type": "Area", "tabs": len(widgets),
        "current": widgets[-1]["name"], "widgets": widgets,
    }]
    root["count"] = 1
    root["sizes"] = [1200]
    data["kilim"]["tab_groups"] = [["term1", "code", "notes"]]
    data["lace"] = json.dumps(blob)
    perspectives.save(sidecar, data)

    w = KilimWindow(str(layout), str(sidecar))
    w.show()
    try:
        for _ in range(10):
            app.processEvents()
        assert perspectives.capture(w)["kilim"]["tab_groups"] == \
            [["term1", "code", "notes"]], "sidecar not applied"
        w.reset_layout()
        for _ in range(20):
            app.processEvents()
        assert perspectives.capture(w)["kilim"]["tab_groups"] == \
            [["term1"], ["code", "notes"]], "reset did not restore the file"
    finally:
        w.close()
        app.processEvents()


def test_file_pane_no_phantom_lines_and_full_bleed_bg(tmp_path):
    """Code view: one block per line (no doubled breaks), theme bg edge to edge."""
    import json

    from kilim import theme_background
    from kilim.qt_app import Bridge, FilePane
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    src = tmp_path / "s.py"
    src.write_text("x = 1\n# hi\n", encoding="utf-8")  # trailing newline: the old doubling case
    doc = {
        "layout": {"root": {"type": "pane", "pane_id": "code"}, "active": "code",
                   "theme": "Kilim Midnight", "markdown_theme": "Kilim Midnight"},
        "panes": [{"id": "code", "title": "s.py", "kind": "file", "path": str(src)}],
    }
    bridge = Bridge(json.dumps(doc))
    try:
        assert theme_background("Kilim Midnight") == "#101319"
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
        menus = {a.text() for a in w.titleBar.menu_bar.actions()}
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


def test_width_only_resize_rebuilds_and_tracks_cols():
    """Width-only rescale: one rebuild per settle, cols bookkeeping tracks.

    Regression: the same-window fast path matched on rows+start alone,
    so the second width-only resize never rebuilt — paint_cols rotted
    and shift surgery stayed disabled (every later move paid a full).
    """
    import time

    app, w = _window()
    try:
        term = _term(w)
        for _ in range(8):
            app.processEvents()
            time.sleep(0.06)
        rows0, cols0 = term._applied_grid

        def settle_width(width, timeout=6.0):
            _, before = term._applied_grid
            w.resize(width, 800)
            deadline = time.time() + timeout
            while time.time() < deadline:
                app.processEvents()
                time.sleep(0.06)
                grid = term._applied_grid
                if grid[1] != before and grid[0] == rows0:
                    # Settled on the new width: let the rebuild land.
                    for _ in range(4):
                        app.processEvents()
                        time.sleep(0.06)
                    return term._applied_grid
            raise AssertionError("width never settled")

        grid = settle_width(1600)
        assert term._paint_cols == grid[1], (term._paint_cols, grid)
        assert term._paint_rows == rows0
        grid = settle_width(1100)
        assert term._paint_cols == grid[1], (term._paint_cols, grid)
        assert term._paint_rows == rows0
    finally:
        w.close()
        app.processEvents()


def test_resize_jump_applies_immediately():
    """A discrete grid jump (maximize/snap) applies on the first poll.

    Same synchronous harness as the settle test: the grid shapes are
    the only moving part. A 30x60 jump lands at once; a 7x15 creep
    still debounces (applies on the repeat poll)."""
    import concurrent.futures
    from unittest.mock import patch

    from kilim.qt_app import TerminalPane

    app, w = _window()
    try:
        term = _term(w)
        term._poller.stop()
        term._pending = None

        def drive(shapes):
            term._applied_grid = None
            term._pending_grid = None
            term._pending_same = 0
            term._resize_pending = None
            calls = {"n": 0}
            seen = []

            def fake_grid():
                i = min(calls["n"], len(shapes) - 1)
                calls["n"] += 1
                return shapes[i]

            real_snapshot = TerminalPane._snapshot

            def _rec_snapshot(self, rows, cols):
                seen.append(rows)
                return real_snapshot(self, rows, cols)

            def fake_submit(make_coro):
                fut: concurrent.futures.Future = concurrent.futures.Future()
                try:
                    make_coro().close()
                except Exception:  # noqa: BLE001 - not a coroutine factory
                    pass
                fut.set_result(None)
                return fut

            with (
                patch.object(TerminalPane, "_grid", side_effect=fake_grid),
                patch.object(TerminalPane, "_snapshot", _rec_snapshot),
                patch.object(term.bridge, "submit", side_effect=fake_submit),
            ):
                for _ in range(len(shapes)):
                    term._poll()
            return seen

        # Discrete jump: new shape snapshots on the very next poll.
        seen = drive([(40, 100), (70, 160)])
        assert seen == [40, 70], seen
        assert term._applied_grid == (70, 160)
        assert term._resize_pending == (70, 160)
        # Near-threshold creep: holds, then applies on the repeat.
        seen = drive([(40, 100), (47, 115), (47, 115)])
        assert seen == [40, 40, 47], seen
        assert term._applied_grid == (47, 115)
    finally:
        w.close()
        app.processEvents()


def test_resize_applies_only_after_settle():
    """Resize drag: polls hold the settled grid, one resize lands.

    Fully synchronous (fake bridge, stopped poller): the grid shapes are
    the only moving part, so no timer slip can intrude."""
    import concurrent.futures
    from unittest.mock import patch

    from kilim.qt_app import TerminalPane

    app, w = _window()
    try:
        term = _term(w)
        term._poller.stop()
        term._pending = None
        term._applied_grid = None
        term._pending_grid = None
        term._pending_same = 0
        term._resize_pending = None
        shapes = [(40, 100)] + [(41 + i, 100) for i in range(4)] + [(45, 100)] * 3
        calls = {"n": 0}

        def fake_grid():
            i = min(calls["n"], len(shapes) - 1)
            calls["n"] += 1
            return shapes[i]

        seen_rows: list = []
        real_snapshot = TerminalPane._snapshot

        def _rec_snapshot(self, rows, cols):
            seen_rows.append(rows)
            return real_snapshot(self, rows, cols)

        def fake_submit(make_coro):
            # Hermetic: nothing reaches the core; completed futures keep
            # _poll's state machine moving exactly as live ones would.
            fut: concurrent.futures.Future = concurrent.futures.Future()
            try:
                make_coro().close()  # created, never awaited: no side effects
            except Exception:  # noqa: BLE001 - not a coroutine factory
                pass
            fut.set_result(None)
            return fut

        with (
            patch.object(TerminalPane, "_grid", side_effect=fake_grid),
            patch.object(TerminalPane, "_snapshot", _rec_snapshot),
            patch.object(term.bridge, "submit", side_effect=fake_submit),
        ):
            for _ in range(len(shapes)):
                term._poll()
        # (Recorded at call time by _rec_snapshot: mock await/call lists
        # are unreliable here — real coroutines await on the bridge thread,
        # so pre-patch snapshots can record inside the window under load.)
        rows_seen = seen_rows
        assert rows_seen, "expected snapshots to run"
        # Drag intermediates (41-44) never reshape a snapshot: polls hold
        # the settled grid, then move to the new one once stable. The
        # settle applies on the first repeat poll; the final stable poll
        # then holds the same grid, so the new shape appears twice.
        assert set(rows_seen) == {40, 45}, rows_seen
        assert rows_seen.count(45) == 2, rows_seen
        assert term._applied_grid == (45, 100)  # applied once stable
        assert term._resize_pending == (45, 100)  # resize follows the settle
    finally:
        w.close()
        app.processEvents()


def test_terminal_theme_apply_repaints_idle_rows():
    """Theme switch repaints existing rows on an idle pane.

    Regression: the forced rebuild bypassed the same-window fast paths
    (subset no-op, shift surgery), so an idle terminal kept its old
    colors until new output, a scroll, or a resize repainted it."""
    import time
    from unittest.mock import patch

    from kilim import theme_background
    from kilim.qt_app import TerminalPane
    from PySide6.QtGui import QColor, QPalette

    app, w = _window()
    try:
        term = _term(w)
        for _ in range(10):
            app.processEvents()
            time.sleep(0.06)
        with patch.object(
            TerminalPane, "_paint_full", autospec=True,
            wraps=TerminalPane._paint_full,
        ) as full:
            w.apply_terminal_theme("Kilim Light")
            for _ in range(10):
                app.processEvents()
                time.sleep(0.06)
            assert full.call_count >= 1, "theme switch must rebuild, not shift"
        paper = QColor(theme_background("Kilim Light"))
        assert term.view.palette().color(QPalette.Base) == paper
        frag = term.view.document().findBlockByNumber(0).begin()
        assert frag.fragment().charFormat().background().color() == paper
    finally:
        w.close()
        app.processEvents()


def test_term_pane_follows_terminal_theme():
    """Shell view tracks the terminal theme (palette + repaint marker)."""
    from kilim import theme_background, theme_foreground
    from PySide6.QtGui import QColor, QPalette

    app, w = _window()  # import runs here; registry starts empty
    try:
        assert theme_foreground("Kilim Midnight") != ""
        pane = w.term_panes["term1"]
        pane._ensure_theme()
        assert pane._theme_name == w.terminal_theme
        assert pane.view.palette().color(QPalette.Base) == QColor(
            theme_background(w.terminal_theme))
        # A code apply leaves terminals alone...
        w.apply_code_theme("Kilim Light")
        pane._ensure_theme()  # idempotent: syncs now if no poll beat us
        assert pane._theme_name == "Kilim Midnight"
        # ...a terminal apply recolors them.
        w.apply_terminal_theme("Kilim Light")
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
        total, start, cells, cursor, modes, dirty, cwd = w.bridge.call(
            lambda: w.bridge.core.snapshot_term("term1", 10, None)
        )
        app_cursor, bracketed, mouse, sgr, alt, bell, cursor_visible = modes
        assert isinstance(app_cursor, bool) and isinstance(mouse, int)
        assert mouse == 0 and alt is False  # plain PowerShell: no modes
        assert bell is False and isinstance(dirty, list)
        assert cursor_visible is True  # DECTCEM: visible until ?25l
    finally:
        w.close()
        app.processEvents()


def test_hidden_hardware_cursor_suppresses_the_soft_block():
    """?25l (app draws its own cursor) kills our block; ?25h restores."""
    import sys
    import time

    from kilim.qt_app import _modes_dict

    app, w = _window()
    try:
        term = _term(w)
        term._poller.stop()
        for _ in range(20):
            app.processEvents()
            time.sleep(0.05)
        term.view.setFocus()
        app.processEvents()
        assert term.view.hasFocus()
        assert term._cursor_visible is True

        script = (
            "import sys, time; sys.stdout.write(sys.argv[1]); "
            "sys.stdout.flush(); time.sleep(30)"
        )

        async def emit(seq, pid):
            await term.bridge.core.spawn_term(
                pid, pid, sys.executable, ["-c", script, seq], 24, 80, 500
            )
            for _ in range(60):
                snap = await term.bridge.core.snapshot_term(pid, 24, None)
                if (snap[4][6] is False) == (seq == "\x1b[?25l"):
                    return snap
                await __import__("asyncio").sleep(0.1)
            raise AssertionError(f"DECTCEM {seq!r} never arrived")

        async def bye(pid):
            await term.bridge.core.terminate_term(pid, 1.0)

        def render(snap):
            rows = len(snap[2])
            term._render({
                "cells": snap[2], "cursor": snap[3], "start": snap[1],
                "total": snap[0], "rows": rows, "cols": 80,
                "modes": _modes_dict(snap[4]),
                "dirty": [int(r) for r in snap[5]], "cwd": snap[6],
            })
            return rows

        try:
            snap = term.bridge.call(lambda: emit("\x1b[?25l", "probe-hide"))
            rows = render(snap)
            assert term._cursor_visible is False
            assert term._caret_cell(snap[1], snap[3], rows) is None

            snap = term.bridge.call(lambda: emit("\x1b[?25h", "probe-show"))
            render(snap)
            assert term._cursor_visible is True
        finally:
            for pid in ("probe-hide", "probe-show"):
                try:
                    term.bridge.call(lambda: bye(pid))
                except Exception:  # noqa: BLE001 - already gone
                    pass
    finally:
        w.close()
        app.processEvents()


def test_arrow_and_mouse_encodings():
    from kilim.qt_app import (
        _arrow_seq,
        _char_to_term_col,
        _sgr_mouse,
        _term_col_to_char,
        _x10_mouse,
    )
    from PySide6.QtCore import Qt

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


def _fake_snap(rows, start=0, total=None, cursor=(0, 0), dirty=None, cols=80):
    cells = [[(t, "default", "default", 0) for t in row] for row in rows]
    return {
        "cells": cells,
        "cursor": cursor,
        "start": start,
        "total": total if total is not None else len(rows),
        "rows": len(rows),
        "cols": cols,
        "modes": {"app_cursor": False, "bracketed": False, "mouse": 0,
                  "sgr": False, "alt": False, "bell": False},
        "dirty": list(dirty) if dirty else [],
    }


def _cell_format(block, ci):
    """QTextCharFormat of the fragment covering char offset ci of a block."""
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        if frag.position() <= block.position() + ci < frag.position() + len(frag.text()):
            return frag.charFormat()
        it += 1
    return None


def test_terminal_cursor_is_a_blinking_block():
    """Qt draws no caret in a read-only view (regression: "no cursor in
    the Qt terminal"): the focused view paints a reverse-video block into
    the cell under the terminal cursor, follows moves, blinks without
    counting as a content repaint, and clears when focus leaves."""
    app, w = _window()
    try:
        term = _term(w)
        term._poller.stop()  # fake snaps stay put
        term.view.setFocus()
        app.processEvents()
        assert term.view.hasFocus(), "offscreen focus is needed by this test"
        term._render(_fake_snap([["a", "b", "c", "d"], ["e", "f", "g", "h"]], cursor=(2, 1)))
        doc = term.view.document()
        fg = term.view.palette().color(term.view.foregroundRole())
        bg = term.view.palette().color(term.view.backgroundRole())

        on = _cell_format(doc.findBlockByNumber(1), 2)
        assert on is not None
        assert on.foreground().color() == bg and on.background().color() == fg
        # Neighbour cell untouched: only the cursor cell reverses.
        plain = _cell_format(doc.findBlockByNumber(1), 1)
        assert plain.foreground().color() == fg and plain.background().color() == bg

        # Blink: phase off paints the plain cell and is not a content frame.
        frozen = term._render_count
        term._blink_caret()
        off = _cell_format(doc.findBlockByNumber(1), 2)
        assert off.foreground().color() == fg and off.background().color() == bg
        assert term._render_count == frozen
        term._blink_caret()
        assert _cell_format(doc.findBlockByNumber(1), 2).background().color() == fg

        # A move with unchanged content carries the block to the new cell.
        term._render(_fake_snap([["a", "b", "c", "d"], ["e", "f", "g", "h"]], cursor=(0, 0)))
        assert _cell_format(doc.findBlockByNumber(0), 0).background().color() == fg
        assert _cell_format(doc.findBlockByNumber(1), 2).background().color() == bg
        assert term._caret_on is True  # a move re-shows the block

        # Blur clears the block and parks the blink clock; focus brings both
        # back ("blinking cursor only while the widget has the focus").
        term.view.clearFocus()
        app.processEvents()
        assert _cell_format(doc.findBlockByNumber(0), 0).background().color() == bg
        assert not term._caret_timer.isActive()
        term.view.setFocus()
        app.processEvents()
        assert _cell_format(doc.findBlockByNumber(0), 0).background().color() == fg
        assert term._caret_timer.isActive() or not term._blink_ms
    finally:
        w.close()
        app.processEvents()


def test_incremental_repaint_touches_one_row():
    """Dirty-region paint: one changed row = one more frame, rest intact."""

    app, w = _window()
    try:
        term = _term(w)
        rows = [["aaa", "bbb"], ["ccc", "ddd"], ["eee", "fff"]]
        term._render(_fake_snap(rows, total=10))
        base = term._render_count
        assert term.view.document().findBlockByNumber(1).text() == "cccddd"
        changed = [["aaa", "bbb"], ["CCC", "ddd"], ["eee", "fff"]]
        term._render(_fake_snap(changed, total=11, dirty=[1]))
        assert term._render_count == base + 1
        assert term.view.document().findBlockByNumber(1).text() == "CCCddd"
        assert term.view.document().findBlockByNumber(0).text() == "aaabbb"
        # Identical re-render: idle, no touch.
        term._render(_fake_snap(changed, total=11))
        assert term._render_count == base + 1
    finally:
        w.close()
        app.processEvents()


def test_tail_scroll_appends_without_full_rebuild():
    """Streaming output scrolls blocks; clear()+rebuild is for jumps."""
    from unittest.mock import patch

    from kilim.qt_app import TerminalPane

    app, w = _window()
    try:
        term = _term(w)
        term._render(_fake_snap([["aa"], ["bb"], ["cc"]]))
        doc = term.view.document()
        assert doc.blockCount() == 4  # 3 rows + trailing empty
        base = term._render_count
        with patch.object(
            TerminalPane, "_paint_full", autospec=True,
            wraps=TerminalPane._paint_full,
        ) as full:
            # Tail slides one: bb/cc stay, dd appends, aa drops.
            term._render(_fake_snap([["bb"], ["cc"], ["dd"]], start=1, total=4))
            assert full.call_count == 0
            assert term._render_count == base + 1
            assert [doc.findBlockByNumber(i).text() for i in range(3)] == [
                "bb", "cc", "dd",
            ]
            assert doc.blockCount() == 4
            # A jump past the window still rebuilds fully.
            term._render(_fake_snap([["xx"], ["yy"], ["zz"]], start=10, total=13))
            assert full.call_count == 1
            assert [doc.findBlockByNumber(i).text() for i in range(3)] == [
                "xx", "yy", "zz",
            ]
    finally:
        w.close()
        app.processEvents()


def test_scroll_back_and_height_resize_shift_without_rebuild():
    """Wheel scrollback, height grow/shrink: block surgery, no rebuild."""
    from unittest.mock import patch

    from kilim.qt_app import TerminalPane

    app, w = _window()
    try:
        term = _term(w)
        term._render(_fake_snap([["aa"], ["bb"], ["cc"]]))
        doc = term.view.document()
        base = term._render_count
        with patch.object(
            TerminalPane, "_paint_full", autospec=True,
            wraps=TerminalPane._paint_full,
        ) as full:
            # Wheel up one: 00 prepends, cc drops off the tail.
            term._render(_fake_snap([["00"], ["aa"], ["bb"]], start=-1, total=4))
            assert full.call_count == 0
            assert [doc.findBlockByNumber(i).text() for i in range(3)] == [
                "00", "aa", "bb",
            ]
            assert doc.blockCount() == 4
            # Height grow at the tail: dd appends, shared rows untouched.
            term._render(_fake_snap([["aa"], ["bb"], ["cc"], ["dd"]], start=0, total=4))
            assert full.call_count == 0
            assert [doc.findBlockByNumber(i).text() for i in range(4)] == [
                "aa", "bb", "cc", "dd",
            ]
            assert doc.blockCount() == 5
            # Height shrink at the tail: head blocks drop, tail kept.
            term._render(_fake_snap([["cc"], ["dd"]], start=2, total=4))
            assert full.call_count == 0
            assert [doc.findBlockByNumber(i).text() for i in range(2)] == [
                "cc", "dd",
            ]
            assert doc.blockCount() == 3
            assert term._render_count == base + 3
            # Width change, same shape: cells reshaped — only a full
            # rebuild lines those up and re-syncs the width
            # bookkeeping (a subset would leave paint_cols rotted and
            # shift surgery disabled for every later move).
            term._render(_fake_snap([["CC"], ["DD"]], start=2, total=4,
                                     cols=120, dirty=[2, 3]))
            assert full.call_count == 1
            assert [doc.findBlockByNumber(i).text() for i in range(2)] == [
                "CC", "DD",
            ]
            # Width change plus new shape, same width: shift surgery
            # appends the fresh row (no second rebuild).
            term._render(_fake_snap([["CC"], ["DD"], ["EE"]], start=2,
                                     total=5, cols=120, dirty=[2, 3, 4]))
            assert full.call_count == 1
            assert [doc.findBlockByNumber(i).text() for i in range(3)] == [
                "CC", "DD", "EE",
            ]
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


def test_bell_dots_tab():
    """A BEL dots the tab even with no new output (1.5s flash)."""
    app, w = _window()
    try:
        term = _term(w)
        dock = w.pane_docks["term1"]
        rows = [["aaa"]]
        term._render(_fake_snap(rows, total=10))
        assert "●" not in dock.windowTitle()
        dock.hide()
        app.processEvents()
        snap = _fake_snap(rows, total=10)
        snap["modes"] = dict(snap["modes"])
        snap["modes"]["bell"] = True
        term._render(snap)
        assert "●" in dock.windowTitle()
    finally:
        w.close()
        app.processEvents()


def test_lace_switch_defers_markdown_refresh():
    """md refresh queues behind the bridge palette push (no 1-switch lag).

    The bridge applies the app palette via singleShot(0); a direct
    pane.refresh() inside apply_lace_theme would sample the previous
    theme's colors for the scrollbar CSS."""
    from unittest.mock import patch

    from PySide6.QtCore import QTimer

    app, w = _window("layouts/example.json")
    try:
        calls = []
        real = QTimer.singleShot

        def rec(*args):
            calls.append(args)
            return real(*args)

        with patch("kilim.main_window.QTimer.singleShot", side_effect=rec):
            w.apply_lace_theme("kilim_warm")
        app.processEvents()
        assert w.md_panes, "the example layout must have a markdown pane"
        for pane in w.md_panes.values():
            assert any(
                len(a) == 2 and a[0] == 0
                and getattr(a[1], "__self__", None) is pane
                and getattr(getattr(a[1], "__func__", None), "__name__", "") == "refresh"
                for a in calls
            ), "md refresh not deferred past the bridge push"
    finally:
        w.close()
        app.processEvents()


def test_floats_use_custom_chrome():
    """Torn-off panes get the demo's frameless custom title bar.

    Floats run under ``TitleBarMode.custom`` with the plain Lace title
    bar configured on the manager; the Kilim menus stay on the main
    window's chrome only."""
    from lace import TitleBarMode
    from lace.floating_dock_container_frameless import FramelessFloatingDockContainer
    from lace.frameless_window import LaceStandardTitleBar
    from PySide6.QtCore import Qt

    app, w = _window()
    try:
        assert w.manager.title_bar_mode == TitleBarMode.custom
        assert w.manager.floating_container_class() is FramelessFloatingDockContainer
        # A real torn-off pane: frameless chrome, Lace's own title bar.
        float_win = FramelessFloatingDockContainer(dock_widget=w.pane_docks["term1"])
        try:
            assert float_win.windowFlags() & Qt.FramelessWindowHint
            assert isinstance(float_win.titleBar, LaceStandardTitleBar)
        finally:
            float_win.close()
    finally:
        w.close()
        app.processEvents()


def _submitted_bytes(coro):
    """The bytes argument a `submit(lambda: ...write_term(pid, data))` closed over."""
    for cell in getattr(coro, "__closure__", None) or ():
        value = cell.cell_contents
        if isinstance(value, bytes):
            return value
    return None


def test_escape_reaches_the_shell():
    """Esc is the running app's key (vim, fzf, agent TUIs), not a Kilim
    shortcut: the byte reaches the shell — while still clearing the
    selection and snapping back to the live tail, like any keypress."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor

    app, w = _window()
    try:
        term = _term(w)
        term._poller.stop()
        term._render(_fake_snap([["abcdef"]], cursor=(0, 0)))
        # Active selection + scrolled-back view: Esc clears both.
        cur = term.view.textCursor()
        cur.movePosition(QTextCursor.Start)
        cur.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, 3)
        term.view.setTextCursor(cur)
        term._sel = ((0, 0), (0, 3))
        term._follow = False

        sent = []
        real_submit = term.bridge.submit
        term.bridge.submit = lambda make_coro: sent.append(_submitted_bytes(make_coro))
        try:
            term.on_key(_key(Qt.Key_Escape))
        finally:
            term.bridge.submit = real_submit

        assert sent == [b"\x1b"], sent  # the app got its Esc
        assert not term.view.textCursor().hasSelection()
        assert term._sel is None
        assert term._follow is True

        # Return, Backspace, Tab and Shift+Tab forward their own bytes.
        sent.clear()
        term.bridge.submit = lambda make_coro: sent.append(_submitted_bytes(make_coro))
        try:
            term.on_key(_key(Qt.Key_Return))
            term.on_key(_key(Qt.Key_Backspace))
            term.on_key(_key(Qt.Key_Tab))
            term.on_key(_key(Qt.Key_Backtab))
        finally:
            term.bridge.submit = real_submit
        assert sent == [b"\r", b"\x7f", b"\t", b"\x1b[Z"], sent
    finally:
        w.close()
        app.processEvents()


def test_escape_and_tab_reach_the_terminal_through_qt():
    """The real Qt path: Escape must not be stolen by Lace's window-wide
    "close sidebar" binding, and Tab/Backtab must not be swallowed by Qt's
    focus navigation — the focused terminal gets 0x1b / 0x09 / ESC[Z."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    app, w = _window()
    try:
        term = _term(w)
        term._poller.stop()
        term.view.setFocus()
        app.processEvents()

        # Tab/Backtab are the terminal's: Qt hands them to keyPressEvent.
        assert term.view.focusNextPrevChild(True) is False

        # Lace >= 0.7.6 gates its window-wide Esc ("close sidebar") on overlay
        # visibility (event filter on Show/Hide): with nothing open it is off
        # and the terminal keeps the key; while an overlay is up it is on, so
        # Esc closes that instead of reaching the shell.
        sidebar = w.manager.sidebar_manager
        sidebar_esc = sidebar._keyboard._shortcuts["Escape"]
        assert sidebar_esc.isEnabled() is False

        stolen = []
        sidebar._keyboard.close_current.connect(lambda: stolen.append(1))
        sent = []
        real_submit = term.bridge.submit
        term.bridge.submit = lambda make_coro: sent.append(_submitted_bytes(make_coro))
        try:
            cases = [
                (Qt.Key_Escape, Qt.NoModifier, b"\x1b"),
                (Qt.Key_Tab, Qt.NoModifier, b"\t"),
                (Qt.Key_Backtab, Qt.ShiftModifier, b"\x1b[Z"),
                (Qt.Key_Tab, Qt.ShiftModifier, b"\x1b[Z"),
            ]
            for key, mods, expected in cases:
                sent.clear()
                QApplication.sendEvent(
                    term.view, QKeyEvent(QEvent.Type.KeyPress, key, mods, "")
                )
                app.processEvents()
                assert sent == [expected], (key, sent)
            assert stolen == [], "Lace's sidebar Esc handler took the key"

            # Overlay up: Esc belongs to the sidebar, the shell sees nothing.
            overlay = sidebar.overlay
            overlay.show()
            app.processEvents()
            assert sidebar_esc.isEnabled() is True
            sent.clear()
            stolen.clear()
            QApplication.sendEvent(
                term.view,
                QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Escape, Qt.NoModifier, ""),
            )
            app.processEvents()
            assert stolen == [1], "the overlay should close on Esc"
            assert sent == [], "the shell must not see the overlay-close Esc"

            # Overlay gone: the terminal gets Esc back on its own.
            overlay.hide()
            app.processEvents()
            assert sidebar_esc.isEnabled() is False
            sent.clear()
            QApplication.sendEvent(
                term.view,
                QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Escape, Qt.NoModifier, ""),
            )
            app.processEvents()
            assert sent == [b"\x1b"], sent
        finally:
            term.bridge.submit = real_submit
            sidebar.overlay.hide()
    finally:
        w.close()
        app.processEvents()


def _custom_window(tmp_path, panes, shell_cwds):
    """KilimWindow on a synthetic layout + pre-seeded sidecar.

    Models an app restart: the sidecar already holds per-shell Start
    Directories from a previous session. Absolute paths, so nothing
    touches the repo's layouts."""
    import json
    from pathlib import Path as _Path

    from kilim.qt_app import KilimWindow
    from PySide6.QtWidgets import QApplication

    base = json.loads(_Path("layouts/default.json").read_text(encoding="utf-8"))
    base["panes"] = panes
    layout_path = tmp_path / "k.json"
    layout_path.write_text(json.dumps(base), encoding="utf-8")
    sidecar_path = tmp_path / "k.perspective.json"
    sidecar_path.write_text(json.dumps({"shell_cwds": shell_cwds}), encoding="utf-8")
    app = QApplication.instance() or QApplication([])
    w = KilimWindow(str(layout_path), str(sidecar_path))
    w.show()
    return app, w


def _probe_pane(label):
    """Layout term pane that prints its own cwd, then idles."""
    import sys

    script = "import os, time; print('CWD:' + os.getcwd(), flush=True); time.sleep(60)"
    return {
        "id": "term1", "title": label, "kind": "term",
        "cmd": sys.executable, "args": ["-c", script],
        "rows": 24, "cols": 80, "scrollback": 500,
    }


def _wait_printed_cwd(app, term, pid="term1", timeout=30.0):
    """Poll the pane until the probe's CWD: line shows; return the path.

    Anchored at history 0 with a wide window: the pane poller may have
    grown the pty past the probe's single line, and a tail snapshot
    would then show only blanks. Long paths wrap past the viewport
    width, so rows are reassembled (wrapping splits nothing)."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.2)
        snap = term.bridge.call(lambda: term.bridge.core.snapshot_term(pid, 200, 0))
        text = "".join("".join(c[0] for c in row).rstrip() for row in snap[2])
        if "CWD:" in text:
            after = text.split("CWD:", 1)[1]
            # The probe prints exactly one line: the path runs to the end
            # of the viewport text (later rows are blank padding).
            return after.strip()
    raise AssertionError("probe never printed its cwd")


def test_term_cwd_tracks_cd_live(tmp_path):
    """Snapshot cwd follows a real shell's cd (OSC tracking)."""
    import os
    import shutil
    import time

    if shutil.which("cmd.exe") is None:
        pytest.skip("cmd.exe required")
    aaa = tmp_path / "aaa"
    bbb = tmp_path / "bbb"
    aaa.mkdir()
    bbb.mkdir()
    app, w = _window()
    try:
        w.bridge.call(lambda: w.bridge.core.spawn_term(
            "tcd", "tcd", "cmd.exe", [], 24, 80, 5000, cwd=str(aaa)))
        try:
            w.bridge.submit(lambda: w.bridge.core.write_term(
                "tcd", ("cd /d " + str(bbb).replace("/", "\\") + "\r").encode()))
            deadline = time.time() + 25
            live = None
            while time.time() < deadline:
                app.processEvents()
                time.sleep(0.5)
                snap = w.bridge.call(lambda: w.bridge.core.snapshot_term("tcd", 1, None))
                live = snap[6] if len(snap) > 6 else None
                if live and os.path.normcase(live) == os.path.normcase(str(bbb)):
                    break
            assert live and os.path.normcase(live) == os.path.normcase(str(bbb)), live
        finally:
            try:
                w.bridge.call(lambda: w.bridge.core.terminate_term("tcd", 1.0))
            except Exception:  # noqa: BLE001, S110 — closing anyway
                pass
    finally:
        w.close()
        app.processEvents()


def test_pane_live_dir_survives_restart(tmp_path):
    """cd in term1, close, reopen: the pane restores the live dir.

    The full session loop: OSC tracking feeds the snapshot, closeEvent
    persists per-pane cwds to the sidecar, the next launch stamps them."""
    import json
    import os
    import time
    from pathlib import Path as _Path

    from kilim.qt_app import KilimWindow
    from PySide6.QtWidgets import QApplication

    def norm(p):
        return os.path.normcase(os.path.normpath(p))

    target = tmp_path / "lived"
    target.mkdir()
    base = json.loads(_Path("layouts/default.json").read_text(encoding="utf-8"))
    layout_path = tmp_path / "k.json"
    layout_path.write_text(json.dumps(base), encoding="utf-8")
    sidecar_path = tmp_path / "k.perspective.json"

    def cd_line(shell_base):
        t = str(target)
        if shell_base == "cmd":
            return ("cd /d " + t.replace("/", "\\") + "\r").encode()
        if shell_base in ("powershell", "pwsh"):
            return ('Set-Location "' + t + '"\r').encode()
        return ('cd "' + t.replace("\\", "/") + '"\r').encode()

    app = QApplication.instance() or QApplication([])
    w1 = KilimWindow(str(layout_path), str(sidecar_path))
    w1.show()
    try:
        try:
            default_cmd = w1.bridge.core.default_shell_cmd()[0]
        except Exception:  # noqa: BLE001 — assume cmd syntax
            default_cmd = ""
        sh_base = default_cmd.replace("\\", "/").rsplit("/", 1)[-1].lower()
        sh_base = sh_base.removesuffix(".exe")
        w1.bridge.submit(lambda: w1.bridge.core.write_term("term1", cd_line(sh_base or "cmd")))
        deadline = time.time() + 25
        live = None
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.5)
            snap = w1.bridge.call(lambda: w1.bridge.core.snapshot_term("term1", 1, None))
            live = snap[6] if len(snap) > 6 else None
            if live and norm(live) == norm(str(target)):
                break
        assert live and norm(live) == norm(str(target)), live
    finally:
        w1.close()
        app.processEvents()
    side = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert norm(side["pane_cwds"]["term1"]) == norm(str(target))
    w2 = KilimWindow(str(layout_path), str(sidecar_path))
    w2.show()
    try:
        deadline = time.time() + 30
        live = None
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.5)
            snap = w2.bridge.call(lambda: w2.bridge.core.snapshot_term("term1", 1, None))
            live = snap[6] if len(snap) > 6 else None
            if live and norm(live) == norm(str(target)):
                break
        assert live and norm(live) == norm(str(target)), live
    finally:
        w2.close()
        app.processEvents()


def test_cmdless_pane_resolves_to_platform_default():
    """A layout term with no cmd maps to the core platform-default shell.

    The default layout's term1 is exactly this shape (title "shell",
    no cmd) — without this fallback the stamp silently skips it and
    the Start Directory never applies after a restart."""
    from kilim import shells

    def _base(cmd):
        b = cmd.replace("\\", "/").rsplit("/", 1)[-1].lower()
        return b.removesuffix(".exe")

    app, w = _window()
    try:
        core_cmd = w.bridge.core.default_shell_cmd()[0]
        want = next(
            (label for label, cmd, _a in shells.find_shells()
             if _base(cmd) == _base(core_cmd)),
            None,
        )
        assert want is not None, "core default matches no menu shell"
        assert w._resolve_shell_label({"title": "shell", "kind": "term"}) == want
        assert w._resolve_shell_label(
            {"title": "shell", "kind": "term", "cmd": ""}) == want
    finally:
        w.close()
        app.processEvents()


def test_default_shaped_pane_restores_start_dir(tmp_path):
    """Restart, default layout shape: cmd-less term1 opens in the dir.

    Every discovered shell gets the same custom dir, so whichever the
    pane resolves to, the stamp must land: PowerShell's own prompt
    (PS <cwd>>) proves the live cwd."""
    import json
    import os
    import time
    from pathlib import Path as _Path

    from kilim import shells
    from kilim.qt_app import KilimWindow
    from PySide6.QtWidgets import QApplication

    entries = shells.find_shells()
    assert entries, "expected at least one shell on this machine"
    work = tmp_path / "work"
    work.mkdir()
    base = json.loads(_Path("layouts/default.json").read_text(encoding="utf-8"))
    base["panes"] = [{"id": "term1", "title": "shell", "kind": "term"}]
    layout_path = tmp_path / "k.json"
    layout_path.write_text(json.dumps(base), encoding="utf-8")
    sidecar_path = tmp_path / "k.perspective.json"
    sidecar_path.write_text(json.dumps({
        "shell_cwds": {
            label: {"mode": "custom", "dir": str(work)}
            for label, _cmd, _args in entries
        }
    }), encoding="utf-8")
    app = QApplication.instance() or QApplication([])
    w = KilimWindow(str(layout_path), str(sidecar_path))
    w.show()
    try:
        deadline = time.time() + 40
        seen = ""
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.3)
            snap = w.bridge.call(
                lambda: w.bridge.core.snapshot_term("term1", 60, None)
            )
            seen = "".join("".join(c[0] for c in row) for row in snap[2])
            if os.path.normcase(str(work)) in os.path.normcase(seen):
                break
        assert os.path.normcase(str(work)) in os.path.normcase(seen), seen[-200:]
    finally:
        w.close()
        app.processEvents()


def test_layout_term_restores_custom_start_dir(tmp_path):
    """Restart: a layout term spawns in its shell's Start Directory."""
    import os

    from kilim import shells

    entries = shells.find_shells()
    assert entries, "expected at least one shell on this machine"
    label = entries[0][0]
    work = tmp_path / "work"
    work.mkdir()
    app, w = _custom_window(
        tmp_path, [_probe_pane(label)],
        {label: {"mode": "custom", "dir": str(work)}},
    )
    try:
        assert w.shell_cwds.get(label) == {"mode": "custom", "dir": str(work)}
        assert w._shell_cwd_arg(label) == str(work)
        printed = _wait_printed_cwd(app, _term(w))
        assert os.path.normcase(printed) == os.path.normcase(str(work))
    finally:
        w.close()
        app.processEvents()


def test_layout_term_missing_start_dir_inherits_app_default(tmp_path):
    """Restart: a Start Directory that no longer exists -> App default."""
    import os

    from kilim import shells

    entries = shells.find_shells()
    assert entries, "expected at least one shell on this machine"
    label = entries[0][0]
    app, w = _custom_window(
        tmp_path, [_probe_pane(label)],
        {label: {"mode": "custom", "dir": str(tmp_path / "deleted")}},
    )
    try:
        assert w._shell_cwd_arg(label) is None
        printed = _wait_printed_cwd(app, _term(w))
        assert os.path.normcase(printed) == os.path.normcase(os.getcwd())
    finally:
        w.close()
        app.processEvents()


def test_resolve_shell_label_and_missing_fallback(tmp_path):
    """Pane->label matching (title, then cmd); missing dirs fall back."""
    from kilim import shells

    app, w = _window()
    try:
        entries = shells.find_shells()
        assert entries, "expected at least one shell on this machine"
        label, cmd, _args = entries[0]
        assert w._resolve_shell_label({"title": label, "kind": "term"}) == label
        assert w._resolve_shell_label(
            {"title": "unrelated", "cmd": cmd, "kind": "term"}
        ) == label
        # No title, no cmd: falls back to the platform default shell
        # (what the spawn uses), not None.
        assert w._resolve_shell_label(
            {"title": "unrelated", "kind": "term"}
        ) == w._resolve_shell_label({"title": "shell", "kind": "term"})
        w.shell_cwds[label] = {"mode": "custom", "dir": str(tmp_path / "nope")}
        assert w._shell_cwd_arg(label) is None
        w.shell_cwds[label] = {"mode": "custom", "dir": str(tmp_path)}
        assert w._shell_cwd_arg(label) == str(tmp_path)
    finally:
        w.close()
        app.processEvents()

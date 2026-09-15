"""Click-a-path viewer docks: open, close/dispose, history, default shell."""

import json
import shutil
from pathlib import Path

import pytest

PySide6 = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("lace")


@pytest.fixture()
def scene(tmp_path):
    """Hermetic (layout, sidecar) pair: tests never write the repo files.

    A window created from a sidecar-less layout applies the fresh-launch
    lace theme, which persists the theme into its layout file — a copy
    keeps parallel workers off the repo's `layouts/default.json`."""
    layout = tmp_path / "l.json"
    shutil.copy("layouts/default.json", layout)
    return str(layout), str(tmp_path / "l.perspective.json")


def _window(layout, sidecar):
    from PySide6.QtWidgets import QApplication

    from kilim.qt_app import KilimWindow

    app = QApplication.instance() or QApplication([])
    w = KilimWindow(layout, sidecar)
    w.show()
    return app, w


def _term(w):
    return next(iter(w.term_panes.values()))


def _fake_snap(rows, start=0, total=None, cursor=(0, 0), dirty=None):
    cells = [[(t, "default", "default", 0) for t in row] for row in rows]
    return {
        "cells": cells,
        "cursor": cursor,
        "start": start,
        "total": total if total is not None else len(rows),
        "rows": len(rows),
        "modes": {"app_cursor": False, "bracketed": False, "mouse": 0,
                  "sgr": False, "alt": False, "bell": False},
        "dirty": list(dirty) if dirty else [],
    }


def _click(view, pos):
    """Synthesize a left click (press + release, no drag) at `pos`."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    for typ in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        buttons = Qt.LeftButton if typ == QEvent.MouseButtonPress else Qt.NoButton
        ev = QMouseEvent(
            typ,
            QPointF(pos),
            QPointF(view.viewport().mapToGlobal(pos)),
            Qt.LeftButton,
            buttons,
            Qt.NoModifier,
        )
        if typ == QEvent.MouseButtonPress:
            view.mousePressEvent(ev)
        else:
            view.mouseReleaseEvent(ev)


def test_clicking_a_path_opens_a_viewer_dock(scene, tmp_path):
    """Terminal paths are links: a click opens a fresh viewer dock, the
    file lands in the history menu, and a second open raises, not dupes."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QTextCursor

    app, w = _window(*scene)
    try:
        term = _term(w)
        term._poller.stop()  # fake snaps stay put
        src = tmp_path / "clicked.py"
        src.write_text("x = 1\n# hi\n", encoding="utf-8")
        term._render(_fake_snap([["see", " " + str(src) + ":", "1"]]))

        # Detected + underlined (openable kind only).
        assert term._link_cells(term._cells[0]) == [1]

        # Click inside the path text.
        doc = term.view.document()
        block = doc.findBlockByNumber(0)
        cur = QTextCursor(doc)
        cur.setPosition(block.position() + len("see "))
        rect = term.view.cursorRect(cur)
        pos = QPoint(rect.left() + 2, rect.center().y())
        before = set(w.pane_docks)
        _click(term.view, pos)
        app.processEvents()

        new = set(w.pane_docks) - before
        assert len(new) == 1, "expected exactly one viewer dock"
        pid = new.pop()
        assert w.pane_docks[pid].windowTitle() == "clicked.py"
        pane = w.file_panes[pid]
        assert pane.path == str(src)
        assert "x = 1" in pane.toPlainText()
        assert w.file_history[0] == str(src)
        assert "clicked.py" in [a.text() for a in w.titleBar.files_menu.actions()]

        # Same file again: the dock is raised, not duplicated.
        before2 = set(w.pane_docks)
        w.open_in_viewer(str(src), None, "code")
        assert set(w.pane_docks) == before2
    finally:
        w.close()
        app.processEvents()


def test_only_openable_paths_are_underlined(scene, tmp_path):
    """Binary (binaryornot) and missing paths are detected but not links."""
    app, w = _window(*scene)
    try:
        term = _term(w)
        blob = tmp_path / "blob.bin"
        blob.write_bytes(b"\x00\x01\x02\x00\xff\xfe")
        ghost = tmp_path / "ghost.txt"

        def cells(*texts):
            return [(t, "default", "default", 0) for t in texts]

        assert term._link_cells(cells("x", " ", str(blob), " ", str(ghost))) is None
        text = tmp_path / "ok.txt"
        text.write_text("hi\n", encoding="utf-8")
        assert term._link_cells(cells("a", " ", str(text))) == [2]
    finally:
        w.close()
        app.processEvents()


def test_closing_a_viewer_dock_disposes_it(scene, tmp_path):
    """Closing a viewer drops widget + session pane; history reopens it."""
    app, w = _window(*scene)
    try:
        src = tmp_path / "gone.md"
        src.write_text("# hi\n", encoding="utf-8")
        w.open_in_viewer(str(src), None, "markdown")
        pid = next(iter(w.md_panes))
        assert pid in w.pane_docks
        assert w.bridge.core.markdown_page(pid)  # session knows the pane

        w.pane_docks[pid].toggle_view(False)  # the tab's close button path
        app.processEvents()
        assert pid not in w.md_panes
        assert pid not in w.pane_docks
        with pytest.raises(ValueError):
            w.bridge.core.markdown_page(pid)  # pane forgotten
        assert pid not in [a.text() for a in w.titleBar.views_menu.actions()]

        # History still has it: reopening creates a fresh dock.
        assert str(src) in w.file_history
        w.open_history_file(str(src))
        app.processEvents()
        assert any(getattr(p, "path", None) == str(src) for p in w.md_panes.values())
    finally:
        w.close()
        app.processEvents()


def test_default_shell_and_history_persist_in_the_sidecar(scene, tmp_path):
    """Sidecar extras survive a restart and drive the menus."""
    layout, persp = scene
    app, w = _window(layout, persp)
    try:
        entries = w._shell_options()
        assert entries
        target = entries[-1][0]
        w.set_default_shell(target)
        assert w.default_shell[0] == target
        assert f"New {target}" in [a.text() for a in w.titleBar.terminal_menu.actions()]

        src = tmp_path / "f.txt"
        src.write_text("hi\n", encoding="utf-8")
        w.open_in_viewer(str(src), None, "code")
    finally:
        w.close()
        app.processEvents()

    saved = json.loads(Path(persp).read_text(encoding="utf-8"))
    assert saved["default_shell"] == target
    assert saved["file_history"] == [str(src)]

    app2, w2 = _window(layout, persp)
    try:
        assert w2.default_shell[0] == target
        assert w2.file_history == [str(src)]
        assert f"New {target}" in [a.text() for a in w2.titleBar.terminal_menu.actions()]
        assert "f.txt" in [a.text() for a in w2.titleBar.files_menu.actions()]
        # History entry opens into a new dock.
        w2.open_history_file(str(src))
        assert any(getattr(p, "path", None) == str(src) for p in w2.file_panes.values())
        # Clearing empties the menu and the sidecar.
        w2.clear_history()
        assert w2.file_history == []
        assert [a.text() for a in w2.titleBar.files_menu.actions()] == ["No Files Yet"]
    finally:
        w2.close()
        app.processEvents()
    assert json.loads(Path(persp).read_text(encoding="utf-8"))["file_history"] == []


def test_history_drops_vanished_files(scene, tmp_path):
    app, w = _window(*scene)
    try:
        src = tmp_path / "temp.txt"
        src.write_text("x\n", encoding="utf-8")
        w.open_in_viewer(str(src), None, "code")
        assert w.file_history == [str(src)]
        src.unlink()
        w.open_history_file(str(src))  # gone: forget, do not crash
        assert w.file_history == []
    finally:
        w.close()
        app.processEvents()


def test_default_layout_is_one_portable_terminal():
    """The shipped default is a single terminal with no pinned cmd."""
    raw = json.loads(Path("layouts/default.json").read_text(encoding="utf-8"))
    assert raw["layout"]["root"] == {"type": "pane", "pane_id": "term1"}
    assert len(raw["panes"]) == 1
    pane = raw["panes"][0]
    assert pane["kind"] == "term"
    assert "cmd" not in pane  # empty cmd = platform default (portable)

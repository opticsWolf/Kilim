"""Qt status bar: focused-pane + theme labels, link hover, geometry.

Two things this pins down: no resize *grip* (the corner QSizeGrip the
frameless window does not need) and no resize *handler* — QMainWindow lays
the bar out itself, so resizing needs no code in Kilim."""

import pytest

pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("lace")

MIDDLE_DOT = "\u00b7"


@pytest.fixture()
def scene(tmp_path):
    """Hermetic (layout, sidecar) pair: tests never write the repo files."""
    from _util import copy_layout

    layout = copy_layout("layouts/default.json", tmp_path / "l.json")
    return str(layout), str(tmp_path / "l.perspective.json")


def _window(layout, sidecar):
    from PySide6.QtWidgets import QApplication

    from kilim.qt_app import KilimWindow

    app = QApplication.instance() or QApplication([])
    w = KilimWindow(layout, sidecar)
    w.resize(900, 620)
    w.show()
    return app, w


def _settle(app, n=12):
    for _ in range(n):
        app.processEvents()


def _fake_snap(rows, cursor=(0, 0)):
    cells = [[(t, "default", "default", 0) for t in row] for row in rows]
    return {
        "cells": cells,
        "cursor": cursor,
        "start": 0,
        "total": len(rows),
        "rows": len(rows),
        "modes": {"app_cursor": False, "bracketed": False, "mouse": 0,
                  "sgr": False, "alt": False, "bell": False},
        "dirty": [],
    }


def _move(view, pos):
    """Synthesize a hover (mouse move, no buttons) at `pos`."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    ev = QMouseEvent(
        QEvent.MouseMove,
        QPointF(pos),
        QPointF(view.viewport().mapToGlobal(pos)),
        Qt.NoButton,
        Qt.NoButton,
        Qt.NoModifier,
    )
    view.mouseMoveEvent(ev)


def _window_palette(bar):
    role = bar.palette().ColorRole
    return bar.palette().color(role.Window).name()


def test_status_bar_names_the_focused_pane_and_theme(scene, tmp_path):
    app, w = _window(*scene)
    try:
        _settle(app)
        bar = w.statusBar()
        assert bar.isVisible()
        assert bar.height() > 0

        # Theme label mirrors the code theme and follows a switch, palette
        # included (Lace's app-wide DockThemeBridge themes standard widgets).
        assert w._status_theme.text() == f"[{w.bridge.core.theme()}]"
        chrome_before = _window_palette(bar)
        w.apply_lace_theme("kilim_light")
        _settle(app)
        assert w._status_theme.text() == "[Kilim Light]"
        assert _window_palette(bar) != chrome_before

        # Terminal pane label, then a viewer dock takes focus.
        term = next(iter(w.term_panes.values()))
        term._poller.stop()
        term.view.setFocus()
        _settle(app)
        assert w._status_pane.text().startswith(f"Terminal {MIDDLE_DOT}")
        assert "exited" not in w._status_pane.text()

        src = tmp_path / "sample.py"
        src.write_text("x = 1\n", encoding="utf-8")
        w.open_in_viewer(str(src), 1, "code")
        _settle(app)
        assert w._status_pane.text().startswith(f"File {MIDDLE_DOT}")
        assert w._status_pane.toolTip() == str(src)
    finally:
        w.close()
        app.processEvents()


def test_status_bar_is_laid_out_and_has_no_resize_grip(scene):
    """No QSizeGrip in the corner, and the bar stays full-width/bottom-flush
    after every resize — QMainWindow lays it out, so Kilim carries neither a
    grip nor a resizeEvent / eventFilter for it."""
    from PySide6.QtWidgets import QSizeGrip

    app, w = _window(*scene)
    try:
        _settle(app)
        bar = w.statusBar()
        assert bar.isSizeGripEnabled() is False
        assert bar.findChild(QSizeGrip) is None
        # 5px breathing room on both sides: message text starts 5px in, the
        # permanent theme label ends 5px from the right edge.
        w._set_status_message("x")
        _settle(app, 4)
        msg = w._status_message
        assert msg.geometry().x() + msg.contentsMargins().left() == 5
        assert bar.width() - (w._status_theme.geometry().right() + 1) == 5
        w._set_status_message("")
        for width, height in ((1100, 700), (640, 480), (900, 620)):
            w.resize(width, height)
            _settle(app, 4)
            g = bar.geometry()
            assert g.width() == w.width(), (width, height, g)
            assert g.y() + g.height() == w.height(), (width, height, g)
            assert bar.isVisible()
    finally:
        w.close()
        app.processEvents()


def test_relative_paths_resolve_against_snapshot_cwd(scene, tmp_path):
    """Relative hits resolve where the shell is, not where the app is."""
    src = tmp_path / "m.py"
    src.write_text("x = 1\n", encoding="utf-8")
    app, w = _window(*scene)
    try:
        _settle(app)
        term = next(iter(w.term_panes.values()))
        term._poller.stop()
        # Snapshot says the shell lives in tmp: m.py linkifies with :line.
        snap = _fake_snap(["see m.py:2 here"])
        snap["cwd"] = str(tmp_path)
        term._render(snap)
        assert term._link_cwd == str(tmp_path)
        (hit,) = term._links_for_text("see m.py:2 here")
        _s, _e, path, line, _col, kind = hit
        assert kind == "code" and line == 2 and path.endswith("m.py")
        # Same text with no snapshot cwd falls back to the app dir, where
        # m.py does not exist: no link (yesterday's miss, by design now).
        snap2 = _fake_snap(["see m.py:2 here"])
        term._render(snap2)
        assert term._link_cwd is None
        assert term._links_for_text("see m.py:2 here") == []
    finally:
        w.close()
        app.processEvents()


def test_status_bar_hover_follows_the_link_under_the_mouse(scene, tmp_path):
    """A real mouse move over a path shows the target; moving off clears it."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QTextCursor

    app, w = _window(*scene)
    try:
        _settle(app)
        term = next(iter(w.term_panes.values()))
        term._poller.stop()
        src = tmp_path / "hovered.py"
        src.write_text("x = 1\n", encoding="utf-8")
        term._render(_fake_snap([["see", " " + str(src) + ":", "1"]]))
        bar = w.statusBar()
        assert w._status_message.text() == ""

        doc = term.view.document()
        block = doc.findBlockByNumber(0)
        cur = QTextCursor(doc)
        cur.setPosition(block.position() + len("see "))
        rect = term.view.cursorRect(cur)
        _move(term.view, QPoint(rect.left() + 2, rect.center().y()))
        assert w._status_message.text() == f"Open {src}"

        # Off the link — back over the plain text before it — it clears again.
        _move(term.view, QPoint(2, rect.center().y()))
        assert w._status_message.text() == ""
    finally:
        w.close()
        app.processEvents()

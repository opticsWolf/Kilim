"""Themes → Code → Blend: tool-painted color blocks mix into the theme."""

import json
import shutil
from pathlib import Path

import pytest

PySide6 = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("lace")


@pytest.fixture()
def scene(tmp_path):
    """Hermetic (layout, sidecar) pair — Blend persists to the layout."""
    layout = tmp_path / "l.json"
    shutil.copy("layouts/default.json", layout)
    return str(layout), str(tmp_path / "l.perspective.json")


def _window(layout, sidecar=None):
    from PySide6.QtWidgets import QApplication

    from kilim.qt_app import KilimWindow

    app = QApplication.instance() or QApplication([])
    w = KilimWindow(layout, sidecar)
    w.show()
    return app, w


def test_blend_menu_toggles_and_persists(scene):
    """The Code submenu carries a checkable Blend; layout keeps the flag."""
    layout, sidecar = scene
    app, w = _window(layout, sidecar)
    try:
        term = next(iter(w.term_panes.values()))
        assert term._blend == 0.0
        act = w._blend_action
        assert act.text() == "Blend"
        assert act.isCheckable() and not act.isChecked()
        act.trigger()  # what a user click does
        app.processEvents()
        assert w.bridge.core.code_blend() is True
        assert act.isChecked()
        assert term._blend == pytest.approx(0.5)
        saved = json.loads(Path(layout).read_text(encoding="utf-8"))
        assert saved["layout"]["code_blend"] is True
    finally:
        w.close()
        app.processEvents()

    # A fresh window on the same layout picks the flag up (TUI reads it too).
    app2, w2 = _window(layout, sidecar)
    try:
        assert w2.bridge.core.code_blend() is True
        assert next(iter(w2.term_panes.values()))._blend == pytest.approx(0.5)
        assert w2._blend_action.isChecked()
        # Blend sits outside the theme radio group: a theme switch keeps it.
        w2.apply_code_theme("Kilim Dark")
        assert w2.bridge.core.code_blend() is True
        assert w2._blend_action.isChecked()
    finally:
        w2.close()
        app.processEvents()


def test_blend_mixes_explicit_cells_only(scene):
    """`_fmt` pulls ANSI/truecolor cells toward the theme; defaults keep
    their exact theme colors, and Blend off is byte-exact passthrough."""
    app, w = _window(*scene)
    try:
        term = next(iter(w.term_panes.values()))
        term._poller.stop()
        fg0 = term.view.palette().color(term.view.foregroundRole())
        bg0 = term.view.palette().color(term.view.backgroundRole())

        fmt = term._fmt(("#ffffff", "#000000", 0), fg0, bg0)
        assert fmt.foreground().color().name() == "#ffffff"
        assert fmt.background().color().name() == "#000000"

        term._blend = 0.5
        fmt = term._fmt(("#ffffff", "#000000", 0), fg0, bg0)
        f = fmt.foreground().color().name()
        b = fmt.background().color().name()
        assert f != "#ffffff" and b != "#000000", "explicit colors not blended"

        fmt = term._fmt(("default", "default", 0), fg0, bg0)
        assert fmt.foreground().color().name() == fg0.name()
        assert fmt.background().color().name() == bg0.name()

        # Reverse blocks blend on their swapped sides too.
        fmt = term._fmt(("#ffffff", "#000000", 1 << 5), fg0, bg0)
        assert fmt.foreground().color().name() != "#000000"
        assert fmt.background().color().name() != "#ffffff"
    finally:
        w.close()
        app.processEvents()

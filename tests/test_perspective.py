"""Perspective round-trip tests — offscreen Qt, offline safe."""

import json

import pytest

from kilim.perspective import apply, capture, layout_groups, load, save

PySide6 = pytest.importorskip("PySide6.QtWidgets")
Lace = pytest.importorskip("lace")


def test_layout_groups_flattens_tree():
    root = {
        "type": "split",
        "a": {"type": "pane", "pane_id": "t"},
        "b": {
            "type": "tabs",
            "tabs": [{"pane_id": "c"}, {"pane_id": "n"}],
        },
    }
    assert layout_groups(root) == [["t"], ["c", "n"]]


def test_capture_apply_roundtrip(tmp_path):
    from PySide6.QtWidgets import QApplication

    from kilim.qt_app import KilimWindow

    app = QApplication.instance() or QApplication([])
    w = KilimWindow("layouts/default.json")
    w.show()
    app.processEvents()
    snap = capture(w)
    flat = sorted(p for g in snap["kilim"]["tab_groups"] for p in g)
    assert flat == ["code", "notes", "term1"]
    assert snap["lace"], "expected opaque Lace geometry blob"
    sidecar = tmp_path / "rt.perspective.json"
    save(sidecar, snap)
    assert load(sidecar)["kilim"]["tab_groups"] == snap["kilim"]["tab_groups"]

    w2 = KilimWindow("layouts/default.json")
    w2.show()
    app.processEvents()
    assert apply(w2, load(sidecar)) is True
    snap2 = capture(w2)
    assert sorted(p for g in snap2["kilim"]["tab_groups"] for p in g) == flat
    w.close()
    w2.close()
    app.processEvents()


def test_apply_rejects_diverged_names(tmp_path):
    from PySide6.QtWidgets import QApplication

    from kilim.qt_app import KilimWindow

    app = QApplication.instance() or QApplication([])
    w = KilimWindow("layouts/default.json")
    w.show()
    app.processEvents()
    bad = {"version": 1, "kilim": {"tab_groups": [["ghost"]], "active": "ghost"}, "lace": "{}"}
    assert apply(w, bad) is False
    w.close()
    app.processEvents()

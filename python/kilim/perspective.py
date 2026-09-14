"""Perspective round-trip (v0.1.6): layout.json ⇄ Lace dock state.

Sidecar file (`<layout>.perspective.json`) holds two layers:
- `kilim`: readable tab groups + active pane (debuggable, TUI-inspectable).
- `lace`: opaque `DockManager.save_state()` blob for exact geometry.

Launch: build docks from layout.json (+ tab groups), then `apply()` the
sidecar if widget names still match. Close: `capture()` + save.
"""

from __future__ import annotations

import json
from pathlib import Path

VERSION = 1


def layout_groups(node: dict) -> list[list[str]]:
    """Flatten a layout `root` node into ordered tab groups.

    Split -> concatenate children; Tabs -> one group; Pane -> singleton.
    """
    t = node.get("type")
    if t == "pane":
        return [[node["pane_id"]]]
    if t == "tabs":
        return [[t2["pane_id"] for t2 in node.get("tabs", [])]]
    if t == "split":
        return layout_groups(node["a"]) + layout_groups(node["b"])
    return []


def capture(window) -> dict:
    """Snapshot the live window: tab groups, active pane, Lace blob."""
    manager = window.manager
    seen_areas: dict[int, list] = {}
    order: list[int] = []
    for dock in manager._dock_widgets_map.values():
        area = dock._dock_area
        key = id(area) if area is not None else -1
        if key not in seen_areas:
            seen_areas[key] = []
            order.append(key)
        seen_areas[key].append(dock)
    groups: list[list[str]] = []
    for key in order:
        docks = seen_areas[key]
        if len(docks) > 1 and docks[0]._dock_area is not None:
            try:
                docks = sorted(docks, key=lambda d: d._dock_area.index(d))
            except (ValueError, AttributeError):
                pass
        groups.append([window.pane_of(d) for d in docks])
    try:
        lace = manager.save_state()
    except Exception:  # noqa: BLE001 — geometry best-effort
        lace = ""
    return {
        "version": VERSION,
        "kilim": {"tab_groups": groups, "active": window.session_active()},
        "lace": lace,
    }


def save(path: str | Path, data: dict) -> None:
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")


def load(path: str | Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if data.get("version") == VERSION else None


def apply(window, data: dict) -> bool:
    """Restore exact Lace geometry. False = names diverged, keep creation layout."""
    if not data or not data.get("lace"):
        return False
    try:
        blob_names = {w for g in data["kilim"]["tab_groups"] for w in g}
        # objectNames are pane ids (KilimWindow sets them), so this is exact.
        live = {d.objectName() for d in window.manager._dock_widgets_map.values()}
        if not blob_names <= live:
            return False
        return bool(window.manager.restore_state(data["lace"]))
    except Exception:  # noqa: BLE001 — stale sidecar must never break launch
        return False


def sidecar_for(layout_path: str | Path) -> Path:
    p = Path(layout_path)
    return p.with_name(p.stem + ".perspective.json")

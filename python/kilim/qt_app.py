"""Kilim Qt frontend (v0.1.1): layout.json hosted in Lace docks.

Same CoreSession as the TUI — no wheel, no second PTY stack.
Asyncio lives on a background thread; the GUI thread never blocks:
polls are submitted as coroutines and applied when done.

Run:  uv run python -m kilim.qt_app layouts/default.json
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QApplication, QHBoxLayout, QPlainTextEdit, QScrollBar, QTextBrowser, QVBoxLayout, QWidget

from lace.dock_styled import DockStyled
from lace.dock_theme import DockStyleCategory
from lace.frameless_window import FramelessLaceMainWindow, LaceStandardTitleBar

from kilim import CoreSession
from kilim import perspective as perspectives
from kilim.perspective import layout_groups

ANSI = {
    "black": (0, 0, 0), "red": (170, 0, 0), "green": (0, 170, 0),
    "yellow": (170, 85, 0), "blue": (0, 0, 170), "magenta": (170, 0, 170),
    "cyan": (0, 170, 170), "white": (170, 170, 170),
    "brightblack": (85, 85, 85), "gray": (85, 85, 85), "grey": (85, 85, 85),
    "brightred": (255, 85, 85), "brightgreen": (85, 255, 85),
    "brightyellow": (255, 255, 85), "brightblue": (85, 85, 255),
    "brightmagenta": (255, 85, 255), "brightcyan": (85, 255, 255),
    "brightwhite": (255, 255, 255),
}


def cell_qcolor(s: str, default: QColor) -> QColor:
    t = (s or "").strip().lower()
    if not t or t == "default":
        return QColor(default)
    if t in ANSI:
        r, g, b = ANSI[t]
        return QColor(r, g, b)
    h = t.lstrip("#")
    if len(h) == 6:
        try:
            return QColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except ValueError:
            pass
    return QColor(default)


# ── Kilim core themes: shared Lace chassis, Rust-owned palette ──
# All four Kilim Lace themes share one geometry (the Lace `dark` chassis);
# only colors differ. Palettes live in kilim-core (`kilim_themes_json`),
# so Rust and Python can never disagree on a hex value.
KILIM_GEOMETRY = dict(
    corner_radius=4,
    tab_radius=4,
    border_width=1.5,
    title_margin=0.5,
    content_margin=0.5,
    tab_dimming=True,
    title_mode="darker",
    hover_mode="darker",
)

# Second chassis: the neo/edge mix. Card values are identical in both
# sources (10px card, 1.5px outline, 32px title bar, flush tabs); tabs take
# neo's shape at edge's measure (8px tops, 3px gaps, 1.5px bottom
# indicator so it can't step the title rule); the rule itself plus
# ring-every-tab sidebar are the edge signature, recolored per palette.
KILIM_NEO_GEOMETRY = dict(
    corner_radius=10,
    border_width=1.5,
    title_height=32,
    title_padding_left=0,
    title_padding_right=8,
    title_button_spacing=6,
    title_margin=0,
    tab_radius=8,
    tab_margin=3,
    content_margin=(8, 2),
    indicator_width=1.5,
    indicator_position="bottom",
    tab_dimming=True,
    title_mode="darker",
    hover_mode="lighter",
    title_border_bottom=1.5,
    sidebar_tab_flat_edge="none",
    sidebar_tab_border_width=1.5,
    sidebar_indicator_width=1.5,
)


def _hex_to_rgba(s: str) -> list[int]:
    h = (s or "").strip().lstrip("#")
    if len(h) == 6:
        try:
            return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255]
        except ValueError:
            pass
    return [255, 0, 255, 255]  # magenta = corrupt palette, never silent


def kilim_theme_defs() -> list[dict]:
    """The four unified themes (palette + keys), Rust as source of truth."""
    import json as _json

    from kilim import kilim_themes_json

    try:
        defs = _json.loads(kilim_themes_json())
    except (ValueError, ImportError):
        return []
    return defs if isinstance(defs, list) else []


def _normalize_lace_key(key: str) -> str:
    """Forward legacy Kilim Lace keys to the unified names.

    `kilim_neo_<pal>` (v0.1.34–35) and `kilim_neon_<pal>` (≤v0.1.33) both
    became `kilim_<pal>_neo`, whose label renders the syntect name.
    Sidecars saved under old names keep working."""
    for pal in ("dark", "neutral", "light", "warm"):
        if key in (f"kilim_neo_{pal}", f"kilim_neon_{pal}"):
            return f"kilim_{pal}_neo"
    return key


def register_kilim_lace_themes() -> dict[str, str]:
    """Build the Kilim Lace themes into Lace's registries.

    One "Kilim" group, eight entries: four palettes × classic chassis
    (`kilim_*`) + neo/edge chassis (`kilim_*_neo`). Lace labels render
    the syntect names ("Kilim Dark Neo"), so all three menus share one
    vocabulary. Returns {lace_key: syntect_name} for unified apply —
    neo maps to neo. Idempotent — safe across multiple windows/tests
    in one process.
    """
    from lace import dock_custom_theme as _dct
    from lace.dock_theme import ThemeSpec, build_theme

    def _spec(d, geometry, extra=None):
        kw = dict(
            base=_hex_to_rgba(d["bg"]),
            accent=_hex_to_rgba(d["accent"]),
            text=_hex_to_rgba(d["text"]),
            surface=_hex_to_rgba(d.get("surface", d["bg"])),
            border=_hex_to_rgba(d.get("border", d["bg"])),
            focus_border_color=_hex_to_rgba(d["accent"]),
            is_light=bool(d.get("is_light", False)),
            **geometry,
        )
        kw.update(extra or {})
        return ThemeSpec(**kw)

    def _per_palette_neo(d):
        # Edge rule + sidebar rings follow the palette accent.
        accent = _hex_to_rgba(d["accent"])
        return dict(
            title_border_focus_color=accent,
            sidebar_tab_border_color=_hex_to_rgba(d.get("border", d["bg"])),
            sidebar_tab_border_active_color=accent,
            sidebar_tab_border_hover_color=accent,
        )

    mapping: dict[str, str] = {}
    for d in kilim_theme_defs():
        key, syntect = d["lace_key"], d["syntect"]
        mapping[key] = syntect
        if key not in _dct.DOCK_THEMES:
            _dct.DOCK_THEMES[key] = build_theme(_spec(d, KILIM_GEOMETRY))
        neo_key = d["neo_key"]
        mapping[neo_key] = d["neo_syntect"]  # neo chrome → neo code/md
        if neo_key not in _dct.DOCK_THEMES:
            _dct.DOCK_THEMES[neo_key] = build_theme(
                _spec(d, KILIM_NEO_GEOMETRY, _per_palette_neo(d))
            )
    groups = _dct.THEME_GROUPS
    if "Kilim" not in groups:
        keys: list[str] = []
        for d in kilim_theme_defs():
            keys.append(d["lace_key"])
            keys.append(d["neo_key"])
        groups["Kilim"] = tuple(keys)
        groups.move_to_end("Kilim", last=False)
    else:
        # Rebuild from current defs: drops stale keys from older
        # installs (e.g. `kilim_neon_*`) instead of carrying them.
        keys = []
        for d in kilim_theme_defs():
            keys.append(d["lace_key"])
            keys.append(d["neo_key"])
        groups["Kilim"] = tuple(keys)
        groups.pop("Kilim Neon", None)
    # Keep the stock "default" look under Basics: theme_groups() files
    # ungrouped keys into the FIRST group, which is now ours.
    if "Basics" in groups and "default" not in groups["Basics"]:
        groups["Basics"] = ("default",) + tuple(groups["Basics"])
    return mapping


class Bridge:
    """CoreSession + background asyncio loop. GUI thread calls submit()/call().

    All core coroutines are *created* on the background loop (inside driver),
    because `future_into_py` requires a running loop in the creating thread.
    Call sites always pass a zero-arg lambda, never a pre-made coroutine.
    """

    def __init__(self, layout_doc: str):
        self.core = CoreSession(layout_doc)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, make_coro) -> concurrent.futures.Future:
        out: concurrent.futures.Future = concurrent.futures.Future()

        async def driver():
            try:
                out.set_result(await make_coro())
            except BaseException as e:  # noqa: BLE001
                out.set_exception(e)

        asyncio.run_coroutine_threadsafe(driver(), self.loop)
        return out

    def call(self, make_coro, timeout: float = 15.0):
        return self.submit(make_coro).result(timeout)

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)


def _char_width(ch: str) -> int:
    """Terminal cell width of one character (approximation: W/F = 2,
    combining = 0, else 1). Qt lays out by glyphs; the PTY counts cells —
    this maps between the two for cursor/selection placement."""
    import unicodedata

    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _term_col_to_char(text: str, col: int) -> int:
    """Terminal cell column -> Qt character offset within the line."""
    ci = ti = 0
    while ti < col and ci < len(text):
        ti += _char_width(text[ci])
        ci += 1
    return ci


def _char_to_term_col(text: str, ci: int) -> int:
    """Qt character offset -> terminal cell column."""
    return sum(_char_width(c) for c in text[:ci])


def _sgr_mouse(btn: int, col: int, row: int, press: bool) -> bytes:
    """SGR (1006) mouse report. col/row are 1-based terminal cells."""
    return f"\x1b[<{btn};{col};{row}{'M' if press else 'm'}".encode("ascii")


def _x10_mouse(btn: int, col: int, row: int) -> bytes:
    """Legacy X10 mouse report (no release event exists here)."""
    col = min(max(col, 1), 223)
    row = min(max(row, 1), 223)
    return bytes((0x1B, ord('M'), 32 + btn, 32 + col, 32 + row))


def _modes_dict(m) -> dict:
    """Snapshot modes 6-tuple -> named dict (stable defaults)."""
    try:
        app_cursor, bracketed, mouse, sgr, alt, bell = m
    except (TypeError, ValueError):
        try:
            app_cursor, bracketed, mouse, sgr, alt = m
        except (TypeError, ValueError):
            app_cursor, bracketed, mouse, sgr, alt = (False, False, 0, False, False)
        bell = False
    return {
        "app_cursor": bool(app_cursor),
        "bracketed": bool(bracketed),
        "mouse": int(mouse or 0),
        "sgr": bool(sgr),
        "alt": bool(alt),
        "bell": bool(bell),
    }


def _arrow_seq(key, app_cursor: bool) -> bytes | None:
    """Cursor-key bytes: SS3 (\\x1bO) in application mode, CSI otherwise."""
    from PySide6.QtCore import Qt

    table = {
        Qt.Key_Up: (b"\x1b[A", b"\x1bOA"),
        Qt.Key_Down: (b"\x1b[B", b"\x1bOB"),
        Qt.Key_Right: (b"\x1b[C", b"\x1bOC"),
        Qt.Key_Left: (b"\x1b[D", b"\x1bOD"),
        Qt.Key_Home: (b"\x1b[H", b"\x1bOH"),
        Qt.Key_End: (b"\x1b[F", b"\x1bOF"),
    }
    pair = table.get(key)
    return pair[1] if pair and app_cursor else (pair[0] if pair else None)


class _TermView(QPlainTextEdit):
    """Paint surface. Forwards gestures to the owning TerminalPane."""

    def __init__(self, owner: "TerminalPane"):
        super().__init__()
        self._owner = owner
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setFont(QFont("Cascadia Mono", 10))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def keyPressEvent(self, e):
        self._owner.on_key(e)

    def wheelEvent(self, e):
        self._owner.on_wheel(e)

    def mousePressEvent(self, e):
        if self._owner._send_mouse("press", e):
            return  # app owns the mouse (tracking mode): no selection
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._owner._send_mouse("move", e):
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._owner._mouse_active():
            self._owner._send_mouse("release", e)
            return
        super().mouseReleaseEvent(e)
        self._owner._capture_selection()  # persist drag, anchored to history

    def contextMenuEvent(self, e):
        self._owner.on_context(e.globalPos())


class TerminalPane(QWidget):
    """Live term pane: polls term_range(), forwards keys.

    Scrollback lives on a dedicated external QScrollBar addressing absolute
    history lines (bottom = live tail). The built-in bar stays off: Qt
    recomputes it from the (one-viewport) document on every layout and
    would collapse our range. Wheel/drag sets an offset; any keypress
    snaps back to follow mode.
    """

    def __init__(self, bridge: Bridge, pane_id: str):
        super().__init__()
        self.bridge = bridge
        self.pane_id = pane_id
        self.view = _TermView(self)
        self.bar = QScrollBar(Qt.Vertical)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.bar, 0)
        self._pending: concurrent.futures.Future | None = None
        self._resize_pending: tuple[int, int] | None = None
        self._theme_name: str | None = None  # themed palette tracker
        self._start = 0  # first history line currently shown
        self._total = 0
        self._follow = True
        self.bar.actionTriggered.connect(self._on_scroll_action)
        self._last_sig = None  # (total, cursor, start, rows) — idle-skip
        self._render_count = 0  # frames actually painted (tests/perf)
        self._paint_rows = -1  # viewport rows of the current document
        self._paint_start = None  # history offset of row 0 (scrolls full-repaint)
        self._cursor_at = None  # absolute (x, y) of the painted cursor
        self._sel = None  # selection in absolute history coords (or None)
        self._modes = _modes_dict(None)  # DEC modes from latest snapshot
        self._bell_until = 0.0  # monotonic deadline of the bell flash
        self._seen_total = None  # activity-badge baseline
        self._set_badge = lambda on: None  # wired by the window (tab dot)
        self._wheel_accum = 0  # fractional scroll accumulation (smooth pads)
        self._poller = QTimer(self)
        self._poller.timeout.connect(self._poll)
        self._poller.start(60)

    # ── poll ──
    def _poll(self):
        # Resize first: a slow snapshot must never stall dimension sync
        # (regression: resizes piled up unprocessed while snapshots lagged).
        rows, cols = self._grid()
        if self._resize_pending != (rows, cols):
            self._resize_pending = (rows, cols)
            fut = self.bridge.submit(lambda: self.bridge.core.resize_term(self.pane_id, rows, cols))
            fut.add_done_callback(lambda f, grid=(rows, cols): self._on_resized(f, grid))
        if self._pending is not None and not self._pending.done():
            return
        if self._pending is not None:
            try:
                self._render(self._pending.result())
            except Exception:
                pass
            self._pending = None
        self._pending = self.bridge.submit(lambda: self._snapshot(rows))

    def _on_resized(self, fut, grid):
        """Resize completion: a lost resize (term respawning, pty
        hiccup) clears the pending mark so the next poll retries
        instead of sticking at the wrong dimensions. Stale grids
        (superseded by a newer resize) are ignored."""
        try:
            fut.result()
        except Exception:
            if self._resize_pending == grid:
                self._resize_pending = None

    async def _snapshot(self, rows: int):
        # Alt screen has no scrollback: always track the tail there.
        anchor = None if (self._follow or self._modes.get("alt")) else self._start
        total, start, cells, cursor, modes, dirty = await self.bridge.core.snapshot_term(
            self.pane_id, rows, anchor
        )
        return {
            "cells": cells,
            "cursor": cursor,
            "start": start,
            "total": total,
            "rows": rows,
            "modes": _modes_dict(modes),
            "dirty": [int(r) for r in (dirty or [])],
        }

    def _grid(self) -> tuple[int, int]:
        fm = self.view.fontMetrics()
        # Clamped: a pathological viewport (broken restore, overlay
        # sizing) must never ask the pty for a gigantic screen.
        cols = max(20, self.view.viewport().width() // max(1, fm.horizontalAdvance("M")))
        rows = max(5, self.view.viewport().height() // max(1, fm.lineSpacing()))
        return (min(400, rows), min(800, cols))

    def _ensure_theme(self):
        """Track the session code theme into the widget palette.

        Default-fg/bg cells resolve through the palette, so theming it
        recolors the whole shell view (background included) and clearing
        the paint signature forces a rebuild on switch. Explicit shell
        colors pass through untouched."""
        from PySide6.QtGui import QPalette

        from kilim import theme_background, theme_foreground

        name = self.bridge.core.theme()
        if name == self._theme_name:
            return
        self._theme_name = name
        pal = self.view.palette()
        if bg := theme_background(name):
            pal.setColor(self.view.backgroundRole(), QColor(bg))
            pal.setColor(QPalette.Base, QColor(bg))
        if fg := theme_foreground(name):
            pal.setColor(self.view.foregroundRole(), QColor(fg))
            pal.setColor(QPalette.Text, QColor(fg))
        self.view.setPalette(pal)
        self._last_sig = None  # repaint with the new mapping
        self._paint_rows = -1  # explicit shell colors are baked: full rebuild

    def _render(self, snap):
        self._ensure_theme()
        self._modes = snap.get("modes") or _modes_dict(None)
        if self._modes.get("bell"):
            import time

            self._bell_until = time.monotonic() + 1.5  # visible dot flash
        # Selection round-trips through absolute history coords every
        # paint: capture Qt's selection BEFORE touching the document so
        # output can keep flowing underneath it (no freeze).
        self._capture_selection()
        sig = (snap["total"], snap["cursor"], snap["start"], snap["rows"])
        if sig != self._last_sig:
            self._last_sig = sig
            self._start = snap["start"]
            self._total = snap["total"]
            bar = self.bar
            top = max(0, self._total - snap["rows"])
            bar.blockSignals(True)
            try:
                bar.setRange(0, top)
                bar.setPageStep(snap["rows"])
                if not bar.isSliderDown():  # never fight an active drag
                    bar.setValue(snap["start"])
            finally:
                bar.blockSignals(False)
            self._paint(snap)
        elif snap.get("dirty"):
            # Same viewport, touched rows (progress bars, spinners): the
            # old sig-gate went stale here — paint the dirty set.
            self._paint(snap)
        self._cursor_to(snap["cursor"], snap["start"], snap["rows"])
        self._reapply_selection()
        self._badges(snap)
        self.view.viewport().setMouseTracking(self._modes.get("mouse") == 1003)
    def _paint(self, snap):
        """Full rebuild or dirty-subset repaint (stitch-pty dirty rows).

        Full when the viewport shape or history offset changed (resize,
        scroll, first paint, theme switch); otherwise only the rows the
        screen reports touched. No content comparison: the screen is the
        source of truth, our old row-diff was re-deriving it."""
        fg0 = self.view.palette().color(self.view.foregroundRole())
        bg0 = self.view.palette().color(self.view.backgroundRole())
        rows = snap["cells"]
        lo, n = snap["start"], len(rows)
        if self._paint_rows == snap["rows"] and self._paint_start == snap["start"]:
            vis = sorted(r - lo for r in snap.get("dirty") or [] if lo <= r < lo + n)
            if vis:
                self._paint_subset(rows, vis, fg0, bg0)
        else:
            self._paint_full(rows, fg0, bg0)
            self._paint_rows = snap["rows"]
            self._paint_start = snap["start"]

    def _paint_full(self, rows, fg0, bg0):
        """Wipe + rebuild (first paint, resize, theme switch)."""
        self._render_count += 1
        self.view.clear()
        cur = self.view.textCursor()
        cur.beginEditBlock()  # one layout pass for the whole rebuild
        try:
            for row in rows:
                self._insert_runs(cur, row, fg0, bg0)
                cur.insertBlock()
        finally:
            cur.endEditBlock()
        self._cursor_at = None  # document is new: reposition unconditionally

    def _paint_subset(self, rows, indices, fg0, bg0):
        """Rewrite exactly the given visible rows (dirty set)."""
        doc = self.view.document()
        cur = QTextCursor(doc)
        cur.beginEditBlock()
        try:
            touched = False
            for i in indices:
                if not (0 <= i < len(rows)):
                    continue
                block = doc.findBlockByNumber(i)
                if not block.isValid():
                    continue
                cur.setPosition(block.position())
                cur.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
                self._insert_runs(cur, rows[i], fg0, bg0)
                touched = True
        finally:
            cur.endEditBlock()
        if touched:
            self._render_count += 1

    def _insert_runs(self, cur, row, fg0, bg0):
        """Insert one viewport row, merging adjacent same-style cells."""
        run_text: list[str] = []
        run_key = None
        for text, fg, bg, attrs in row:
            key = (fg, bg, attrs)
            if key != run_key:
                if run_text:
                    cur.insertText("".join(run_text), self._fmt(run_key, fg0, bg0))
                    run_text = []
                run_key = key
            run_text.append(text if not (attrs & (1 << 6)) else " ")
        if run_text:
            cur.insertText("".join(run_text), self._fmt(run_key, fg0, bg0))

    def _cursor_to(self, cursor, start, nrows):
        """Reposition only on move: untouched polls keep Qt's native blink."""
        if tuple(cursor) == self._cursor_at:
            return
        self._cursor_at = tuple(cursor)
        if QApplication.mouseButtons() != Qt.NoButton:
            return  # live drag: don't yank the selection anchor
        cx, cy_abs = cursor
        if not (start <= cy_abs < start + nrows):
            return  # scrolled back: cursor out of view, leave it
        doc = self.view.document()
        vis = cy_abs - start
        if not (0 <= vis < doc.blockCount()):
            return
        block = doc.findBlockByNumber(vis)
        ci = _term_col_to_char(block.text(), cx)
        c = QTextCursor(doc)
        c.setPosition(block.position() + min(ci, len(block.text())))
        self.view.setTextCursor(c)

    def _capture_selection(self):
        """Qt selection -> absolute history coords (survives repaints)."""
        cur = self.view.textCursor()
        if not cur.hasSelection():
            return
        doc = self.view.document()
        s, e = cur.selectionStart(), cur.selectionEnd()
        sb, eb = doc.findBlock(s), doc.findBlock(e)
        a = (self._start + sb.blockNumber(),
             _char_to_term_col(sb.text(), s - sb.position()))
        b = (self._start + eb.blockNumber(),
             _char_to_term_col(eb.text(), e - eb.position()))
        self._sel = (a, b) if a <= b else (b, a)

    def _reapply_selection(self):
        """Absolute coords -> Qt selection (clamped to the viewport)."""
        if self._sel is None:
            return
        if QApplication.mouseButtons() != Qt.NoButton:
            return  # live drag: Qt owns the in-progress selection
        (a_line, a_col), (b_line, b_col) = self._sel
        doc = self.view.document()
        lo, hi = self._start, self._start + max(self._paint_rows, 0)
        if b_line < lo or a_line >= hi:
            if self.view.textCursor().hasSelection():  # scrolled out: drop Qt's, keep _sel
                self.view.setTextCursor(QTextCursor(doc))
            return
        a_line, b_line = max(a_line, lo), min(b_line, hi - 1)
        ab, eb = doc.findBlockByNumber(a_line - self._start), doc.findBlockByNumber(b_line - self._start)
        if not (ab.isValid() and eb.isValid()):
            return
        a_pos = ab.position() + min(_term_col_to_char(ab.text(), a_col), len(ab.text()))
        b_pos = eb.position() + min(_term_col_to_char(eb.text(), b_col), len(eb.text()))
        c = QTextCursor(doc)
        c.setPosition(a_pos)
        c.setPosition(b_pos, QTextCursor.KeepAnchor)
        self.view.setTextCursor(c)

    def _mouse_active(self) -> bool:
        """App owns the mouse (tracking mode 1000/1002/1003)."""
        return self._modes.get("mouse", 0) != 0

    def _mouse_cell(self, pos) -> tuple[int, int]:
        """Viewport position -> 1-based (col, row) terminal cells."""
        cur = self.view.cursorForPosition(pos)
        block = cur.block()
        col = _char_to_term_col(block.text(), cur.position() - block.position()) + 1
        return (max(col, 1), block.blockNumber() + 1)

    def _send_mouse(self, kind, e) -> bool:
        """Report mouse to the app (SGR 1006, X10 fallback). True = eaten."""
        proto = self._modes.get("mouse", 0)
        if proto == 0:
            return False
        if kind == "move":
            if proto == 1000:
                return False
            if proto == 1002 and e.buttons() == Qt.NoButton:
                return False  # 1002 reports drags only; 1003 reports all motion
        mod = 0
        m = e.modifiers()
        if m & Qt.ShiftModifier:
            mod |= 4
        if m & Qt.AltModifier:
            mod |= 8
        if m & Qt.ControlModifier:
            mod |= 16
        col, row = self._mouse_cell(e.pos())
        if kind == "press":
            btn = {Qt.LeftButton: 0, Qt.MiddleButton: 1, Qt.RightButton: 2}.get(e.button(), 3)
            press, b = True, btn + mod
        elif kind == "release":
            press, b = False, 3 + mod
        else:  # move
            held = e.buttons()
            if held & Qt.LeftButton:
                b = 0
            elif held & Qt.MiddleButton:
                b = 1
            elif held & Qt.RightButton:
                b = 2
            else:
                b = 3  # 1003 hover motion
            press, b = True, 32 + b + mod
        if self._modes.get("sgr"):
            data = _sgr_mouse(b, col, row, press)
        else:
            data = _x10_mouse(b, col, row)
        self.bridge.submit(lambda: self.bridge.core.write_term(self.pane_id, data))
        return True

    def _send_wheel_mouse(self, pos, delta_y: int, modifiers) -> None:
        """Wheel report at the event position (tracking apps scroll)."""
        mod = 0
        if modifiers & Qt.ShiftModifier:
            mod |= 4
        if modifiers & Qt.AltModifier:
            mod |= 8
        if modifiers & Qt.ControlModifier:
            mod |= 16
        btn = (64 if delta_y > 0 else 65) + mod
        col, row = self._mouse_cell(pos)
        data = _sgr_mouse(btn, col, row, True) if self._modes.get("sgr") else _x10_mouse(btn, col, row)
        self.bridge.submit(lambda: self.bridge.core.write_term(self.pane_id, data))

    def _badges(self, snap):
        """Dot the tab on hidden output or a recent bell (1.5s flash)."""
        import time

        total = snap["total"]
        if self._seen_total is None:
            self._seen_total = total
        elif self.view.isVisible():
            self._seen_total = total
        if time.monotonic() < self._bell_until:
            self._set_badge(True)  # bell flash shows even when visible
        elif self.view.isVisible():
            self._set_badge(False)
        elif total != self._seen_total:
            self._set_badge(True)

    @staticmethod
    def _fmt(key, fg0, bg0) -> QTextCharFormat:
        fg, bg, attrs = key
        fmt = QTextCharFormat()
        fgc, bgc = cell_qcolor(fg, fg0), cell_qcolor(bg, bg0)
        if attrs & (1 << 5):  # reverse
            fgc, bgc = bgc, fgc
        fmt.setForeground(fgc)
        fmt.setBackground(bgc)
        if attrs & (1 << 0):
            fmt.setFontWeight(QFont.Bold)
        if attrs & (1 << 2):
            fmt.setFontItalic(True)
        if attrs & (1 << 3):
            fmt.setFontUnderline(True)
        if attrs & (1 << 7):
            fmt.setFontStrikeOut(True)
        return fmt

    # ── scrollback ──
    def _on_scroll_action(self, _action: int):
        # Fires for user gestures (drag/click/wheel-on-bar/keys) only —
        # never for programmatic setValue or Qt's internal resets.
        bar = self.bar
        top = bar.maximum()
        self._start = min(max(0, bar.sliderPosition()), top)
        self._follow = self._start >= top
        self._poll()  # repaint now, don't wait for the timer

    def on_wheel(self, e):
        if self._mouse_active():
            # Tracking app owns the wheel (vim scrolls); no scrollback.
            self._send_wheel_mouse(e.pos(), e.angleDelta().y(), e.modifiers())
            e.accept()
            return
        # Route the wheel to history offset, not the (rebuilt) document.
        # One notch (120 units) = 3 lines; fractions accumulate so precision
        # touchpads still glide instead of chunking.
        self._wheel_accum += e.angleDelta().y()
        num = self._wheel_accum * 3
        lines = abs(num) // 120 * (1 if num >= 0 else -1)
        self._wheel_accum -= lines * 40
        if lines:
            if e.modifiers() & Qt.ShiftModifier:
                lines *= max(1, self._grid()[0] // 4)
            top = max(0, self._total - self._grid()[0])
            self._start = min(top, max(0, self._start - lines))
            self._follow = self._start >= top
            bar = self.bar
            bar.blockSignals(True)
            try:
                bar.setRange(0, top)
                bar.setValue(self._start)
            finally:
                bar.blockSignals(False)
            self._poll()  # repaint now, don't wait for the timer
        e.accept()

    # ── selection, anchored to absolute history (Konsole-style) ──
    # The document is rebuilt every paint, so Qt's selection would die
    # within one poll. Instead it round-trips through absolute history
    # coords (_sel) on every paint: output keeps flowing underneath and
    # the selection follows its content. Ctrl+Shift+C copies (Ctrl+C
    # stays \x03 for the shell).

    def _copy_selection(self) -> bool:
        cur = self.view.textCursor()
        if cur.hasSelection():
            QApplication.clipboard().setText(cur.selectedText())
            return True
        return False

    def on_context(self, global_pos):
        """Right-click: Copy when selecting, always Paste (terminal convention)."""
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        has_sel = self.view.textCursor().hasSelection()
        clip = QApplication.clipboard().text()
        copy_act = menu.addAction("Copy")
        copy_act.setEnabled(has_sel)
        paste_act = menu.addAction("Paste")
        paste_act.setEnabled(bool(clip))
        chosen = menu.exec(global_pos)
        if chosen == copy_act:
            if self._copy_selection():
                self.view.setTextCursor(QTextCursor(self.view.document()))
                self._sel = None
        elif chosen == paste_act:
            self._paste_from_clipboard()

    def _paste_from_clipboard(self):
        text = QApplication.clipboard().text()
        if not text:
            return None
        self._follow = True
        self._sel = None
        data = text.encode("utf-8", "replace")
        if self._modes.get("bracketed"):
            data = b"\x1b[200~" + data + b"\x1b[201~"
        return self.bridge.submit(
            lambda: self.bridge.core.write_term(self.pane_id, data)
        )

    # ── input (snaps back to live tail) ──
    def on_key(self, e):
        if e.key() == Qt.Key_Escape:
            self.view.setTextCursor(QTextCursor(self.view.document()))
            self._sel = None
            self._follow = True
            return
        if (e.modifiers() & Qt.ControlModifier) and (e.modifiers() & Qt.ShiftModifier) \
                and e.key() == Qt.Key_C:
            self._copy_selection()
            return
        data: bytes | None = None
        t = e.text()
        app_cursor = self._modes.get("app_cursor", False)
        if e.key() == Qt.Key_Return or e.key() == Qt.Key_Enter:
            data = b"\r"
        elif e.key() == Qt.Key_Backspace:
            data = b"\x7f"
        elif (seq := _arrow_seq(e.key(), app_cursor)) is not None:
            data = seq
        elif e.modifiers() & Qt.ControlModifier and t:
            data = bytes([ord(t.upper()) & 0x1F]) if len(t) == 1 else None
        elif t:
            data = t.encode("utf-8", "replace")
        if data:
            self._follow = True  # typing snaps back to the live tail
            self._sel = None  # ...and clears the selection, like a terminal
            if self.view.textCursor().hasSelection():
                self.view.setTextCursor(QTextCursor(self.view.document()))
            self.bridge.submit(lambda: self.bridge.core.write_term(self.pane_id, data))
        # read-only widget: swallow (no super() call)


class FilePane(QPlainTextEdit):
    """Highlighted file — repaintable when the code theme changes.

    Two things the naive paint gets wrong, both fixed here:
    - syntect rows arrive with trailing newlines (`LinesWithEndings`);
      inserting those AND a block break doubles every line. The break is
      stripped; blocks separate rows.
    - char formats paint behind text only. The widget Base plus every
      block background are set to the theme bg, so the pane is full-bleed
      theme color edge to edge (no widget-gray gutters or gaps).
    """

    def __init__(self, bridge: Bridge, pane_id: str):
        super().__init__()
        self.bridge = bridge
        self.pane_id = pane_id
        self.setReadOnly(True)
        self.setFont(QFont("Cascadia Mono", 10))
        self.refresh()

    def refresh(self):
        from PySide6.QtGui import QPalette, QTextBlockFormat

        from kilim import theme_background

        page_bg = theme_background(self.bridge.core.theme()) or None
        if page_bg:
            pal = self.palette()
            pal.setColor(QPalette.Base, QColor(page_bg))
            self.setPalette(pal)
            block_fmt = QTextBlockFormat()
            block_fmt.setBackground(QColor(page_bg))
        else:
            block_fmt = None
        fg0 = self.palette().color(self.foregroundRole())
        bg0 = self.palette().color(self.backgroundRole())
        self.clear()
        cur = self.textCursor()
        for i, row in enumerate(self.bridge.core.highlighted_file(self.pane_id)):
            if i > 0:
                cur.insertBlock()
            if block_fmt is not None:
                cur.setBlockFormat(block_fmt)
            spans = list(row)
            if spans:
                text, fg, bg = spans[-1]
                if text.endswith("\n"):
                    text = text[:-1]
                    if text.endswith("\r"):
                        text = text[:-1]
                    spans[-1] = (text, fg, bg)
            for text, fg, bg in spans:
                fmt = QTextCharFormat()
                fmt.setForeground(cell_qcolor(fg, fg0))
                fmt.setBackground(cell_qcolor(bg, bg0))
                cur.insertText(text, fmt)


def _fusion_scrollbar_css() -> str:
    """Sample the live style's scrollbar colors for the markdown preview.

    The preview is Chromium (QWebEngineView): Qt stylesheets can't reach
    its scrollbars, but CSS `scrollbar-color` can. Colors are sampled by
    rendering a scratch scrollbar twice (thumb top vs bottom — differing
    pixels are the thumb) so they follow the live palette/bridge theme
    instead of inventing greys. Borders and arrow buttons can't be
    expressed in CSS: flat thumb + track is the honest approximation."""
    try:
        from collections import Counter

        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QApplication, QScrollBar

        app = QApplication.instance()
        if app is None:
            return ""
        bar = QScrollBar()
        bar.resize(15, 200)
        bar.setRange(0, 100)
        bar.setPageStep(20)

        def shot(v):
            bar.setValue(v)
            img = QImage(15, 200, QImage.Format_ARGB32)
            img.fill(0)
            bar.render(img)
            return img

        top, bottom = shot(0), shot(100)
        thumb, groove = Counter(), Counter()
        same = []
        for y in range(200):
            for x in range(15):
                c = top.pixelColor(x, y).name()
                if c != bottom.pixelColor(x, y).name():
                    thumb[c] += 1
                elif 6 <= x <= 8:
                    same.append(c)  # center column: groove + button faces
        if not thumb or not same:
            return ""
        thumb_hex = thumb.most_common(1)[0][0]
        # Groove first: button faces share its color, glyphs are few
        # pixels, and the thumb color is excluded (giant thumbs can sit
        # still over the middle in both shots).
        groove.update(c for c in same if c != thumb_hex)
        track_hex = groove.most_common(1)[0][0] if groove else thumb_hex

        def luminance(hexcol):
            r, g, b = (int(hexcol[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
            lin = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
            return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

        scheme = "dark" if luminance(track_hex) < 0.4 else "light"
        return (
            f"html{{scrollbar-color:{thumb_hex} {track_hex};color-scheme:{scheme};}}"
        )
    except Exception:  # noqa: BLE001 — unstyled page beats no page
        return ""


class MarkdownPane(QWidget):
    """Markdown preview, pure Rust: core.markdown_page (mordant fragment
    + KaTeX shell). WebEngine when present, else rich text; highlighted
    source only if the fragment itself fails. No wheel involved."""

    def __init__(self, bridge: Bridge, pane_id: str, path: str | None = None):
        super().__init__()
        self.bridge = bridge
        self.pane_id = pane_id
        self.path = path
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._build_view())

    def refresh(self):
        """Re-render with the current markdown theme (menu switching)."""
        lay = self.layout()
        old = lay.takeAt(0).widget() if lay.count() else None
        if old is not None:
            old.deleteLater()
        lay.addWidget(self._build_view())

    def _build_view(self):
        try:
            page = self.bridge.core.markdown_page(self.pane_id)
        except (AttributeError, ValueError):
            page = None
        if page is None:
            try:
                frag = self.bridge.core.markdown_html(self.pane_id)
                page = f"<html><body>{frag}</body></html>" if frag else None
            except (AttributeError, ValueError):
                page = None
        view = self._make_view(page)
        if view is None:  # last resort: highlighted source as text
            fb = QPlainTextEdit()
            fb.setReadOnly(True)
            fb.setFont(QFont("Cascadia Mono", 10))
            fb.setPlainText("\n".join(
                "".join(t for t, _, _ in row) for row in self.bridge.core.highlighted_file(self.pane_id)
            ))
            view = fb
        return view

    @staticmethod
    def _style_page(page: str) -> str:
        # Chromium scrollbars ignore Qt stylesheets: tint them to the live
        # Fusion colors (sampled, never invented) before handing over.
        css = _fusion_scrollbar_css()
        if not css:
            return page
        tag = f"<style>{css}</style>"
        return page.replace("</head>", tag + "</head>") if "</head>" in page else tag + page

    @staticmethod
    def _make_view(page: str | None):
        if page is None:
            return None
        page = MarkdownPane._style_page(page)
        if page is None:
            return None
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except ImportError:
            QWebEngineView = None  # type: ignore[assignment]
        if QWebEngineView is not None:
            view = QWebEngineView()
            view.setHtml(page)
            return view
        text = QTextBrowser()
        text.setHtml(page)
        return text


def _round_window_corners(win) -> None:
    """Restore the rounded outer corners lost to FramelessWindowHint.

    Win11 DWM rounds captioned windows by itself; frameless ones default
    to square unless asked. One attribute call restores parity with the
    old native window (maximized state un-rounds itself). Best effort:
    unknown platforms and compositor-less test runs just stay square."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(win.winId())
        pref = ctypes.c_int(2)  # DWMWCP_ROUND (33 = WINDOW_CORNER_PREFERENCE)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref)
        )
    except Exception:  # noqa: BLE001 — cosmetic only, never fatal
        pass


class KilimTitleBar(LaceStandardTitleBar, DockStyled):
    """Lace title bar with the Kilim menu bar embedded in the chrome.

    VS Code-style unified bar: icon + Kilim title, then Views/Terminal/
    Themes menus, then the window buttons. The menu bar is transparent so
    the bar's own themed background shows through; popups pull their
    colors from the same dock-theme tokens. Follows the Lace
    `demo_app_custom_titlebar_menus` pattern (menus sit after our title
    instead of replacing it like the demo)."""

    STYLE_CATEGORIES = (DockStyleCategory.TITLE_BAR, DockStyleCategory.SIDEBAR, DockStyleCategory.CORE)

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QMenuBar

        self.menu_bar = QMenuBar(self)
        # Same height as the bar: one continuous surface, items centered.
        self.menu_bar.setFixedHeight(self.height())
        # Base layout: stretch, icon, title, stretch, buttons — menus go
        # after the title, before the middle stretch.
        self.hBoxLayout.insertWidget(3, self.menu_bar, 0, Qt.AlignVCenter)
        self._init_dock_style()

    def refresh_style(self):
        """Theme the embedded menu bar from the active dock theme."""
        from lace.dock_theme import DockStyleCategory
        from lace.frameless_titlebar import _color_hex

        sm = getattr(self, "_style_mgr", None)
        if sm is None:
            return
        bg = sm.get(DockStyleCategory.SIDEBAR, "bg_color") or sm.get(
            DockStyleCategory.TITLE_BAR, "bg_normal"
        )
        text = sm.get(DockStyleCategory.TITLE_BAR, "text_normal")
        hover_bg = sm.get(DockStyleCategory.TITLE_BAR, "button_hover_bg")
        border = sm.get(DockStyleCategory.TITLE_BAR, "border_normal")
        bg_hex = _color_hex(bg) if bg else "transparent"
        text_hex = _color_hex(text) if text else "#cccccc"
        hover_hex = _color_hex(hover_bg) if hover_bg else "#555555"
        border_hex = _color_hex(border) if border else hover_hex
        # 7px vertical padding centers a default item inside the 32px bar.
        self.menu_bar.setStyleSheet(f"""
            QMenuBar {{
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }}
            QMenuBar::item {{
                background: transparent;
                color: {text_hex};
                padding: 7px 12px;
                margin: 0px;
                border: none;
            }}
            QMenuBar::item:selected {{
                background: {hover_hex};
            }}
            QMenuBar::item:pressed {{
                background: {hover_hex};
            }}
            QMenu {{
                background: {bg_hex};
                color: {text_hex};
                border: 1px solid {border_hex};
                padding: 4px;
            }}
            QMenu::item {{
                padding: 4px 16px;
                background: transparent;
            }}
            QMenu::item:selected {{
                background: {hover_hex};
            }}
            QMenu::separator {{
                background: {border_hex};
                height: 1px;
                margin: 4px 8px;
            }}
        """)

    def paintEvent(self, event):
        """Solid theme fill first: the qframeless base does not always
        render the QSS background, so the menus and surroundings share
        the exact same color."""
        from PySide6.QtGui import QColor, QPainter

        from lace.dock_theme import DockStyleCategory
        from lace.frameless_titlebar import _color_hex

        try:
            sm = self._style_mgr
            bg = sm.get(DockStyleCategory.SIDEBAR, "bg_color") or sm.get(
                DockStyleCategory.TITLE_BAR, "bg_normal"
            )
        except (AttributeError, RuntimeError):
            bg = None
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(_color_hex(bg)) if bg else QColor("#1e1e1e"))
        painter.end()
        super().paintEvent(event)

    def canDrag(self, pos) -> bool:
        """No window-drag from menus or buttons (else a menu press could
        start an OS move loop on some platforms)."""
        from PySide6.QtWidgets import QAbstractButton, QMenu, QMenuBar

        child = self.childAt(pos)
        while child is not None and child is not self:
            if isinstance(child, (QMenuBar, QMenu, QAbstractButton)):
                return False
            child = child.parent()
        return super().canDrag(pos)


class KilimWindow(FramelessLaceMainWindow):
    def __init__(self, layout_path: str, perspective_path: str | None = None):
        # Frameless chrome with the menus embedded in the title bar.
        super().__init__(title_bar=KilimTitleBar)
        from lace import DockManager, DockWidget
        from lace.enums import DockWidgetArea

        # If setup fails half-way (no window, but PTYs + loop thread alive),
        # don't orphan a runaway: stop the bridge before propagating.
        try:
            self._init_inner(layout_path, perspective_path, DockManager, DockWidget, DockWidgetArea)
        except BaseException:
            try:
                self.bridge.stop()
            except Exception:  # noqa: BLE001
                pass
            raise

    def _init_inner(self, layout_path, perspective_path, DockManager, DockWidget, DockWidgetArea):
        doc = Path(layout_path).read_text(encoding="utf-8")
        self.layout_path = str(layout_path)
        self.perspective_path = perspective_path
        self.bridge = Bridge(doc)
        self.bridge.call(lambda: self.bridge.core.ensure_terms())
        self.setWindowTitle("Kilim")
        self.resize(1200, 800)
        self.manager = DockManager(self)
        # Frameless floats keep the plain Lace title bar (no menus — those
        # live in the main window's chrome only).
        from lace import DockThemeBridge, TitleBarMode

        self.manager.title_bar_mode = TitleBarMode.custom
        self.manager.floating_title_bar = LaceStandardTitleBar
        # Top-level popups (dock tab menus, pane context menus) read the
        # application palette, not the dock root's — without the bridge
        # they stay on the system palette while the bar is themed.
        self.theme_bridge = DockThemeBridge()
        # Explicit, after the manager: keeps the title bar on top.
        self.setCentralWidget(self.manager._root)
        if self.windowIcon().isNull():
            from PySide6.QtWidgets import QStyle

            fallback = self.style().standardIcon(QStyle.SP_TitleBarMenuButton)
            self.setWindowIcon(fallback)
        # Both sidebars exist from the start: title-bar pin buttons appear
        # and explicit pin actions always have a target.
        self.manager.sidebar_manager.add_sidebar(DockWidgetArea.left)
        self.manager.sidebar_manager.add_sidebar(DockWidgetArea.right)
        self.pane_docks: dict[str, object] = {}
        self.term_panes: dict[str, QWidget] = {}
        self.file_panes: dict[str, QWidget] = {}
        self.md_panes: dict[str, QWidget] = {}
        self._badge_base: dict[str, str] = {}  # un-dotted tab titles
        self.lace_theme: str | None = None
        self._kilim_by_lace = register_kilim_lace_themes()
        self._theme_actions: dict[str, dict[str, object]] = {"lace": {}, "code": {}, "md": {}}

        import json

        raw = json.loads(doc)
        panes = {p["id"]: p for p in raw["panes"]}
        InitialActive = raw["layout"].get("active", "")
        self._active = InitialActive
        groups = layout_groups(raw["layout"]["root"])
        if not groups:  # schema fallback: creation order singletons
            groups = [[pid] for pid in panes]
        for group in groups:
            area_widget = None
            for pid in group:
                if pid in self.pane_docks or pid not in panes:
                    continue
                p = panes[pid]
                kind = p["kind"]
                inner: QWidget
                if kind == "term":
                    inner = TerminalPane(self.bridge, pid)
                elif kind == "markdown":
                    inner = MarkdownPane(self.bridge, pid, p.get("path"))
                else:
                    inner = FilePane(self.bridge, pid)
                dock = DockWidget(p.get("title", pid))
                dock.setObjectName(pid)  # pane ids = dock names (round-trip key)
                dock.set_widget(inner)
                if kind == "term":
                    self.term_panes[pid] = inner
                    inner._set_badge = lambda on, pid=pid: self._badge_pane(pid, on)
                elif kind == "markdown":
                    self.md_panes[pid] = inner
                else:
                    self.file_panes[pid] = inner
                area = DockWidgetArea.left if kind == "term" else DockWidgetArea.right
                if area_widget is None:
                    area_widget = self.manager.add_dock_widget(area, dock)
                else:
                    # center + target = tabify (keeps manager registration).
                    self.manager.add_dock_widget(DockWidgetArea.center, dock, area_widget)
                self.pane_docks[pid] = dock
        # Round-trip: restore previous dock geometry when names still match.
        sidecar = perspective_path or str(perspectives.sidecar_for(self.layout_path))
        self.perspective_path = sidecar
        data = perspectives.load(sidecar)
        if data is not None:
            perspectives.apply(self, data)
        # Lace chrome theme rides in the sidecar (not the shared layout).
        try:
            import json as _json

            saved = _json.loads(Path(sidecar).read_text(encoding="utf-8"))
            if isinstance(saved, dict) and saved.get("lace_theme"):
                self.apply_lace_theme(saved["lace_theme"], persist=False)
        except (OSError, ValueError):
            pass
        if self.lace_theme is None and "kilim_dark" in self._kilim_by_lace:
            self.apply_lace_theme("kilim_dark")  # fresh launch opens unified
        self._build_menus()
        # Focused dock == active pane (session_active reads it that
        # way), and only a focused text widget draws its cursor. Lace
        # chrome (tab bar) grabs focus on show and beats the one-shot,
        # so: watch focus changes + retry the claim until a pane holds it.
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)
        for ms in (300, 800, 1500):
            QTimer.singleShot(ms, self._claim_pane_focus)
        # Unfocused text widgets draw no cursor (see _claim_pane_focus
        # retries above — one shot loses to Lace chrome / inactive window).
        active_view = self.term_panes.get(self._active, None)
        if active_view is not None:
            QTimer.singleShot(300, self._claim_pane_focus)

    def _inside_panes(self, w) -> bool:
        """True when the widget lives inside one of our pane containers."""
        try:
            containers = (
                list(self.term_panes.values())
                + list(self.file_panes.values())
                + list(self.md_panes.values())
            )
            while w is not None:
                if w in containers:
                    return True
                w = w.parentWidget()
            return False
        except RuntimeError:
            return True  # half-torn-down: don't touch focus

    def _active_widget(self):
        """Widget to focus for the active pane (term view / file / md view)."""
        pid = self.session_active() if hasattr(self, "pane_docks") else self._active
        if pid in self.term_panes:
            return self.term_panes[pid].view
        if pid in self.file_panes:
            return self.file_panes[pid]
        if pid in self.md_panes:
            lay = self.md_panes[pid].layout()
            if lay is not None and lay.count():
                return lay.itemAt(0).widget()
            return self.md_panes[pid]
        return None

    def _claim_pane_focus(self):
        """Focus the active pane unless the user focused pane content.

        Only steals from Lace chrome (tab bar, title bars) — never from
        our own views, menus, popups, or dialogs. Retried at startup
        because the window may still be inactive on the first shot."""
        try:
            from PySide6.QtWidgets import QDialog, QMenu, QMenuBar

            if not self.isVisible():
                return  # closed/hidden: a previous test's retry must not steal
            fw = QApplication.focusWidget()
            if fw is not None and self._inside_panes(fw):
                return  # user is somewhere real: leave it
            if QApplication.activePopupWidget() is not None:
                return
            if fw is not None and isinstance(fw, (QMenu, QMenuBar, QDialog)):
                return
            target = self._active_widget()
            if target is not None and not target.hasFocus():
                target.setFocus()
        except RuntimeError:
            pass  # closing: panes half-deleted

    def _on_focus_changed(self, _old, new):
        if new is None:
            return
        try:
            if self._inside_panes(new):
                return
        except RuntimeError:
            return
        self._claim_pane_focus()

    def _build_menus(self):
        """Views menu: re-open closed panes + reset layout.

        Closing the last dock leaves an empty window; these actions are the
        way back (Lace re-docks on toggle_view(True))."""
        views = self.titleBar.menu_bar.addMenu("&Views")
        for pid, dock in self.pane_docks.items():
            action = dock.toggle_view_action()
            action.setText(dock.windowTitle())
            views.addAction(action)
        views.addSeparator()
        show_all = views.addAction("Show All Panes")
        show_all.triggered.connect(self.restore_all_panes)
        reset = views.addAction("Reset Layout")
        reset.triggered.connect(self.reset_layout)
        terminal = self.titleBar.menu_bar.addMenu("&Terminal")
        shells = self._shell_options()
        if not shells:
            none = terminal.addAction("No shells found")
            none.setEnabled(False)
        for name, cmd, args in shells:
            act = terminal.addAction(name)
            act.triggered.connect(
                lambda _checked=False, n=name, c=cmd, a=args: self.launch_shell(n, c, a)
            )
        self._term_seq = 0
        self._build_themes_menu()

    def _build_themes_menu(self):
        """Themes menu: Lace chrome, code (Qt + TUI), markdown fences.

        One shared registry (the Kilim eight, Kilim Dark default) feeds
        code + markdown alike, so no choice can silently fall back.
        Choices persist: code/markdown into the layout file, Lace into
        the sidecar.
        """
        from PySide6.QtGui import QActionGroup

        from kilim import list_themes

        themes = self.titleBar.menu_bar.addMenu("&Themes")

        # Lace dock chrome: the Kilim group only (flat, radio-checked).
        # Stock Lace presets stay out — the app exposes Kilim chrome.
        lace_menu = themes.addMenu("Lace")
        try:
            from lace.dock_style_manager import apply_dock_theme, theme_groups

            groups = [c for t, c in theme_groups() if t == "Kilim"]
            choices = groups[0] if groups else []
        except (ImportError, AttributeError, IndexError):
            choices = []
        lace_group = QActionGroup(self)
        lace_group.setExclusive(True)
        for label, key in choices:
                act = lace_menu.addAction(label)
                act.setCheckable(True)
                act.setChecked(key == self.lace_theme)
                self._theme_actions["lace"][key] = act
                act.triggered.connect(
                    lambda _c=False, k=key: self.apply_lace_theme(k)
                )
                lace_group.addAction(act)
        if not choices:
            none = lace_menu.addAction("No Kilim themes")
            none.setEnabled(False)

        # Code theme: TUI file panes + Qt FilePane share the session theme.
        code_menu = themes.addMenu("Code (Qt + TUI)")
        code_group = QActionGroup(self)
        code_group.setExclusive(True)
        current_code = self.bridge.core.theme()
        for name in list_themes():
            act = code_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == current_code)
            self._theme_actions["code"][name] = act
            act.triggered.connect(
                lambda _c=False, n=name: self.apply_code_theme(n)
            )
            code_group.addAction(act)

        # Markdown fence theme: Qt preview only (TUI shows markdown source
        # with this theme too, but has no menu — Ctrl+T cycles code only).
        # Same eight as code: fences can never name a missing theme.
        md_menu = themes.addMenu("Markdown")
        md_group = QActionGroup(self)
        md_group.setExclusive(True)
        current_md = self.bridge.core.markdown_theme()
        for name in list_themes():
            act = md_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == current_md)
            self._theme_actions["md"][name] = act
            act.triggered.connect(
                lambda _c=False, n=name: self.apply_markdown_theme(n)
            )
            md_group.addAction(act)

    def apply_code_theme(self, name: str):
        """Session code theme → repaint Qt FilePanes, persist to layout."""
        if self.bridge.core.theme() == name:
            return
        self.bridge.core.set_theme(name)
        for pane in self.file_panes.values():
            pane.refresh()
        self.save_themes()
        self._sync_theme_checks()

    def apply_markdown_theme(self, name: str):
        """Fence theme → re-render Qt MarkdownPanes, persist to layout."""
        if self.bridge.core.markdown_theme() == name:
            return
        self.bridge.core.set_markdown_theme(name)
        for pane in self.md_panes.values():
            pane.refresh()
        self.save_themes()
        self._sync_theme_checks()

    def apply_lace_theme(self, key: str, persist: bool = True):
        """Dock chrome theme, effective immediately.

        Kilim chrome (`kilim_*`) is unified: selecting it also sets the
        same-named code + markdown themes and repaints (one click, all
        surfaces) — neo chrome selects the neo code/md themes.
        Legacy keys (`kilim_neo_*`, `kilim_neon_*`) normalize forward.
        """
        from lace.dock_style_manager import apply_dock_theme

        key = _normalize_lace_key(key)
        if apply_dock_theme(key):
            self.lace_theme = key
            if key in self._kilim_by_lace:
                syntect = self._kilim_by_lace[key]
                self.bridge.core.set_theme(syntect)
                self.bridge.core.set_markdown_theme(syntect)
                for pane in self.file_panes.values():
                    pane.refresh()
                for pane in self.md_panes.values():
                    pane.refresh()
                self.save_themes()
            if persist:
                self.save_lace_theme()
            self._sync_theme_checks()

    def _sync_theme_checks(self):
        """Radio-check the Themes menu to live state (unified applies)."""
        want = {
            "lace": self.lace_theme,
            "code": self.bridge.core.theme(),
            "md": self.bridge.core.markdown_theme(),
        }
        for group, current in want.items():
            for name, act in self._theme_actions.get(group, {}).items():
                try:
                    act.setChecked(name == current)
                except RuntimeError:  # wrapped C++ object deleted
                    pass

    def save_themes(self):
        """Write code/markdown theme choices back into the layout file."""
        import json as _json

        try:
            raw = _json.loads(Path(self.layout_path).read_text(encoding="utf-8"))
            raw.setdefault("layout", {})["theme"] = self.bridge.core.theme()
            raw["layout"]["markdown_theme"] = self.bridge.core.markdown_theme()
            Path(self.layout_path).write_text(_json.dumps(raw, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            pass

    def save_lace_theme(self):
        """Stash the Lace chrome choice in the perspective sidecar."""
        import json as _json

        try:
            try:
                raw = _json.loads(Path(self.perspective_path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
            raw["lace_theme"] = self.lace_theme
            Path(self.perspective_path).write_text(_json.dumps(raw, indent=2), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _shell_options() -> list[tuple[str, str, list[str]]]:
        """Launchable shells, skipping what isn't installed."""
        import os
        import shutil

        found: list[tuple[str, str, list[str]]] = []
        ps = shutil.which("powershell.exe") or shutil.which("powershell")
        if ps:
            found.append(("PowerShell", ps, []))
        bash = (
            shutil.which("bash.exe")
            or shutil.which("bash")
            or next(
                (p for p in (
                    "C:/Program Files/Git/bin/bash.exe",
                    "C:/Program Files (x86)/Git/bin/bash.exe",
                ) if Path(p).is_file()),
                None,
            )
        )
        if bash:
            found.append(("Git Bash", bash, ["--login", "-i"]))
        cmd = os.environ.get("COMSPEC") or shutil.which("cmd.exe") or shutil.which("cmd")
        if cmd and Path(cmd).is_file():
            found.append(("Command Prompt", cmd, []))
        return found

    def launch_shell(self, name: str, cmd: str, args: list[str]):
        """Spawn a new shell pane (core) and dock it left."""
        from lace import DockWidget
        from lace.enums import DockWidgetArea

        self._term_seq += 1
        pid = f"term-new{self._term_seq}"
        while pid in self.pane_docks:
            self._term_seq += 1
            pid = f"term-new{self._term_seq}"
        self.bridge.call(
            lambda: self.bridge.core.spawn_term(pid, name, cmd, args, 24, 80, 5000)
        )
        inner = TerminalPane(self.bridge, pid)
        dock = DockWidget(name)
        dock.setObjectName(pid)
        dock.set_widget(inner)
        self.manager.add_dock_widget(DockWidgetArea.left, dock)
        self.pane_docks[pid] = dock
        self.term_panes[pid] = inner
        inner._set_badge = lambda on, pid=pid: self._badge_pane(pid, on)
        views = self.titleBar.menu_bar.actions()[0].menu()
        views.insertAction(views.actions()[0], dock.toggle_view_action())
        inner.view.setFocus()

    def _badge_pane(self, pid: str, on: bool):
        """Dot a background tab while its shell produces output (P2).

        The dot lives in the dock title (Lace re-renders the tab from
        windowTitle automatically); cleared the moment the pane shows."""
        try:
            dock = self.pane_docks.get(pid)
            if dock is None:
                return
            base = self._badge_base.setdefault(pid, dock.windowTitle().lstrip("● "))
            want = ("● " + base) if on else base
            if dock.windowTitle() != want:
                dock.setWindowTitle(want)
        except RuntimeError:
            pass  # closing

    def restore_all_panes(self):
        """Re-open every closed pane (no-op for visible ones)."""
        for dock in self.pane_docks.values():
            dock.toggle_view(True)

    def reset_layout(self):
        """Show all panes and re-apply the saved perspective (or creation layout)."""
        self.restore_all_panes()
        data = perspectives.load(self.perspective_path)
        if data is not None:
            perspectives.apply(self, data)

    def pane_of(self, dock) -> str:
        name = dock.objectName()
        return name if name in self.pane_docks else ""

    def session_active(self) -> str:
        # Focused dock wins; falls back to layout's initial active pane.
        w = QApplication.focusWidget()
        while w is not None:
            from lace import DockWidget as _DW

            if isinstance(w, _DW):
                name = self.pane_of(w)
                if name:
                    return name
                break
            w = w.parentWidget()
        return self._active

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_corners_rounded", False):
            self._corners_rounded = True
            _round_window_corners(self)

    def closeEvent(self, e):
        try:
            QApplication.instance().focusChanged.disconnect(self._on_focus_changed)
        except (RuntimeError, TypeError):
            pass  # never connected / already gone
        try:
            perspectives.save(self.perspective_path, perspectives.capture(self))
            if self.lace_theme:  # save() rewrites the sidecar: re-stash.
                self.save_lace_theme()
        except Exception:  # noqa: BLE001 — sidecar must never block shutdown
            pass
        for pid in self.bridge.core.pane_ids():
            try:
                if self.bridge.core.term_alive(pid):
                    self.bridge.call(lambda: self.bridge.core.terminate_term(pid, 1.0))
            except ValueError:
                pass
        self.bridge.stop()
        super().closeEvent(e)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    layout = argv[0] if len(argv) > 0 else "layouts/default.json"
    persp = argv[1] if len(argv) > 1 else None
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # frameless chrome + themed QSS need it
    win = KilimWindow(layout, persp)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

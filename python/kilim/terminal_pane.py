"""Live terminal pane: Qt paint surface over a core PTY term."""

from __future__ import annotations

import concurrent.futures
import os

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QPlainTextEdit,
    QScrollBar,
    QWidget,
)

from kilim.qt_bridge import Bridge
from kilim.qt_themes import cell_qcolor
from kilim.qt_termkeys import (
    _arrow_seq,
    _char_to_term_col,
    _char_width,
    _modes_dict,
    _sgr_mouse,
    _term_col_to_char,
    _x10_mouse,
)

# Bare extensionless text files the Rust detector linkifies (kept in sync
# with `plausible` in kilim-core/src/paths.rs): rows without any path-ish
# character still get a Rust call when they name one of these.
_BARE_TEXT_NAMES = (
    "makefile", "gnumakefile", "dockerfile", "containerfile",
    "license", "licence", "copying", "notice", "authors",
    "readme", "changelog", "changes", "news", "todo", "version",
)


def _looks_like_path(text: str) -> bool:
    """Cheap pre-filter: can this row possibly hold a detector hit?

    Mirrors the Rust `plausible` gate (separator, extension dot, tilde,
    bare text name) so skipped rows are ones `detect_paths_in` would
    reject anyway — behavior identical, minus the FFI + stat cost."""
    if ("/" in text or "\\" in text or "." in text or "~" in text):
        return True
    low = text.lower()
    return any(n in low for n in _BARE_TEXT_NAMES)


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
        self.viewport().setMouseTracking(True)  # links need hover, always
        self._press_pos = None  # click-vs-drag bookkeeping for links
        self._press_link = None

    def focusNextPrevChild(self, next: bool) -> bool:  # noqa: A002 (Qt name)
        """Tab belongs to the terminal, not to focus navigation.

        QWidget::event intercepts Tab/Backtab for focusNextPrevChild()
        *before* keyPressEvent runs, so without this the shell never sees a
        Tab at all (it just moves focus into the dock chrome)."""
        return False

    def keyPressEvent(self, e):
        self._owner.on_key(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self._owner._on_view_focus(True)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._owner._on_view_focus(False)

    def wheelEvent(self, e):
        self._owner.on_wheel(e)

    def mousePressEvent(self, e):
        self._press_pos = e.position().toPoint()
        self._press_link = None
        if self._owner._send_mouse("press", e):
            return  # app owns the mouse (tracking mode): no selection
        if e.button() == Qt.LeftButton:
            self._press_link = self._owner._link_at(self._press_pos)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._owner._send_mouse("move", e):
            return
        if QApplication.mouseButtons() == Qt.NoButton:
            hit = self._owner._link_at(e.position().toPoint())
            self.viewport().setCursor(Qt.PointingHandCursor if hit else Qt.IBeamCursor)
            self._owner._hover_status(hit)
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._owner._hover_status(None)  # no stale link in the status bar
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._owner._mouse_active():
            self._owner._send_mouse("release", e)
            return
        pos = e.position().toPoint()
        hit = self._press_link
        clicked = (
            hit is not None
            and e.button() == Qt.LeftButton
            and self._press_pos is not None
            and (pos - self._press_pos).manhattanLength() <= 4
        )
        self._press_pos = self._press_link = None
        super().mouseReleaseEvent(e)
        if clicked:
            self._owner.open_link(hit)  # a click on a path opens it
            return
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

    def __init__(self, bridge: Bridge, pane_id: str, theme: str | None = None):
        super().__init__()
        self.bridge = bridge
        self.pane_id = pane_id
        # Explicit terminal theme (Themes menu); None follows the code theme.
        self._terminal_theme = theme
        self.view = _TermView(self)
        self.bar = QScrollBar(Qt.Vertical)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.bar, 0)
        self._pending: concurrent.futures.Future | None = None
        self._resize_pending: tuple[int, int] | None = None
        self._applied_grid: tuple[int, int] | None = None  # settled grid (debounce)
        self._pending_grid: tuple[int, int] | None = None
        self._pending_same = 0
        self._theme_name: str | None = None  # themed palette tracker
        self._start = 0  # first history line currently shown
        self._total = 0
        self._follow = True
        self.bar.actionTriggered.connect(self._on_scroll_action)
        self._last_sig = None  # (total, cursor, start, rows) — idle-skip
        self._render_count = 0  # frames actually painted (tests/perf)
        self._paint_rows = -1  # viewport rows of the current document
        self._force_full = False  # theme/visibility flip: rebuild, no surgery
        self._paint_cols: int | None = None  # width the cells were shaped for
        self._paint_start = None  # history offset of row 0 (scrolls full-repaint)
        self._cursor_at = None  # absolute (x, y) of the painted cursor
        self._sel = None  # selection in absolute history coords (or None)
        self._modes = _modes_dict(None)  # DEC modes from latest snapshot
        self._cursor_visible = True  # DECTCEM (?25): app hides -> no block
        self._bell_until = 0.0  # monotonic deadline of the bell flash
        self._seen_total = None  # activity-badge baseline
        self._set_badge = lambda on: None  # wired by the window (tab dot)
        self._wheel_accum = 0  # fractional scroll accumulation (smooth pads)
        self._link_cache: dict[str, list] = {}  # row text -> openable hits
        self._link_cwd: str | None = None  # snapshot cwd hits resolved against
        self._fmt_cache: dict = {}  # (fg, bg, attrs, theme-gen) -> QTextCharFormat
        self._open_path = lambda *_: None  # wired by the window
        self._poller = QTimer(self)
        self._poller.timeout.connect(self._poll)
        self._poller.start(60)
        # Soft block cursor (see _caret_cell): Qt draws no caret in a
        # read-only QPlainTextEdit, so the cell under the terminal cursor
        # is reverse-video, painted with the rows, blinking on the
        # system's caret clock.
        self._cells = None  # last painted viewport rows (caret repaints)
        self._cells_start = 0
        self._caret_on = True
        self._caret_timer = QTimer(self)
        self._caret_timer.timeout.connect(self._blink_caret)
        flash = QApplication.styleHints().cursorFlashTime()
        self._blink_ms = max(100, flash // 2) if flash > 0 else 0
        if self._blink_ms:
            self._caret_timer.start(self._blink_ms)

    # ── poll ──
    def _poll(self):
        # Resize first: a slow snapshot must never stall dimension sync
        # (regression: resizes piled up unprocessed while snapshots lagged).
        # Drag coalescing: while the grid keeps moving, hold the settled
        # one — output keeps streaming into it (the buffer), so a gesture
        # costs zero resizes and zero rebuilds; one resize_term + one
        # rebuild land on the first repeat poll (~120ms after the last
        # motion). A discrete jump (maximize, snap, big yank — far more
        # than one poll of smooth dragging can move) applies immediately
        # instead: it is not a drag, so there is no thrash to avoid, and
        # the reflow starts this poll. Qt clips the stable content
        # natively meanwhile, which is what makes drags feel instant.
        # Anchoring needs no help: resize never rewraps scrollback
        # (viewport truncates/pads, history untouched), so a frozen
        # absolute anchor and tail-follow both survive unchanged.
        rows, cols = self._grid()
        if self._applied_grid is None:
            self._applied_grid = (rows, cols)  # first paint: no debounce
        elif (rows, cols) != self._applied_grid:
            dr = abs(rows - self._applied_grid[0])
            dc = abs(cols - self._applied_grid[1])
            if dr >= 8 or dc >= 16:
                self._applied_grid = (rows, cols)
                self._pending_grid = None
                self._pending_same = 0
            elif (rows, cols) == self._pending_grid:
                self._pending_same += 1
                if self._pending_same >= 1:
                    self._applied_grid = (rows, cols)
                    self._pending_grid = None
                    self._pending_same = 0
            else:
                self._pending_grid = (rows, cols)
                self._pending_same = 0
            rows, cols = self._applied_grid
        else:
            self._pending_grid = None
            self._pending_same = 0
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
        self._pending = self.bridge.submit(lambda: self._snapshot(rows, cols))

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

    async def _snapshot(self, rows: int, cols: int):
        # Alt screen has no scrollback: always track the tail there.
        anchor = None if (self._follow or self._modes.get("alt")) else self._start
        total, start, cells, cursor, modes, dirty, cwd = await self.bridge.core.snapshot_term(
            self.pane_id, rows, anchor
        )
        return {
            "cells": cells,
            "cursor": cursor,
            "start": start,
            "total": total,
            "rows": rows,
            "cols": cols,  # width the cells were shaped for (resize gate)
            "modes": _modes_dict(modes),
            "dirty": [int(r) for r in (dirty or [])],
            "cwd": cwd,
        }

    def _grid(self) -> tuple[int, int]:
        fm = self.view.fontMetrics()
        # Clamped: a pathological viewport (broken restore, overlay
        # sizing) must never ask the pty for a gigantic screen.
        cols = max(20, self.view.viewport().width() // max(1, fm.horizontalAdvance("M")))
        rows = max(5, self.view.viewport().height() // max(1, fm.lineSpacing()))
        return (min(400, rows), min(800, cols))

    def set_terminal_theme(self, name: str | None) -> None:
        """Explicit terminal theme (Themes menu); None follows code theme."""
        self._terminal_theme = name
        self._ensure_theme()

    def _ensure_theme(self):
        """Track the terminal theme into the widget palette.

        An explicit choice wins; unset panes follow the session code theme.
        Default-fg/bg cells resolve through the palette, so theming it
        recolors the whole shell view (background included) and clearing
        the paint signature forces a rebuild on switch. Explicit shell
        colors pass through untouched."""
        from PySide6.QtGui import QPalette

        from kilim import theme_background, theme_foreground

        name = self._terminal_theme or self.bridge.core.theme()
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
        self._fmt_cache.clear()  # defaults baked into formats changed too
        self._last_sig = None  # repaint with the new mapping
        self._force_full = True  # explicit shell colors are baked: rebuild, not shift

    def _render(self, snap):
        self._ensure_theme()
        self._modes = snap.get("modes") or _modes_dict(None)
        if self._modes.get("cursor_visible", True) != getattr(
            self, "_cursor_visible", True
        ):
            # DECTCEM flip (?25l/h): the block must vanish/appear now, but
            # nothing else moved — force a rebuild so the row repaints.
            self._cursor_visible = self._modes.get("cursor_visible", True)
            self._force_full = True
        cwd = snap.get("cwd") or None
        if cwd != self._link_cwd:
            # Fresh base dir (cd, restart, new pane): cached hits resolved
            # against the old one, so drop them.
            self._link_cwd = cwd
            self._link_cache.clear()
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
    def _paint(self, snap):
        """Full rebuild, window shift, or dirty-subset repaint.

        Full when the viewport width changed (cells reshaped), on first
        paint, or after a theme switch. Shift when old and new windows
        overlap at the same width — streaming scrolls, wheel scrollback,
        and height resizes all slide blocks instead of rebuilding:
        qtermwidget's scrollImage/memmove equivalent for a document.
        Subset when the window sits still (spinners, progress bars). No
        content comparison: the screen is the source of truth, our old
        row-diff was re-deriving it."""
        fg0 = self.view.palette().color(self.view.foregroundRole())
        bg0 = self.view.palette().color(self.view.backgroundRole())
        rows = snap["cells"]
        lo, n = snap["start"], len(rows)
        self._cells = rows  # caret repaints read this between polls
        self._cells_start = snap["start"]
        caret = self._caret_cell(snap["start"], snap["cursor"], n)
        cols = snap.get("cols", self._paint_cols)
        if (
            not self._force_full
            and self._paint_rows == snap["rows"]
            and self._paint_start == snap["start"]
        ):
            vis = sorted(r - lo for r in snap.get("dirty") or [] if lo <= r < lo + n)
            if vis:
                self._paint_subset(rows, vis, fg0, bg0, caret)
        elif (
            not self._force_full
            and self._paint_start is not None
            and self._paint_cols is not None
            and cols == self._paint_cols
            and self._overlap(lo, n)
        ):
            fresh = self._paint_shift(rows, lo, n, fg0, bg0, caret)
            # Dirty rows outside the freshly inserted range (a spinner
            # that fired the same poll as the scroll) repaint after.
            vis = sorted(
                r - lo for r in snap.get("dirty") or []
                if lo <= r < lo + n and (r - lo) not in fresh
            )
            if vis:
                self._paint_subset(rows, vis, fg0, bg0, caret, count=False)
            self._paint_start = snap["start"]
            self._paint_rows = snap["rows"]
            self._render_count += 1
        else:
            self._paint_full(rows, fg0, bg0, caret)
            self._force_full = False
            self._paint_rows = snap["rows"]
            self._paint_cols = cols
            self._paint_start = snap["start"]

    def _overlap(self, lo: int, n: int) -> bool:
        """New window [lo, lo+n) shares at least one row with the paint."""
        old_lo = self._paint_start
        old_n = self.view.document().blockCount() - 1  # trailing empty
        return lo < old_lo + old_n and old_lo < lo + n

    @staticmethod
    def _row_text(row) -> str:
        """The row as painted: hidden cells show a space (links index this)."""
        return "".join(t if not (a & (1 << 6)) else " " for t, _, _, a in row)

    def _links_for_text(self, text: str) -> list:
        """Openable paths in one row text, cached per text.

        Rows repaint constantly; detection runs in Rust
        (`CoreSession.detect_paths_in`, shared with the TUI) against the
        term's snapshot cwd — the shell's live directory, else its
        configured start dir, else the app directory — and only
        `code`/`markdown`/`html` kinds are links — binary and missing
        paths get no underline and no click.

        A cheap pre-filter keeps the Rust call (FFI + filesystem stats)
        off rows that cannot hold a path: qtermwidget likewise runs its
        FilterChain separately from painting instead of per repaint."""
        hits = self._link_cache.get(text)
        if hits is None:
            if _looks_like_path(text):
                try:
                    hits = [
                        h
                        for h in self.bridge.core.detect_paths_in(text, self._link_cwd or os.getcwd())
                        if h[5] in ("code", "markdown", "html")
                    ]
                except (AttributeError, ValueError):
                    hits = []
            else:
                hits = []
            if len(self._link_cache) > 400:
                self._link_cache.clear()
            self._link_cache[text] = hits
        return hits

    def _link_cells(self, row):
        """Cell indices under a link (None when the row has none)."""
        hits = self._links_for_text(self._row_text(row))
        if not hits:
            return None
        out: list[int] = []
        pos = 0
        for ci, (text, _, _, attrs) in enumerate(row):
            n = 1 if attrs & (1 << 6) else len(text)
            if any(s < pos + n and pos < e for s, e, *_ in hits):
                out.append(ci)
            pos += n
        return out

    def _link_at(self, pos):
        """Link hit under a viewport position, or None (hover + click)."""
        cur = self.view.cursorForPosition(pos)
        block = cur.block()
        if not block.isValid():
            return None
        hits = self._links_for_text(block.text())
        if not hits:
            return None
        ci = cur.position() - block.position()
        doc = self.view.document()
        for hit in hits:
            start, end = hit[0], hit[1]
            if not (start <= ci <= end):
                continue
            a, b = QTextCursor(doc), QTextCursor(doc)
            a.setPosition(block.position() + start)
            b.setPosition(block.position() + end)
            if self.view.cursorRect(a).left() <= pos.x() <= self.view.cursorRect(b).left():
                return hit
        return None

    def _hover_status(self, hit):
        """Browser-style link feedback in the window status bar.

        mouseMove is a hot path, so the bar is only touched when the
        hovered target actually changes.
        """
        text = f"Open {hit[2]}" if hit else ""
        if text == getattr(self, "_hover_text", None):
            return
        self._hover_text = text
        try:
            self.window()._set_status_message(text)
        except (AttributeError, RuntimeError):
            pass  # windowless pane (tests, teardown)

    def open_link(self, hit):
        """A clicked path: hand it to the window's viewer router."""
        self._open_path(hit[2], hit[3], hit[5])

    def _paint_full(self, rows, fg0, bg0, caret=None):
        """Wipe + rebuild inside one edit block (first paint, width
        resize, theme switch).

        One edit block = one layout pass and a single viewport update
        when we return to the event loop — no blank frame between wipe
        and rebuild, no per-block relayout churn even with a full
        screen of output."""
        self._render_count += 1
        cur = self.view.textCursor()
        cur.beginEditBlock()
        try:
            cur.select(QTextCursor.Document)
            cur.removeSelectedText()
            for i, row in enumerate(rows):
                self._insert_runs(
                    cur,
                    row,
                    fg0,
                    bg0,
                    caret[1] if caret and caret[0] == i else None,
                    self._link_cells(row),
                )
                cur.insertBlock()
        finally:
            cur.endEditBlock()
        self._cursor_at = None  # document is new: reposition unconditionally

    def _paint_shift(self, rows, lo, n, fg0, bg0, caret=None) -> set[int]:
        """Slide blocks to the new window, touching only the delta.

        Old window [old_lo, old_lo+old_n) and new [lo, lo+n) overlap
        (checked by the caller): drop scrolled-off head/tail blocks,
        prepend/append the fresh rows, keep everything shared — no
        clear(), no re-detect for carried rows. Returns the fresh
        viewport indices (dirty rows outside them still need paint).
        Block i always shows absolute line cells_start+i, so selection
        anchors and cursor tracking (absolute coords) survive untouched;
        baked caret art slides with its row, and `_cursor_to` (after
        `_paint` in `_render`) repaints on real moves.
        """
        doc = self.view.document()
        old_lo = self._paint_start
        old_n = doc.blockCount() - 1  # row blocks (trailing block is empty)
        keep_lo = max(old_lo, lo)
        keep_hi = min(old_lo + old_n, lo + n)
        cur = QTextCursor(doc)
        cur.beginEditBlock()
        try:
            # Drop scrolled-off head blocks.
            if keep_lo > old_lo:
                cur.movePosition(QTextCursor.Start)
                end = QTextCursor(doc)
                end.movePosition(QTextCursor.Start)
                end.movePosition(QTextCursor.NextBlock, QTextCursor.MoveAnchor, keep_lo - old_lo)
                cur.setPosition(end.position(), QTextCursor.KeepAnchor)
                cur.removeSelectedText()
            # Drop scrolled-off tail blocks (keep the trailing empty).
            tail_drop = (old_lo + old_n) - keep_hi
            if tail_drop > 0:
                # Select [start of first dropped row, start of trailing
                # empty): from End, t PreviousBlocks land on R_{m-t}.
                tail = QTextCursor(doc)
                tail.movePosition(QTextCursor.End)
                tail.movePosition(QTextCursor.StartOfBlock, QTextCursor.MoveAnchor)
                head = QTextCursor(doc)
                head.movePosition(QTextCursor.End)
                head.movePosition(QTextCursor.PreviousBlock, QTextCursor.MoveAnchor, tail_drop)
                head.movePosition(QTextCursor.StartOfBlock, QTextCursor.MoveAnchor)
                tail.setPosition(head.position(), QTextCursor.KeepAnchor)
                tail.removeSelectedText()
            # Prepend fresh head rows.
            fresh: set[int] = set()
            if lo < keep_lo:
                head = QTextCursor(doc)
                head.movePosition(QTextCursor.Start)
                for j, row in enumerate(rows[:keep_lo - lo]):
                    self._insert_runs(
                        head,
                        row,
                        fg0,
                        bg0,
                        caret[1] if caret and caret[0] == j else None,
                        self._link_cells(row),
                    )
                    head.insertBlock()
                    fresh.add(j)
            # Append fresh tail rows into the trailing empty block.
            if keep_hi < lo + n:
                tail = QTextCursor(doc)
                tail.movePosition(QTextCursor.End)
                for j in range(keep_hi - lo, n):
                    row = rows[j]
                    self._insert_runs(
                        tail,
                        row,
                        fg0,
                        bg0,
                        caret[1] if caret and caret[0] == j else None,
                        self._link_cells(row),
                    )
                    tail.insertBlock()
                    fresh.add(j)
        finally:
            cur.endEditBlock()
        return fresh

    def _paint_subset(self, rows, indices, fg0, bg0, caret=None, count=True):
        """Rewrite exactly the given visible rows (dirty set).

        `caret` = viewport (row, col) of the soft block to bake in;
        blink repaints pass count=False so overlay frames don't read as
        content repaints."""
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
                self._insert_runs(
                    cur,
                    rows[i],
                    fg0,
                    bg0,
                    caret[1] if caret and caret[0] == i else None,
                    self._link_cells(rows[i]),
                )
                touched = True
        finally:
            cur.endEditBlock()
        if touched and count:
            self._render_count += 1

    def _insert_runs(self, cur, row, fg0, bg0, caret_col=None, links=None):
        """Insert one viewport row, merging adjacent same-style cells.

        `caret_col` reverses the block cursor cell (TUI soft-cursor
        parity); `links` underlines clickable path cells."""
        run_text: list[str] = []
        run_key = None
        for ci, (text, fg, bg, attrs) in enumerate(row):
            if links and ci in links:
                attrs |= 1 << 3  # underline
            key = (fg, bg, attrs)
            if caret_col is not None and ci == caret_col:
                key = (bg, fg, attrs ^ (1 << 5))
            if key != run_key:
                if run_text:
                    cur.insertText("".join(run_text), self._fmt(run_key, fg0, bg0))
                    run_text = []
                run_key = key
            run_text.append(text if not (attrs & (1 << 6)) else " ")
        if run_text:
            cur.insertText("".join(run_text), self._fmt(run_key, fg0, bg0))

    def _caret_cell(self, start, cursor, nrows):
        """Viewport (row, col) of the soft block cursor, or None.

        Focus-gated like a real terminal: an unfocused pane shows no
        block, and loses it the moment focus leaves the view. Also
        DECTCEM-gated: an app that hides its cursor (?25l) to draw its
        own must not get our block blinking beside it."""
        if not self._caret_on or not self.view.hasFocus():
            return None
        if not self._modes.get("cursor_visible", True):
            return None
        cx, cy_abs = cursor
        row = cy_abs - start
        return (row, cx) if 0 <= row < nrows else None

    def _on_view_focus(self, focused: bool):
        """Show/clear the block on focus changes and run the blink clock
        only while focused."""
        self._caret_on = True
        if focused:
            if self._blink_ms:
                self._caret_timer.start(self._blink_ms)  # fresh phase
        else:
            self._caret_timer.stop()  # unfocused: no blink churn
        if self._cursor_at is not None:
            self._repaint_row(self._cursor_at[1])

    def _repaint_row(self, abs_line):
        """Repaint one cached row (cursor move/blink: content unchanged)."""
        if self._cells is None:
            return
        i = abs_line - self._cells_start
        if not (0 <= i < len(self._cells)):
            return
        fg0 = self.view.palette().color(self.view.foregroundRole())
        bg0 = self.view.palette().color(self.view.backgroundRole())
        caret = self._caret_cell(self._cells_start, self._cursor_at, len(self._cells))
        self._paint_subset(self._cells, [i], fg0, bg0, caret, count=False)

    def _blink_caret(self):
        """Toggle the block cursor phase; only its own row repaints."""
        if not self.view.hasFocus():
            return  # timer raced a blur: nothing to blink
        if not self._modes.get("cursor_visible", True):
            return  # app hid its cursor: no block to blink, no churn
        if QApplication.mouseButtons() != Qt.NoButton:
            return  # live drag: leave the selection alone
        self._caret_on = not self._caret_on
        if self._cursor_at is not None:
            self._repaint_row(self._cursor_at[1])

    def _cursor_to(self, cursor, start, nrows):
        """Track the terminal cursor and paint its block on move.

        A move re-shows the block and restarts the blink phase, like a
        terminal: the rows left and entered are repainted from the cached
        cells (a move need not dirty any row). Only the focused view
        shows a block."""
        prev = self._cursor_at
        self._cursor_at = tuple(cursor)
        if prev == self._cursor_at:
            return
        focused = self.view.hasFocus()
        if focused:
            self._caret_on = True
            if self._blink_ms:
                self._caret_timer.start(self._blink_ms)  # sync the phase
            if prev is not None and prev[1] != self._cursor_at[1]:
                self._repaint_row(prev[1])  # clear the old block
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
        if focused:
            self._repaint_row(cy_abs)  # draw the block on its new row

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

    def _fmt(self, key, fg0, bg0) -> QTextCharFormat:
        """Cell format: the theme's ink/paper for terminal defaults, and the
        tool's own colors verbatim for everything it painted.

        Cached per style key (qtermwidget keeps a color table for the
        same reason): a streaming poll repaints dozens of rows of the
        same few styles, and rebuilding QTextCharFormat + re-parsing
        color strings per run dominated the paint cost."""
        cached = self._fmt_cache.get(key)
        if cached is not None:
            return cached
        fg, bg, attrs = key
        fmt = QTextCharFormat()
        fgc, bgc = cell_qcolor(fg, fg0), cell_qcolor(bg, bg0)
        if attrs & (1 << 5):  # reverse: swap the resolved roles
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
        if len(self._fmt_cache) > 256:  # degenerate rainbow: stay bounded
            self._fmt_cache.clear()
        self._fmt_cache[key] = fmt
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
        elif e.key() == Qt.Key_Escape:
            # Esc belongs to the running app (vim, less, fzf, agent TUIs).
            # Lace's window-wide "close sidebar" Esc binding is disabled in
            # KilimWindow, so the key arrives here (see _init_inner).
            data = b"\x1b"
        elif e.key() == Qt.Key_Tab:
            # Plain Tab is a tab character; Shift+Tab is CBT (back-tab),
            # which is what TUIs expect for backwards navigation.
            data = b"\x1b[Z" if e.modifiers() & Qt.ShiftModifier else b"\t"
        elif e.key() == Qt.Key_Backtab:
            data = b"\x1b[Z"
        elif (seq := _arrow_seq(e.key(), app_cursor)) is not None:
            data = seq
        elif e.modifiers() & Qt.ControlModifier and t:
            data = bytes([ord(t.upper()) & 0x1F]) if len(t) == 1 else None
        elif t:
            data = t.encode("utf-8", "replace")
        if data:
            self._send(data)
        # read-only widget: swallow (no super() call)

    def _send(self, data: bytes):
        """Forward input to the shell, snapping back to the live tail."""
        self._follow = True  # typing snaps back to the live tail
        self._sel = None  # ...and clears the selection, like a terminal
        if self.view.textCursor().hasSelection():
            self.view.setTextCursor(QTextCursor(self.view.document()))
        self.bridge.submit(lambda: self.bridge.core.write_term(self.pane_id, data))

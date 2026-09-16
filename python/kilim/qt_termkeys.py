"""Terminal-protocol encodings: key bytes, mouse reports, cell widths."""

from __future__ import annotations




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

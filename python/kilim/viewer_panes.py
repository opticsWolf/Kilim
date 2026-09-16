"""File and markdown viewer panes plus their shared web CSS."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QPlainTextEdit,
    QScrollBar,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from kilim.qt_bridge import Bridge
from kilim.qt_themes import cell_qcolor


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
        self.path: str | None = None  # click-opened files know their path
        self.setReadOnly(True)
        self.setFont(QFont("Cascadia Mono", 10))
        self.refresh()

    def goto_line(self, line: int) -> None:
        """Scroll a `path:line` hit to its line (1-based, clamped)."""
        block = self.document().findBlockByNumber(max(0, line - 1))
        if block.isValid():
            self.setTextCursor(QTextCursor(block))
            self.centerCursor()

    def refresh(self):
        from PySide6.QtGui import QPalette, QTextBlockFormat

        from kilim import theme_background

        try:
            rows = list(self.bridge.core.highlighted_file(self.pane_id))
        except (AttributeError, ValueError) as e:  # vanished/undecodable
            rows = [[(f"Cannot display this file: {e}", "default", "default")]]
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
        for i, row in enumerate(rows):
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

    def __init__(self, bridge: Bridge, pane_id: str, path: str | None = None, html_file: str | None = None):
        super().__init__()
        self.bridge = bridge
        self.pane_id = pane_id
        self.path = path
        self.html_file = html_file  # set: render the file itself, not markdown
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._build_view())

    def set_source(self, path: str, html_file: str | None = None) -> None:
        """Retarget this pane (reopen from history / open another html)."""
        self.path = path
        self.html_file = html_file
        self.refresh()

    def refresh(self):
        """Re-render with the current markdown theme (menu switching)."""
        lay = self.layout()
        old = lay.takeAt(0).widget() if lay.count() else None
        if old is not None:
            old.deleteLater()
        lay.addWidget(self._build_view())

    def _page(self) -> str | None:
        """HTML for this pane: the raw file for html, else mordant."""
        if self.html_file:
            try:
                text = self.bridge.core.read_text_file(self.html_file)
            except (AttributeError, ValueError, OSError):
                try:
                    text = Path(self.html_file).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    return None
            return self._style_page(text)
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
        return page

    def _build_view(self):
        page = self._page()
        view = self._make_view(page)
        if view is None and not self.html_file:  # last resort: source as text
            fb = QPlainTextEdit()
            fb.setReadOnly(True)
            fb.setFont(QFont("Cascadia Mono", 10))
            try:
                source = "\n".join(
                    "".join(t for t, _, _ in row)
                    for row in self.bridge.core.highlighted_file(self.pane_id)
                )
            except (AttributeError, ValueError) as e:  # vanished/undecodable
                source = f"Preview unavailable: {e}"
            fb.setPlainText(source)
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

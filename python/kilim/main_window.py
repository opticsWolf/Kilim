"""KilimWindow: docks, menus, themes, viewers, shells, status bar."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from lace.frameless_window import FramelessLaceMainWindow

from kilim import perspective as perspectives
from kilim.perspective import layout_groups
from kilim.qt_bridge import Bridge
from kilim.qt_themes import register_kilim_lace_themes
from kilim.qt_util import _elide_path, _same_path
from kilim.terminal_pane import TerminalPane
from kilim.title_bar import KilimTitleBar
from kilim.viewer_panes import FilePane, MarkdownPane


class KilimWindow(FramelessLaceMainWindow):
    def __init__(self, layout_path: str, perspective_path: str | None = None):
        # Frameless chrome with the menus embedded in the title bar.
        super().__init__(title_bar=KilimTitleBar)
        from lace import DockManager, DockWidget
        from lace import DockWidgetArea

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
        self._setup_icon()
        self.manager = DockManager(self)
        # Floats are frameless with the plain Lace title bar. Lace 0.7.5
        # resolves `floating_title_bar = None` to LaceStandardTitleBar
        # itself, so only the mode is set; the Kilim menus stay on the main
        # window's bar while a float gets the themed Lace bar with its own
        # icon / title / window buttons. (The manager installs the palette
        # bridges — dock tree and app-wide for top-level popups — on its
        # own since 0.7.5; no manual DockThemeBridge.)
        from lace import TitleBarMode

        self.manager.title_bar_mode = TitleBarMode.custom
        # Esc belongs to the focused terminal. Lace >= 0.7.6 gates its
        # window-wide "close sidebar" Esc binding on overlay visibility, so
        # no workaround is needed here; with an overlay up, Esc closes it.
        # Explicit, after the manager: keeps the title bar on top.
        self.setCentralWidget(self.manager._root)
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
        self.terminal_theme: str | None = None
        self._kilim_by_lace = register_kilim_lace_themes()
        self._theme_actions: dict[str, dict[str, object]] = {"lace": {}, "code": {}, "terminal": {}}
        self.file_history: list[str] = []  # viewer files, newest first
        self.default_shell: tuple[str, str, list[str]] | None = None
        self._viewer_seq = 0  # unique ids for click-opened viewer docks
        self._viewer_area = None  # viewer tab group's DockAreaWidget

        import json

        raw = json.loads(doc)
        panes = {p["id"]: p for p in raw["panes"]}
        InitialActive = raw["layout"].get("active", "")
        self._active = InitialActive
        # Terminals theme separately (Themes menu); layouts predating the
        # key follow the code theme, which is what they always did.
        self.terminal_theme = raw["layout"].get("terminal_theme") or self.bridge.core.theme()
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
                    inner = TerminalPane(self.bridge, pid, self.terminal_theme)
                elif kind == "markdown":
                    inner = MarkdownPane(self.bridge, pid, p.get("path"))
                else:
                    inner = FilePane(self.bridge, pid)
                    inner.path = p.get("path")
                dock = DockWidget(p.get("title", pid))
                dock.setObjectName(pid)  # pane ids = dock names (round-trip key)
                dock.set_widget(inner)
                if kind == "term":
                    self.term_panes[pid] = inner
                    inner._set_badge = lambda on, pid=pid: self._badge_pane(pid, on)
                    inner._open_path = self.open_in_viewer
                elif kind == "markdown":
                    self.md_panes[pid] = inner
                    dock.closed.connect(lambda pid=pid: self._dispose_viewer(pid))
                else:
                    self.file_panes[pid] = inner
                    dock.closed.connect(lambda pid=pid: self._dispose_viewer(pid))
                area = DockWidgetArea.left if kind == "term" else DockWidgetArea.right
                if area_widget is None:
                    area_widget = self.manager.add_dock_widget(area, dock)
                else:
                    # center + target = tabify (keeps manager registration).
                    self.manager.add_dock_widget(DockWidgetArea.center, dock, area_widget)
                self.pane_docks[pid] = dock
        # The arrangement the layout file defines, captured before the
        # sidecar is applied: Reset Layout returns here. The sidecar is a
        # *session* restore; it must not become the default.
        try:
            self._creation_state = self.manager.save_state()
        except Exception:  # noqa: BLE001 — reset falls back to the sidecar
            self._creation_state = None
        # Round-trip: restore previous dock geometry when names still match.
        sidecar = perspective_path or str(perspectives.sidecar_for(self.layout_path))
        self.perspective_path = sidecar
        data = perspectives.load(sidecar)
        if data is not None:
            perspectives.apply(self, data)
        # Session extras ride in the sidecar (not the shared layout): the
        # Lace chrome theme, the chosen default terminal, recent files.
        saved = self._sidecar()
        if saved.get("lace_theme"):
            self.apply_lace_theme(saved["lace_theme"], persist=False)
        from kilim import shells

        if saved.get("default_shell"):
            entry = shells.find_shell(str(saved["default_shell"]))
            if entry is not None:
                self.default_shell = entry
        if self.default_shell is None:
            self.default_shell = shells.default_entry()
        history = saved.get("file_history")
        if isinstance(history, list):
            self.file_history = [p for p in history if isinstance(p, str)]
        if self.lace_theme is None and "kilim_midnight" in self._kilim_by_lace:
            self.apply_lace_theme("kilim_midnight")  # fresh launch opens unified
        self._build_menus()
        self._build_status_bar()
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
        # Chromium (the markdown preview) recreates the top-level native
        # handle when the first QWebEngineView loads its page. Lace 0.7.5
        # watches QEvent.WinIdChange and re-applies the frameless chrome
        # (ensure_frameless_chrome) for exactly this, so no manual
        # updateFrameless() is needed here anymore.

    def _build_status_bar(self):
        """Bottom bar: transient messages left, active pane + theme right.

        QMainWindow lays the status bar out itself — no resize handler and
        no manual geometry anywhere — and Lace's app-wide DockThemeBridge
        pushes the themed palette onto it, so it follows theme switches the
        same way dock chrome does.
        """
        bar = self.statusBar()
        # No resize grip (QSizeGrip in the corner): the frameless window's own
        # native edges already resize, and the grip reads as a stray glyph.
        bar.setSizeGripEnabled(False)
        # 5px breathing room left and right. The message is a label (Qt paints
        # its temporary message at a 2px offset no margin can move); the bar
        # already indents it 2px, so +3 lands it at 5. The right 5px are
        # *widget* margins: the bar's layout object gets replaced on relayout
        # (dropping anything set on it), while widget margins survive and the
        # bar's reformatting honors them.
        bar.setContentsMargins(0, 0, 5, 0)
        self._status_message = QLabel("")
        self._status_message.setContentsMargins(3, 0, 0, 0)
        bar.addWidget(self._status_message, 1)
        self._status_pane = QLabel("")
        self._status_theme = QLabel("")
        bar.addPermanentWidget(self._status_pane)
        bar.addPermanentWidget(self._status_theme)
        self._refresh_status()

    def _set_status_message(self, text: str):
        """Left-hand transient text (link hover); empty clears it."""
        if hasattr(self, "_status_message"):
            self._status_message.setText(text)

    def _refresh_status(self):
        """Both permanent widgets: active pane + code theme (as the TUI bar)."""
        if not hasattr(self, "_status_pane"):
            return  # theme setup runs before _build_status_bar
        self._refresh_status_pane()
        try:
            self._status_theme.setText(f"[{self.bridge.core.theme()}]")
            self._status_theme.setToolTip("Code / preview theme (Themes menu)")
        except (AttributeError, RuntimeError):
            pass  # half-torn-down window

    def _refresh_status_pane(self):
        text, tip = self._status_pane_info()
        self._status_pane.setText(text)
        self._status_pane.setToolTip(tip)

    def _status_pane_info(self) -> tuple[str, str]:
        """(label, tooltip) for the pane that currently has focus."""
        if not hasattr(self, "_status_pane"):
            return "", ""
        try:
            pid = self.session_active()
            if pid in self.term_panes:
                note = " (exited)" if pid in set(self.bridge.core.exited_terms()) else ""
                return f"Terminal \u00b7 {pid}{note}", f"terminal pane {pid}"
            pane = self.file_panes.get(pid)
            if pane is not None and getattr(pane, "path", None):
                return f"File \u00b7 {_elide_path(pane.path)}", pane.path
            pane = self.md_panes.get(pid)
            if pane is not None and getattr(pane, "path", None):
                what = "Web" if pane.html_file else "Preview"
                return f"{what} \u00b7 {_elide_path(pane.path)}", pane.path
        except (AttributeError, RuntimeError):
            pass
        return "", ""

    def _setup_icon(self):
        """Kilim icon for the window (drawn in the title bar) and the app.

        Demo `_setup_icon` parity: `icon.ico` ships next to this file but
        was never loaded, so the frameless chrome drew Qt's stock icon."""
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QStyle

        icon = QIcon(str(Path(__file__).with_name("icon.ico")))
        if icon.isNull() or icon.pixmap(16, 16).isNull():
            icon = self.style().standardIcon(QStyle.SP_TitleBarMenuButton)
        self.setWindowIcon(icon)
        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)

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
        self._refresh_status_pane()  # status bar tracks the focused pane
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
        views = self.titleBar.views_menu
        for pid, dock in self.pane_docks.items():
            action = dock.toggle_view_action()
            action.setText(dock.windowTitle())
            views.addAction(action)
        views.addSeparator()
        show_all = views.addAction("Show All Panes")
        show_all.triggered.connect(self.restore_all_panes)
        reset = views.addAction("Reset Layout")
        reset.triggered.connect(self.reset_layout)
        self._build_terminal_menu()
        self._build_files_menu()
        self._term_seq = 0
        self._build_themes_menu()

    def _build_terminal_menu(self):
        """Terminal menu: new default shell, default picker, all found shells.

        Only shells discovered on this machine are listed (cross-platform
        `kilim.shells`); the default is remembered in the sidecar and the
        `New <label>` action spawns exactly it."""
        from PySide6.QtGui import QActionGroup

        from kilim import shells

        menu = self.titleBar.terminal_menu
        menu.clear()
        entries = shells.find_shells()
        default = self.default_shell or shells.default_entry()
        if default is not None:
            label, cmd, args = default
            act = menu.addAction(f"New {label}")
            act.triggered.connect(
                lambda _c=False, n=label, c=cmd, a=args: self.launch_shell(n, c, a)
            )
            menu.addSeparator()
        pick = menu.addMenu("Default Terminal")
        group = QActionGroup(self)
        group.setExclusive(True)
        for label, _cmd, _args in entries:
            act = pick.addAction(label)
            act.setCheckable(True)
            act.setChecked(default is not None and label == default[0])
            act.triggered.connect(
                lambda _c=False, l=label: self.set_default_shell(l)
            )
            group.addAction(act)
        if not entries:
            none = pick.addAction("No shells found")
            none.setEnabled(False)
        menu.addSeparator()
        for label, cmd, args in entries:
            act = menu.addAction(label)
            act.triggered.connect(
                lambda _c=False, n=label, c=cmd, a=args: self.launch_shell(n, c, a)
            )

    def set_default_shell(self, label: str) -> None:
        """Pick the shell `New <label>` spawns; remembered across launches."""
        from kilim import shells

        entry = shells.find_shell(label)
        if entry is None:
            return
        self.default_shell = entry
        self._save_sidecar(default_shell=label)
        self._build_terminal_menu()

    def _build_files_menu(self):
        """Files menu: reopen recently opened viewer files (newest first).

        Closing a viewer drops its dock; the history is what brings files
        back, so an unbounded pile of docks never accumulates."""
        menu = self.titleBar.files_menu
        menu.clear()
        menu.setToolTipsVisible(True)
        if not self.file_history:
            empty = menu.addAction("No Files Yet")
            empty.setEnabled(False)
            return
        for path in self.file_history:
            act = menu.addAction(Path(path).name or path)
            act.setToolTip(path)
            act.triggered.connect(
                lambda _c=False, p=path: self.open_history_file(p)
            )
        menu.addSeparator()
        clear = menu.addAction("Clear History")
        clear.triggered.connect(self.clear_history)

    def open_history_file(self, path: str):
        """Reopen a history entry (dropped when the file is gone)."""
        if not Path(path).is_file():
            self._forget_file(path)
            return
        try:
            kind = self.bridge.core.classify_file(path)
        except (AttributeError, ValueError):
            kind = "missing"
        if kind in ("binary", "missing"):
            self._forget_file(path)
            return
        self.open_in_viewer(path, None, kind)

    def clear_history(self):
        self.file_history.clear()
        self._build_files_menu()
        self._save_sidecar(file_history=[])

    def _remember_file(self, path: str):
        """Newest first, deduped (case-insensitive paths), capped."""
        self.file_history = [p for p in self.file_history if not _same_path(p, path)]
        self.file_history.insert(0, path)
        del self.file_history[20:]
        self._build_files_menu()
        self._save_sidecar(file_history=self.file_history)

    def _forget_file(self, path: str):
        before = len(self.file_history)
        self.file_history = [p for p in self.file_history if not _same_path(p, path)]
        if len(self.file_history) != before:
            self._build_files_menu()
            self._save_sidecar(file_history=self.file_history)

    def open_in_viewer(self, path: str, line: int | None = None, kind: str | None = None):
        """Open a clicked terminal path in a fresh viewer dock.

        `kind` comes from the Rust detector: markdown/html get the web
        pane (raw file for html, mordant render for markdown), everything
        else the syntect text viewer. Binary/missing never get here —
        those paths are not links. A file that is already open is raised
        instead of duplicated."""
        if not kind:
            try:
                kind = self.bridge.core.classify_file(path)
            except (AttributeError, ValueError):
                kind = "missing"
        if kind in ("binary", "missing"):
            return
        open_here = self._viewer_with_path(path)
        if open_here is not None:
            self._raise_viewer(open_here)
            return
        dock = self._add_viewer(path, kind, line)
        self._remember_file(path)
        self._raise_viewer(dock)

    def _viewer_with_path(self, path: str):
        """Dock of an already-open viewer for `path`, or None."""
        for pid, pane in list(self.file_panes.items()) + list(self.md_panes.items()):
            if getattr(pane, "path", None) and _same_path(pane.path, path):
                return self.pane_docks.get(pid)
        return None

    def _add_viewer(self, path: str, kind: str, line: int | None = None):
        """Create the session pane + dock for one viewer file."""
        from lace import DockWidget
        from lace import DockWidgetArea

        self._viewer_seq += 1
        pid = f"view{self._viewer_seq}"
        while pid in self.pane_docks:
            self._viewer_seq += 1
            pid = f"view{self._viewer_seq}"
        title = Path(path).name or path
        self.bridge.core.insert_file_pane(
            pid, title, path, kind in ("markdown", "html")
        )
        if kind == "code":
            inner = FilePane(self.bridge, pid)
            inner.path = path
            self.file_panes[pid] = inner
            if line:
                inner.goto_line(line)
        else:
            inner = MarkdownPane(
                self.bridge, pid, path, html_file=path if kind == "html" else None
            )
            self.md_panes[pid] = inner
        dock = DockWidget(title)
        dock.setObjectName(pid)
        dock.set_widget(inner)
        target = self._live_viewer_area()
        if target is not None:
            # Tabify into the open viewer group (center + *area* target:
            # Lace's add_dock_widget expects a DockAreaWidget here).
            self._viewer_area = self.manager.add_dock_widget(
                DockWidgetArea.center, dock, target
            )
        else:
            self._viewer_area = self.manager.add_dock_widget(DockWidgetArea.right, dock)
        self.pane_docks[pid] = dock
        dock.closed.connect(lambda pid=pid: self._dispose_viewer(pid))
        views = self.titleBar.views_menu
        views.insertAction(views.actions()[0], dock.toggle_view_action())
        return dock

    def _live_viewer_area(self):
        """The viewer tab group's area, or None (touching a deleted Qt
        wrapper raises: a disposed dock may have taken the area with it)."""
        if self._viewer_area is None:
            return None
        try:
            self._viewer_area.dock_widgets()
            return self._viewer_area
        except RuntimeError:
            self._viewer_area = None
            return None

    def _raise_viewer(self, dock) -> None:
        """Show + focus a viewer dock. Lace's own show path raises the tab
        (`set_current_dock_widget`), so `toggle_view(True)` is enough."""
        try:
            dock.toggle_view(True)
            pid = self.pane_of(dock)
            pane = self.file_panes.get(pid) or self.md_panes.get(pid)
            if pane is not None:
                self._focus_viewer(pane)
        except RuntimeError:
            pass  # closing

    def _focus_viewer(self, pane) -> None:
        """Focus the pane's inner widget (web view / text view)."""
        if isinstance(pane, MarkdownPane):
            lay = pane.layout()
            if lay is not None and lay.count():
                w = lay.itemAt(0).widget()
                if w is not None:
                    w.setFocus()
            return
        pane.setFocus()

    def _dispose_viewer(self, pid: str) -> None:
        """A viewer dock closed: drop widget + session pane (history keeps
        the path, so reopening is one click)."""
        pane = self.file_panes.pop(pid, None)
        if pane is None:
            pane = self.md_panes.pop(pid, None)
        if pane is None:
            return  # not a viewer (terminals stay restorable)
        dock = self.pane_docks.pop(pid, None)
        if dock is not None:
            act = dock.toggle_view_action()
            views = self.titleBar.views_menu
            try:
                if act in views.actions():
                    views.removeAction(act)
            except RuntimeError:
                pass
        try:
            self.bridge.core.remove_pane(pid)
        except (AttributeError, ValueError):
            pass
        pane.setParent(None)
        pane.deleteLater()

    def _build_themes_menu(self):
        """Themes menu: Lace chrome, code (Qt + TUI), terminal (Qt terms).

        One shared registry (the Kilim ten, Kilim Midnight default) feeds
        every submenu, so no choice can silently fall back. Code covers
        Markdown too (fences follow the code theme); terminals theme
        separately. Choices persist: code/terminal into the layout file,
        Lace into the sidecar.
        """
        from PySide6.QtGui import QActionGroup

        from kilim import list_themes

        themes = self.titleBar.themes_menu

        # Lace dock chrome: the Kilim group only (flat, radio-checked).
        # Stock Lace presets stay out — the app exposes Kilim chrome.
        lace_menu = themes.addMenu("Lace")
        try:
            from lace import apply_dock_theme, theme_groups

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

        # Code theme: TUI file panes + Qt FilePane share the session theme;
        # Qt MarkdownPanes follow it too (one apply, no divergence).
        code_menu = themes.addMenu("Code")
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

        # Terminal theme: Qt terminal panes only. Same ten as code;
        # an unset choice follows the code theme (the old behavior).
        term_menu = themes.addMenu("Terminal")
        term_group = QActionGroup(self)
        term_group.setExclusive(True)
        for name in list_themes():
            act = term_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == self.terminal_theme)
            self._theme_actions["terminal"][name] = act
            act.triggered.connect(
                lambda _c=False, n=name: self.apply_terminal_theme(n)
            )
            term_group.addAction(act)

    def apply_code_theme(self, name: str):
        """Session code theme → repaint Qt FilePanes + MarkdownPanes.

        One apply covers both: fences always use the code theme, so the
        two can never diverge from this surface. Both keys persist equal
        (the TUI reads them separately) into the layout file.
        """
        if self.bridge.core.theme() == name and self.bridge.core.markdown_theme() == name:
            return
        self.bridge.core.set_theme(name)
        self.bridge.core.set_markdown_theme(name)
        for pane in self.file_panes.values():
            pane.refresh()
        # No deferral (unlike the Lace path below): the app palette does
        # not change here, so sampling it now reads current colors.
        for pane in self.md_panes.values():
            pane.refresh()
        self.save_themes()
        self._sync_theme_checks()

    def apply_terminal_theme(self, name: str):
        """Terminal theme → recolor Qt terminal panes, persist to layout."""
        if self.terminal_theme == name:
            return
        self.terminal_theme = name
        for pane in self.term_panes.values():
            pane.set_terminal_theme(name)
        self.save_themes()
        self._sync_theme_checks()

    def apply_lace_theme(self, key: str, persist: bool = True):
        """Dock chrome theme, effective immediately.

        Kilim chrome (`kilim_*`) is unified: selecting it also sets the
        same-named code + markdown + terminal themes and repaints (one
        click, all surfaces) — neo chrome selects the neo themes.
        """
        from lace import apply_dock_theme

        if apply_dock_theme(key):
            self.lace_theme = key
            if key in self._kilim_by_lace:
                syntect = self._kilim_by_lace[key]
                self.bridge.core.set_theme(syntect)
                self.bridge.core.set_markdown_theme(syntect)
                self.terminal_theme = syntect
                for pane in self.file_panes.values():
                    pane.refresh()
                for pane in self.term_panes.values():
                    pane.set_terminal_theme(syntect)
                # Markdown refresh is deferred: the theme bridge pushes the
                # app palette via singleShot(0), so a direct refresh here
                # would sample the previous theme's colors — scrollbar CSS
                # lagging exactly one switch behind. Queued after the
                # bridge, the palette is current when we sample it.
                for pane in self.md_panes.values():
                    QTimer.singleShot(0, pane.refresh)
            if persist:
                # Only a *chosen* theme reaches the layout file: the
                # fresh-launch/restored defaults must not rewrite the
                # shared layout (readers race the write) — the sidecar
                # already records what this session runs.
                self.save_themes()
                self.save_lace_theme()
            self._sync_theme_checks()
            self._refresh_status()  # theme label follows the switch

    def _sync_theme_checks(self):
        """Radio-check the Themes menu to live state (unified applies)."""
        want = {
            "lace": self.lace_theme,
            "code": self.bridge.core.theme(),
            "terminal": self.terminal_theme,
        }
        for group, current in want.items():
            for name, act in self._theme_actions.get(group, {}).items():
                try:
                    act.setChecked(name == current)
                except RuntimeError:  # wrapped C++ object deleted
                    pass

    def save_themes(self):
        """Write code/markdown/terminal choices back into the layout file."""
        import json as _json

        try:
            raw = _json.loads(Path(self.layout_path).read_text(encoding="utf-8"))
            raw.setdefault("layout", {})["theme"] = self.bridge.core.theme()
            raw["layout"]["markdown_theme"] = self.bridge.core.markdown_theme()
            raw["layout"]["terminal_theme"] = self.terminal_theme
            Path(self.layout_path).write_text(_json.dumps(raw, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            pass

    def _sidecar(self) -> dict:
        """Sidecar JSON as a dict (session extras live beside the Lace blob)."""
        import json as _json

        try:
            raw = _json.loads(Path(self.perspective_path).read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_sidecar(self, **extra) -> None:
        """Read-modify-write the sidecar: `perspectives.save` drops extras."""
        import json as _json

        raw = self._sidecar()
        raw.update(extra)
        try:
            Path(self.perspective_path).write_text(_json.dumps(raw, indent=2), encoding="utf-8")
        except OSError:
            pass

    def save_lace_theme(self):
        """Stash the Lace chrome choice in the perspective sidecar."""
        self._save_sidecar(lace_theme=self.lace_theme)

    @staticmethod
    def _shell_options() -> list[tuple[str, str, list[str]]]:
        """Launchable shells for this machine (cross-platform discovery)."""
        from kilim import shells

        return shells.find_shells()

    def launch_shell(self, name: str, cmd: str, args: list[str]):
        """Spawn a new shell pane (core) and dock it left."""
        from lace import DockWidget
        from lace import DockWidgetArea

        self._term_seq += 1
        pid = f"term-new{self._term_seq}"
        while pid in self.pane_docks:
            self._term_seq += 1
            pid = f"term-new{self._term_seq}"
        self.bridge.call(
            lambda: self.bridge.core.spawn_term(pid, name, cmd, args, 24, 80, 5000)
        )
        inner = TerminalPane(self.bridge, pid, self.terminal_theme)
        dock = DockWidget(name)
        dock.setObjectName(pid)
        dock.set_widget(inner)
        self.manager.add_dock_widget(DockWidgetArea.left, dock)
        self.pane_docks[pid] = dock
        self.term_panes[pid] = inner
        inner._set_badge = lambda on, pid=pid: self._badge_pane(pid, on)
        inner._open_path = self.open_in_viewer
        views = self.titleBar.views_menu
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
        """Show all panes and restore the layout file's creation arrangement.

        The sidecar only restores a *session* at launch; Reset Layout is
        the way back to the split/tab groups the layout file defines, using
        the state captured just before the sidecar was applied. Only when
        that capture failed does it fall back to the saved perspective."""
        self.restore_all_panes()
        state = getattr(self, "_creation_state", None)
        if state:
            try:
                if self.manager.restore_state(state):
                    self._persist_perspective()
                    return
            except Exception:  # noqa: BLE001 — fall back to the sidecar
                pass
        data = perspectives.load(self.perspective_path)
        if data is not None:
            perspectives.apply(self, data)

    def _persist_perspective(self):
        """Write the current arrangement + session extras to the sidecar."""
        perspectives.save(self.perspective_path, perspectives.capture(self))
        # save() rewrites the sidecar: re-stash the keys it doesn't know.
        extras = {"file_history": self.file_history}
        if self.lace_theme:
            extras["lace_theme"] = self.lace_theme
        if self.default_shell:
            extras["default_shell"] = self.default_shell[0]
        self._save_sidecar(**extras)

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

    def closeEvent(self, e):
        try:
            QApplication.instance().focusChanged.disconnect(self._on_focus_changed)
        except (RuntimeError, TypeError):
            pass  # never connected / already gone
        try:
            self._persist_perspective()
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

"""Kilim title bar: Lace chrome with the menus embedded."""

from __future__ import annotations

from lace.dock_styled import DockStyled
from lace.dock_theme import DockStyleCategory
from lace.frameless_window import LaceStandardTitleBar


class KilimTitleBar(LaceStandardTitleBar, DockStyled):
    """Lace title bar with the Kilim menu bar embedded in the chrome.

    VS Code-style unified bar: icon, then Views / Files / Terminal /
    Themes menus, then the window buttons — the shape of Lace's
    `demo_app_custom_titlebar_menus.MenuEmbeddedTitleBar`, minus its
    Window menu (the title-bar buttons already do that job). The menu bar
    is transparent so the bar's own themed background shows through;
    popups pull their colors from the same dock-theme tokens."""

    STYLE_CATEGORIES = (DockStyleCategory.TITLE_BAR, DockStyleCategory.SIDEBAR, DockStyleCategory.CORE)

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QMenuBar

        # The embedded menus are the chrome content; the window title is
        # not needed (demo pattern).
        self.titleLabel.hide()

        self.menu_bar = QMenuBar(self)
        # Same height as the bar: one continuous surface, items centered.
        self.menu_bar.setFixedHeight(self.height())
        # Anchored after the title (hidden, so this reads as right after the
        # icon). Never a literal index: the base layout order changed before
        # (Lace 0.7.5 added insert_content_widget for exactly this).
        self.insert_content_widget(self.menu_bar)

        self._build_menus()
        self._init_dock_style()

    def _build_menus(self) -> None:
        """Create the menus the window populates, and keep their names.

        The bar owns its menus (demo pattern), so callers never fish them
        out of `menu_bar.actions()` by position."""
        self.views_menu = self.menu_bar.addMenu("&Views")
        self.files_menu = self.menu_bar.addMenu("&Files")
        self.terminal_menu = self.menu_bar.addMenu("&Terminal")
        self.themes_menu = self.menu_bar.addMenu("&Themes")

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

    # paintEvent + canDrag are inherited from LaceStandardTitleBar (0.7.5):
    # the base fills the live theme background and vetoes drags starting on
    # QMenuBar/QMenu/QAbstractButton/QLineEdit children — our copies of both
    # were doing exactly the same thing.

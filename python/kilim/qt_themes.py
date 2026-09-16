"""Kilim Lace themes: Rust-owned palettes, Lace registration."""

from __future__ import annotations

from PySide6.QtGui import QColor

from kilim import kilim_themes_json


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


def register_kilim_lace_themes() -> dict[str, str]:
    """Build the Kilim Lace themes into Lace's registries.

    One "Kilim" group, ten entries: five palettes × classic chassis
    (`kilim_*`) + neo/edge chassis (`kilim_*_neo`). Lace labels render
    the syntect names ("Kilim Midnight Neo"), so all three menus share one
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

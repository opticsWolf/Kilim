"""Kilim Python surface — thin. All layout/highlight work lives in Rust."""

try:
    from kilim._core import CoreSession, list_themes, list_syntaxes

    try:
        from kilim._core import markdown_theme_names
    except ImportError:  # wheel built without the `markdown` feature
        def markdown_theme_names():  # type: ignore
            return list_themes()
    from kilim._core import blend_weight
    from kilim._core import kilim_themes_json
    from kilim._core import theme_background
    from kilim._core import theme_foreground
except ImportError:  # maturin develop not run yet
    CoreSession = None  # type: ignore

    def list_themes():  # type: ignore
        return []

    def list_syntaxes():  # type: ignore
        return []

    def markdown_theme_names():  # type: ignore
        return []

    def kilim_themes_json():  # type: ignore
        return "[]"

    def blend_weight():  # type: ignore
        return 0.0

    def theme_background(name):  # type: ignore
        return ""

    def theme_foreground(name):  # type: ignore
        return ""

__all__ = ["CoreSession", "list_themes", "list_syntaxes", "markdown_theme_names", "kilim_themes_json", "theme_background", "theme_foreground", "blend_weight"]

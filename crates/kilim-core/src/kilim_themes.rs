//! Kilim core themes: one palette driving Lace chrome + syntect code.
//!
//! Five themes, same expressive intent on every surface:
//! - Midnight: Lace `dark` blended toward `midnight` (deep navy-charcoal).
//! - Dark: Lace's stock default chrome (VS Code Dark+ gray/blue).
//! - Neutral: Lace `neutral` silver workstation.
//! - Light: Lace `light` high-clarity gray.
//! - Warm: Lace `warm` cozy brown.
//!
//! The syntect side starts from the closest existing theme and adjusts
//! only the chrome (background/foreground/selection) — token colors are
//! the base theme's own functional scope rules, never rewritten here:
//! - Midnight → Night Owl (deep-navy, vivid tokens)
//! - Dark → Visual Studio Dark+ (the stock look)
//! - Neutral → GitHub light (crisp on silver)
//! - Light → OneHalfLight
//! - Warm → gruvbox-dark (the warm expressive classic)
//!
//! Lace geometry is intentionally NOT here: all five share one chassis
//! (built Python-side from these colors). This module owns color only.

/// One unified theme: palette + closest syntect base.
///
/// Each palette ships two Lace keys sharing one syntect theme: the
/// classic chassis (`lace_key`) and the neo/edge chassis (`neo_key`).
/// A `neo_*` sibling shares the palette with a more colorful base for
/// users who want maximum token hues.
#[derive(Debug, Clone, Copy)]
pub struct KilimThemeDef {
    /// Lace registry key, classic chassis (`kilim_midnight`, …).
    pub lace_key: &'static str,
    /// Lace registry key, neo/edge chassis (`kilim_midnight_neo`, …).
    pub neo_key: &'static str,
    /// Syntect registry name (`Kilim Midnight`, …).
    pub syntect_name: &'static str,
    /// Closest existing syntect theme to adjust.
    pub base_syntect: &'static str,
    /// Neo sibling registry name (`Kilim Midnight Neo`, …).
    pub neo_name: &'static str,
    /// Its more colorful base.
    pub neo_base: &'static str,
    pub is_light: bool,
    /// Editor background: the code-pane/paper color. Light palettes use
    /// near-white (Lace default-light look); darks keep deep identity.
    /// Drives syntect `background`/`gutter` — every surface (Qt, TUI,
    /// markdown page, diagrams) follows this one value.
    pub editor_bg: &'static str,
    pub bg: &'static str,
    pub surface: &'static str,
    pub border: &'static str,
    pub text: &'static str,
    pub accent: &'static str,
    pub selection: &'static str,
}

pub const KILIM_THEMES: &[KilimThemeDef; 5] = &[
    KilimThemeDef {
        lace_key: "kilim_midnight",
        neo_key: "kilim_midnight_neo",
        syntect_name: "Kilim Midnight",
        base_syntect: "Night Owl-color-theme",
        neo_name: "Kilim Midnight Neo",
        neo_base: "Dracula",
        is_light: false,
        // 2/3 Lace dark + 1/3 Lace midnight (computed, see README).
        editor_bg: "#101319",
        bg: "#101319",
        surface: "#161a23",
        // Lace dark leaves border == bg, so the unfocused dock-area outline
        // (CORE.border_color) is invisible. Kilim follows warm's treatment
        // instead: border == surface, a faint lighter outline on the base.
        border: "#161a23",
        text: "#cbd0dc",
        accent: "#325ac6",
        selection: "#26355c",
    },
    KilimThemeDef {
        lace_key: "kilim_dark",
        neo_key: "kilim_dark_neo",
        syntect_name: "Kilim Dark",
        base_syntect: "Visual Studio Dark+",
        neo_name: "Kilim Dark Neo",
        neo_base: "OneDark-Pro",
        is_light: false,
        // Lace's stock default chrome: canvas #141414, panel/paper
        // #1e1e1e, #2d2d2d unfocused outline, Windows-blue accent.
        editor_bg: "#1e1e1e",
        bg: "#141414",
        surface: "#1e1e1e",
        border: "#2d2d2d",
        text: "#cccccc",
        accent: "#0078d4",
        selection: "#264f78",
    },
    KilimThemeDef {
        lace_key: "kilim_neutral",
        neo_key: "kilim_neutral_neo",
        syntect_name: "Kilim Neutral",
        base_syntect: "GitHub",
        neo_name: "Kilim Neutral Neo",
        neo_base: "tokyo-night-light-color-theme",
        is_light: true,
        editor_bg: "#dddee0",
        bg: "#bec1c5",
        surface: "#d2d5d9",
        border: "#aaadb2",
        text: "#1e232d",
        accent: "#286ebe",
        selection: "#a9c6e8",
    },
    KilimThemeDef {
        lace_key: "kilim_light",
        neo_key: "kilim_light_neo",
        syntect_name: "Kilim Light",
        base_syntect: "OneHalfLight",
        neo_name: "Kilim Light Neo",
        neo_base: "ayu-light",
        is_light: true,
        // Chrome is near-neutral with a whisper of cool (the stock Lace
        // light cast read too blue; ~4% saturation at the same lightness).
        editor_bg: "#ffffff",
        bg: "#dddee0",
        surface: "#f7f8f9",
        // Lace light leaves border == bg (invisible outline). Kilim follows
        // neutral's darker unfocused outline: the same -21/channel step off
        // bg that neutral takes (#bec1c5 -> #aaadb2).
        border: "#c8c9cb",
        text: "#2f3134",
        accent: "#3651d9",
        selection: "#ccd3e2",
    },
    KilimThemeDef {
        lace_key: "kilim_warm",
        neo_key: "kilim_warm_neo",
        syntect_name: "Kilim Warm",
        base_syntect: "gruvbox-dark",
        neo_name: "Kilim Warm Neo",
        neo_base: "LaserWave-color-theme",
        is_light: false,
        editor_bg: "#26201e",
        bg: "#26201e",
        surface: "#2e2724",
        border: "#2e2724",
        text: "#ebe1d2",
        accent: "#c86e3c",
        selection: "#5a3d2c",
    },
];

/// Bat asset themes the Kilim set is built from (everything else in the
/// asset blob stays out — the registry ships exactly the Kilim ten).
/// Kept next to the defs; a test pins them to the defs' bat-side bases.
#[cfg(feature = "markdown")]
pub(crate) const BAT_BASES: &[&str] = &["GitHub", "OneHalfLight", "gruvbox-dark", "Dracula"];

/// Look up a definition by either Lace key or syntect name.
pub fn kilim_theme(id: &str) -> Option<&'static KilimThemeDef> {
    KILIM_THEMES
        .iter()
        .find(|d| d.lace_key == id || d.neo_key == id || d.syntect_name == id || d.neo_name == id)
}

/// Build the ten syntect themes (five palettes × standard/neo) from
/// their bases, adjusting only chrome. `extra_bases` carries bases that
/// must NOT enter the registry (bat picks + parsed wheel files).
/// Registers into our registry; mirrors into mordant's (fences) via the
/// tmTheme writer. Requires the `markdown` feature. Names present win.
#[cfg(feature = "markdown")]
pub fn ensure_kilim_themes(
    extra_bases: &std::collections::HashMap<String, syntect::highlighting::Theme>,
    themes: &mut syntect::highlighting::ThemeSet,
    mirror: impl Fn(&str, &str) -> Result<(), String>,
) {
    use syntect::highlighting::Color;

    fn parse(hex: &str) -> Option<Color> {
        let h = hex.trim_start_matches('#');
        if h.len() == 6 {
            if let (Ok(r), Ok(g), Ok(b)) = (
                u8::from_str_radix(&h[0..2], 16),
                u8::from_str_radix(&h[2..4], 16),
                u8::from_str_radix(&h[4..6], 16),
            ) {
                return Some(Color { r, g, b, a: 0xFF });
            }
        }
        None
    }

    fn build(
        extra_bases: &std::collections::HashMap<String, syntect::highlighting::Theme>,
        themes: &mut syntect::highlighting::ThemeSet,
        mirror: &impl Fn(&str, &str) -> Result<(), String>,
        name: &str,
        base_name: &str,
        def: &KilimThemeDef,
    ) {
        if themes.themes.contains_key(name) {
            return;
        }
        let Some(base) = themes
            .themes
            .get(base_name)
            .or_else(|| extra_bases.get(base_name))
            .cloned()
        else {
            eprintln!("kilim: base theme '{base_name}' missing, skipping {name}");
            return;
        };
        let mut t = base;
        // Editor chrome: the curated paper color (near-white for light
        // palettes); every surface follows this one value.
        if let Some(bg) = parse(def.editor_bg) {
            t.settings.background = Some(bg);
            t.settings.gutter = Some(bg);
        }
        if let Some(fg) = parse(def.text) {
            t.settings.foreground = Some(fg);
        }
        if let Some(sel) = parse(def.selection) {
            t.settings.selection = Some(sel);
        }
        // Neo shares the standard sibling's display settings wholesale
        // (line highlight, caret, brackets …): same background behavior,
        // only token colors differ.
        if name == def.neo_name {
            if let Some(std) = themes.themes.get(def.syntect_name) {
                t.settings = std.settings.clone();
            }
        }
        let xml = super::highlight::theme_to_tmtheme(name, &t);
        let _ = mirror(name, &xml);
        themes.themes.insert(name.to_string(), t);
    }

    for def in KILIM_THEMES {
        build(extra_bases, themes, &mirror, def.syntect_name, def.base_syntect, def);
        build(extra_bases, themes, &mirror, def.neo_name, def.neo_base, def);
    }
}

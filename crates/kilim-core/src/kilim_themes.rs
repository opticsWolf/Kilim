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
//! the chrome (background/foreground/selection) plus one functional
//! token palette: each canonical element class (comment, string, keyword,
//! storage.type, storage.modifier, entity.name.function, …) gets its own
//! color, authored per theme and test-pinned pairwise-distinct. Selectors
//! are always element classes — never a language- or word-specific atom:
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
    /// Functional scope palette `(selector, hex)` for the standard theme:
    /// one entry per canonical element class (comment, string, keyword,
    /// storage.type, storage.modifier, entity.name.function, …) — never a
    /// language- or word-specific atom. Applied by recolor-and-fill, more
    /// specific selectors last; each theme's set is authored (and pinned
    /// by test) so no two classes render alike.
    pub tokens: &'static [(&'static str, &'static str)],
    /// Same for the neo sibling.
    pub neo_tokens: &'static [(&'static str, &'static str)],
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
        tokens: &[
            ("comment", "#637777"),
            ("string", "#ecc48d"),
            ("keyword", "#c792ea"),
            ("storage.type", "#7fdbca"),
            ("storage.modifier", "#ff5874"),
            ("entity.name.function", "#82aaff"),
            ("entity.name.type", "#d9f5dd"),
            ("entity.name.class", "#d9f5dd"),
            ("entity.name.struct", "#d9f5dd"),
            ("entity.name.enum", "#d9f5dd"),
            ("entity.name.trait", "#d9f5dd"),
            ("variable.parameter", "#c5e478"),
            ("constant.numeric", "#f78c6c"),
            ("constant.language", "#b2ccd6"),
        ],
        neo_tokens: &[
            ("comment", "#6272a4"),
            ("string", "#f1fa8c"),
            ("keyword", "#ff79c6"),
            ("storage.type", "#8be9fd"),
            ("storage.modifier", "#ff5555"),
            ("entity.name.function", "#50fa7b"),
            ("entity.name.type", "#f8f8f2"),
            ("entity.name.class", "#f8f8f2"),
            ("entity.name.struct", "#f8f8f2"),
            ("entity.name.enum", "#f8f8f2"),
            ("entity.name.trait", "#f8f8f2"),
            ("variable.parameter", "#ffb86c"),
            ("constant.numeric", "#bd93f9"),
            ("constant.language", "#66d9ef"),
        ],
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
        tokens: &[
            ("comment", "#608b4e"),
            ("string", "#ce9178"),
            ("keyword", "#c586c0"),
            ("storage.type", "#569cd6"),
            ("storage.modifier", "#d16969"),
            ("entity.name.function", "#dcdcaa"),
            ("entity.name.type", "#4ec9b0"),
            ("entity.name.class", "#4ec9b0"),
            ("entity.name.struct", "#4ec9b0"),
            ("entity.name.enum", "#4ec9b0"),
            ("entity.name.trait", "#4ec9b0"),
            ("variable.parameter", "#9cdcfe"),
            ("constant.numeric", "#b5cea8"),
            ("constant.language", "#e3bbab"),
        ],
        neo_tokens: &[
            ("comment", "#7f848e"),
            ("string", "#98c379"),
            ("keyword", "#c678dd"),
            ("storage.type", "#56b6c2"),
            ("storage.modifier", "#e06c75"),
            ("entity.name.function", "#61afef"),
            ("entity.name.type", "#e5c07b"),
            ("entity.name.class", "#e5c07b"),
            ("entity.name.struct", "#e5c07b"),
            ("entity.name.enum", "#e5c07b"),
            ("entity.name.trait", "#e5c07b"),
            ("variable.parameter", "#d4d4d4"),
            ("constant.numeric", "#d19a66"),
            ("constant.language", "#abb2bf"),
        ],
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
        tokens: &[
            ("comment", "#969896"),
            ("string", "#183691"),
            ("keyword", "#a71d5d"),
            ("storage.type", "#6f42c1"),
            ("storage.modifier", "#df5000"),
            ("entity.name.function", "#795da3"),
            ("entity.name.type", "#333333"),
            ("entity.name.class", "#333333"),
            ("entity.name.struct", "#333333"),
            ("entity.name.enum", "#333333"),
            ("entity.name.trait", "#333333"),
            ("variable.parameter", "#0086b3"),
            ("constant.numeric", "#63a35c"),
            ("constant.language", "#ed6a43"),
        ],
        neo_tokens: &[
            ("comment", "#888b94"),
            ("string", "#385f0d"),
            ("keyword", "#65359d"),
            ("storage.type", "#2959aa"),
            ("storage.modifier", "#7b43ba"),
            ("entity.name.function", "#006c86"),
            ("entity.name.type", "#343b58"),
            ("entity.name.class", "#343b58"),
            ("entity.name.struct", "#343b58"),
            ("entity.name.enum", "#343b58"),
            ("entity.name.trait", "#343b58"),
            ("variable.parameter", "#b15c00"),
            ("constant.numeric", "#965027"),
            ("constant.language", "#6172b0"),
        ],
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
        tokens: &[
            ("comment", "#a0a1a7"),
            ("string", "#50a14f"),
            ("keyword", "#a626a4"),
            ("storage.type", "#4078f2"),
            ("storage.modifier", "#e45649"),
            ("entity.name.function", "#0184bc"),
            ("entity.name.type", "#c18401"),
            ("entity.name.class", "#c18401"),
            ("entity.name.struct", "#c18401"),
            ("entity.name.enum", "#c18401"),
            ("entity.name.trait", "#c18401"),
            ("variable.parameter", "#7a3e9d"),
            ("constant.numeric", "#986801"),
            ("constant.language", "#5c6370"),
        ],
        neo_tokens: &[
            ("comment", "#adaeb1"),
            ("string", "#86b300"),
            ("keyword", "#fa8532"),
            ("storage.type", "#22a4e6"),
            ("storage.modifier", "#f07171"),
            ("entity.name.function", "#eba400"),
            ("entity.name.type", "#55b4d4"),
            ("entity.name.class", "#55b4d4"),
            ("entity.name.struct", "#55b4d4"),
            ("entity.name.enum", "#55b4d4"),
            ("entity.name.trait", "#55b4d4"),
            ("variable.parameter", "#a37acc"),
            ("constant.numeric", "#695680"),
            ("constant.language", "#4cbf99"),
        ],
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
        tokens: &[
            ("comment", "#928374"),
            ("string", "#b8bb26"),
            ("keyword", "#fb4934"),
            ("storage.type", "#8ec07c"),
            ("storage.modifier", "#d3869b"),
            ("entity.name.function", "#fabd2f"),
            ("entity.name.type", "#83a598"),
            ("entity.name.class", "#83a598"),
            ("entity.name.struct", "#83a598"),
            ("entity.name.enum", "#83a598"),
            ("entity.name.trait", "#83a598"),
            ("variable.parameter", "#fbf1c7"),
            ("constant.numeric", "#fe8019"),
            ("constant.language", "#98971a"),
        ],
        neo_tokens: &[
            ("comment", "#91889b"),
            ("string", "#74dfc4"),
            ("keyword", "#eb6f92"),
            ("storage.type", "#40b4c4"),
            ("storage.modifier", "#ffb85b"),
            ("entity.name.function", "#eb64b9"),
            ("entity.name.type", "#ffe261"),
            ("entity.name.class", "#ffe261"),
            ("entity.name.struct", "#ffe261"),
            ("entity.name.enum", "#ffe261"),
            ("entity.name.trait", "#ffe261"),
            ("variable.parameter", "#b4dce7"),
            ("constant.numeric", "#a96bc0"),
            ("constant.language", "#21b6a8"),
        ],
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
        hex_color(hex)
    }

    fn build(
        extra_bases: &std::collections::HashMap<String, syntect::highlighting::Theme>,
        themes: &mut syntect::highlighting::ThemeSet,
        mirror: &impl Fn(&str, &str) -> Result<(), String>,
        name: &str,
        base_name: &str,
        def: &KilimThemeDef,
        table: &[(&str, &str)],
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
        // Functional token palette: one color per canonical element
        // class; recolor-and-fill (see apply_token_table).
        apply_token_table(&mut t, table);
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
        build(extra_bases, themes, &mirror, def.syntect_name, def.base_syntect, def, def.tokens);
        build(extra_bases, themes, &mirror, def.neo_name, def.neo_base, def, def.neo_tokens);
    }
}

/// Parse `#rrggbb` into an opaque syntect color.
#[cfg(feature = "markdown")]
fn hex_color(hex: &str) -> Option<syntect::highlighting::Color> {
    let h = hex.trim_start_matches('#');
    if h.len() == 6 {
        if let (Ok(r), Ok(g), Ok(b)) = (
            u8::from_str_radix(&h[0..2], 16),
            u8::from_str_radix(&h[2..4], 16),
            u8::from_str_radix(&h[4..6], 16),
        ) {
            return Some(syntect::highlighting::Color { r, g, b, a: 0xFF });
        }
    }
    None
}

/// True when any selector path in the rule contains `atom` as a full
/// scope element (`variable.parameter` matches
/// `variable.parameter.function`, not `variable.parameterized`).
/// Matched on the Debug rendering: syntect exposes no stable accessor.
#[cfg(feature = "markdown")]
fn rule_has_atom(rule: &syntect::highlighting::ThemeItem, atom: &str) -> bool {
    let dbg = format!("{:?}", rule.scope);
    let pat = format!("<{atom}");
    let mut rest = dbg.as_str();
    while let Some(i) = rest.find(&pat) {
        match rest[i + pat.len()..].chars().next() {
            Some('.') | Some('>') => return true,
            _ => rest = &rest[i + pat.len()..],
        }
    }
    false
}

/// Replace the base's token coloring for the canonical element classes:
/// base rules belonging to any table class are dropped (VSCode-derived
/// themes merge many selectors per color, so recoloring by atom
/// cross-contaminates classes), then the canonical rules are appended,
/// generic → specific. Families outside the table keep the base theme's
/// own rules. The append is what outranks broader base selectors
/// (OneDark's bare `storage` carries Rust `fn`).
#[cfg(feature = "markdown")]
fn apply_token_table(t: &mut syntect::highlighting::Theme, table: &[(&str, &str)]) {
    use syntect::highlighting::{ScopeSelectors, StyleModifier, ThemeItem};

    t.scopes.retain(|item| {
        !table
            .iter()
            .any(|(selector, _)| rule_has_atom(item, selector))
    });
    for (selector, hex) in table {
        let Some(color) = hex_color(hex) else {
            eprintln!("kilim: bad token hex '{hex}' for '{selector}', skipping");
            continue;
        };
        match selector.parse::<ScopeSelectors>() {
            Ok(scope) => t.scopes.push(ThemeItem {
                scope,
                style: StyleModifier {
                    foreground: Some(color),
                    ..Default::default()
                },
            }),
            Err(e) => eprintln!("kilim: bad token selector '{selector}': {e}"),
        }
    }
}

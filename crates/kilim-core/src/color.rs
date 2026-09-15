//! Terminal cell color blending — theme integration for tool-painted blocks.
//!
//! CLI tools (coding agents like `pi`, linters, TUIs) often paint their own
//! truecolor blocks. Those fixed palettes fight the active theme, especially
//! across polarity (a tool's dark chip on a light theme). Blending mixes each
//! explicit cell color toward the theme's paper/ink so foreign colors sit
//! *inside* the theme; a contrast guard keeps the result legible when the
//! blend would have collapsed the pair.
//!
//! Pure math, no rendering deps: the frontends keep their own color-name
//! resolution (the TUI's named ANSI colors are mapped by the *host* terminal,
//! so only RGB cells can blend there; Qt resolves names itself and blends
//! those too). "Default" cells already resolve to the theme, so they pass
//! through untouched either way.

/// Default blend strength for the "Blend" toggle: half-way to the theme.
pub const BLEND_WEIGHT: f32 = 0.5;

/// Minimum WCAG contrast a blended pair is allowed to fall to. The original
/// pair's contrast is respected as an upper bound: a blend may soften the
/// tool's own look, never below this floor.
const MIN_RATIO: f32 = 3.0;

/// 8-bit RGB, the only shape cells carry once resolved.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Rgb {
    pub r: u8,
    pub g: u8,
    pub b: u8,
}

impl Rgb {
    pub fn new(r: u8, g: u8, b: u8) -> Self {
        Self { r, g, b }
    }

    /// Parse `#rrggbb` / `rrggbb` (case-insensitive). `None` on anything else.
    pub fn parse(s: &str) -> Option<Self> {
        let h = s.strip_prefix('#').unwrap_or(s);
        if h.len() != 6 || !h.bytes().all(|b| b.is_ascii_hexdigit()) {
            return None;
        }
        let v = u32::from_str_radix(h, 16).ok()?;
        Some(Self::new((v >> 16) as u8, (v >> 8) as u8, v as u8))
    }

    pub fn hex(self) -> String {
        format!("#{:02x}{:02x}{:02x}", self.r, self.g, self.b)
    }

    /// WCAG relative luminance (0.0 black .. 1.0 white).
    pub fn luminance(self) -> f32 {
        fn channel(v: u8) -> f32 {
            let s = v as f32 / 255.0;
            if s <= 0.039_28 {
                s / 12.92
            } else {
                ((s + 0.055) / 1.055).powf(2.4)
            }
        }
        0.2126 * channel(self.r) + 0.7152 * channel(self.g) + 0.0722 * channel(self.b)
    }

    /// WCAG contrast ratio (1.0 .. 21.0), order-independent.
    pub fn contrast(self, other: Self) -> f32 {
        let (hi, lo) = {
            let (a, b) = (self.luminance(), other.luminance());
            if a >= b {
                (a, b)
            } else {
                (b, a)
            }
        };
        (hi + 0.05) / (lo + 0.05)
    }

    /// `self` mixed toward `target` by `weight` (0 = self, 1 = target).
    pub fn mix(self, target: Self, weight: f32) -> Self {
        let w = weight.clamp(0.0, 1.0);
        let f = |a: u8, b: u8| (a as f32 * (1.0 - w) + b as f32 * w).round() as u8;
        Self::new(f(self.r, target.r), f(self.g, target.g), f(self.b, target.b))
    }
}

/// Blend a cell's on-screen colors toward the theme, only for the sides the
/// tool actually painted.
///
/// `fg`/`bg` are the *displayed* roles after any reverse-video swap, already
/// resolved (a terminal default side carries the theme's ink/paper for that
/// role — which for a reversed cell means paper-on-ink). `fg_painted`/
/// `bg_painted` say whether the tool painted each side; unpainted sides come
/// back exactly as passed, while still defining the pair the contrast guard
/// checks against. `weight` 0 is a no-op.
pub fn blend_painted(
    fg: Rgb,
    bg: Rgb,
    ink: Rgb,
    paper: Rgb,
    weight: f32,
    fg_painted: bool,
    bg_painted: bool,
) -> (Rgb, Rgb) {
    if weight <= 0.0 {
        return (fg, bg);
    }
    let (f, b) = blend_cell(fg, bg, ink, paper, weight);
    (
        if fg_painted { f } else { fg },
        if bg_painted { b } else { bg },
    )
}

/// Blend a cell's on-screen colors toward the theme.
///
/// `fg`/`bg` are the *final* roles after any reverse-video swap, `ink`/
/// `paper` the active theme's foreground/background. `weight` 0 is a no-op
/// (callers keep their unblended path). Returns the blended pair.
pub fn blend_cell(fg: Rgb, bg: Rgb, ink: Rgb, paper: Rgb, weight: f32) -> (Rgb, Rgb) {
    if weight <= 0.0 {
        return (fg, bg);
    }
    let w = weight.clamp(0.0, 1.0);
    let bg_out = bg.mix(paper, w);
    let mut fg_out = fg.mix(ink, w);
    // A tool block can flip polarity (dark chip on a light theme): the two
    // mixes then land on each other. Walk the foreground toward whichever
    // theme pole is farther from the blended background until the pair is
    // legible again — the color stays inside the theme either way.
    let wanted = fg.contrast(bg).min(MIN_RATIO).max(1.0);
    if fg_out.contrast(bg_out) < wanted {
        let pole = if ink.contrast(bg_out) >= paper.contrast(bg_out) {
            ink
        } else {
            paper
        };
        for step in 1..=10 {
            let candidate = fg_out.mix(pole, step as f32 / 10.0);
            if candidate.contrast(bg_out) >= wanted {
                return (candidate, bg_out);
            }
        }
        fg_out = pole;
    }
    (fg_out, bg_out)
}

#[cfg(test)]
mod tests {
    use super::*;

    const INK: Rgb = Rgb { r: 0xcb, g: 0xd0, b: 0xdc }; // Kilim Midnight-ish
    const PAPER: Rgb = Rgb { r: 0x10, g: 0x13, b: 0x19 };
    const LIGHT_INK: Rgb = Rgb { r: 0x2f, g: 0x31, b: 0x34 };
    const LIGHT_PAPER: Rgb = Rgb { r: 0xff, g: 0xff, b: 0xff };

    #[test]
    fn parses_and_formats_hex() {
        assert_eq!(Rgb::parse("#ff8000"), Some(Rgb::new(255, 128, 0)));
        assert_eq!(Rgb::parse("FF8000"), Some(Rgb::new(255, 128, 0)));
        assert_eq!(Rgb::parse("#fff"), None);
        assert_eq!(Rgb::parse("nope"), None);
        assert_eq!(Rgb::new(1, 2, 3).hex(), "#010203");
    }

    #[test]
    fn mix_endpoints_and_rounding() {
        let black = Rgb::new(0, 0, 0);
        let white = Rgb::new(255, 255, 255);
        assert_eq!(black.mix(white, 0.0), black);
        assert_eq!(black.mix(white, 1.0), white);
        assert_eq!(black.mix(white, 0.5), Rgb::new(128, 128, 128));
        assert_eq!(black.mix(white, 2.0), white); // clamped
    }

    #[test]
    fn zero_weight_is_identity() {
        let fg = Rgb::new(0xe6, 0xe6, 0xe6);
        let bg = Rgb::new(0x1a, 0x1a, 0x1a);
        assert_eq!(blend_cell(fg, bg, INK, PAPER, 0.0), (fg, bg));
        assert_eq!(blend_cell(fg, bg, INK, PAPER, -1.0), (fg, bg));
    }

    #[test]
    fn tool_colors_move_toward_the_theme() {
        // Tool block: near-white text on a near-black chip, dark theme.
        let fg = Rgb::new(0xff, 0xff, 0xff);
        let bg = Rgb::new(0x00, 0x00, 0x00);
        let (fg2, bg2) = blend_cell(fg, bg, INK, PAPER, BLEND_WEIGHT);
        assert!(fg2.contrast(INK) < fg.contrast(INK), "fg not pulled to ink");
        assert!(bg2.contrast(PAPER) < bg.contrast(PAPER), "bg not pulled to paper");
        assert!(fg2.contrast(bg2) >= MIN_RATIO, "blend broke contrast: {fg2:?} {bg2:?}");
    }

    #[test]
    fn polarity_flip_stays_legible() {
        // Dark tool chip on a light theme: naive mixing lands fg and bg on
        // top of each other; the guard must pull the fg back to a pole.
        let fg = Rgb::new(0xe6, 0xe6, 0xe6);
        let bg = Rgb::new(0x1a, 0x1a, 0x1a);
        let (fg2, bg2) = blend_cell(fg, bg, LIGHT_INK, LIGHT_PAPER, BLEND_WEIGHT);
        assert!(fg2.contrast(bg2) >= MIN_RATIO, "still unreadable: {fg2:?} {bg2:?}");
        // And the block itself did move toward the light paper.
        assert!(bg2.luminance() > bg.luminance());
    }

    #[test]
    fn unpainted_sides_pass_through_exactly() {
        // Default (theme-derived) sides are not the tool's to blend: Kilim's
        // reversed cursor cell is exactly this shape and must stay crisp.
        // Both unpainted: the resolved pair (paper-on-ink here) survives.
        assert_eq!(
            blend_painted(PAPER, INK, INK, PAPER, BLEND_WEIGHT, false, false),
            (PAPER, INK)
        );
        // A painted chip side still moves; the theme side does not.
        let chip = Rgb::new(0x2d, 0x1b, 0x3d);
        let (fg2, bg2) = blend_painted(INK, chip, INK, PAPER, BLEND_WEIGHT, false, true);
        assert_eq!(fg2, INK);
        assert_ne!(bg2, chip);
        assert!(bg2.contrast(PAPER) < chip.contrast(PAPER));
        // Zero weight returns both sides as given.
        assert_eq!(blend_painted(PAPER, INK, INK, PAPER, 0.0, true, true), (PAPER, INK));
    }

    #[test]
    fn dim_pairs_are_not_made_worse() {
        // Already-low-contrast pair: the floor is the original ratio, so the
        // guard may not darken the situation further.
        let fg = Rgb::new(0x80, 0x80, 0x80);
        let bg = Rgb::new(0x60, 0x60, 0x60);
        let before = fg.contrast(bg);
        let (fg2, bg2) = blend_cell(fg, bg, INK, PAPER, BLEND_WEIGHT);
        assert!(fg2.contrast(bg2) >= before, "{before} -> {}", fg2.contrast(bg2));
    }
}

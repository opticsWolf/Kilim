//! ratatui rendering of kilim-core Layout from live screens (v0.2).
//! Sync draw: snapshot via `try_lock` (screens are fed by tokio tasks).

use kilim_core::layout::{Dir, Node};
use ratatui::layout::{Constraint, Layout as RLayout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Paragraph};
use ratatui::Frame;

use super::app::App;

pub fn render(f: &mut Frame, app: &mut App) {
    let area = f.area();
    let main = Rect {
        x: area.x,
        y: area.y,
        width: area.width,
        height: area.height.saturating_sub(1),
    };
    render_node(f, app, &app.session.layout.root.clone(), main);
    let bar = Rect {
        x: area.x,
        y: area.height.saturating_sub(1),
        width: area.width,
        height: 1,
    };
    let panes = app.session.layout.pane_ids().join(" | ");
    let exited = app.session.exited_terms();
    let exit_note = if exited.is_empty() {
        String::new()
    } else {
        format!("  ✗ {} (exited, Ctrl+R restart)", exited.join(","))
    };
    f.render_widget(
        Paragraph::new(Line::from(vec![
            Span::styled(" kilim ", Style::default().add_modifier(Modifier::BOLD)),
            Span::raw(format!(" {panes}{exit_note}   [{}] Tab focus ^Tab tabs ^T theme PgUp/Dn Esc quit ", app.session.theme())),
        ])),
        bar,
    );
}

fn render_node(f: &mut Frame, app: &mut App, node: &Node, area: Rect) {
    match node {
        Node::Pane { pane_id } => render_pane(f, app, pane_id, area),
        Node::Split { dir, ratio, a, b } => {
            let pct = (ratio.clamp(0.05, 0.95) * 100.0) as u16;
            let (first, second) = match dir {
                Dir::Horizontal => {
                    let c = RLayout::horizontal([Constraint::Percentage(pct), Constraint::Min(0)]).split(area);
                    (c[0], c[1])
                }
                Dir::Vertical => {
                    let c = RLayout::vertical([Constraint::Percentage(pct), Constraint::Min(0)]).split(area);
                    (c[0], c[1])
                }
            };
            render_node(f, app, a, first);
            render_node(f, app, b, second);
        }
        Node::Tabs { tabs, current } => {
            if let Some(tab) = tabs.get(*current) {
                render_pane(f, app, &tab.pane_id, area);
            }
        }
    }
}

fn render_pane(f: &mut Frame, app: &mut App, pane_id: &str, area: Rect) {
    let Some(pane) = app.session.panes.get(pane_id) else {
        f.render_widget(Paragraph::new("unknown pane"), area);
        return;
    };
    let active = app.session.layout.active == *pane_id;
    // Paper: file/markdown panes fill the whole block (borders, short
    // lines, rows below EOF) with the theme bg — same value the Qt
    // panes and page chrome use. Terminals own their cells; untouched.
    let paper_bg: Option<Color> = match &pane.kind {
        kilim_core::layout::PaneKind::File { .. } => paper_color(app.session.theme()),
        kilim_core::layout::PaneKind::Markdown { .. } => {
            paper_color(app.session.markdown_theme())
        }
        // Shells paint their own cells (themed defaults via
        // term_cell_span); the frame still takes the code paper.
        kilim_core::layout::PaneKind::Term { .. } => paper_color(app.session.theme()),
    };
    let border_style = if active {
        Style::default().fg(Color::Cyan)
    } else {
        Style::default()
    };
    let border_style = match paper_bg {
        Some(paper) => border_style.bg(paper),
        None => border_style,
    };
    let mut block = Block::default()
        .title(pane.title.as_str())
        .borders(Borders::ALL)
        .border_style(border_style);
    if let Some(paper) = paper_bg {
        block = block.title_style(Style::default().bg(paper));
    }
    let inner = block.inner(area);
    f.render_widget(block, area);

    match &pane.kind {
        kilim_core::layout::PaneKind::Term { .. } => {
            let Some(h) = app.session.terms.get(pane_id) else {
                f.render_widget(Paragraph::new("spawning…"), inner);
                return;
            };
            let Ok(screen) = h.screen.try_lock() else { return };
            app.sizes.insert(pane_id.to_string(), (inner.width, inner.height));
            // Theme defaults: shells leave most cells "default" — paint
            // those with the theme fg/bg so the shell view follows theme
            // switches like the code panes (else the console colors win).
            let theme_name = app.session.theme();
            let theme_fg = kilim_core::highlight::ThemeRegistry::foreground(theme_name)
                .map(|h| parse_hex(&h))
                .unwrap_or(Color::Reset);
            let theme_bg = kilim_core::highlight::ThemeRegistry::background(theme_name)
                .map(|h| parse_hex(&h))
                .unwrap_or(Color::Reset);
            let total = screen.total_lines();
            let vis_h = inner.height as usize;
            let vis_w = inner.width as usize;
            let off = app.scroll.get(pane_id).copied().unwrap_or(0);
            let start = total.saturating_sub(vis_h).saturating_sub(off);
            let rows = screen.styled_range(start, vis_h);
            let (cx, cy_abs) = screen.absolute_cursor();
            let cursor_here =
                cy_abs >= start && cy_abs < start + vis_h && cx < vis_w;
            let mut lines: Vec<Line> = rows
                .iter()
                .enumerate()
                .map(|(ri, row)| {
                    Line::from(
                        row.iter()
                            .enumerate()
                            .map(|(ci, (text, fg, bg, attrs))| {
                                let mut sp = term_cell_span(text, fg, bg, *attrs, theme_fg, theme_bg);
                                // Soft cursor: reverse the cell under the cursor.
                                if cursor_here && cy_abs - start == ri && cx == ci {
                                    sp.style = sp.style.add_modifier(Modifier::REVERSED);
                                }
                                sp
                            })
                            .collect::<Vec<_>>(),
                    )
                })
                .collect();
            // Shortfall rows (fresh shell, fewer lines than height) take
            // the theme paper so no terminal-default band shows below.
            while lines.len() < vis_h {
                lines.push(Line::from(vec![Span::styled(
                    " ".repeat(vis_w),
                    Style::default().bg(theme_bg),
                )]));
            }
            f.render_widget(Paragraph::new(lines), inner);
            // Cursor: hardware position + soft reverse-video cell. The soft
            // cell guarantees visibility on consoles that swallow the
            // hardware cursor; where hardware works it looks identical.
            if cursor_here {
                f.set_cursor_position((inner.x + cx as u16, inner.y + (cy_abs - start) as u16));
            }
        }
        kilim_core::layout::PaneKind::File { .. }
        | kilim_core::layout::PaneKind::Markdown { .. } => {
            match app.highlighted_cached(pane_id) {
                Ok(rows) => {
                    let vis = inner.height as usize;
                    let wid = inner.width as usize;
                    let off = app.scroll.get(pane_id).copied().unwrap_or(0);
                    let mut lines: Vec<Line> = rows
                        .into_iter()
                        .map(|row| {
                            Line::from(
                                row.into_iter()
                                    .map(|s| {
                                        Span::styled(
                                            s.text,
                                            Style::default().fg(parse_hex(&s.fg)).bg(parse_hex(&s.bg)),
                                        )
                                    })
                                    .collect::<Vec<_>>(),
                            )
                        })
                        .collect();
                    // Scroll from top; clamp so a full page remains.
                    let start = off.min(lines.len().saturating_sub(vis.min(lines.len())));
                    let mut tail: Vec<Line> = lines.split_off(start);
                    let _ = lines;
                    // Full-bleed paper: pad short lines, fill rows below
                    // EOF, so no terminal-default cells show through.
                    if let Some(paper) = paper_bg {
                        let fill = Style::default().bg(paper);
                        for line in tail.iter_mut() {
                            let w = line.width();
                            if w < wid {
                                line.spans.push(Span::styled(" ".repeat(wid - w), fill));
                            }
                        }
                        while tail.len() < vis {
                            tail.push(Line::from(vec![Span::styled(
                                " ".repeat(wid),
                                fill,
                            )]));
                        }
                    }
                    f.render_widget(Paragraph::new(tail), inner);
                }
                Err(e) => {
                    f.render_widget(Paragraph::new(format!("ERR: {e}")), inner);
                }
            }
        }
    }
}

/// One pty cell -> Span, themed: `Reset` sides (shell "default") take
/// the theme fg/bg so the shell view follows theme switches.
/// Resolved AFTER reverse-swap, so reversed defaults theme correctly.
fn term_cell_span<'a>(
    text: &'a str,
    fg: &str,
    bg: &str,
    attrs: u8,
    theme_fg: Color,
    theme_bg: Color,
) -> Span<'a> {
    let mut sp = cell_span(text, fg, bg, attrs);
    if sp.style.fg == Some(Color::Reset) {
        sp.style.fg = Some(theme_fg);
    }
    if sp.style.bg == Some(Color::Reset) {
        sp.style.bg = Some(theme_bg);
    }
    sp
}

/// One stitch-pty cell -> ratatui Span.
/// fg/bg: "default" | ANSI name ("red", "brightblue") | 6-hex ("ff0000", "#ff0000").
fn cell_span<'a>(text: &'a str, fg: &str, bg: &str, attrs: u8) -> Span<'a> {
    let bold = attrs & (1 << 0) != 0;
    let dim = attrs & (1 << 1) != 0;
    let italic = attrs & (1 << 2) != 0;
    let underline = attrs & (1 << 3) != 0;
    let _blink = attrs & (1 << 4) != 0;
    let reverse = attrs & (1 << 5) != 0;
    let hidden = attrs & (1 << 6) != 0;
    let struck = attrs & (1 << 7) != 0;

    let mut fg_c = cell_color(fg);
    let mut bg_c = cell_color(bg);
    if reverse {
        std::mem::swap(&mut fg_c, &mut bg_c);
    }
    let mut style = Style::default().fg(fg_c).bg(bg_c);
    if bold {
        style = style.add_modifier(Modifier::BOLD);
    }
    if dim {
        style = style.add_modifier(Modifier::DIM);
    }
    if italic {
        style = style.add_modifier(Modifier::ITALIC);
    }
    if underline {
        style = style.add_modifier(Modifier::UNDERLINED);
    }
    if struck {
        style = style.add_modifier(Modifier::CROSSED_OUT);
    }
    let text = if hidden { " " } else { text };
    Span::styled(text, style)
}

fn cell_color(s: &str) -> Color {
    // Fast path: byte-exact matches, no allocation, no parsing. The PTY
    // layer emits lowercase names and "default", so this hits for ~all
    // cells; anything exotic falls through to the original slow path.
    match s {
        "default" | "" => return Color::Reset,
        "black" => return Color::Black,
        "red" => return Color::Red,
        "green" => return Color::Green,
        "yellow" => return Color::Yellow,
        "blue" => return Color::Blue,
        "magenta" => return Color::Magenta,
        "cyan" => return Color::Cyan,
        "white" => return Color::Gray,
        "brightblack" | "gray" | "grey" => return Color::DarkGray,
        "brightred" => return Color::LightRed,
        "brightgreen" => return Color::LightGreen,
        "brightyellow" => return Color::LightYellow,
        "brightblue" => return Color::LightBlue,
        "brightmagenta" => return Color::LightMagenta,
        "brightcyan" => return Color::LightCyan,
        "brightwhite" => return Color::White,
        _ => {}
    }
    slow_color(s)
}

/// Original resolution: whitespace/case variants, spaced bright names,
/// 6-hex. Only reached on fast-path miss (rare).

fn slow_color(s: &str) -> Color {
    let t = s.trim();
    if t.eq_ignore_ascii_case("default") || t.is_empty() {
        return Color::Reset;
    }
    if let Some(c) = ansi_name(t) {
        return c;
    }
    parse_hex(t)
}

fn ansi_name(t: &str) -> Option<Color> {
    Some(match t.to_ascii_lowercase().as_str() {
        "black" => Color::Black,
        "red" => Color::Red,
        "green" => Color::Green,
        "yellow" => Color::Yellow,
        "blue" => Color::Blue,
        "magenta" => Color::Magenta,
        "cyan" => Color::Cyan,
        "white" => Color::Gray,
        "brightblack" | "bright black" | "gray" | "grey" => Color::DarkGray,
        "brightred" | "bright red" => Color::LightRed,
        "brightgreen" | "bright green" => Color::LightGreen,
        "brightyellow" | "bright yellow" => Color::LightYellow,
        "brightblue" | "bright blue" => Color::LightBlue,
        "brightmagenta" | "bright magenta" => Color::LightMagenta,
        "brightcyan" | "bright cyan" => Color::LightCyan,
        "brightwhite" | "bright white" => Color::White,
        _ => return None,
    })
}

/// Theme paper color for full-bleed file/markdown panes. None when the
/// theme is unknown AND no default exists (bare registry) — panes then
/// render as before, on the terminal default.
fn paper_color(theme: &str) -> Option<Color> {
    kilim_core::highlight::ThemeRegistry::background(theme).map(|h| parse_hex(&h))
}

fn parse_hex(s: &str) -> Color {
    let h = s.trim_start_matches('#');
    let b = h.as_bytes();
    // Manual nibble loop: ~5x faster than 3x u8::from_str_radix and
    // panic-free (byte-indexed, non-hex bails to Reset).
    if b.len() == 6 {
        let mut v: u32 = 0;
        for &c in b {
            let n = match c {
                b'0'..=b'9' => (c - b'0') as u32,
                b'a'..=b'f' => (c - b'a' + 10) as u32,
                b'A'..=b'F' => (c - b'A' + 10) as u32,
                _ => return Color::Reset,
            };
            v = (v << 4) | n;
        }
        return Color::Rgb((v >> 16) as u8, (v >> 8) as u8, v as u8);
    }
    Color::Reset
}

#[cfg(test)]
mod live_tests {
    use ratatui::Terminal;
    use ratatui::backend::{Backend, TestBackend};
    use ratatui::style::Color;

    use super::{cell_color, parse_hex, render};
    use crate::app::App;

    /// Fast path and slow path must agree exactly (fast misses fall
    /// through to the original logic, so behavior never changes).
    #[test]
    fn color_resolution_matches() {
        // Fast-path hits.
        assert_eq!(cell_color("default"), Color::Reset);
        assert_eq!(cell_color(""), Color::Reset);
        assert_eq!(cell_color("red"), Color::Red);
        assert_eq!(cell_color("brightblue"), Color::LightBlue);
        assert_eq!(cell_color("grey"), Color::DarkGray);
        // Slow-path fallbacks (case/space variants, hex, garbage).
        assert_eq!(cell_color("Default"), Color::Reset);
        assert_eq!(cell_color("  red  "), Color::Red);
        assert_eq!(cell_color("Bright Red"), Color::LightRed);
        assert_eq!(cell_color("ff0000"), Color::Rgb(255, 0, 0));
        assert_eq!(cell_color("#00ff00"), Color::Rgb(0, 255, 0));
        assert_eq!(cell_color("nope"), Color::Reset);
        assert_eq!(cell_color(" red"), Color::Red);
    }


    #[test]
    fn hex_parsing_edges() {
        assert_eq!(parse_hex("aBcDeF"), Color::Rgb(0xab, 0xcd, 0xef));
        assert_eq!(parse_hex("#101319"), Color::Rgb(0x10, 0x13, 0x19));
        assert_eq!(parse_hex("short"), Color::Reset);
        assert_eq!(parse_hex("toolong!"), Color::Reset);
        assert_eq!(parse_hex("zzzzzz"), Color::Reset);
        assert_eq!(parse_hex(""), Color::Reset);
    }

    const DOC: &str = r#"{"layout": {"root": {"type": "pane", "pane_id": "t"}, "active": "t", "theme": "InspiredGitHub"}, "panes": [{"id": "t", "title": "sh", "kind": "term", "cmd": "powershell.exe"}]}"#;

    /// Spawns a real shell, renders one frame headless, proves the cursor
    /// lands visibly inside the term pane (regression: "no cursor in cli").
    #[tokio::test]
    #[cfg(windows)]
    async fn cursor_lands_inside_term_pane() {
        let mut app = App::new(DOC).unwrap();
        app.session.ensure_terms().await.unwrap();
        // Let conpty + powershell print the banner/prompt.
        tokio::time::sleep(std::time::Duration::from_millis(2500)).await;

        let backend = TestBackend::new(100, 30);
        let mut term = Terminal::new(backend).unwrap();
        term.draw(|f| render(f, &mut app)).unwrap();

        let pos = term.backend_mut().get_cursor_position().unwrap();
        // Pane border at 0..100 x 0..29, inner area inset by 1.
        assert!(pos.x >= 1 && pos.x < 99, "cursor x off-pane: {pos:?}");
        assert!(pos.y >= 1 && pos.y < 28, "cursor y off-pane: {pos:?}");
        // And the shell actually printed something on the cursor row.
        let row: String = (0..100)
            .map(|x| term.backend_mut().buffer()[(x, pos.y)].symbol().to_string())
            .collect();
        assert!(row.trim().len() > 1, "cursor row is blank: {row:?}");
        // Soft cursor: the cell under the cursor carries REVERSED.
        let cell = term.backend_mut().buffer()[(pos.x, pos.y)].clone();
        assert!(
            cell.modifier.contains(ratatui::style::Modifier::REVERSED),
            "cursor cell not reversed: {cell:?}"
        );
    }

    /// File panes paint full-bleed theme paper — padded line tails and
    /// rows below EOF carry the theme bg, matching the Qt code viewer
    /// (regression: TUI showed terminal-default around the text).    #[test]
    fn file_pane_fills_theme_paper() {
        use ratatui::style::Color;

        let dir = std::env::temp_dir().join("kilim-tui-paper");
        std::fs::create_dir_all(&dir).unwrap();
        let src = dir.join("s.py");
        std::fs::write(&src, "x = 1\n# hi\n").unwrap();
        let doc = format!(
            r#"{{"layout": {{"root": {{"type": "pane", "pane_id": "code"}}, "active": "code", "theme": "Kilim Dark", "markdown_theme": "Kilim Dark"}}, "panes": [{{"id": "code", "title": "s.py", "kind": "file", "path": "{}"}}]}}"#,
            src.to_string_lossy().replace('\\', "/")
        );
        let mut app = App::new(&doc).unwrap();
        let backend = TestBackend::new(100, 30);
        let mut term = Terminal::new(backend).unwrap();
        term.draw(|f| render(f, &mut app)).unwrap();
        let paper = Color::Rgb(0x10, 0x13, 0x19); // Kilim Dark editor_bg
        let buf = term.backend().buffer();
        // End-of-line padding beyond the short first row.
        assert_eq!(buf[(97, 1)].bg, paper, "line tail not paper");
        // Empty row far below EOF.
        assert_eq!(buf[(50, 20)].bg, paper, "below-EOF row not paper");
        // Border frame itself.
        assert_eq!(buf[(0, 0)].bg, paper, "border not paper");
    }

    /// Shell default cells take the theme fg/bg (unit: no shell needed).
    #[test]
    fn term_defaults_follow_theme() {
        use ratatui::style::Color;

        use super::term_cell_span;

        let fg = Color::Rgb(0xCB, 0xD0, 0xDC); // Kilim Dark text
        let bg = Color::Rgb(0x10, 0x13, 0x19); // Kilim Dark paper
        let sp = term_cell_span("x", "default", "default", 0, fg, bg);
        assert_eq!(sp.style.fg, Some(fg));
        assert_eq!(sp.style.bg, Some(bg));
        // Explicit colors survive; empty counts as default.
        let sp = term_cell_span("y", "red", "", 0, fg, bg);
        assert_eq!(sp.style.fg, Some(Color::Red));
        assert_eq!(sp.style.bg, Some(bg));
        // Reversed defaults resolve on the swapped sides (real-terminal
        // semantics: reverse of default-on-red is red-on-default).
        let sp = term_cell_span("z", "default", "red", 1 << 5, fg, bg);
        assert_eq!(sp.style.fg, Some(Color::Red));
        assert_eq!(sp.style.bg, Some(bg));
    }

    /// Draw small, sync, draw big, sync: committed dims track the
    /// drawn area so the shell view refreshes into the new size
    /// (regression: resize left the shell view stale).
    #[tokio::test]
    #[cfg(windows)]
    async fn term_pane_syncs_resize_and_fills() {
        const TERM_DOC: &str = r#"{"layout": {"root": {"type": "pane", "pane_id": "t"}, "active": "t", "theme": "Kilim Dark"}, "panes": [{"id": "t", "title": "sh", "kind": "term", "cmd": "powershell.exe", "rows": 24, "cols": 80}]}"#;

        fn kind_dims(app: &App) -> (u16, u16) {
            match &app.session.panes["t"].kind {
                kilim_core::layout::PaneKind::Term { rows, cols, .. } => (*rows, *cols),
                _ => panic!("t is not a term"),
            }
        }

        let mut app = App::new(TERM_DOC).unwrap();
        app.session.ensure_terms().await.unwrap();
        let mut term = Terminal::new(TestBackend::new(60, 15)).unwrap();
        term.draw(|f| render(f, &mut app)).unwrap();
        app.sync_sizes().await;
        // Borders + status line consume 3 rows, 2 cols.
        assert_eq!(kind_dims(&app), (12, 58));
        term.backend_mut().resize(120, 40);
        term.draw(|f| render(f, &mut app)).unwrap();
        app.sync_sizes().await;
        assert_eq!(kind_dims(&app), (37, 118));
    }
}

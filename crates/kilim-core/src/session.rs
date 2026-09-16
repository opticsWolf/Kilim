//! Live session: layout + pane inventory + theme + shared term handles.
//!
//! v0.2: `TermHandle` lives here — both frontends share
//! spawn/write/resize/styled_viewport verbatim (setup B).

use std::collections::HashMap;
use std::sync::Arc;

use crate::highlight::{StyledSpan, ThemeRegistry};
use crate::layout::{Dir, Layout, Node, Pane, PaneKind};
use crate::term::TermHandle;

pub struct Session {
    pub layout: Layout,
    pub panes: HashMap<String, Pane>,
    pub terms: HashMap<String, Arc<TermHandle>>,
}

impl Session {
    /// Parse a `layouts/*.json` doc: `{ "layout": Layout, "panes": [Pane] }`.
    pub fn from_json(s: &str) -> Result<Self, String> {
        let (layout, panes) = Layout::from_json(s).map_err(|e| e.to_string())?;
        let map: HashMap<String, Pane> = panes.into_iter().map(|p| (p.id.clone(), p)).collect();
        // Validate: every referenced pane exists.
        for id in layout.pane_ids() {
            if !map.contains_key(&id) {
                return Err(format!("layout references unknown pane '{id}'"));
            }
        }
        // TUI shares mordant's theme registry (Dracula et al. just work).
        #[cfg(feature = "markdown")]
        ThemeRegistry::import_mordant_themes();
        Ok(Self { layout, panes: map, terms: HashMap::new() })
    }

    /// Spawn every Term pane without a *live* handle. Dead handles are
    /// replaced, so this doubles as "restart dead panes" (TUI: Ctrl+R).
    pub async fn ensure_terms(&mut self) -> Result<(), String> {
        let jobs: Vec<(String, String, Vec<String>, u16, u16, usize, String)> = self
            .panes
            .iter()
            .filter_map(|(id, pane)| match &pane.kind {
                PaneKind::Term { cmd, args, rows, cols, scrollback, cwd } => {
                    let live = self.terms.get(id).map(|h| h.is_alive()).unwrap_or(false);
                    if live {
                        None
                    } else {
                        Some((id.clone(), cmd.clone(), args.clone(), *rows, *cols, *scrollback, cwd.clone()))
                    }
                }
                _ => None,
            })
            .collect();
        for (id, cmd, args, rows, cols, scrollback, cwd) in jobs {
            let h = TermHandle::spawn(&cmd, &args, rows, cols, scrollback, Some(cwd.as_str())).await?;
            self.terms.insert(id, h);
        }
        Ok(())
    }

    /// Kill (if alive) and respawn one term pane.
    pub async fn restart_term(&mut self, pane_id: &str) -> Result<(), String> {
        let pane = self.panes.get(pane_id).ok_or_else(|| format!("unknown pane '{pane_id}'"))?;
        let (cmd, args, rows, cols, scrollback, cwd) = match &pane.kind {
            PaneKind::Term { cmd, args, rows, cols, scrollback, cwd } => {
                (cmd.clone(), args.clone(), *rows, *cols, *scrollback, cwd.clone())
            }
            _ => return Err(format!("pane '{pane_id}' is not a term")),
        };
        if let Some(h) = self.terms.remove(pane_id) {
            h.terminate(1.0).await;
        }
        let h = TermHandle::spawn(&cmd, &args, rows, cols, scrollback, Some(cwd.as_str())).await?;
        self.terms.insert(pane_id.to_string(), h);
        Ok(())
    }

    /// Pane ids whose process has exited (for status bars / supervision).
    pub fn exited_terms(&self) -> Vec<String> {
        self.terms
            .iter()
            .filter(|(_, h)| !h.is_alive())
            .map(|(id, _)| id.clone())
            .collect()
    }

    /// Launch a new shell: insert the pane, splice it into the tree beside
    /// the current root, focus it, and spawn the handle. Both frontends pick
    /// it up live (TUI renders the tree every frame; Qt adds a dock).
    pub async fn spawn_term(
        &mut self,
        pane_id: &str,
        title: &str,
        cmd: &str,
        args: &[String],
        rows: u16,
        cols: u16,
        scrollback: usize,
        cwd: Option<String>,
    ) -> Result<(), String> {
        let (cmd, args) = self.insert_term_pane(pane_id, title, cmd, args, rows, cols, scrollback, cwd.clone())?;
        let h = TermHandle::spawn(&cmd, &args, rows, cols, scrollback, cwd.as_deref()).await?;
        self.terms.insert(pane_id.to_string(), h);
        Ok(())
    }

    /// Sync half of spawn_term (no await): insert + splice + focus.
    /// Bridges spawn the handle themselves so no lock crosses an await.
    pub fn insert_term_pane(
        &mut self,
        pane_id: &str,
        title: &str,
        cmd: &str,
        args: &[String],
        rows: u16,
        cols: u16,
        scrollback: usize,
        cwd: Option<String>,
    ) -> Result<(String, Vec<String>), String> {
        self.splice_pane(Pane {
            id: pane_id.to_string(),
            title: title.to_string(),
            kind: PaneKind::Term {
                cmd: cmd.to_string(),
                args: args.to_vec(),
                rows,
                cols,
                scrollback,
                cwd: cwd.unwrap_or_default(),
            },
        })?;
        Ok((cmd.to_string(), args.to_vec()))
    }

    /// Add a viewer pane (clicked-path docks): `markdown=false` for the
    /// syntect file viewer, `true` for markdown/HTML preview panes.
    pub fn insert_file_pane(
        &mut self,
        pane_id: &str,
        title: &str,
        path: &str,
        markdown: bool,
    ) -> Result<(), String> {
        let kind = if markdown {
            PaneKind::Markdown { path: path.to_string() }
        } else {
            PaneKind::File { path: path.to_string() }
        };
        self.splice_pane(Pane { id: pane_id.to_string(), title: title.to_string(), kind })
    }

    /// Forget a pane (a frontend disposed its dock). Returns false when the
    /// id was already gone. The layout tree keeps its slot: frontends
    /// rebuild trees from the layout file, and only the Qt app disposes
    /// docks live (the TUI has no pane closing yet).
    pub fn remove_pane(&mut self, pane_id: &str) -> bool {
        self.terms.remove(pane_id);
        self.panes.remove(pane_id).is_some()
    }

    /// Insert `pane` into the inventory and splice a pane node beside the
    /// root, focusing it. Both frontends pick it up live (the TUI renders
    /// the tree every frame; Qt adds a dock).
    fn splice_pane(&mut self, pane: Pane) -> Result<(), String> {
        if self.panes.contains_key(&pane.id) {
            return Err(format!("pane '{}' already exists", pane.id));
        }
        let id = pane.id.clone();
        self.panes.insert(id.clone(), pane);
        let old = std::mem::replace(&mut self.layout.root, Node::Pane { pane_id: id.clone() });
        self.layout.root = Node::Split {
            dir: Dir::Horizontal,
            ratio: 0.7,
            a: Box::new(old),
            b: Box::new(Node::Pane { pane_id: id.clone() }),
        };
        self.layout.active = id;
        Ok(())
    }

    pub async fn write_term(&self, pane_id: &str, data: &[u8]) -> Result<usize, String> {
        let h = self.terms.get(pane_id).ok_or_else(|| format!("no live term '{pane_id}'"))?;
        h.write(data).await.map_err(|e| e.to_string())
    }

    pub async fn resize_term(&self, pane_id: &str, rows: u16, cols: u16) -> Result<(), String> {
        let h = self.terms.get(pane_id).ok_or_else(|| format!("no live term '{pane_id}'"))?;
        h.resize(rows, cols)?;
        h.resize_screen(rows as usize, cols as usize).await;
        Ok(())
    }

    pub async fn term_viewport(&self, pane_id: &str) -> Result<Vec<Vec<(String, String, String, u8)>>, String> {
        let h = self.terms.get(pane_id).ok_or_else(|| format!("no live term '{pane_id}'"))?;
        Ok(h.styled_viewport().await)
    }

    /// O(window) slice for large scrollbacks — prefer this per frame.
    pub async fn term_range(&self, pane_id: &str, start: usize, count: usize) -> Result<Vec<Vec<(String, String, String, u8)>>, String> {
        let h = self.terms.get(pane_id).ok_or_else(|| format!("no live term '{pane_id}'"))?;
        Ok(h.styled_range(start, count).await)
    }

    /// One-call snapshot for bridges (replaces total+range+cursor trips).
    /// `anchor=None` follows the tail; `Some(a)` holds scrollback position.
    /// modes = (app_cursor, bracketed_paste, mouse_proto, sgr_mouse,
    /// alt_screen, bell). dirty = history-absolute changed rows.
    pub async fn snapshot_term(
        &self,
        pane_id: &str,
        rows: usize,
        anchor: Option<usize>,
    ) -> Result<
        (
            usize,
            usize,
            Vec<Vec<(String, String, String, u8)>>,
            (usize, usize),
            (bool, bool, u16, bool, bool, bool),
            Vec<usize>,
        ),
        String,
    > {
        Ok(self.term(pane_id)?.snapshot_tail(rows, anchor).await)
    }

    /// Drain a pane's event log without reading it (bounds memory for
    /// consumers that render straight from the screen, like the TUI).
    pub async fn drain_term_events(&self, pane_id: &str) -> Result<(), String> {
        self.term(pane_id)?.drain_events().await;
        Ok(())
    }

    fn term(&self, pane_id: &str) -> Result<Arc<TermHandle>, String> {
        self.terms.get(pane_id).cloned().ok_or_else(|| format!("no live term '{pane_id}'"))
    }

    pub async fn terminate_term(&self, pane_id: &str, grace_secs: f64) -> Result<(), String> {
        self.term(pane_id)?.terminate(grace_secs).await;
        Ok(())
    }

    pub fn kill_term(&self, pane_id: &str) -> Result<(), String> {
        self.term(pane_id)?.kill()
    }

    pub fn interrupt_term(&self, pane_id: &str) -> Result<(), String> {
        self.term(pane_id)?.interrupt()
    }

    pub fn signal_term(&self, pane_id: &str, sig: i32) -> Result<(), String> {
        self.term(pane_id)?.send_signal(sig)
    }

    pub async fn wait_term(&self, pane_id: &str) -> Result<Option<(u32, Option<i32>, Option<i32>)>, String> {
        Ok(self.term(pane_id)?.wait().await)
    }

    pub async fn term_title(&self, pane_id: &str) -> Result<String, String> {
        Ok(self.term(pane_id)?.title().await)
    }

    pub async fn term_total_lines(&self, pane_id: &str) -> Result<usize, String> {
        Ok(self.term(pane_id)?.total_lines().await)
    }

    /// Private DEC mode flag for a pane (1=DECCKM app cursor keys, ...).
    pub async fn term_dec_mode(&self, pane_id: &str, mode: u16) -> Result<bool, String> {
        Ok(self.term(pane_id)?.dec_private(mode).await)
    }

    pub async fn set_term_scrollback(&self, pane_id: &str, n: usize) -> Result<(), String> {
        self.term(pane_id)?.set_scrollback_lines(n).await;
        Ok(())
    }

    pub fn to_layout_json(&self) -> String {
        serde_json::to_string_pretty(&self.layout).unwrap_or_default()
    }

    pub fn set_theme(&mut self, theme: &str) -> Result<(), String> {
        if !ThemeRegistry::list_themes().contains(&theme.to_string()) {
            return Err(format!("unknown code theme '{theme}'"));
        }
        self.layout.theme = theme.to_string();
        Ok(())
    }

    pub fn theme(&self) -> &str {
        &self.layout.theme
    }

    pub fn set_markdown_theme(&mut self, theme: &str) -> Result<(), String> {
        if !ThemeRegistry::list_themes().contains(&theme.to_string()) {
            return Err(format!("unknown markdown theme '{theme}'"));
        }
        self.layout.markdown_theme = theme.to_string();
        Ok(())
    }

    pub fn markdown_theme(&self) -> &str {
        &self.layout.markdown_theme
    }

    pub fn list_themes() -> Vec<String> {
        ThemeRegistry::list_themes()
    }

    /// Render a Markdown pane to HTML via mordant (requires `markdown` feature).
    /// Qt shows this in a QTextBrowser; the TUI keeps syntect fallback.
    #[cfg(feature = "markdown")]
    pub fn markdown_html(&self, pane_id: &str) -> Result<String, String> {
        let pane = self
            .panes
            .get(pane_id)
            .ok_or_else(|| format!("unknown pane '{pane_id}'"))?;
        let path = match &pane.kind {
            crate::layout::PaneKind::Markdown { path } => path.clone(),
            _ => return Err("markdown_html needs a Markdown pane".into()),
        };
        // Not `read_to_string`: a text file in any encoding must render
        // (BOM-aware, cp1252 fallback) — binaryornot-rs already decided.
        let source = crate::paths::read_text(std::path::Path::new(&path)).map_err(|e| e.to_string())?;
        // GFM extensions are first-class but opt-in: tables, strikethrough
        // and task lists stay literal text without them (verified).
        use mordant::parser::ParserExtension as _;
        use mordant::renderer::html::RendererExtension as _;
        let md_theme = self.markdown_theme().to_string();
        // Fenced code follows the markdown theme (default renderer leaves it
        // plain); Attribute mode = inline styles, no stylesheet needed.
        let highlight_ext = mordant::highlighter::highlighting_html_renderer_extension(
            mordant::highlighter::HighlightingRendererOptions {
                theme: md_theme.clone(),
                mode: mordant::highlighter::HighlightingMode::Attribute,
                math_options: None,
            },
        );
        // Mermaid follows the markdown theme too (md_viewer's "sync"
        // default): native preset by name, else derived from the syntect
        // theme via our registry hook. Math renders KaTeX HTML+MathML.
        let diagram_ext = mordant::diagram::diagram_html_renderer_extension(
            mordant::diagram::DiagramHtmlRendererOptions {
                mermaid: mordant::diagram::MermaidHtmlRenderingOptions {
                    theme_spec: mordant::mermaid_theme::resolve_mermaid_theme(&md_theme),
                    ..Default::default()
                },
            },
        );
        let convert = mordant::new_markdown_to_html_string(
            mordant::parser::Options::default(),
            mordant::renderer::html::Options::default(),
            mordant::parser::gfm_table()
                .and(mordant::parser::gfm_strikethrough())
                .and(mordant::parser::gfm_task_list_item())
                .and(mordant::math::math_parser_extension(
                    mordant::math::MathParserOptions::default(),
                ))
                .and(mordant::diagram::diagram_parser_extension(
                    mordant::diagram::DiagramParserOptions::default(),
                )),
            mordant::math::math_html_renderer_extension(
                    mordant::math::MathRendererOptions::default(),
                )
                .and(mordant::math::math_inline_html_renderer_extension(
                    mordant::math::MathInlineRendererOptions::default(),
                ))
                .and(diagram_ext)
                // NOTE: highlighting LAST — node renderers shadow each other
                // (last wins); this is exactly the mordant-py chain order.
                .and(highlight_ext),
        );
        let mut html = String::new();
        convert(&mut html, &source).map_err(|e| e.to_string())?;
        Ok(html)
    }

    /// Full HTML document for a Markdown pane: fragment + KaTeX CSS shell.
    /// Qt shows this in QWebEngineView (or QTextBrowser); no wheel needed.
    /// The page chrome (body/table/blockquote/link colors) follows the
    /// markdown theme's own background/foreground — no light/dark toggle.
    #[cfg(feature = "markdown")]
    pub fn markdown_page(&self, pane_id: &str) -> Result<String, String> {
        let body = self.markdown_html(pane_id)?;
        let (bg, fg) = self.page_colors();
        let dark = luminance(&bg) < 128.0;
        let (accent, border, link) = if dark {
            ("#353535", "#444444", "#4c9aff")
        } else {
            ("#f6f8fa", "#d0d7de", "#0969da")
        };
        let scheme = if dark { "dark" } else { "light" };
        Ok(format!(
            "<!DOCTYPE html><html><head><meta charset='utf-8'>\
             <style>{}</style>\
             <style>\
             :root {{ color-scheme: {scheme}; }} \
             body {{ background-color: {bg}; color: {fg}; \
             font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; \
             padding: 30px; line-height: 1.6; max-width: 850px; margin: 0 auto; }} \
             pre {{ border: 1px solid {border} !important; padding: 16px !important; \
             border-radius: 6px !important; overflow: auto !important; }} \
             code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 85%; }} \
             p > code, li > code {{ background-color: {accent}; padding: .2em .4em; border-radius: 6px; }} \
             table {{ border-collapse: collapse; width: 100%; margin-bottom: 16px; }} \
             th, td {{ border: 1px solid {border}; padding: 6px 13px; }} \
             tr:nth-child(2n) {{ background-color: {accent}; }} \
             blockquote {{ padding: 0 1em; opacity: .75; border-left: .25em solid {border}; margin: 0; }} \
             a {{ color: {link}; text-decoration: none; }} \
             </style></head><body>{body}</body></html>",
            mordant::math::KATEX_CSS
        ))
    }

    /// Page background/foreground from the markdown theme (our registry,
    /// so bat themes work too). Falls back to light defaults.
    #[cfg(feature = "markdown")]
    fn page_colors(&self) -> (String, String) {
        fn hex(c: Option<syntect::highlighting::Color>, fallback: &str) -> String {
            c.map(|c| format!("#{:02x}{:02x}{:02x}", c.r, c.g, c.b))
                .unwrap_or_else(|| fallback.to_string())
        }
        match crate::highlight::ThemeRegistry::resolve(self.markdown_theme()) {
            Some(t) => (
                hex(t.settings.background, "#ffffff"),
                hex(t.settings.foreground, "#000000"),
            ),
            None => ("#ffffff".to_string(), "#000000".to_string()),
        }
    }

    /// Highlight a file pane: code panes use the code theme, markdown
    /// panes (TUI source view) use the markdown theme.
    pub fn highlighted_file(&self, pane_id: &str) -> Result<Vec<Vec<StyledSpan>>, String> {
        let pane = self
            .panes
            .get(pane_id)
            .ok_or_else(|| format!("unknown pane '{pane_id}'"))?;
        let (path, lang) = match &pane.kind {
            crate::layout::PaneKind::File { path } => {
                let ext = std::path::Path::new(path)
                    .extension()
                    .and_then(|e| e.to_str())
                    .unwrap_or("")
                    .to_string();
                (path.clone(), ext)
            }
            crate::layout::PaneKind::Markdown { path } => (path.clone(), "markdown".to_string()),
            crate::layout::PaneKind::Term { .. } => {
                return Err("term panes have no static highlight".into())
            }
        };
        let code = crate::paths::read_text(std::path::Path::new(&path)).map_err(|e| e.to_string())?;
        // Markdown-as-source (TUI) follows the markdown theme; code the code theme.
        let theme = match &pane.kind {
            crate::layout::PaneKind::Markdown { .. } => self.markdown_theme(),
            _ => self.theme(),
        };
        Ok(ThemeRegistry::highlight(&lang, &code, theme))
    }

    /// Full `{layout, panes}` document (panes sorted by id) for saving back
    /// theme choices etc. without clobbering the file's pane inventory.
    pub fn to_doc_json(&self) -> String {
        let mut panes: Vec<&crate::layout::Pane> = self.panes.values().collect();
        panes.sort_by(|a, b| a.id.cmp(&b.id));
        serde_json::json!({ "layout": self.layout, "panes": panes }).to_string()
    }
}

/// Rec.601 luma of a `#rrggbb` string; unparsable → mid-grey.
#[cfg(feature = "markdown")]
fn luminance(hex: &str) -> f32 {
    let h = hex.trim_start_matches('#');
    if h.len() == 6 {
        if let (Ok(r), Ok(g), Ok(b)) = (
            u8::from_str_radix(&h[0..2], 16),
            u8::from_str_radix(&h[2..4], 16),
            u8::from_str_radix(&h[4..6], 16),
        ) {
            return 0.299 * r as f32 + 0.587 * g as f32 + 0.114 * b as f32;
        }
    }
    128.0
}

#[cfg(all(test, feature = "markdown"))]
mod markdown_tests {
    use super::Session;

    fn doc_with_md(path: &str) -> String {
        format!(
            r#"{{"layout": {{"root": {{"type": "pane", "pane_id": "notes"}}, "active": "notes", "theme": "InspiredGitHub"}}, "panes": [{{"id": "notes", "title": "readme", "kind": "markdown", "path": "{path}"}}]}}"#
        )
    }

    #[test]
    fn markdown_renders_html() {
        let dir = std::env::temp_dir().join("kilim-md-test");
        std::fs::create_dir_all(&dir).unwrap();
        let md = dir.join("n.md");
        std::fs::write(&md, "# Hi\n\nSome **bold** text.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- [x] done\n\n~~gone~~\n").unwrap();
        let s = Session::from_json(&doc_with_md(&md.to_string_lossy().replace('\\', "/"))).unwrap();
        let html = s.markdown_html("notes").unwrap();
        assert!(html.contains("<h1"), "expected heading, got: {html}");
        assert!(html.contains("bold"), "expected body, got: {html}");
        assert!(html.contains("<table"), "expected GFM table, got: {html}");
        assert!(html.contains("checked") || html.contains("checkbox"), "expected task list, got: {html}");
        assert!(html.contains("<del") || html.contains("<s>"), "expected strikethrough, got: {html}");
    }

    #[test]
    fn insert_term_pane_stores_cwd_for_restarts() {
        // The stored spec carries cwd so ensure/restart respawn there too.
        let doc = r#"{"layout": {"root": {"type": "pane", "pane_id": "t"}, "active": "t", "theme": "Kilim Midnight"}, "panes": [{"id": "t", "title": "t", "kind": "term", "cmd": "sh"}]}"#;
        let mut s = Session::from_json(doc).unwrap();
        s.insert_term_pane("n", "n", "sh", &[], 24, 80, 5000, Some("/tmp/work".into())).unwrap();
        match &s.panes["n"].kind {
            crate::layout::PaneKind::Term { cwd, .. } => assert_eq!(cwd, "/tmp/work"),
            other => panic!("expected term pane, got {other:?}"),
        }
        // Omitted cwd stores empty = inherit, like layout files omit it.
        s.insert_term_pane("m", "m", "sh", &[], 24, 80, 5000, None).unwrap();
        assert!(matches!(&s.panes["m"].kind, crate::layout::PaneKind::Term { cwd, .. } if cwd.is_empty()));
    }

    #[test]
    fn fences_follow_markdown_theme_not_code_theme() {        let dir = std::env::temp_dir().join("kilim-md-test");
        std::fs::create_dir_all(&dir).unwrap();
        let md = dir.join("f.md");
        std::fs::write(&md, "# F\n\n```python\n# comment\nx = 1\n```\n").unwrap();
        let mut s = Session::from_json(&doc_with_md(&md.to_string_lossy().replace('\\', "/"))).unwrap();
        s.set_theme("Kilim Light").unwrap();
        s.set_markdown_theme("Kilim Midnight Neo").unwrap();
        let html = s.markdown_html("notes").unwrap();
        // Neo keeps Dracula's scopes: comment green; Kilim Light differs.
        assert!(html.contains("#6272a4"), "expected neo fence colors, got: {html}");
        // And markdown-as-source (TUI) uses the markdown theme too.
        let rows = s.highlighted_file("notes").unwrap();
        let vivid_rows = crate::highlight::ThemeRegistry::highlight(
            "markdown",
            &std::fs::read_to_string(&md).unwrap(),
            "Kilim Midnight Neo",
        );
        assert_eq!(rows.len(), vivid_rows.len());
    }

    #[test]
    fn mermaid_derives_from_markdown_theme() {
        // md_viewer's "sync" mode: the diagram follows the fence theme.
        let dir = std::env::temp_dir().join("kilim-md-test");
        std::fs::create_dir_all(&dir).unwrap();
        let md = dir.join("m.md");
        std::fs::write(&md, "# M\n\n```mermaid\ngraph TD; A-->B\n```\n").unwrap();
        let mut s = Session::from_json(&doc_with_md(&md.to_string_lossy().replace('\\', "/"))).unwrap();
        s.set_markdown_theme("Kilim Midnight").unwrap();
        let html = s.markdown_html("notes").unwrap();
        assert!(html.contains("<svg"), "expected server-rendered diagram, got: {html}");
        assert!(
            html.contains("#101319"),
            "expected Kilim-Midnight-derived diagram colors, got: {}",
            &html[..html.len().min(2000)]
        );
    }

    #[test]
    fn page_chrome_follows_markdown_theme() {
        let dir = std::env::temp_dir().join("kilim-md-test");
        std::fs::create_dir_all(&dir).unwrap();
        let md = dir.join("p.md");
        std::fs::write(&md, "# P\n\nHi.\n").unwrap();
        let mut s = Session::from_json(&doc_with_md(&md.to_string_lossy().replace('\\', "/"))).unwrap();
        s.set_markdown_theme("Kilim Midnight").unwrap();
        let dark = s.markdown_page("notes").unwrap();
        assert!(dark.contains("background-color: #101319"), "Kilim Midnight page bg missing");
        assert!(dark.contains("color-scheme: dark"), "expected dark scheme");
        s.set_markdown_theme("Kilim Light").unwrap();
        let light = s.markdown_page("notes").unwrap();
        assert!(light.contains("color-scheme: light"), "expected light scheme");
        assert!(!light.contains("background-color: #101319"), "dark bg leaked");
    }

    #[test]
    fn unknown_theme_names_are_rejected() {
        let mut s = Session::from_json(&doc_with_md("x.md")).unwrap();
        assert!(s.set_theme("Dracula").is_err());
        assert!(s.set_markdown_theme("Solarized (dark)").is_err());
        assert!(s.set_theme("Kilim Midnight Neo").is_ok());
        // Rejected names leave the previous theme in place.
        assert_eq!(s.theme(), "Kilim Midnight Neo");
    }

    #[test]
    fn math_renders_katex() {
        let dir = std::env::temp_dir().join("kilim-md-test");
        std::fs::create_dir_all(&dir).unwrap();
        let md = dir.join("k.md");
        std::fs::write(&md, "# K\n\nEinstein: $E = mc^2$.\n").unwrap();
        let s = Session::from_json(&doc_with_md(&md.to_string_lossy().replace('\\', "/"))).unwrap();
        let html = s.markdown_html("notes").unwrap();
        assert!(html.contains("katex"), "expected KaTeX math, got: {html}");
    }
}

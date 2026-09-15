//! Python surface v0.2: same shared TermHandle as the TUI (setup B).
//! Pattern mirrors stitch-pty: `future_into_py`, plain Rust values cross
//! the boundary, GIL re-acquired only for conversion.

use kilim_core::{Session, TermHandle, ThemeRegistry};
use pyo3::prelude::*;
use std::sync::{Arc, RwLock};

#[pyclass]
pub struct CoreSession {
    inner: Arc<RwLock<Session>>,
}

#[pymethods]
impl CoreSession {
    #[new]
    fn new(layout_json: &str) -> PyResult<Self> {
        let s = Session::from_json(layout_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e))?;
        Ok(Self {
            inner: Arc::new(RwLock::new(s)),
        })
    }

    fn layout_json(&self) -> String {
        self.inner.read().unwrap().to_layout_json()
    }

    fn set_layout(&self, layout_json: &str) -> PyResult<()> {
        if let Ok((layout, _)) = kilim_core::Layout::from_json(layout_json) {
            self.inner.write().unwrap().layout = layout;
            Ok(())
        } else {
            let layout: kilim_core::Layout = serde_json::from_str(layout_json)
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
            self.inner.write().unwrap().layout = layout;
            Ok(())
        }
    }

    fn pane_ids(&self) -> Vec<String> {
        self.inner.read().unwrap().layout.pane_ids()
    }

    /// Detect file paths in one terminal row: tuples of
    /// `(start, end, path, line, col, kind)` with **char** offsets into the
    /// row (quotes excluded). `kind` is one of `code` / `markdown` /
    /// `html` (openable viewers) or `binary` / `missing` (not openable —
    /// binaryornot-rs decides). `line`/`col` come from a `:line[:col]`
    /// suffix. This is `kilim_core::paths::detect_paths`, shared with the
    /// TUI.
    fn detect_paths(
        &self,
        line: &str,
    ) -> Vec<(usize, usize, String, Option<usize>, Option<usize>, String)> {
        kilim_core::paths::detect_paths(line)
            .into_iter()
            .map(|h| (h.start, h.end, h.path, h.line, h.col, h.kind.as_str().to_string()))
            .collect()
    }

    /// Read a text file with the viewer's decoding rules (UTF-8, BOM'd
    /// UTF-16/32, cp1252 fallback) — the html pane uses this so an
    /// 8-bit-encoded page still renders.
    fn read_text_file(&self, path: &str) -> PyResult<String> {
        kilim_core::paths::read_text(std::path::Path::new(path))
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
    }

    /// Viewer kind for one path (binaryornot-rs + extension routing,
    /// cached): `code` / `markdown` / `html` / `binary` / `missing`.
    fn classify_file(&self, path: &str) -> String {
        kilim_core::paths::classify(std::path::Path::new(path))
            .as_str()
            .to_string()
    }

    /// Add a viewer pane to the session (the frontend adds the dock).
    /// `markdown=true` registers it as a markdown/HTML preview pane.
    fn insert_file_pane(
        &self,
        pane_id: &str,
        title: &str,
        path: &str,
        markdown: bool,
    ) -> PyResult<()> {
        self.inner
            .write()
            .unwrap()
            .insert_file_pane(pane_id, title, path, markdown)
            .map_err(pyo3::exceptions::PyValueError::new_err)
    }

    /// Forget a pane (its dock was closed). True when it was known.
    fn remove_pane(&self, pane_id: &str) -> bool {
        self.inner.write().unwrap().remove_pane(pane_id)
    }

    fn theme(&self) -> String {
        self.inner.read().unwrap().theme().to_string()
    }

    fn set_theme(&self, theme: &str) -> PyResult<()> {
        self.inner.write().unwrap().set_theme(theme).map_err(|e| pyo3::exceptions::PyValueError::new_err(e))
    }

    fn markdown_theme(&self) -> String {
        self.inner.read().unwrap().markdown_theme().to_string()
    }

    fn set_markdown_theme(&self, theme: &str) -> PyResult<()> {
        self.inner.write().unwrap().set_markdown_theme(theme).map_err(|e| pyo3::exceptions::PyValueError::new_err(e))
    }

    /// Full `{layout, panes}` document for saving theme choices back to disk.
    fn doc_json(&self) -> String {
        self.inner.read().unwrap().to_doc_json()
    }

    /// Register a .tmTheme plist (always) or VSCode JSON (`markdown` wheel).
    /// After this, `set_theme(name)` works in both frontends.
    #[allow(clippy::boxed_local)]
    fn register_custom_theme(&self, name: &str, content: &str) -> PyResult<()> {
        ThemeRegistry::register_custom_theme(name, content)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e))
    }

    /// Themes → Code → Blend state (shared with the TUI via the layout).
    fn code_blend(&self) -> bool {
        self.inner.read().unwrap().layout.code_blend
    }

    /// Flip blending on/off in the session (the app persists it).
    fn set_code_blend(&self, on: bool) {
        self.inner.write().unwrap().layout.code_blend = on;
    }

    /// Blend a terminal cell's on-screen colors toward the theme
    /// (`kilim_core::color::blend_cell`): `#rrggbb` in, `#rrggbb` out.
    /// `weight` 0 returns the inputs unchanged; malformed hex raises.
    fn blend_cell(
        &self,
        fg: &str,
        bg: &str,
        theme_fg: &str,
        theme_bg: &str,
        weight: f64,
    ) -> PyResult<(String, String)> {
        let bad = |s: &str| pyo3::exceptions::PyValueError::new_err(format!("bad color '{s}'"));
        let fg = kilim_core::Rgb::parse(fg).ok_or_else(|| bad(fg))?;
        let bg = kilim_core::Rgb::parse(bg).ok_or_else(|| bad(bg))?;
        let ink = kilim_core::Rgb::parse(theme_fg).ok_or_else(|| bad(theme_fg))?;
        let paper = kilim_core::Rgb::parse(theme_bg).ok_or_else(|| bad(theme_bg))?;
        let (f, b) = kilim_core::blend_cell(fg, bg, ink, paper, weight as f32);
        Ok((f.hex(), b.hex()))
    }

    /// Highlighted file pane as list of rows of (text, fg, bg) tuples for Qt.
    fn highlighted_file(&self, pane_id: &str) -> PyResult<Vec<Vec<(String, String, String)>>> {
        let s = self.inner.read().unwrap();
        s.highlighted_file(pane_id)
            .map(|rows| {
                rows.into_iter()
                    .map(|row| row.into_iter().map(|sp| (sp.text, sp.fg, sp.bg)).collect())
                    .collect()
            })
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e))
    }

    // ── Live terms (shared core, same as TUI) ──

    /// Spawn every Term pane without a live handle. Async, GIL released.
    fn ensure_terms<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            // Collect missing jobs under a short read lock (never held across await).
            let jobs: Vec<(String, String, Vec<String>, u16, u16, usize)> = {
                let s = inner.read().unwrap();
                s.panes
                    .iter()
                    .filter_map(|(id, pane)| match &pane.kind {
                        kilim_core::PaneKind::Term { cmd, args, rows, cols, scrollback } => {
                            let live = s.terms.get(id).map(|h| h.is_alive()).unwrap_or(false);
                            if live {
                                None
                            } else {
                                Some((id.clone(), cmd.clone(), args.clone(), *rows, *cols, *scrollback))
                            }
                        }
                        _ => None,
                    })
                    .collect()
            };
            for (id, cmd, args, rows, cols, scrollback) in jobs {
                let h = TermHandle::spawn(&cmd, &args, rows, cols, scrollback)
                    .await
                    .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
                inner.write().unwrap().terms.insert(id, h);
            }
            Ok(())
        })
    }

    /// Kill (if alive) and respawn one term pane.
    fn restart_term<'py>(&self, py: Python<'py>, pane_id: String) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let (cmd, args, rows, cols, scrollback) = {
                let s = inner.read().unwrap();
                let pane = s.panes.get(&pane_id).ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("unknown pane '{pane_id}'")))?;
                match &pane.kind {
                    kilim_core::PaneKind::Term { cmd, args, rows, cols, scrollback } => {
                        (cmd.clone(), args.clone(), *rows, *cols, *scrollback)
                    }
                    _ => return Err(pyo3::exceptions::PyValueError::new_err(format!("pane '{pane_id}' is not a term"))),
                }
            };
            let old = inner.write().unwrap().terms.remove(&pane_id);
            if let Some(h) = old {
                h.terminate(1.0).await;
            }
            let h = TermHandle::spawn(&cmd, &args, rows, cols, scrollback)
                .await
                .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
            inner.write().unwrap().terms.insert(pane_id, h);
            Ok(())
        })
    }

    /// Pane ids whose process has exited.
    fn exited_terms(&self) -> PyResult<Vec<String>> {
        Ok(self.inner.read().unwrap().exited_terms())
    }

    /// Launch a new shell pane (insert + splice + spawn). Async, GIL released.
    fn spawn_term<'py>(
        &self,
        py: Python<'py>,
        pane_id: String,
        title: String,
        cmd: String,
        args: Vec<String>,
        rows: u16,
        cols: u16,
        scrollback: usize,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let (cmd, args) = {
                inner
                    .write()
                    .unwrap()
                    .insert_term_pane(&pane_id, &title, &cmd, &args, rows, cols, scrollback)
                    .map_err(pyo3::exceptions::PyRuntimeError::new_err)?
            };
            let h = TermHandle::spawn(&cmd, &args, rows, cols, scrollback)
                .await
                .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
            inner.write().unwrap().terms.insert(pane_id, h);
            Ok(())
        })
    }

    /// Write bytes to a term pane. Returns bytes written.
    fn write_term<'py>(
        &self,
        py: Python<'py>,
        pane_id: String,
        data: Vec<u8>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let h = {
                let s = inner.read().unwrap();
                s.terms
                    .get(&pane_id)
                    .cloned()
                    .ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("no live term '{pane_id}'")))?
            };
            let n = h.write(&data).await.map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))?;
            Ok(n)
        })
    }

    fn resize_term<'py>(
        &self,
        py: Python<'py>,
        pane_id: String,
        rows: u16,
        cols: u16,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let h = {
                let s = inner.read().unwrap();
                s.terms
                    .get(&pane_id)
                    .cloned()
                    .ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("no live term '{pane_id}'")))?
            };
            h.resize(rows, cols)
                .map_err(pyo3::exceptions::PyValueError::new_err)?;
            h.resize_screen(rows as usize, cols as usize).await;
            Ok(())
        })
    }

    /// Full styled viewport: rows of (text, fg, bg, attrs_bitmask).
    fn term_viewport<'py>(&self, py: Python<'py>, pane_id: String) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let h = {
                let s = inner.read().unwrap();
                s.terms
                    .get(&pane_id)
                    .cloned()
                    .ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("no live term '{pane_id}'")))?
            };
            Ok(h.styled_viewport().await)
        })
    }

    /// O(window) slice for Qt paint paths.
    fn term_range<'py>(
        &self,
        py: Python<'py>,
        pane_id: String,
        start: usize,
        count: usize,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let h = {
                let s = inner.read().unwrap();
                s.terms
                    .get(&pane_id)
                    .cloned()
                    .ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("no live term '{pane_id}'")))?
            };
            Ok(h.styled_range(start, count).await)
        })
    }

    /// One-call snapshot: (total, start, cells, cursor). anchor=None = tail.
    fn snapshot_term<'py>(
        &self,
        py: Python<'py>,
        pane_id: String,
        rows: usize,
        anchor: Option<usize>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let h = {
                let s = inner.read().unwrap();
                s.terms.get(&pane_id).cloned().ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("no live term '{pane_id}'")))?
            };
            Ok(h.snapshot_tail(rows, anchor).await)
        })
    }

    fn term_cursor<'py>(&self, py: Python<'py>, pane_id: String) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let h = {
                let s = inner.read().unwrap();
                s.terms
                    .get(&pane_id)
                    .cloned()
                    .ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("no live term '{pane_id}'")))?
            };
            Ok(h.absolute_cursor().await)
        })
    }

    fn term_alive(&self, pane_id: &str) -> PyResult<bool> {
        let s = self.inner.read().unwrap();
        s.terms
            .get(pane_id)
            .map(|h| h.is_alive())
            .ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("no live term '{pane_id}'")))
    }

    fn term_pid(&self, pane_id: &str) -> PyResult<u32> {
        let s = self.inner.read().unwrap();
        s.terms
            .get(pane_id)
            .map(|h| h.pid())
            .ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("no live term '{pane_id}'")))
    }

    /// Markdown pane -> HTML via mordant. Only present with `markdown` feature;
    /// Qt tries this first and falls back to highlighted_file.
    #[cfg(feature = "markdown")]
    fn markdown_html(&self, pane_id: &str) -> PyResult<String> {
        self.inner.read().unwrap().markdown_html(pane_id).map_err(|e| pyo3::exceptions::PyValueError::new_err(e))
    }

    /// Full HTML document (fragment + KaTeX shell) for Qt preview panes.
    #[cfg(feature = "markdown")]
    fn markdown_page(&self, pane_id: &str) -> PyResult<String> {
        self.inner.read().unwrap().markdown_page(pane_id).map_err(|e| pyo3::exceptions::PyValueError::new_err(e))
    }

    // ── Process control (wheel parity, Rust-backed) ──

    fn terminate_term<'py>(&self, py: Python<'py>, pane_id: String, grace_secs: f64) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let h = {
                let s = inner.read().unwrap();
                s.terms.get(&pane_id).cloned().ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("no live term '{pane_id}'")))?
            };
            h.terminate(grace_secs).await;
            Ok(())
        })
    }

    fn kill_term(&self, pane_id: &str) -> PyResult<()> {
        self.inner.read().unwrap().kill_term(pane_id).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
    }

    fn interrupt_term(&self, pane_id: &str) -> PyResult<()> {
        self.inner.read().unwrap().interrupt_term(pane_id).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
    }

    fn send_signal_term(&self, pane_id: &str, sig: i32) -> PyResult<()> {
        self.inner.read().unwrap().signal_term(pane_id, sig).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
    }

    /// Returns (pid, exit_code, signal) or None if already reaped.
    fn wait_term<'py>(&self, py: Python<'py>, pane_id: String) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let h = {
                let s = inner.read().unwrap();
                s.terms.get(&pane_id).cloned().ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("no live term '{pane_id}'")))?
            };
            Ok(h.wait().await)
        })
    }

    fn term_title<'py>(&self, py: Python<'py>, pane_id: String) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let h = {
                let s = inner.read().unwrap();
                s.terms.get(&pane_id).cloned().ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("no live term '{pane_id}'")))?
            };
            Ok(h.title().await)
        })
    }

    fn term_total_lines<'py>(&self, py: Python<'py>, pane_id: String) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let h = {
                let s = inner.read().unwrap();
                s.terms.get(&pane_id).cloned().ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("no live term '{pane_id}'")))?
            };
            Ok(h.total_lines().await)
        })
    }

    fn set_term_scrollback<'py>(&self, py: Python<'py>, pane_id: String, n: usize) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let h = {
                let s = inner.read().unwrap();
                s.terms.get(&pane_id).cloned().ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("no live term '{pane_id}'")))?
            };
            h.set_scrollback_lines(n).await;
            Ok(())
        })
    }
}

#[pyfunction]
pub fn list_themes() -> Vec<String> {
    ThemeRegistry::list_themes()
}

/// Fence theme names: the same shared registry as code (one list, eight
/// names — no silent fallback). Only present with the `markdown` feature.
#[cfg(feature = "markdown")]
#[pyfunction]
pub fn markdown_theme_names() -> Vec<String> {
    kilim_core::ThemeRegistry::list_themes()
}

#[pyfunction]
pub fn list_syntaxes() -> Vec<String> {
    ThemeRegistry::list_syntaxes()
}

/// Theme background as `#rrggbb` ("" when unknown) for full-bleed panes.
#[pyfunction]
pub fn theme_background(name: &str) -> String {
    ThemeRegistry::background(name).unwrap_or_default()
}

/// Theme foreground as `#rrggbb` ("" when unknown) for terminal panes.
#[pyfunction]
pub fn theme_foreground(name: &str) -> String {
    ThemeRegistry::foreground(name).unwrap_or_default()
}

/// Blend strength behind the Blend toggle (`kilim_core::color`).
#[pyfunction]
pub fn blend_weight() -> f64 {
    kilim_core::BLEND_WEIGHT as f64
}

/// The four unified Kilim themes as JSON: palette hexes + flags for the
/// Lace side (single source of truth lives in kilim-core).
#[pyfunction]
pub fn kilim_themes_json() -> String {
    serde_json::to_string_pretty(&kilim_core::kilim_themes::KILIM_THEMES.iter().map(|d| {
        serde_json::json!({
            "lace_key": d.lace_key,
            "neo_key": d.neo_key,
            "label": d.syntect_name,
            "syntect": d.syntect_name,
            "neo_syntect": d.neo_name,
            "base": d.base_syntect,
            "is_light": d.is_light,
            "bg": d.bg,
            "surface": d.surface,
            "border": d.border,
            "text": d.text,
            "accent": d.accent,
            "selection": d.selection,
        })
    }).collect::<Vec<_>>()).unwrap_or_default()
}

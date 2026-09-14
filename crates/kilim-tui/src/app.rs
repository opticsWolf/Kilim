//! App: ratatui event loop over shared kilim-core Session (v0.1.2).
//! Term I/O lives in `kilim_core::TermHandle`; here is input + draw + resize.

use std::collections::HashMap;

use crossterm::event::{self, Event, KeyCode, KeyModifiers};
use kilim_core::Session;

pub struct App {
    pub session: Session,
    /// Source file the session was loaded from (theme choices save back).
    pub layout_path: Option<String>,
    /// Live-tail scrollback offset per term pane (0 = follow).
    pub scroll: HashMap<String, usize>,
    /// Last drawn inner size per term pane (cols, rows) for resize sync.
    pub sizes: HashMap<String, (u16, u16)>,
}

impl App {
    pub fn new(doc: &str) -> anyhow::Result<Self> {
        let session = Session::from_json(doc).map_err(|e| anyhow::anyhow!(e))?;
        Ok(Self {
            session,
            layout_path: None,
            scroll: HashMap::new(),
            sizes: HashMap::new(),
        })
    }

    /// Cycle the code theme (TUI file panes + Qt FilePane share it) and
    /// persist the choice to the layout file when one is known.
    fn cycle_theme(&mut self, dir: i32) {
        let names = kilim_core::Session::list_themes();
        if names.is_empty() {
            return;
        }
        let cur = names
            .iter()
            .position(|n| n == self.session.theme())
            .unwrap_or(0);
        let next = (cur as i32 + dir).rem_euclid(names.len() as i32) as usize;
        // Infallible by construction: the name comes from the live list in
        // this single-threaded loop (registry only grows). Old theme stays
        // on the theoretical race — visible, never silently wrong.
        let _ = self.session.set_theme(&names[next].clone());
        if let Some(path) = self.layout_path.clone() {
            let _ = std::fs::write(path, self.session.to_doc_json());
        }
    }

    fn cycle_focus(&mut self, dir: i32) {
        let ids = self.session.layout.pane_ids();
        if ids.is_empty() {
            return;
        }
        let cur = ids.iter().position(|id| *id == self.session.layout.active).unwrap_or(0);
        let next = (cur as i32 + dir).rem_euclid(ids.len() as i32) as usize;
        self.session.layout.active = ids[next].clone();
    }

    async fn send(&mut self, data: &[u8]) {
        // Any real input snaps back to live tail.
        let active = self.session.layout.active.clone();
        self.scroll.insert(active.clone(), 0);
        let _ = self.session.write_term(&active, data).await;
    }

    /// Sync drawn sizes -> PTY winsize. Extracted for tests: `render`
    /// records inner sizes into `self.sizes`; this pushes changed ones
    /// to the pty and commits dims only on success (failures retry).
    pub async fn sync_sizes(&mut self) {
        let changed: Vec<(String, u16, u16)> = self
            .sizes
            .iter()
            .filter_map(|(id, &(cols, rows))| {
                let pane = self.session.panes.get(id)?;
                match &pane.kind {
                    kilim_core::layout::PaneKind::Term { rows: r, cols: c, .. } => {
                        if *r != rows || *c != cols {
                            Some((id.clone(), rows, cols))
                        } else {
                            None
                        }
                    }
                    _ => None,
                }
            })
            .collect();
        for (id, rows, cols) in changed {
            // Commit dims only on success: a failed resize (term
            // respawning, pty hiccup) stays stale and retries next
            // frame instead of sticking at the wrong size.
            if self.session.resize_term(&id, rows, cols).await.is_ok() {
                if let Some(p) = self.session.panes.get_mut(&id) {
                    if let kilim_core::layout::PaneKind::Term { rows: r, cols: c, .. } = &mut p.kind {
                        *r = rows;
                        *c = cols;
                    }
                }
            }
        }
    }

    pub async fn run(&mut self) -> anyhow::Result<()> {
        use crossterm::execute;
        use crossterm::terminal::{
            disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
        };
        use ratatui::Terminal;
        use ratatui::backend::CrosstermBackend;

        self.session.ensure_terms().await.map_err(|e| anyhow::anyhow!(e))?;
        enable_raw_mode()?;
        let mut stdout = std::io::stdout();
        execute!(stdout, EnterAlternateScreen)?;
        let backend = CrosstermBackend::new(stdout);
        let mut term = Terminal::new(backend)?;

        loop {
            term.draw(|f| {
                // Sync draw: snapshot screens via try_lock (fed by tokio tasks).
                super::ui::render(f, self);
            })?;
            // Sync drawn sizes -> PTY winsize.
            self.sync_sizes().await;
            if event::poll(std::time::Duration::from_millis(50))? {
                if let Event::Key(k) = event::read()? {
                    let ctrl = k.modifiers.contains(KeyModifiers::CONTROL);
                    let shift = k.modifiers.contains(KeyModifiers::SHIFT);
                    match k.code {
                        KeyCode::Char('q') if ctrl => break,
                        // Restart dead panes (ensure_terms replaces exited handles).
                        KeyCode::Char('r') if ctrl => {
                            let _ = self.session.ensure_terms().await;
                        }
                        // Cycle code theme (shared with Qt FilePane), persist.
                        KeyCode::Char('t') if ctrl => {
                            self.cycle_theme(if shift { -1 } else { 1 });
                        }
                        // Tab switching within a Tabs group (Ctrl+Tab). Plain Tab = focus.
                        KeyCode::Tab if ctrl => {
                            let a = self.session.layout.active.clone();
                            self.session.layout.cycle_tab(&a, if shift { -1 } else { 1 });
                        }
                        KeyCode::BackTab if ctrl => {
                            let a = self.session.layout.active.clone();
                            self.session.layout.cycle_tab(&a, -1);
                        }
                        // Focus cycle.
                        KeyCode::Tab => self.cycle_focus(if shift { -1 } else { 1 }),
                        KeyCode::BackTab => self.cycle_focus(-1),
                        // Scrollback (stays until next input snaps to tail).
                        KeyCode::PageUp => {
                            let a = self.session.layout.active.clone();
                            *self.scroll.entry(a).or_insert(0) += 10;
                        }
                        KeyCode::PageDown => {
                            let a = self.session.layout.active.clone();
                            let e = self.scroll.entry(a).or_insert(0);
                            *e = e.saturating_sub(10);
                        }
                        // Control codes.
                        KeyCode::Char('c') if ctrl => self.send(b"\x03").await,
                        KeyCode::Char('d') if ctrl => self.send(b"\x04").await,
                        KeyCode::Char(c) => {
                            // Ctrl+letter -> control byte (Ctrl-A..Z), else text.
                            if ctrl && c.is_ascii_alphabetic() {
                                let b = (c.to_ascii_uppercase() as u8) & 0x1F;
                                self.send(&[b]).await;
                            } else {
                                self.send(c.to_string().as_bytes()).await;
                            }
                        }
                        KeyCode::Enter => self.send(b"\r").await,
                        KeyCode::Backspace => self.send(b"\x7f").await,
                        KeyCode::Delete => self.send(b"\x1b[3~").await,
                        KeyCode::Up => self.send(b"\x1b[A").await,
                        KeyCode::Down => self.send(b"\x1b[B").await,
                        KeyCode::Right => self.send(b"\x1b[C").await,
                        KeyCode::Left => self.send(b"\x1b[D").await,
                        KeyCode::Home => self.send(b"\x1b[H").await,
                        KeyCode::End => self.send(b"\x1b[F").await,
                        KeyCode::Esc => break,
                        _ => {}
                    }
                }
            }
        }

        disable_raw_mode()?;
        execute!(term.backend_mut(), LeaveAlternateScreen)?;
        Ok(())
    }
}

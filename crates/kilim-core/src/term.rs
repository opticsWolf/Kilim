//! Shared PTY handle (setup B, v0.2).
//!
//! Both frontends own the same object: `stitch-pty` backend for I/O +
//! `HistoryScreen` for emulation. Reader task feeds bytes straight into
//! the screen; UI renders `styled_viewport()` / `styled_range()`.
//! No GIL, no Qt, no ratatui in here.

use std::sync::Arc;

use stitch_pty::platform::{spawn_platform, ChildBackend, PtyBackend};
use stitch_pty::terminal::HistoryScreen;
use tokio::sync::Mutex;

pub struct TermHandle {
    pub backend: Arc<dyn PtyBackend>,
    pub child: Arc<dyn ChildBackend>,
    pub screen: Arc<Mutex<HistoryScreen>>,
}

impl TermHandle {
    /// Spawn `cmd` and pump PTY output into a scrollback screen.
    /// `cwd` is resolved by [`expand_cwd`] (inherit on empty/missing).
    pub async fn spawn(
        cmd: &str,
        args: &[String],
        rows: u16,
        cols: u16,
        scrollback: usize,
        cwd: Option<&str>,
    ) -> Result<Arc<Self>, String> {
        // Empty cmd = "the platform default" — layouts stay portable.
        let (cmd, args) = if cmd.is_empty() {
            crate::shell::default_shell()
        } else {
            (cmd.to_string(), args.to_vec())
        };
        let env: Vec<(String, String)> = std::env::vars().collect();
        let ws = stitch_pty::winsize::Winsize {
            rows,
            cols,
            xpixel: 0,
            ypixel: 0,
        };
        let resolved = expand_cwd(cwd);
        let (backend, child) = spawn_platform(&cmd, &args, &env, Some(ws), resolved.as_deref())
            .await
            .map_err(|e| e.to_string())?;
        let screen = Arc::new(Mutex::new(HistoryScreen::new(
            cols as usize,
            rows as usize,
            scrollback,
        )));
        let handle = Arc::new(Self {
            backend,
            child,
            screen,
        });

        // Pump: read bytes -> feed screen. Ends on EOF/error.
        let reader = handle.backend.clone();
        let scr = handle.screen.clone();
        tokio::spawn(async move {
            let mut buf = vec![0u8; 8192];
            loop {
                match reader.read(&mut buf).await {
                    Ok(0) => break,
                    Ok(n) => {
                        scr.lock().await.feed(&buf[..n]);
                    }
                    Err(_) => break,
                }
            }
        });
        Ok(handle)
    }

    pub async fn write(&self, data: &[u8]) -> std::io::Result<usize> {
        self.backend.write(data).await
    }

    pub fn resize(&self, rows: u16, cols: u16) -> Result<(), String> {
        self.backend
            .set_winsize(stitch_pty::winsize::Winsize {
                rows,
                cols,
                xpixel: 0,
                ypixel: 0,
            })
            .map_err(|e| e.to_string())?;
        // Screen resize needs the mutex; frontends call `resize_screen` (async).
        Ok(())
    }

    pub async fn resize_screen(&self, rows: usize, cols: usize) {
        self.screen.lock().await.resize(rows, cols);
    }

    pub fn is_alive(&self) -> bool {
        self.child.is_running()
    }

    pub fn pid(&self) -> u32 {
        self.child.pid()
    }

    /// Full buffer as styled cells: (text, fg, bg, attrs). fg/bg are
    /// `"default"`, an ANSI name, or 6-hex RGB (no `#`) — see stitch-pty docs.
    pub async fn styled_viewport(&self) -> Vec<Vec<(String, String, String, u8)>> {
        self.screen.lock().await.styled_viewport()
    }

    /// O(window) slice for large scrollbacks.
    pub async fn styled_range(
        &self,
        start: usize,
        count: usize,
    ) -> Vec<Vec<(String, String, String, u8)>> {
        self.screen.lock().await.styled_range(start, count)
    }

    /// One-lock-call snapshot for bridges: (total, start, cells, cursor,
    /// modes, dirty). `anchor=None` tracks the live tail; `Some(a)` holds
    /// position clamped to the tail (scrollback). Replaces 3 round-trips
    /// with 1. modes = (app_cursor, bracketed_paste, mouse_proto,
    /// sgr_mouse, alt_screen, bell). dirty = history-absolute rows the
    /// screen reports changed (Konsole-style dirty set — frontends repaint
    /// only these). Draining events here bounds the log: nobody else does.
    pub async fn snapshot_tail(
        &self,
        rows: usize,
        anchor: Option<usize>,
    ) -> (
        usize,
        usize,
        Vec<Vec<(String, String, String, u8)>>,
        (usize, usize),
        (bool, bool, u16, bool, bool, bool),
        Vec<usize>,
    ) {
        let mut screen = self.screen.lock().await;
        let total = screen.total_lines();
        let tail = total.saturating_sub(rows);
        let start = match anchor {
            None => tail,
            Some(a) => a.min(tail),
        };
        let cells = screen.styled_range(start, rows);
        let cursor = screen.absolute_cursor();
        let mode = screen.mode();
        let (app_cursor, bracketed, mouse, sgr, alt) = (
            mode.has_private(1),    // DECCKM: application cursor keys
            mode.has_private(2004), // bracketed paste
            mode.mouse_protocol(),  // 0/1000/1002/1003
            mode.sgr_mouse(),       // 1006 SGR encoding
            mode.is_alt_screen(),
        );
        // Drain the event log (bell + titles/cwd: titles are the shell's
        // business — Lace tab titles are pane identity, not synced).
        let events = screen.take_events();
        let bell = events
            .iter()
            .any(|e| matches!(e, stitch_pty::terminal::events::TermEvent::Bell));
        let modes = (app_cursor, bracketed, mouse, sgr, alt, bell);
        let base = total.saturating_sub(screen.lines());
        let dirty: Vec<usize> = screen
            .take_dirty_rows()
            .into_iter()
            .map(|r| base.saturating_add(r))
            .collect();
        (total, start, cells, cursor, modes, dirty)
    }

    /// Drain pending low-frequency events (bell/title/cwd) without reading
    /// them. For consumers that never snapshot (TUI renders straight from
    /// the screen): keeps the log bounded on long sessions.
    pub async fn drain_events(&self) {
        let _ = self.screen.lock().await.take_events();
    }

    pub async fn total_lines(&self) -> usize {
        self.screen.lock().await.total_lines()
    }

    /// Query one private DEC mode flag (1=DECCKM, 2004=bracketed, ...).
    /// Cheap single-lock read for input paths (TUI arrows, Qt paste).
    pub async fn dec_private(&self, mode: u16) -> bool {
        self.screen.lock().await.mode().has_private(mode)
    }

    pub async fn absolute_cursor(&self) -> (usize, usize) {
        self.screen.lock().await.absolute_cursor()
    }

    pub async fn visible_rows(&self) -> usize {
        self.screen.lock().await.visible_display().len()
    }

    // ── Process control (parity with stitch-pty wheel) ──

    /// SIGTERM, wait up to `grace_secs`, then SIGKILL. Mirrors the wheel's
    /// `terminate()` polling loop (no tokio timer across the PyO3 bridge).
    pub async fn terminate(&self, grace_secs: f64) {
        let _ = self.child.signal(15);
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs_f64(grace_secs.max(0.0));
        while self.child.is_running() && std::time::Instant::now() < deadline {
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        }
        if self.child.is_running() {
            let _ = self.child.kill();
        }
    }

    pub fn kill(&self) -> Result<(), String> {
        self.child.kill().map_err(|e| e.to_string())
    }

    /// Ctrl-C: SIGINT on POSIX, console Ctrl event on Windows.
    pub fn interrupt(&self) -> Result<(), String> {
        self.child.signal(2).map_err(|e| e.to_string())
    }

    pub fn send_signal(&self, sig: i32) -> Result<(), String> {
        self.child.signal(sig).map_err(|e| e.to_string())
    }

    /// Wait for exit. `None` = already reaped. Tuple is
    /// (pid, exit_code, signal) like the wheel's `ExitStatus`.
    pub async fn wait(&self) -> Option<(u32, Option<i32>, Option<i32>)> {
        self.child.wait().await.map(|e| (e.pid, e.exit_code, e.signal))
    }

    // ── Screen state (parity for Qt paint paths) ──

    /// Window title from OSC sequences.
    pub async fn title(&self) -> String {
        self.screen.lock().await.title().to_string()
    }

    pub async fn history_size(&self) -> usize {
        self.screen.lock().await.history_size()
    }

    pub async fn scrollback_lines(&self) -> usize {
        self.screen.lock().await.scrollback_lines()
    }

    pub async fn set_scrollback_lines(&self, n: usize) {
        self.screen.lock().await.set_scrollback_lines(n);
    }
}

/// Resolve a pane/shell start dir for `spawn_platform`.
///
/// Empty or missing input (and missing dirs) mean inherit — the child
/// keeps the Kilim process cwd, exactly as before. A leading `~`
/// expands to the home dir (USERPROFILE, then HOME). Never fails:
/// bad input degrades to inherit instead of erroring the spawn.
fn expand_cwd(cwd: Option<&str>) -> Option<String> {
    let raw = cwd.map(str::trim).filter(|s| !s.is_empty())?;
    let expanded = if raw == "~" || raw.starts_with("~/") || raw.starts_with("~\\") {
        let home = std::env::var("USERPROFILE")
            .or_else(|_| std::env::var("HOME"))
            .ok()?;
        format!("{}{}", home, &raw[1..])
    } else {
        raw.to_string()
    };
    std::path::Path::new(&expanded).is_dir().then_some(expanded)
}

#[cfg(test)]
mod tests {
    use super::expand_cwd;

    #[test]
    fn cwd_empty_or_missing_means_inherit() {
        assert_eq!(expand_cwd(None), None);
        assert_eq!(expand_cwd(Some("")), None);
        assert_eq!(expand_cwd(Some("   ")), None);
        assert_eq!(expand_cwd(Some("kilim-definitely-not-a-dir")), None);
    }

    #[test]
    fn cwd_existing_dir_passes_through() {
        let dir = std::env::temp_dir();
        let back = expand_cwd(dir.to_str());
        assert!(back.is_some(), "temp dir should resolve");
    }

    #[test]
    fn cwd_tilde_expands_to_home() {
        let home = expand_cwd(Some("~"));
        assert!(home.is_some(), "home dir should exist");
        assert!(std::path::Path::new(&home.unwrap()).is_dir());
    }
}

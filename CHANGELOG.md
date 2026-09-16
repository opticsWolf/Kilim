# Changelog

Full version history (condensed from the original dev-log README).
- v0.4.17: qtermwidget-style paint economy — block surgery now covers every overlapping window move (wheel scrollback, height resizes), not just forward streaming — kept rows are never reinserted or re-detected. Cell formats are cached per style (was: rebuilt + color strings re-parsed per run per paint), and path detection skips rows that cannot hold a hit. Width changes still rebuild (cells reshape). Also fixed a test flake: mock await-lists record on the bridge thread, so late pre-patch awaits leaked into the window under load.
- v0.4.16: instant-feeling resizes — resize drags coalesce: polls hold the settled grid (output keeps streaming into it) and one resize plus one rebuild land after 2 quiet polls, instead of a full rebuild every 60 ms. Anchoring needed no help — resize never rewraps scrollback, so frozen and tail positions survive unchanged.
- v0.4.15: more paths linkify — pytest/rust `path::test` node ids link the path part, bare Makefile/README-style names open when such a file exists, and a pinned sweep keeps .txt/.json/.yaml/.toml/.rs/.py plus the usual language extensions opening as code. Path tests now serialize on a mutex (shared kind cache vs parallel threads flaked once in ten runs).
- v0.4.14: calmer terminal repaints — streaming output now scrolls blocks (drop head, append tail) instead of clear()+rebuild every poll, so long sessions stop flashing; jumps still rebuild. Relative paths linkify against the shell's live directory (OSC 7 via the snapshot), else its configured start dir, else the app dir — the Start Directory menu now also fixes relative detection for shells without OSC 7.
- v0.4.13: start directory modes — each shell chooses App default (inherit Kilim's directory), Shell default (home), or a picked directory, radio-checked per shell in the Start Directory submenu; every new terminal starts in its shell's setting. v0.4.12 bare-string sidecars still read.
- v0.4.12: shell start directories — the Terminal menu gains Start Directory: pick where each shell launches (sidecar-stored, resettable, inherited cwd when unset), threaded through core spawn (layout panes accept a `cwd` key too, restarts keep it, missing dirs fall back silently). Themes submenus renamed Lace — App, Code — Syntax.
- v0.4.11: separate terminal theme — the Themes menu is Lace / Code / Terminal: one Code apply sets code + Markdown fences together (both keys persist equal, so they can never diverge), terminals theme on their own track persisted as `terminal_theme`, and a Lace choice themes every part at once. The TUI cycles both together on Ctrl+T too. Fresh launch (or a sidecar restore) still reunifies everything behind the Lace selection.
- v0.4.10: Qt split — qt_app.py (2338 lines) is now eight focused modules plus a thin facade (main_window, terminal_pane, viewer_panes, title_bar, qt_themes, qt_bridge, qt_termkeys, qt_util); every existing `kilim.qt_app` import keeps working, and the test that patched QTimer now targets kilim.main_window. Status bar message gets its 5px left and right (a label with a 3px inset for the left, widget margins for the right — layout margins are silently dropped when the bar relayouts). CodeRadar index initialized (.coderadar.toml committed, store ignored).
- v0.4.9: status bar without the resize handle — QSizeGrip switched off (setSizeGripEnabled(False)); the frameless window's native edges still resize. Regression test asserts no QSizeGrip child exists and that the bar stays full-width/bottom-flush across resizes.
- v0.4.8: Qt status bar — hovered link target (transient, browser style, cleared when the pointer leaves), plus focused pane and code theme as permanent widgets. Plain QMainWindow status bar: the window's own layout owns the geometry (no resize handler, no manual placement — a test resizes 3x and asserts full width + bottom-flush) and Lace's app-wide DockThemeBridge themes its palette, so it follows theme switches like the dock chrome. Active pane comes from session_active() (Terminal/File/Preview/Web, long paths middle-elided with the full path as tooltip, dead shells marked '(exited)').
- v0.4.7: TUI keys match the Qt surface — Esc is forwarded to the active pane (0x1b) instead of quitting, with Ctrl-Q as the way out. Tab stays Kilim's native key (plain Tab/Shift-Tab cycle pane focus, Ctrl-Tab switches tabs in a Tabs group) and **Alt-Tab now sends a real Tab to the active pane** (Alt-Shift-Tab / Alt-BackTab send ESC[Z back-tab), so vim/fzf/agents can receive one. The whole map is a pure key_action() function with unit tests. Also fixed a #[test] that had been swallowed by a doc-comment line in ui.rs, so file_pane_fills_theme_paper actually runs (TUI tests 6 -> 10).
- v0.4.6: lace-dock 0.7.6 — Esc is now scoped upstream. The window-wide "close sidebar" shortcut is enabled only while an overlay is visible (Lace's event filter on the overlay's Show/Hide), so the terminal keeps Escape whenever nothing is open and Esc still dismisses a hover overlay. Kilim's local shortcut-disabling workaround is gone; pyproject/README require >= 0.7.6. Test: the Qt-path key test now walks the whole cycle (no overlay — shell gets 0x1b; overlay up — sidebar fires and the shell sees nothing; overlay hidden — shell gets 0x1b again).
- v0.4.5: terminal keys reach the app — Tab/Shift+Tab were swallowed by Qt's focus navigation before keyPressEvent (a new focusNextPrevChild() on the view hands them over; Shift+Tab now sends ESC[Z back-tab, Tab sends 0x09), and Escape was eaten by Lace's window-wide "close sidebar" shortcut (disabled in Kilim — with a second same-key shortcut Qt called both ambiguous and neither fired). Esc forwards 0x1b again, with an integration test on the real Qt event path.
- v0.4.4: stitch-pty 0.8.1 — colored blocks (pi-style chips) render across their whole width now. ConPTY trims trailing whitespace and hands the padding back as EL/ECH with the block SGR still active; the emulator has to implement background-color-erase, which 0.8.1 does (erase/insert/scroll fills keep the current SGR, wide-char continuations follow their glyph). Kilim requires stitch-pty >= 0.8.1 and pins the behavior with a real-ConPTY regression test.
- v0.4.3: the Themes submenu is plain Code — it read "Code (Qt + TUI)", which was noise in a menu that only holds the ten themes.
- v0.4.2: no theming of terminal content — Blend is gone (menu toggle, kilim_core::color, the layout flag, the TUI weight and their tests): cells take the theme's ink/paper only where the shell left them at "default", and every color a tool painted (ANSI blocks, truecolor chips like pi's) renders exactly as emitted. The TUI keeps the resolve-then-swap fix for reversed default cells (paper-on-ink, not the raw Reset swap). A stale code_blend key in a layout file is ignored.
- v0.4.1: Lace updated 0.7.0 — 0.7.5 and Kilim adapted to its API — LaceStandardTitleBar is a real base now (anchored insert_content_widget instead of a hardcoded layout index; inherited paintEvent + canDrag, so Kilim's copies are gone), DockManager installs its own palette bridges (manual DockThemeBridge dropped), floating_title_bar defaults to LaceStandardTitleBar (explicit assignment dropped), and the WinIdChange chrome auto-heal replaces Kilim's manual updateFrameless() after the WebEngine page load (verified on the live window: CAPTION/THICKFRAME survive). Lace imports moved to the documented top level, lace-dock>=0.7.5 is declared in the qt extra (it was missing), and the test suite's layout copies now wait out a live app rewriting the shared layout files.
- v0.4.0: Themes — Code — Blend — tool-painted terminal color blocks (agents like pi, linter highlights) mix into the active code theme: backgrounds toward its paper, text toward its ink, with a WCAG contrast floor and a polarity guard so a dark chip on a light theme stays readable. The flag persists in the layout file so the TUI blends identically (RGB cells; the TUI leaves named ANSI colors to the host terminal). Math lives in kilim_core::color with a Python bridge for the Qt renderer, cached per color pair.
- v0.3.3: the Window menu is gone from the title bar — its Minimize / Toggle Maximize entries duplicated the window buttons next to it; the bar is now Views / Files / Terminal / Themes.
- v0.3.2: viewer text decoding — panes read through a BOM-aware reader with a cp1252 fallback, so a text file binaryornot-rs accepts opens whatever its encoding (a cp1252 markdown raised "stream did not contain valid UTF-8"); the html pane reads through the same path. Also: a fresh launch no longer rewrites the shared layout file (persist=False gates both files), which keeps the default layout untouched and stops parallel test windows racing on it.
- v0.3.1: second viewer dock fix — tabifying a click-opened dock used a DockWidget as Lace's target (which expects a DockAreaWidget), so opening a second, different file raised AttributeError; the viewer group area is now tracked and the regression test opens two files.
- v0.3.0: clickable terminal paths — a Rust detector in kilim-core (char offsets, `:line:col`, quotes, `~`/`file://`/Git-Bash roots) classifies hits with binaryornot-rs; the Qt surface underlines text paths and opens each click in a fresh viewer dock (code / markdown / html), closed docks dispose their pane, and a Files menu keeps the last 20 for one-click reopening. The Terminal menu gains a sidecar-persisted default-shell picker over the shells actually installed (cross-platform discovery), `New <default>` spawns it, and the shipped default layout is a single terminal pane with no pinned cmd (empty cmd = platform default, so layouts stay portable)
- v0.2.2: the Qt terminal block cursor is focus-gated — it appears when the pane's view has the focus, clears on blur, and the blink clock only runs while focused.
- v0.2.1: Qt terminal block cursor — a read-only QPlainTextEdit draws no caret, so the Qt frontend now bakes a reverse-video block into the cell under the terminal cursor (TUI soft-cursor parity), blinking on the system caret clock; it follows moves without dirty rows and its blink frames count as overlay, not content, repaints.
- v0.2.0: version cut — the theme system rework (base theme + functional token palettes) and the demo-parity frameless Qt chrome rounded up into the 0.2 line; no code changes over v0.1.71.
- v0.1.71: functional token palettes — every theme maps the canonical element classes (comment/string/keyword/declaration/modifier/function/type/parameter/number/constant) to its own contrasting colors; no per-word rules, and a test pins each applied hex plus a redmean >= 55 gap for all ten themes.
- v0.1.70: hand-maintained per-scope color tables removed — Kilim themes now adjust chrome only (paper/foreground/selection) and inherit their base theme's functional scope rules verbatim; the Dark Neo pub/render collision came from a patched storage.modifier.rust rule and goes with the tables.
- v0.1.69: Kilim Light chrome neutralized — the stock Lace light-blue cast reduced to a faint cool tint (bg/surface/border/text/selection); paper stays white.
- v0.1.68: Reset Layout restores the arrangement the layout file defines — the sidecar only restores a *session* at launch, so a saved layout can no longer masquerade as the default.
- v0.1.67: legacy theme-name mapping removed (no aliases, no name canonicalization); the stock-chrome pair takes the freed name — Kilim Dark / Kilim Dark Neo (was Default / Default Neo).
- v0.1.66: Kilim Dark / Dark Neo renamed Kilim Midnight / Midnight Neo two new dark themes on Lace's stock default chrome — Kilim Default / Default Neo, the Kilim chassis and outline on the VS Code Dark+ palette, with Visual Studio Dark+ / One Dark Pro mordant bases. Ten unified themes.
- v0.1.65: title bar follows the Lace demo (shipped Kilim icon, hidden title label, menus right after the icon, Window menu, plus frameless custom bars on floating panes); markdown panes keep the native frame — Chromium's handle recreation no longer costs Win11 rounded corners or Aero Snap; dark/light get the unfocused dock-area outline warm/neutral already had.
- v0.1.64: removed the ctypes DWM corner workaround — window setup back to demo shape; rounding now waits on the Lace-side fix.
- v0.1.63: floating panes back to native OS windows (snap, taskbar, Win+arrows); only the main window stays frameless.
- v0.1.62: markdown refresh deferred past the bridge palette push — preview scrollbar CSS no longer lags one theme switch behind.
- v0.1.61: markdown preview scrollbars tinted to the live Fusion colors (sampled by rendering, scrollbar-color + color-scheme).
- v0.1.60: Win11 rounded outer corners restored (DWM corner preference, lost to the frameless hint).
- v0.1.59: title-bar demo parity — DockThemeBridge (themed popups), Fusion style, explicit central widget, fallback window icon.
- v0.1.58: frameless window with the menus embedded in a custom title bar (Lace demo pattern) — icon + Kilim title, then Views/Terminal/Themes; bar re-themes with the dock theme; floats keep the plain Lace bar.
- v0.1.57: reverted the darker dock gaps — all chrome back to stock Lace derivation.
- v0.1.55: stitch-pty 0.8.0 — event log now capped at MAX_EVENTS (1024, drop-oldest) upstream, so the TUI drain is belt-and-braces; no integration changes needed, full suite green.
- v0.1.54: stitch-pty 0.7.6 — BEL flag + event log + dirty rows consumed: bell flashes the tab dot (1.5s), Qt repaint driven by the real dirty set (my snapshot-diff deleted), titles deliberately not synced (shells retitle every prompt), TUI drains the event log per frame so it cannot grow unbounded.
- v0.1.53: QTermWidget lessons — P0 dirty-region repaint (only changed blocks relayout) + cursor repositioned on move only (native blink survives); P1 history-anchored selection (paints continue underneath), DECCKM app-cursor keys (Qt+TUI), bracketed paste, SGR/X10 mouse incl. wheel; P2 background-tab activity dots, wide-char cursor/selection mapping. Bell stays out: stitch-pty swallows BEL with no flag to read.
- v0.1.52: TUI color resolution 2.8x faster — byte-exact fast path kills the per-cell lowercase allocation; manual nibble loop replaces 3x from_str_radix; slow path kept as exact-behavior fallback.
- v0.1.51: Qt input cursor actually fixed — Lace tab-bar chrome stole focus at startup (only a focused text widget draws its cursor); active-pane focus is now claimed on retries + focus changes, never stealing from content/menus.
- v0.1.50: TUI input lag fixed — file highlights cached on (mtime, len, theme) instead of re-running syntect every frame; all pending keys drained per frame; poll 50ms to 33ms.
- v0.1.49: TUI no longer doubles key input — the key handler ignored event kind, so Windows Press + Release both wrote to the PTY; only Press/Repeat act now.

- v0.1.0: layout model, syntect highlight, TUI shell+file panes, Py surface.
- v0.1.1: Qt window â€” `python/kilim/qt_app.py` hosts layout.json in Lace docks
  (TerminalPane polls `term_range`, FilePane paints `highlighted_file`).
- v0.1.2: TUI input parity â€” Tab focus cycle, arrows/keys, Ctrl codes, PgUp/PgDn
  scrollback, live resize sync, `\r` Enter.
- v0.1.3: Markdown panes via mordant (`markdown` feature, opt-in) + test.
- v0.1.4: theme sharing â€” `register_custom_theme` (.tmTheme everywhere,
  VSCode JSON via mordant, mirrored into both registries).
- v0.1.5: lifecycle â€” per-pane `scrollback`, `ensure_terms` respawns dead,
  `restart_term`/`exited_terms`, Ctrl+R + status bar, pytest suite.
- v0.1.6: perspective round-trip â€” `python/kilim/perspective.py` sidecar
  (`tab_groups` + active + opaque Lace blob); launch honors layout `Tabs`
  via tabify (`center`+target), close captures, launch applies on name match.
- v0.1.7: TUI navigation â€” PgUp/PgDn scrolls file panes (clamped), Ctrl+Tab
  cycles `Tabs` groups with active-follows (`Layout::cycle_tab`).
- v0.1.8: Qt scrollback â€” scrollbar addresses absolute history (bottom =
  live tail); wheel/drag holds position, any keypress snaps back to follow;
  cursor hidden while scrolled back.
- v0.1.9: Qt paint fast path â€” idle-skip on `(total, cursor, start, rows)`
  signature plus same-style run-merging (~2k Qt edits â†’ ~50â€“200/frame).
- v0.1.10: profiled bridges â€” `snapshot_tail` folds total+range+cursor
  into one lock/call (snap p50 âˆ’60%, max 59â†’8ms); `beginEditBlock`
  batches Qt layout (paint max âˆ’70%). `scripts/profile_qt.py` keeps numbers.
- v0.1.11: Qt selection â€” mouse selection freezes rebuilds (polls continue,
  resume repaints via stale signature); Ctrl+Shift+C copies, Esc resumes.
  TUI needs nothing: it never captures the mouse, so outer-terminal
  selection already works there.
- v0.1.12: scrollback that stays put â€” the built-in bar was decorative
  (Qt recomputes it from the one-viewport document every layout) and its
  resets clobbered position via `valueChanged`. TerminalPane is now a
  QWidget with a dedicated external scrollbar driven by `actionTriggered`
  (user gestures only) + drag guard.
- v0.1.13: right-click menu â€” Copy when selecting, always Paste. Ctrl+C
  stays `\x03`: stealing it would kill SIGINT, hence Ctrl+Shift+C.
- v0.1.14: way back from empty â€” `Views` menu (per-pane toggles, Show All
  Panes, Reset Layout over the saved perspective).
- v0.1.15: smooth wheel â€” fractional delta accumulation (120 units = 1
  line, touchpad-precise; Shift Ã—quarter-page) plus instant repaint on
  scroll gestures instead of waiting out the 60ms poll.
- v0.1.16: README renders â€” MarkdownPane follows mordant's md_viewer
  pattern (wheel `markdown_to_html` + GFM/math/mermaid, WebEngine when
  present else rich text); Rust `markdown` feature stays as fallback.
- v0.1.17: pure Rust preview â€” `markdown_page()` (fragment + KaTeX shell)
  ships in every wheel via `kilim-py/markdown` in maturin features (bare
  CLI `--features` silently built without the symbols); wheel dep dropped.
- v0.1.18: 3-line wheel notches with fractional accumulation + instant
  repaint kept â€” reactive and smooth.
- v0.1.19: cursor hunt â€” TUI cursor proven by live-shell TestBackend test
  (was already correct); Qt shell got autofocus (unfocused text widgets
  draw no cursor).
- v0.1.20: unmissable cursor â€” TUI paints a reverse-video cell under the
  cursor (Modifier::REVERSED, live-tested) on top of hardware positioning,
  so it shows on consoles that swallow the hardware cursor.
- v0.1.21: Terminal menu â€” launch PowerShell / Git Bash (auto-detected)
  / Command Prompt into new left docks; `Session::spawn_term` splices the
  pane into the live tree, so the TUI shows it too.
- v0.1.22: GFM on â€” `markdown_page` chains `gfm_table/strikethrough/
  task_list_item` (in-tree but opt-in; default path left them literal).
- v0.1.23: mordant 0.10 â€” `highlighter` finally carries `serde_json`
  (`diagram` workaround dropped; `math` listed explicitly for KaTeX);
  TUI registry filled from bat assets (7 â†’ 29 themes, Dracula real).
- v0.1.24: Themes menu â€” Lace chrome (grouped, sidecar-persisted), Code
  (Qt + TUI, Dracula default, Ctrl+T cycles in TUI), Markdown fences
  (mordant set; bat themes mirrored via a Themeâ†’tmTheme writer so no
  silent InspiredGitHub fallback). Choices persist to the layout file.
- v0.1.25: md_viewer parity â€” one markdown theme drives fences + mermaid
  (native `Derived` spec via a registry resolver hook) + KaTeX math;
  renderer order is load-bearing (highlighting last, as in mordant-py).
- v0.1.26: themed page chrome â€” `markdown_page` derives body/table/code/
  link CSS from the markdown theme's own bg/fg (Dracula â†’ dark page,
  no toggle), modeled on md_viewer's shell.
- v0.1.27: user theme dirs â€” `load_builtin_themes()` runs at import so
  `~/.mordant/themes` customs join the shared registry (wheel parity).
- v0.1.28: themes from upstream â€” build.rs fetches the wheel theme files
  from the mordant GitHub tag (no vendoring); `.github/workflows/release.yml`
  pre-fetches the same way for wheels + TUI binaries + tests.
- v0.1.29: four unified core themes â€” Kilim Dark (darkâŠ•midnight blend) /
  Neutral / Light / Warm. Palette in kilim-core, one shared Lace chassis,
  syntect built from the closest expressive base (Night Owl, GitHub,
  OneHalfLight, gruvbox-dark) with chrome-only adjustment; selecting
  Kilim chrome applies code + markdown too. Default on all surfaces.
- v0.1.30: code-view paint fix â€” syntect's trailing newlines no longer
  double every line; widget Base + block backgrounds set to the theme bg
  for full-bleed color (new `theme_background` core helper).
- v0.1.31: second chassis â€” the 4 palettes re-housed in a neo/edge mix
  (10px card, 8px tabs, edge title rule, ringed pills; accents recolored
  per palette). New `Kilim Neo` entries, same syntect themes, unified apply.
- v0.1.32: sidebar pinning â€” both sidebars exist upfront (title-bar pin
  buttons included); pins persist via the perspective sidecar.
- v0.1.33: theme reduction â€” one shared registry of exactly 8 (4 Kilim
  palettes Ã— standard/neo); single "Kilim" Lace submenu; unknown names
  raise; Pin menu removed.
- v0.1.34: Kilim-only exposure â€” user customs (~/.mordant) no longer
  auto-import (explicit `register_custom_theme` still works); Vivid
  renamed Neo (`Kilim Dark Neo`, `kilim_neo_dark`) everywhere.
- v0.1.35: Lace menu is Kilim-only and flat â€” stock presets hidden.
- v0.1.37: richer code colors â€” curated scope tables fill thin bases
  (Warm Neo gains keywords/storage/classes/params; Neutral blacks fixed
  to slate/blue; Light/Neo gaps filled); light editor backgrounds move
  to the chrome surface (`#f5f7fa` / `#d2d5d9`).
- v0.1.38: Dark splits `pub`/`fn`/`render` (pink/keyword-purple/blue);
  neo display settings mirror standard (identical background behavior,
  token colors differ); Light `#ffffff` / Neutral `#e9eaec` paper;
  Light Neo keywords/storage go ayu blue off the orange pile.
- v0.1.39: `pub`â‰ `fn` in all 8 (per-theme split atoms â€” Warm's shared
  group needed `storage.type.annotation` last); dark/warm splitter
  handles drop to 50% base; Neutral paper 5% darker (`#dddee0`).
- v0.1.40: reverted the dark/warm splitter darkening (Lace base handle
  again).
- v0.1.41: Kilim Dark palette = 2/3 Lace dark + 1/3 Lace midnight
  (`#101319`/`#161a23`/`#cbd0dc`/`#325ac6`); editor/paper follows for
  Dark + Dark Neo.
- v0.1.42: TUI file/markdown panes paint full-bleed theme paper
  (line tails, below-EOF rows, borders â€” matches Qt viewers);
  terminal resizes retry on failure (Qt) and commit only on success
  (TUI) instead of sticking at wrong dimensions.
- v0.1.43: TUI shell view follows the theme (default cells take theme
  fg/bg); resize sync extracted for tests (smallâ†’big relayout proven).
- v0.1.44: Qt shell view follows the code theme too (themed widget
  palette + forced repaint on switch; explicit shell colors untouched);
  resize sync runs before the snapshot guard so slow frames can't
  stall dimension updates; TUI term shortfall rows take theme paper.
- v0.1.45: markdown preview scrollbars painted from page CSS (theme
  selection thumb on page-bg track) â€” Chromium ignores Qt stylesheets.
- v0.1.46: reverted all scrollbar styling (v0.1.45 CSS + live-palette
  override) â€” preview keeps default Chromium scrollbars.
- v0.1.47: light/neutral scrollbar thumbs go theme grey (border Ã—
  0.75) via app QSS + minimal Chromium CSS; grooves stay native.
- v0.1.48: reverted the grey scrollbar thumbs â€” all native/default
  bars again.

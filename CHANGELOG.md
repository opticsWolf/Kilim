# Changelog

Full version history (condensed from the original dev-log README).
- v0.1.71: functional token palettes � every theme maps the canonical element classes (comment/string/keyword/declaration/modifier/function/type/parameter/number/constant) to its own contrasting colors; no per-word rules, and a test pins each applied hex plus a redmean >= 55 gap for all ten themes.
- v0.1.70: hand-maintained per-scope color tables removed � Kilim themes now adjust chrome only (paper/foreground/selection) and inherit their base theme's functional scope rules verbatim; the Dark Neo pub/render collision came from a patched storage.modifier.rust rule and goes with the tables.
- v0.1.69: Kilim Light chrome neutralized � the stock Lace light-blue cast reduced to a faint cool tint (bg/surface/border/text/selection); paper stays white.
- v0.1.68: Reset Layout restores the arrangement the layout file defines � the sidecar only restores a *session* at launch, so a saved layout can no longer masquerade as the default.
- v0.1.67: legacy theme-name mapping removed (no aliases, no name canonicalization); the stock-chrome pair takes the freed name � Kilim Dark / Kilim Dark Neo (was Default / Default Neo).
- v0.1.66: Kilim Dark / Dark Neo renamed Kilim Midnight / Midnight Neo two new dark themes on Lace's stock default chrome � Kilim Default / Default Neo, the Kilim chassis and outline on the VS Code Dark+ palette, with Visual Studio Dark+ / One Dark Pro mordant bases. Ten unified themes.
- v0.1.65: title bar follows the Lace demo (shipped Kilim icon, hidden title label, menus right after the icon, Window menu, plus frameless custom bars on floating panes); markdown panes keep the native frame � Chromium's handle recreation no longer costs Win11 rounded corners or Aero Snap; dark/light get the unfocused dock-area outline warm/neutral already had.
- v0.1.64: removed the ctypes DWM corner workaround � window setup back to demo shape; rounding now waits on the Lace-side fix.
- v0.1.63: floating panes back to native OS windows (snap, taskbar, Win+arrows); only the main window stays frameless.
- v0.1.62: markdown refresh deferred past the bridge palette push � preview scrollbar CSS no longer lags one theme switch behind.
- v0.1.61: markdown preview scrollbars tinted to the live Fusion colors (sampled by rendering, scrollbar-color + color-scheme).
- v0.1.60: Win11 rounded outer corners restored (DWM corner preference, lost to the frameless hint).
- v0.1.59: title-bar demo parity � DockThemeBridge (themed popups), Fusion style, explicit central widget, fallback window icon.
- v0.1.58: frameless window with the menus embedded in a custom title bar (Lace demo pattern) � icon + Kilim title, then Views/Terminal/Themes; bar re-themes with the dock theme; floats keep the plain Lace bar.
- v0.1.57: reverted the darker dock gaps � all chrome back to stock Lace derivation.
- v0.1.55: stitch-pty 0.8.0 � event log now capped at MAX_EVENTS (1024, drop-oldest) upstream, so the TUI drain is belt-and-braces; no integration changes needed, full suite green.
- v0.1.54: stitch-pty 0.7.6 � BEL flag + event log + dirty rows consumed: bell flashes the tab dot (1.5s), Qt repaint driven by the real dirty set (my snapshot-diff deleted), titles deliberately not synced (shells retitle every prompt), TUI drains the event log per frame so it cannot grow unbounded.
- v0.1.53: QTermWidget lessons � P0 dirty-region repaint (only changed blocks relayout) + cursor repositioned on move only (native blink survives); P1 history-anchored selection (paints continue underneath), DECCKM app-cursor keys (Qt+TUI), bracketed paste, SGR/X10 mouse incl. wheel; P2 background-tab activity dots, wide-char cursor/selection mapping. Bell stays out: stitch-pty swallows BEL with no flag to read.
- v0.1.52: TUI color resolution 2.8x faster � byte-exact fast path kills the per-cell lowercase allocation; manual nibble loop replaces 3x from_str_radix; slow path kept as exact-behavior fallback.
- v0.1.51: Qt input cursor actually fixed � Lace tab-bar chrome stole focus at startup (only a focused text widget draws its cursor); active-pane focus is now claimed on retries + focus changes, never stealing from content/menus.
- v0.1.50: TUI input lag fixed � file highlights cached on (mtime, len, theme) instead of re-running syntect every frame; all pending keys drained per frame; poll 50ms to 33ms.
- v0.1.49: TUI no longer doubles key input � the key handler ignored event kind, so Windows Press + Release both wrote to the PTY; only Press/Repeat act now.

- v0.1.0: layout model, syntect highlight, TUI shell+file panes, Py surface.
- v0.1.1: Qt window — `python/kilim/qt_app.py` hosts layout.json in Lace docks
  (TerminalPane polls `term_range`, FilePane paints `highlighted_file`).
- v0.1.2: TUI input parity — Tab focus cycle, arrows/keys, Ctrl codes, PgUp/PgDn
  scrollback, live resize sync, `\r` Enter.
- v0.1.3: Markdown panes via mordant (`markdown` feature, opt-in) + test.
- v0.1.4: theme sharing — `register_custom_theme` (.tmTheme everywhere,
  VSCode JSON via mordant, mirrored into both registries).
- v0.1.5: lifecycle — per-pane `scrollback`, `ensure_terms` respawns dead,
  `restart_term`/`exited_terms`, Ctrl+R + status bar, pytest suite.
- v0.1.6: perspective round-trip — `python/kilim/perspective.py` sidecar
  (`tab_groups` + active + opaque Lace blob); launch honors layout `Tabs`
  via tabify (`center`+target), close captures, launch applies on name match.
- v0.1.7: TUI navigation — PgUp/PgDn scrolls file panes (clamped), Ctrl+Tab
  cycles `Tabs` groups with active-follows (`Layout::cycle_tab`).
- v0.1.8: Qt scrollback — scrollbar addresses absolute history (bottom =
  live tail); wheel/drag holds position, any keypress snaps back to follow;
  cursor hidden while scrolled back.
- v0.1.9: Qt paint fast path — idle-skip on `(total, cursor, start, rows)`
  signature plus same-style run-merging (~2k Qt edits → ~50–200/frame).
- v0.1.10: profiled bridges — `snapshot_tail` folds total+range+cursor
  into one lock/call (snap p50 −60%, max 59→8ms); `beginEditBlock`
  batches Qt layout (paint max −70%). `scripts/profile_qt.py` keeps numbers.
- v0.1.11: Qt selection — mouse selection freezes rebuilds (polls continue,
  resume repaints via stale signature); Ctrl+Shift+C copies, Esc resumes.
  TUI needs nothing: it never captures the mouse, so outer-terminal
  selection already works there.
- v0.1.12: scrollback that stays put — the built-in bar was decorative
  (Qt recomputes it from the one-viewport document every layout) and its
  resets clobbered position via `valueChanged`. TerminalPane is now a
  QWidget with a dedicated external scrollbar driven by `actionTriggered`
  (user gestures only) + drag guard.
- v0.1.13: right-click menu — Copy when selecting, always Paste. Ctrl+C
  stays `\x03`: stealing it would kill SIGINT, hence Ctrl+Shift+C.
- v0.1.14: way back from empty — `Views` menu (per-pane toggles, Show All
  Panes, Reset Layout over the saved perspective).
- v0.1.15: smooth wheel — fractional delta accumulation (120 units = 1
  line, touchpad-precise; Shift ×quarter-page) plus instant repaint on
  scroll gestures instead of waiting out the 60ms poll.
- v0.1.16: README renders — MarkdownPane follows mordant's md_viewer
  pattern (wheel `markdown_to_html` + GFM/math/mermaid, WebEngine when
  present else rich text); Rust `markdown` feature stays as fallback.
- v0.1.17: pure Rust preview — `markdown_page()` (fragment + KaTeX shell)
  ships in every wheel via `kilim-py/markdown` in maturin features (bare
  CLI `--features` silently built without the symbols); wheel dep dropped.
- v0.1.18: 3-line wheel notches with fractional accumulation + instant
  repaint kept — reactive and smooth.
- v0.1.19: cursor hunt — TUI cursor proven by live-shell TestBackend test
  (was already correct); Qt shell got autofocus (unfocused text widgets
  draw no cursor).
- v0.1.20: unmissable cursor — TUI paints a reverse-video cell under the
  cursor (Modifier::REVERSED, live-tested) on top of hardware positioning,
  so it shows on consoles that swallow the hardware cursor.
- v0.1.21: Terminal menu — launch PowerShell / Git Bash (auto-detected)
  / Command Prompt into new left docks; `Session::spawn_term` splices the
  pane into the live tree, so the TUI shows it too.
- v0.1.22: GFM on — `markdown_page` chains `gfm_table/strikethrough/
  task_list_item` (in-tree but opt-in; default path left them literal).
- v0.1.23: mordant 0.10 — `highlighter` finally carries `serde_json`
  (`diagram` workaround dropped; `math` listed explicitly for KaTeX);
  TUI registry filled from bat assets (7 → 29 themes, Dracula real).
- v0.1.24: Themes menu — Lace chrome (grouped, sidecar-persisted), Code
  (Qt + TUI, Dracula default, Ctrl+T cycles in TUI), Markdown fences
  (mordant set; bat themes mirrored via a Theme→tmTheme writer so no
  silent InspiredGitHub fallback). Choices persist to the layout file.
- v0.1.25: md_viewer parity — one markdown theme drives fences + mermaid
  (native `Derived` spec via a registry resolver hook) + KaTeX math;
  renderer order is load-bearing (highlighting last, as in mordant-py).
- v0.1.26: themed page chrome — `markdown_page` derives body/table/code/
  link CSS from the markdown theme's own bg/fg (Dracula → dark page,
  no toggle), modeled on md_viewer's shell.
- v0.1.27: user theme dirs — `load_builtin_themes()` runs at import so
  `~/.mordant/themes` customs join the shared registry (wheel parity).
- v0.1.28: themes from upstream — build.rs fetches the wheel theme files
  from the mordant GitHub tag (no vendoring); `.github/workflows/release.yml`
  pre-fetches the same way for wheels + TUI binaries + tests.
- v0.1.29: four unified core themes — Kilim Dark (dark⊕midnight blend) /
  Neutral / Light / Warm. Palette in kilim-core, one shared Lace chassis,
  syntect built from the closest expressive base (Night Owl, GitHub,
  OneHalfLight, gruvbox-dark) with chrome-only adjustment; selecting
  Kilim chrome applies code + markdown too. Default on all surfaces.
- v0.1.30: code-view paint fix — syntect's trailing newlines no longer
  double every line; widget Base + block backgrounds set to the theme bg
  for full-bleed color (new `theme_background` core helper).
- v0.1.31: second chassis — the 4 palettes re-housed in a neo/edge mix
  (10px card, 8px tabs, edge title rule, ringed pills; accents recolored
  per palette). New `Kilim Neo` entries, same syntect themes, unified apply.
- v0.1.32: sidebar pinning — both sidebars exist upfront (title-bar pin
  buttons included); pins persist via the perspective sidecar.
- v0.1.33: theme reduction — one shared registry of exactly 8 (4 Kilim
  palettes × standard/neo); single "Kilim" Lace submenu; unknown names
  raise; Pin menu removed.
- v0.1.34: Kilim-only exposure — user customs (~/.mordant) no longer
  auto-import (explicit `register_custom_theme` still works); Vivid
  renamed Neo (`Kilim Dark Neo`, `kilim_neo_dark`) everywhere.
- v0.1.35: Lace menu is Kilim-only and flat — stock presets hidden.
- v0.1.37: richer code colors — curated scope tables fill thin bases
  (Warm Neo gains keywords/storage/classes/params; Neutral blacks fixed
  to slate/blue; Light/Neo gaps filled); light editor backgrounds move
  to the chrome surface (`#f5f7fa` / `#d2d5d9`).
- v0.1.38: Dark splits `pub`/`fn`/`render` (pink/keyword-purple/blue);
  neo display settings mirror standard (identical background behavior,
  token colors differ); Light `#ffffff` / Neutral `#e9eaec` paper;
  Light Neo keywords/storage go ayu blue off the orange pile.
- v0.1.39: `pub`≠`fn` in all 8 (per-theme split atoms — Warm's shared
  group needed `storage.type.annotation` last); dark/warm splitter
  handles drop to 50% base; Neutral paper 5% darker (`#dddee0`).
- v0.1.40: reverted the dark/warm splitter darkening (Lace base handle
  again).
- v0.1.41: Kilim Dark palette = 2/3 Lace dark + 1/3 Lace midnight
  (`#101319`/`#161a23`/`#cbd0dc`/`#325ac6`); editor/paper follows for
  Dark + Dark Neo.
- v0.1.42: TUI file/markdown panes paint full-bleed theme paper
  (line tails, below-EOF rows, borders — matches Qt viewers);
  terminal resizes retry on failure (Qt) and commit only on success
  (TUI) instead of sticking at wrong dimensions.
- v0.1.43: TUI shell view follows the theme (default cells take theme
  fg/bg); resize sync extracted for tests (small→big relayout proven).
- v0.1.44: Qt shell view follows the code theme too (themed widget
  palette + forced repaint on switch; explicit shell colors untouched);
  resize sync runs before the snapshot guard so slow frames can't
  stall dimension updates; TUI term shortfall rows take theme paper.
- v0.1.45: markdown preview scrollbars painted from page CSS (theme
  selection thumb on page-bg track) — Chromium ignores Qt stylesheets.
- v0.1.46: reverted all scrollbar styling (v0.1.45 CSS + live-palette
  override) — preview keeps default Chromium scrollbars.
- v0.1.47: light/neutral scrollbar thumbs go theme grey (border ×
  0.75) via app QSS + minimal Chromium CSS; grooves stay native.
- v0.1.48: reverted the grey scrollbar thumbs — all native/default
  bars again.

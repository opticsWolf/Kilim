# Kilim

A multi-view terminal workspace: **one headless Rust core, two surfaces** — a
`ratatui` TUI and a Lace/Qt dockable GUI — rendering the same layout files.

Terminal panes, syntax-highlighted code panes, and Markdown previews (GFM,
KaTeX math, mermaid) share one `Session`: spawn a shell in either surface and
it shows up in both. The Qt surface turns file paths in terminal output into
links: a click opens the file in a viewer dock.

## Surfaces

| | TUI (`kilim` binary) | Qt (`python -m kilim.qt_app`) |
|---|---|---|
| Stack | ratatui + crossterm + tokio | PySide6 + Lace docks |
| Terminal | stitch-pty backend, themed default cells | same backend, palette follows code theme |
| Code | syntect, full-bleed theme paper | same spans, Qt paint fast path |
| Markdown | highlighted source | pure-Rust HTML (WebEngine, rich-text fallback) |
| Scrollback | PgUp/PgDn | external scrollbar, wheel holds position |
| Cursor | hardware + reverse-video cell | reverse-video block, blinking, focus-gated |

## Quickstart

Prerequisites: a recent stable Rust toolchain, Python 3.12+, `uv`.

```bash
# TUI — runs straight from cargo
cargo run -p kilim-tui -- layouts/default.json
# Esc / Ctrl-Q quits, typing goes to the active terminal.
# Tab / Ctrl-Tab cycle panes, PgUp/PgDn scrolls, Ctrl+T cycles themes.

# Qt surface — needs PySide6 plus the compiled core
uv venv && uv pip install maturin PySide6
uv run maturin develop --uv
python -m kilim.qt_app layouts/default.json
```

The Qt surface needs `PySide6`, `qframelesswindow` and **`lace-dock` >= 0.7.5**
(the `qt` extra). 0.7.5 is what the surface is built against: the shared
`LaceStandardTitleBar` base (anchored embeds, drag vetoes, themed fill) and
the WinIdChange frameless auto-heal that keeps Win11 rounding/Aero Snap after
Chromium loads its first page — Kilim no longer carries that workaround.

## Layouts

One file drives both surfaces — splits for the TUI, docks for Qt:

```json
{
  "layout": { "root": { "type": "pane", "pane_id": "term1" }, "active": "term1" },
  "panes": [
    { "id": "term1", "title": "shell", "kind": "term" },
    { "id": "code",  "title": "code",  "kind": "file",     "path": "crates/kilim-tui/src/ui.rs" },
    { "id": "notes", "title": "notes", "kind": "markdown", "path": "README.md" }
  ]
}
```

`layouts/default.json` ships a single terminal pane with **no `cmd`** — an
empty cmd spawns the platform default (PowerShell on Windows, `$SHELL` else
`sh`), so the default layout is portable. `layouts/example.json` keeps the
older three-pane arrangement (terminal + code + markdown) as a demo; both
surfaces render either file.

Dock geometry and pins persist in a sidecar (`layouts/<name>.perspective.json`);
the chosen Lace theme, default terminal, and recent viewer files ride in it too.

## Clickable paths

Terminal output is scanned for file paths by `kilim_core::paths` (shared Rust
detector, ready for the TUI to linkify once it has a pointer). In the Qt
surface openable paths are underlined, hover shows a pointing hand, and a
click opens the file in a fresh dock:

- **code/text** → the syntect `FilePane` (unknown extensions render plain),
- **markdown** → the mordant web pane, **html** → the same pane showing the
  raw file,
- **binary / missing** → detected and classified with
  [`binaryornot-rs`](https://crates.io/crates/binaryornot-rs), but never
  underlined or clickable (`binaryornot-rs` verdict plus git's NUL rule;
  BOM'd UTF-16/32 text opts out).

`path:line:col` suffixes scroll the viewer to the line, quoted paths may
contain spaces, and `~`, `file://` and Git-Bash `/c/...` roots resolve.
Closing a viewer dock disposes the pane; the **Files** menu keeps the last 20
paths to reopen with one click.

## Themes

Exactly 10, unified everywhere — shell defaults, code tokens, Markdown fences,
page chrome, and dock chrome all follow one selection:

`Kilim Midnight` / `Midnight Neo`, `Kilim Dark` / `Dark Neo`,
`Kilim Neutral` / `Neutral Neo`, `Kilim Light` / `Light Neo`,
`Kilim Warm` / `Warm Neo`.

Palettes live in `crates/kilim-core/src/kilim_themes.rs` (syntect built from
the closest expressive base, chrome-only adjustment); unknown theme names
raise instead of silently falling back. Custom `.tmTheme` files can still be
registered explicitly via `register_custom_theme`.

**Blend** (Themes → Code → Blend) mixes the color blocks that other apps
paint — agent chips like `pi`, linter highlights — into the active theme:
backgrounds toward its paper, text toward its ink, with a contrast floor so
nothing turns unreadable (a dark chip on a light theme stays legible). The
flag lives in the layout file, so both surfaces blend the same cells the
same way; the math is shared core code (`kilim_core::color`).

## Develop

```bash
cargo test -p kilim-core --features markdown   # Rust core (offline-safe)
QT_QPA_PLATFORM=offscreen uv run pytest tests/   # Qt/Python suite (parallel: -n auto)
cargo run -p kilim-tui -- layouts/example.json   # three-pane demo layout
```

```
crates/kilim-core  layout + syntect highlight + Session + term handles (no UI deps)
crates/kilim-tui   `kilim` binary: ratatui + crossterm + stitch-pty
crates/kilim-py    `kilim._core`: PyO3 abi3 bridge for the Qt surface
python/kilim/      Qt frontends (qt_app, perspective, themes, shells) — surface only
layouts/           default + gitbash examples
scripts/           profile_qt.py, memwatch.py
```

Highlighting is syntect directly; `mordant` (pure Rust, no wheel at runtime)
serves Markdown preview only, with its theme files fetched from upstream at
build time (see `bundled_themes.txt`) — nothing vendored.

## Roadmap

Done: core/surface parity (PTY lifecycle, scrollback, selection, resize
robustness), perspective round-trip, 8 unified themes, release workflow
(wheels + TUI binaries + tests).

Next: publish `kilim-tui` to PyPI and cut a first binary release; TUI
file-highlight cache keyed on `(path, mtime, theme)`; per-diagram mermaid
overrides; `register_custom_syntax`.

Full version history: [CHANGELOG.md](CHANGELOG.md).

## License

MIT OR Apache-2.0 — see [LICENSE-MIT](LICENSE-MIT) and [LICENSE-APACHE](LICENSE-APACHE).

# Kilim

A multi-view terminal workspace: **one headless Rust core, two surfaces** — a
`ratatui` TUI and a Lace/Qt dockable GUI — rendering the same layout files.

Terminal panes, syntax-highlighted code panes, and Markdown previews (GFM,
KaTeX math, mermaid) share one `Session`: spawn a shell in either surface and
it shows up in both.

## Surfaces

| | TUI (`kilim` binary) | Qt (`python -m kilim.qt_app`) |
|---|---|---|
| Stack | ratatui + crossterm + tokio | PySide6 + Lace docks |
| Terminal | stitch-pty backend, themed default cells | same backend, palette follows code theme |
| Code | syntect, full-bleed theme paper | same spans, Qt paint fast path |
| Markdown | highlighted source | pure-Rust HTML (WebEngine, rich-text fallback) |
| Scrollback | PgUp/PgDn | external scrollbar, wheel holds position |
| Cursor | hardware + reverse-video cell | autofocused pane |

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

## Layouts

One file drives both surfaces — splits for the TUI, docks for Qt:

```json
{
  "panes": {
    "term1": { "kind": "term", "shell": "powershell.exe", "scrollback": 5000 },
    "code":  { "kind": "file", "path": "crates/kilim-tui/src/ui.rs" },
    "notes": { "kind": "markdown", "path": "README.md" }
  }
}
```

Dock geometry and pins persist in a sidecar (`layouts/<name>.perspective.json`);
per-pane scrollback, shell choice, and theme names round-trip with it.

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

## Develop

```bash
cargo test -p kilim-core                    # Rust core (offline-safe)
QT_QPA_PLATFORM=offscreen uv run pytest tests/   # Qt/Python suite
cargo run -p kilim-tui -- layouts/gitbash.json   # second layout example
```

```
crates/kilim-core  layout + syntect highlight + Session + term handles (no UI deps)
crates/kilim-tui   `kilim` binary: ratatui + crossterm + stitch-pty
crates/kilim-py    `kilim._core`: PyO3 abi3 bridge for the Qt surface
python/kilim/      Qt frontends (qt_app, perspective, themes) — surface only
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

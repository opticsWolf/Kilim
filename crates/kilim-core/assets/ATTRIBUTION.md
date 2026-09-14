# Theme assets — provenance

Kilim embeds the theme files shipped inside the mordant *wheel*
(`mordant-py/mordant/themes/`: `.tmTheme` + VSCode-JSON, MIT,
(c) 2026 Yusuke Inuzuka). The mordant *Rust crate* never sees those files
— its registry starts at the 7 syntect defaults — so Kilim fetches them
itself **at build time** and embeds the result:

- source: tag `v0.10.0` (override: `MORDANT_THEMES_REV`) of
  https://github.com/opticsWolf/mordant
  (`mordant-py/mordant/themes/`, via codeload tarball);
- offline: `MORDANT_THEMES_DIR` points at a local copy (e.g. a sibling
  mordant checkout), or a previous build's download in `OUT_DIR` is reused;
- total failure degrades non-fatally with a loud warning (empty set).

Names already in the registry (syntect defaults, bat assets) win; fetched
files only fill the remainder (e.g. `aura-theme`, `tokyo-night-*`).
JSON files keep their upstream `author`/`maintainers` metadata inline.

Bump `DEFAULT_REV` in `crates/kilim-core/build.rs` together with the
`mordant` crate version.

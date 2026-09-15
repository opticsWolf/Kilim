//! Path detection in terminal output + viewer routing.
//!
//! Shared by both frontends: the Qt app underlines these hits and opens
//! them on click; the TUI gets the same hits, ready to linkify once it has
//! a pointer. Scanning is pure text (no I/O) and returns **char offsets**
//! into the row, so a frontend can map hits back to cells without caring
//! about UTF-8. Resolution stats the candidate and classifies it with
//! `binaryornot-rs`; a file's text/binary nature is stable for a session,
//! so kinds are cached process-wide.
//!
//! Relative candidates resolve against the process CWD — the same base a
//! shell inherits, which is the best available anchor without OSC 7
//! tracking (stitch-pty does not report the shell's cwd).

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use binaryornot_rs::check::is_binary;

/// Viewer a detected path can be opened with.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FileKind {
    /// Text or code → syntax-highlighted file viewer (unknown extensions
    /// come out as plain text via syntect's own fallback).
    Code,
    Markdown,
    Html,
    /// Exists but reads as binary (binaryornot-rs) → not openable.
    Binary,
    /// Nothing on disk, or unreadable → not openable.
    Missing,
}

impl FileKind {
    pub fn as_str(self) -> &'static str {
        match self {
            FileKind::Code => "code",
            FileKind::Markdown => "markdown",
            FileKind::Html => "html",
            FileKind::Binary => "binary",
            FileKind::Missing => "missing",
        }
    }

    /// True for paths a viewer can show (links worth underlining).
    pub fn openable(self) -> bool {
        matches!(self, FileKind::Code | FileKind::Markdown | FileKind::Html)
    }
}

/// One path found in one row of terminal output.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PathHit {
    /// Char offset of the first path char in the row (quotes excluded).
    pub start: usize,
    /// Char offset one past the last path char.
    pub end: usize,
    /// Path exactly as written (no quotes, no `:line:col` suffix).
    pub raw: String,
    /// Resolved on-disk path when it exists, else `raw`.
    pub path: String,
    /// 1-based line from a `:line[:col]` suffix.
    pub line: Option<usize>,
    /// 1-based column from a `:line:col` suffix.
    pub col: Option<usize>,
    pub kind: FileKind,
}

/// Kind cache: `path → kind`. Plain `HashMap` under a mutex — link
/// detection runs on the UI thread, and hits are already cached per row by
/// the caller, so contention is a non-issue.
fn cache() -> &'static Mutex<HashMap<PathBuf, FileKind>> {
    static CACHE: OnceLock<Mutex<HashMap<PathBuf, FileKind>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

/// Drop every cached classification (tests; also a manual refresh hook).
pub fn clear_cache() {
    if let Ok(mut c) = cache().lock() {
        c.clear();
    }
}

/// Classify one existing path: binaryornot-rs decides text vs binary
/// (known-binary extensions short-circuit before any read), then the
/// extension routes text to a viewer. Cached by resolved path.
pub fn classify(path: &Path) -> FileKind {
    {
        let c = cache().lock().unwrap();
        if let Some(kind) = c.get(path) {
            return *kind;
        }
    }
    let kind = classify_uncached(path);
    cache().lock().unwrap().insert(path.to_path_buf(), kind);
    kind
}

fn classify_uncached(path: &Path) -> FileKind {
    if !path.is_file() {
        return FileKind::Missing;
    }
    match is_binary(path, true) {
        Ok(true) => return FileKind::Binary,
        Ok(false) => {}
        // Unreadable (locked, permission) — treat as not openable.
        Err(_) => return FileKind::Missing,
    }
    // binaryornot-rs is deliberately lenient with sparse NULs; git's
    // classic rule catches blobs that slip through its tree. BOM'd
    // UTF-16/32 text opts out — it is NUL-rich by construction.
    if let Ok(chunk) = binaryornot_rs::check::get_starting_chunk(path) {
        if chunk.contains(&0) && !has_text_bom(&chunk) {
            return FileKind::Binary;
        }
    }
    match extension_lower(path).as_str() {
        "md" | "markdown" | "mdown" | "mkd" | "mkdn" => FileKind::Markdown,
        "html" | "htm" | "xhtml" => FileKind::Html,
        _ => FileKind::Code,
    }
}

/// UTF-32 checked before UTF-16: `FF FE 00 00` starts with the UTF-16 LE
/// BOM, so the longer signature must win.
fn has_text_bom(bytes: &[u8]) -> bool {
    const BOMS: [&[u8]; 5] = [
        b"\xff\xfe\x00\x00", // UTF-32 LE
        b"\x00\x00\xfe\xff", // UTF-32 BE
        b"\xef\xbb\xbf",     // UTF-8
        b"\xff\xfe",         // UTF-16 LE
        b"\xfe\xff",         // UTF-16 BE
    ];
    BOMS.iter().any(|bom| bytes.starts_with(bom))
}

fn extension_lower(path: &Path) -> String {
    path.extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_ascii_lowercase()
}

/// Read a text file for the viewer panes.
///
/// Files the classifier called text must never fail to open: UTF-8 passes
/// through, a BOM selects UTF-16/32 (which the NUL guard explicitly lets
/// through), and the remaining 8-bit encodings decode as Windows-1252 —
/// lossless for every byte, so nothing is dropped or mojibake'd into `?`.
pub fn read_text(path: &Path) -> std::io::Result<String> {
    let bytes = std::fs::read(path)?;
    Ok(decode_text(&bytes))
}

fn decode_text(bytes: &[u8]) -> String {
    if let Some((enc, bom_len)) = encoding_rs::Encoding::for_bom(bytes) {
        let (text, _, _) = enc.decode(&bytes[bom_len..]);
        return text.into_owned();
    }
    match std::str::from_utf8(bytes) {
        Ok(s) => s.to_string(),
        Err(_) => encoding_rs::WINDOWS_1252.decode(bytes).0.into_owned(),
    }
}

/// Resolve a written path against `cwd`, expanding `~` and (on Windows)
/// Git-Bash `/c/...` roots, plus `file://` URLs. `None` when nothing on
/// disk matches.
pub fn resolve(raw: &str, cwd: &Path) -> Option<PathBuf> {
    let mut forms = vec![raw.to_string()];
    if let Some(rest) = raw.strip_prefix("file://") {
        // file:///C:/x and file://host/share are rare enough to try bare.
        forms.push(rest.trim_start_matches('/').to_string());
        forms.push(rest.to_string());
    }
    #[cfg(windows)]
    if let Some(msys) = msys_root(raw) {
        forms.push(msys);
    }
    for form in forms {
        let p = expand(&form, cwd);
        if p.is_file() {
            return Some(p);
        }
    }
    None
}

fn expand(raw: &str, cwd: &Path) -> PathBuf {
    if let Some(rest) = raw.strip_prefix("~/").or_else(|| raw.strip_prefix("~\\")) {
        let home = std::env::var_os("HOME")
            .or_else(|| std::env::var_os("USERPROFILE"))
            .map(PathBuf::from);
        if let Some(home) = home {
            return home.join(rest);
        }
    }
    let p = Path::new(raw);
    if p.is_absolute() {
        p.to_path_buf()
    } else {
        cwd.join(p)
    }
}

/// `/c/Users/x` (Git Bash) → `C:/Users/x`. Anything else → `None`.
#[cfg(windows)]
fn msys_root(raw: &str) -> Option<String> {
    if !raw.starts_with('/') || raw.starts_with("//") {
        return None;
    }
    let rest = &raw[1..];
    let (drive, tail) = rest.split_at(rest.char_indices().nth(1).map_or(rest.len(), |(i, _)| i));
    if drive.len() == 1 && drive.chars().next().unwrap().is_ascii_alphabetic() && tail.starts_with('/')
    {
        Some(format!("{}:{}", drive.to_ascii_uppercase(), tail))
    } else {
        None
    }
}

/// Detect file paths in one line of terminal output, resolved against the
/// process CWD. Offsets are char indices into `line`.
pub fn detect_paths(line: &str) -> Vec<PathHit> {
    let cwd = std::env::current_dir().unwrap_or_default();
    detect_paths_in(line, &cwd)
}

/// `detect_paths` with an explicit base directory (tests / future use).
pub fn detect_paths_in(line: &str, cwd: &Path) -> Vec<PathHit> {
    let chars: Vec<char> = line.chars().collect();
    let n = chars.len();
    let mut out: Vec<PathHit> = Vec::new();
    let mut i = 0;
    while i < n {
        let c = chars[i];
        // Quoted first: the inner text may contain spaces that unquoted
        // runs cannot carry (`"C:\Program Files\app.txt"`).
        if matches!(c, '"' | '\'' | '`') {
            if let Some(j) = (i + 1..n).find(|&j| chars[j] == c) {
                let inner: String = chars[i + 1..j].iter().collect();
                if let Some(hit) = make_hit(&inner, i + 1, cwd) {
                    out.push(hit);
                }
                i = j + 1;
                continue;
            }
        }
        if !is_run_start(c) {
            i += 1;
            continue;
        }
        let start = i;
        while i < n && is_run_char(chars[i]) {
            i += 1;
        }
        let run: String = chars[start..i].iter().collect();
        if let Some(hit) = make_hit(&run, start, cwd) {
            out.push(hit);
        }
    }
    out
}

/// Text → hit: trim sentence punctuation, split `:line:col`, resolve and
/// classify. `None` unless the text still looks like a path.
fn make_hit(text: &str, start: usize, cwd: &Path) -> Option<PathHit> {
    let trimmed = text.trim_start();
    let start = start + (text.chars().count() - trimmed.chars().count());
    let trimmed = trimmed.trim_end();
    let t = trimmed.trim_end_matches(['.', ':', '=', '+', '&', '#', '@', '%', '$', '-']);
    let (path_text, line, col) = split_line_col(t);
    if !plausible(path_text) {
        return None;
    }
    let resolved = resolve(path_text, cwd);
    let kind = match &resolved {
        Some(p) => classify(p),
        None => FileKind::Missing,
    };
    Some(PathHit {
        start,
        end: start + path_text.chars().count(),
        raw: path_text.to_string(),
        path: resolved
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_else(|| path_text.to_string()),
        line,
        col,
        kind,
    })
}

/// Strip a `:line` / `:line:col` suffix, but only when what remains still
/// looks like a path — so `C:12` or a `12:30` timestamp never splits.
fn split_line_col(text: &str) -> (&str, Option<usize>, Option<usize>) {
    if let Some((head, num)) = colon_number(text) {
        if plausible(head) {
            if let Some((head2, num2)) = colon_number(head) {
                if plausible(head2) {
                    return (head2, Some(num2), Some(num));
                }
            }
            return (head, Some(num), None);
        }
    }
    (text, None, None)
}

fn colon_number(s: &str) -> Option<(&str, usize)> {
    let idx = s.rfind(':')?;
    let (head, tail) = (&s[..idx], &s[idx + 1..]);
    if head.is_empty() || tail.is_empty() || tail.len() > 7 {
        return None;
    }
    if !tail.chars().all(|c| c.is_ascii_digit()) {
        return None;
    }
    Some((head, tail.parse().ok()?))
}

/// Does this text look like a file path? Either it carries a separator
/// (`src/main.rs`, `C:\x`, `and/or`) or ends in an extension-ish suffix
/// (`Makefile.txt`, `release-1.2`). Everything else is prose. Existence is
/// deliberately *not* consulted here: classification filters false
/// positives once, and the answer is cached.
fn plausible(s: &str) -> bool {
    if s.is_empty() {
        return false;
    }
    if s.contains('/') || s.contains('\\') {
        return true;
    }
    let name = s.rsplit(|c| c == '/' || c == '\\').next().unwrap_or(s);
    match name.rfind('.') {
        Some(i) if i > 0 => {
            let ext = &name[i + 1..];
            !ext.is_empty() && ext.len() <= 8 && ext.chars().all(|c| c.is_ascii_alphanumeric())
        }
        _ => false,
    }
}

/// Chars a run can contain. `:` is included for drive letters and
/// `path:line:col`; the delimiters (`()[]{}<>|*?`, quotes, commas) are not,
/// so punctuation naturally ends a candidate.
fn is_run_char(c: char) -> bool {
    c.is_alphanumeric()
        || matches!(c, '/' | '\\' | '.' | '_' | '-' | '~' | '+' | '=' | '@' | '#' | '%' | '&' | '$' | ':')
}

fn is_run_start(c: char) -> bool {
    c.is_alphanumeric() || matches!(c, '/' | '\\' | '.' | '~' | '_' | '-')
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dir(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("kilim-paths-{name}"));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn slashes(p: &Path) -> String {
        p.to_string_lossy().replace('\\', "/")
    }

    fn slice(line: &str, hit: &PathHit) -> String {
        line.chars().skip(hit.start).take(hit.end - hit.start).collect()
    }

    #[test]
    fn finds_absolute_path_with_line_and_col() {
        clear_cache();
        let d = dir("abs");
        std::fs::write(d.join("main.rs"), "fn main() {}\n").unwrap();
        let line = format!("error[E]: boom --> {}/main.rs:12:3", slashes(&d));
        let hits = detect_paths_in(&line, &d);
        assert_eq!(hits.len(), 1, "hits: {hits:?}");
        let h = &hits[0];
        assert_eq!(h.raw, format!("{}/main.rs", slashes(&d)));
        assert_eq!(slice(&line, h), h.raw);
        assert_eq!((h.line, h.col), (Some(12), Some(3)));
        assert_eq!(h.kind, FileKind::Code);
        assert!(h.path.ends_with("main.rs"));
    }

    #[test]
    fn relative_path_resolves_against_cwd() {
        clear_cache();
        let d = dir("rel");
        std::fs::create_dir_all(d.join("src")).unwrap();
        std::fs::write(d.join("src/lib.rs"), "// x\n").unwrap();
        let line = "warning: unused in src/lib.rs:9";
        let hits = detect_paths_in(line, &d);
        assert_eq!(hits.len(), 1, "hits: {hits:?}");
        assert_eq!(slice(line, &hits[0]), "src/lib.rs");
        assert_eq!(hits[0].line, Some(9));
        assert_eq!(hits[0].kind, FileKind::Code);
    }

    #[test]
    fn trims_trailing_punctuation() {
        clear_cache();
        let d = dir("trim");
        std::fs::write(d.join("notes.md"), "# n\n").unwrap();
        let line = "see notes.md, and also (see notes.md). done";
        let hits = detect_paths_in(line, &d);
        let got: Vec<String> = hits.iter().map(|h| slice(line, h)).collect();
        assert_eq!(got, vec!["notes.md", "notes.md"], "hits: {hits:?}");
        assert!(hits.iter().all(|h| h.kind == FileKind::Markdown));
    }

    #[test]
    fn quoted_paths_may_contain_spaces() {
        clear_cache();
        let d = dir("quote");
        let spaced = d.join("my docs");
        std::fs::create_dir_all(&spaced).unwrap();
        std::fs::write(spaced.join("a b.txt"), "hello\n").unwrap();
        let line = format!("opened \"{}/a b.txt\" now", slashes(&spaced));
        let hits = detect_paths_in(&line, &d);
        assert_eq!(hits.len(), 1, "hits: {hits:?}");
        assert_eq!(slice(&line, &hits[0]), format!("{}/a b.txt", slashes(&spaced)));
        assert_eq!(hits[0].kind, FileKind::Code);
    }

    #[test]
    fn routes_viewers_by_extension() {
        clear_cache();
        let d = dir("route");
        std::fs::write(d.join("page.html"), "<p>x</p>\n").unwrap();
        std::fs::write(d.join("readme.markdown"), "# x\n").unwrap();
        std::fs::write(d.join("notes.txt"), "x\n").unwrap();
        let line = "a page.html b readme.markdown c notes.txt";
        let hits = detect_paths_in(line, &d);
        let kinds: Vec<(&str, FileKind)> =
            hits.iter().map(|h| (h.raw.as_str(), h.kind)).collect();
        assert_eq!(
            kinds,
            vec![
                ("page.html", FileKind::Html),
                ("readme.markdown", FileKind::Markdown),
                ("notes.txt", FileKind::Code),
            ]
        );
    }

    #[test]
    fn binary_files_are_not_openable() {
        clear_cache();
        let d = dir("binary");
        std::fs::write(d.join("blob.dat"), [0u8, 1, 2, 0, 255, 10]).unwrap();
        std::fs::write(d.join("img.png"), b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0d").unwrap();
        assert_eq!(classify(&d.join("blob.dat")), FileKind::Binary);
        assert_eq!(classify(&d.join("img.png")), FileKind::Binary);
        assert!(!FileKind::Binary.openable());
        let line = "wrote blob.dat and img.png";
        let hits = detect_paths_in(line, &d);
        assert_eq!(hits.len(), 2, "hits: {hits:?}");
        assert!(hits.iter().all(|h| h.kind == FileKind::Binary));
    }

    #[test]
    fn bom_utf16_text_stays_openable() {
        clear_cache();
        let d = dir("utf16");
        let mut bytes = vec![0xff, 0xfe]; // UTF-16 LE BOM
        for c in "hi there".encode_utf16() {
            bytes.extend_from_slice(&c.to_le_bytes());
        }
        std::fs::write(d.join("u16.txt"), bytes).unwrap();
        assert_eq!(classify(&d.join("u16.txt")), FileKind::Code);
    }

    #[test]
    fn missing_paths_are_reported_but_not_openable() {
        clear_cache();
        let d = dir("missing");
        let line = "no such file ghost/thing.txt here";
        let hits = detect_paths_in(line, &d);
        assert_eq!(hits.len(), 1, "hits: {hits:?}");
        assert_eq!(hits[0].kind, FileKind::Missing);
        assert!(!hits[0].kind.openable());
        assert_eq!(hits[0].path, hits[0].raw); // unresolved: raw is all we have
    }

    #[test]
    fn urls_and_prose_do_not_become_links() {
        clear_cache();
        let d = dir("prose");
        for line in [
            "visit https://example.com/a/b.rs for details",
            "and/or either way",
            "version 1.2 released",
            "time is 12:30:45 now",
        ] {
            let hits = detect_paths_in(line, &d);
            assert!(
                hits.iter().all(|h| h.kind == FileKind::Missing),
                "{line}: {hits:?}"
            );
            assert!(hits.iter().all(|h| !h.kind.openable()));
        }
    }

    #[test]
    fn file_urls_resolve() {
        clear_cache();
        let d = dir("fileurl");
        std::fs::write(d.join("u.txt"), "x\n").unwrap();
        let line = format!("open file:///{}/u.txt", slashes(&d).trim_start_matches('/'));
        let hits = detect_paths_in(&line, &d);
        assert_eq!(hits.len(), 1, "hits: {hits:?}");
        assert_eq!(hits[0].kind, FileKind::Code);
        assert!(hits[0].path.ends_with("u.txt"));
    }

    #[test]
    fn read_text_handles_encodings() {
        let d = dir("readtext");
        let utf8 = d.join("u8.txt");
        std::fs::write(&utf8, "héllo\n").unwrap();
        assert_eq!(read_text(&utf8).unwrap(), "héllo\n");

        // UTF-16 LE with BOM: binaryornot-rs calls it text (BOM opt-out of
        // the NUL guard), so the reader must decode it as such.
        let wide = d.join("u16.txt");
        let mut bytes = vec![0xff, 0xfe];
        for unit in "héllo".encode_utf16() {
            bytes.extend_from_slice(&unit.to_le_bytes());
        }
        std::fs::write(&wide, bytes).unwrap();
        assert_eq!(read_text(&wide).unwrap().trim_end(), "héllo");

        // Invalid UTF-8 without BOM: cp1252 keeps it readable.
        let latin = d.join("latin.txt");
        std::fs::write(&latin, b"caf\xe9\n").unwrap();
        assert_eq!(read_text(&latin).unwrap(), "café\n");
    }

    #[test]
    fn kinds_are_cached_and_clearable() {
        clear_cache();
        let d = dir("cache");
        let f = d.join("c.txt");
        std::fs::write(&f, "x\n").unwrap();
        assert_eq!(classify(&f), FileKind::Code);
        // A cached answer survives the file going away…
        std::fs::remove_file(&f).unwrap();
        assert_eq!(classify(&f), FileKind::Code);
        // …until the cache is cleared (a fresh session, or a manual drop).
        clear_cache();
        assert_eq!(classify(&f), FileKind::Missing);
    }

    #[cfg(windows)]
    #[test]
    fn msys_and_tilde_roots_expand() {
        clear_cache();
        let d = dir("msys");
        let f = d.join("m.txt");
        std::fs::write(&f, "x\n").unwrap();
        let s = slashes(&f);
        let msys = format!("/{}/{}", s[0..1].to_lowercase(), &s[2..]);
        let hits = detect_paths_in(&format!("at {msys} here"), &d);
        assert_eq!(hits.len(), 1, "hits: {hits:?}");
        assert_eq!(hits[0].kind, FileKind::Code);
    }
}

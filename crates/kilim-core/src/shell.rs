//! Platform default shell for panes that don't pin one.
//!
//! `layouts/*.json` may omit `cmd` entirely (portable layouts); the
//! frontends then get the OS's own default here — PowerShell on Windows,
//! `$SHELL` else `sh` elsewhere. The Qt app's Terminal menu offers a
//! longer, discovered list; this is the fallback an empty cmd resolves to.

#[cfg(not(windows))]
use std::path::PathBuf;

/// Default shell as `(program, args)`. Never empty: on Windows it falls
/// back to `cmd.exe` even when nothing was found on `PATH`.
pub fn default_shell() -> (String, Vec<String>) {
    #[cfg(windows)]
    {
        for name in ["powershell.exe", "pwsh.exe", "cmd.exe"] {
            if let Some(p) = find_in_path(name) {
                return (p, Vec::new());
            }
        }
        ("cmd.exe".to_string(), Vec::new())
    }
    #[cfg(not(windows))]
    {
        if let Some(shell) = std::env::var_os("SHELL").filter(|s| !s.is_empty()) {
            let p = PathBuf::from(&shell);
            if p.is_file() {
                return (p.to_string_lossy().into_owned(), Vec::new());
            }
        }
        for name in ["bash", "zsh", "sh"] {
            if let Some(p) = find_in_path(name) {
                return (p, Vec::new());
            }
        }
        ("sh".to_string(), Vec::new())
    }
}

/// Look a bare program name up on `PATH` (no PATHEXT expansion — pass
/// `foo.exe` on Windows). Returns the first hit, as written.
pub fn find_in_path(name: &str) -> Option<String> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let cand = dir.join(name);
        if cand.is_file() {
            return Some(cand.to_string_lossy().into_owned());
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_shell_is_never_empty() {
        let (cmd, _args) = default_shell();
        assert!(!cmd.is_empty());
    }

    #[test]
    fn path_lookup_finds_a_real_binary() {
        // Every platform the suite runs on has one of these on PATH.
        let found = ["cmd.exe", "powershell.exe", "sh", "bash"]
            .iter()
            .find_map(|n| find_in_path(n));
        assert!(found.is_some(), "no shell on PATH: {found:?}");
        assert!(std::path::Path::new(&found.unwrap()).is_file());
    }

    #[test]
    fn missing_binary_is_none() {
        assert!(find_in_path("definitely-not-a-shell-9e7f").is_none());
    }
}

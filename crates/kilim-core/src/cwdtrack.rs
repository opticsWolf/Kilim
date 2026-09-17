//! Shell cwd reporting (OSC 7 / OSC 9;9) for spawned shells.
//!
//! stitch tracks the live directory from OSC sequences, but stock
//! shells never emit them — so every spawn gets a best-effort,
//! invisible integration at the single choke point
//! ([`TermHandle::spawn`](crate::term::TermHandle::spawn)):
//!
//! - `cmd.exe`: seed the `PROMPT` variable with
//!   `$E]9;9;$P$E\$P$G` — reports the live dir before every prompt
//!   while rendering exactly the default `C:\dir>`.
//! - `bash` (incl. Git Bash): seed `PROMPT_COMMAND` with a `printf`
//!   emitting OSC 7; the visible prompt is untouched.
//! - `powershell`/`pwsh`: wrap *bare* shells with `-NoExit -Command`
//!   defining a `prompt` function that emits OSC 9;9, then runs the
//!   previous prompt (profile or default) unchanged.
//!
//! Two hard rules keep this non-invasive: an explicitly customized
//! knob always wins (seeded env vars only when absent from the
//! inherited environment; explicit layout args are never rewritten),
//! and unknown programs pass through byte-identical.

/// Program file stem, lowercase, no dirs or extension:
/// `C:\Windows\cmd.exe` -> `cmd`, `/usr/bin/bash` -> `bash`.
fn stem(program: &str) -> String {
    let base = program.rsplit(['/', '\\']).next().unwrap_or(program);
    let base = match base.rfind('.') {
        Some(i) => &base[..i],
        None => base,
    };
    base.to_lowercase()
}

/// Seed cwd-reporting env vars (only when the user hasn't set them).
///
/// A stock `PROMPT=$P$G` counts as unset: it renders identically with
/// the OSC report prepended, so upgrading it changes nothing visible.
/// (Parent processes often export the default, which must not veto
/// the integration.)
pub fn track_env(program: &str, env: &mut Vec<(String, String)>) {
    let has = |key: &str| env.iter().any(|(k, _)| k.eq_ignore_ascii_case(key));
    match stem(program).as_str() {
        "cmd" => {
            let stock = env
                .iter()
                .find(|(k, _)| k.eq_ignore_ascii_case("PROMPT"))
                .map(|(_, v)| v.eq_ignore_ascii_case("$P$G"))
                .unwrap_or(true);
            if stock {
                env.retain(|(k, _)| !k.eq_ignore_ascii_case("PROMPT"));
                env.push(("PROMPT".to_string(), "$E]9;9;$P$E\\$P$G".to_string()));
            }
        }
        "bash" => {
            // `cygpath -w` reports the native Windows spelling: a raw
            // $PWD (`/c/...`, `/tmp/...`) is meaningless to the Windows
            // side (repo resolution, stamps, links). Empty output
            // (missing cygpath) parses to None — status quo, never worse.
            if !has("PROMPT_COMMAND") {
                env.push((
                    "PROMPT_COMMAND".to_string(),
                    "printf '\\e]7;file://localhost%s\\e\\\\' \"$(cygpath -w \"$PWD\")\"".to_string(),
                ));
            }
        }
        _ => {}
    }
}

/// PowerShell `prompt` wrapper: emits OSC 9;9, then chains the
/// previous prompt. Single-quoted throughout so the `-Command`
/// double-quoted arg needs no escaping.
const PS_PROMPT_WRAPPER: &str = concat!(
    "$global:__kilim_pp = if (Test-Path function:global:prompt) ",
    "{ (Get-Item function:global:prompt).ScriptBlock } else { $null }; ",
    "function global:prompt { ",
    "$Host.UI.Write([char]27 + ']9;9;' + $PWD + [char]27 + '\\'); ",
    "if ($global:__kilim_pp) { & $global:__kilim_pp } ",
    "else { 'PS ' + $executionContext.SessionState.Path.CurrentLocation ",
    "+ $('>' * ($nestedPromptLevel + 1)) + ' ' } }"
);

/// Rewrite spawn args for cwd reporting (bare PowerShell only).
pub fn track_args(program: &str, args: Vec<String>) -> Vec<String> {
    let stem = stem(program);
    if (stem == "powershell" || stem == "pwsh") && args.is_empty() {
        return vec![
            "-NoExit".to_string(),
            "-Command".to_string(),
            PS_PROMPT_WRAPPER.to_string(),
        ];
    }
    args
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stem_strips_dirs_ext_case() {
        assert_eq!(stem("C:\\Windows\\System32\\cmd.exe"), "cmd");
        assert_eq!(stem("C:/Program Files/Git/bin/bash.exe"), "bash");
        assert_eq!(stem("/usr/bin/zsh"), "zsh");
        assert_eq!(stem("PowerShell.EXE"), "powershell");
        assert_eq!(stem("pwsh"), "pwsh");
        assert_eq!(stem("python3.13"), "python3");
    }

    #[test]
    fn cmd_seeds_prompt_once() {
        let mut env = vec![("PATH".to_string(), "x".to_string())];
        track_env("cmd.exe", &mut env);
        let prompt = env.iter().find(|(k, _)| k == "PROMPT").unwrap().1.clone();
        assert_eq!(prompt, "$E]9;9;$P$E\\$P$G");
        // Visible tail after the OSC report is the default `$P$G`.
        assert!(prompt.ends_with("$P$G"));
        track_env("cmd.exe", &mut env);
        assert_eq!(env.iter().filter(|(k, _)| k == "PROMPT").count(), 1);
    }

    #[test]
    fn cmd_respects_custom_prompt_any_case() {
        let mut env = vec![("Prompt".to_string(), "$G$S".to_string())];
        track_env("C:\\Windows\\cmd.exe", &mut env);
        assert_eq!(env.len(), 1);
        assert_eq!(env[0].1, "$G$S");
    }

    #[test]
    fn cmd_stock_prompt_upgrades() {
        // Parent shells often export the default; it renders the same
        // with the report prepended, so it must not veto integration.
        for stock in ["$P$G", "$p$g"] {
            let mut env = vec![("PROMPT".to_string(), stock.to_string())];
            track_env("cmd.exe", &mut env);
            assert_eq!(env.len(), 1);
            assert_eq!(env[0].1, "$E]9;9;$P$E\\$P$G");
        }
    }

    #[test]
    fn bash_seeds_prompt_command_once() {
        let mut env = Vec::new();
        track_env("bash", &mut env);
        assert_eq!(env.len(), 1);
        let (k, v) = &env[0];
        assert_eq!(k, "PROMPT_COMMAND");
        assert!(v.contains("]7;file://localhost%s"), "{v}");
        assert!(v.contains("cygpath -w"), "{v}");
        track_env("/usr/bin/bash", &mut env);
        assert_eq!(env.len(), 1);
    }

    #[test]
    fn bash_respects_existing_prompt_command() {
        let mut env = vec![("PROMPT_COMMAND".to_string(), "history -a".to_string())];
        track_env("bash.exe", &mut env);
        assert_eq!(env[0].1, "history -a");
    }

    #[test]
    fn other_shells_untouched_by_env() {
        for prog in ["powershell.exe", "pwsh", "zsh", "sh", "python.exe", "wsl.exe"] {
            let mut env = Vec::new();
            track_env(prog, &mut env);
            assert!(env.is_empty(), "{prog}");
        }
    }

    #[test]
    fn powershell_bare_gets_wrapper() {
        for prog in ["powershell.exe", "pwsh.exe", "pwsh"] {
            let args = track_args(prog, Vec::new());
            assert_eq!(args.len(), 3, "{prog}");
            assert_eq!(args[0], "-NoExit");
            assert_eq!(args[1], "-Command");
            let script = &args[2];
            assert!(script.contains("]9;9;"), "{prog}");
            assert!(script.contains("$global:__kilim_pp"), "{prog}");
            assert!(!script.contains('"'), "{prog} has double quotes: {script}");
        }
    }

    #[test]
    fn powershell_with_args_untouched() {
        let args = vec!["-NoExit".to_string(), "-File".to_string(), "x.ps1".to_string()];
        assert_eq!(track_args("powershell.exe", args.clone()), args);
    }

    #[test]
    fn non_powershell_args_untouched() {
        let args = vec!["--login".to_string(), "-i".to_string()];
        assert_eq!(track_args("bash.exe", args.clone()), args);
        assert_eq!(track_args("cmd.exe", Vec::new()), Vec::<String>::new());
    }
}

"""Shell discovery for the Terminal menu (cross-platform).

Every entry is `(label, program, args)`; only shells that actually exist on
this machine are returned, in preference order (first found = platform
default). The Rust core has its own tiny fallback for layouts that omit
`cmd` (`kilim_core::shell::default_shell`); this list is the menu's, and is
deliberately richer: PowerShell / pwsh / cmd / Git Bash / WSL on Windows,
`$SHELL` plus the usual suspects elsewhere.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

Entry = tuple[str, str, list[str]]


def _which(*names: str) -> str | None:
    for n in names:
        found = shutil.which(n)
        if found:
            return found
        # Absolute candidates (Git for Windows is not always on PATH).
        p = Path(n)
        if p.is_absolute() and p.is_file():
            return str(p)
    return None


def _windows_shells() -> list[Entry]:
    found: list[Entry] = []
    comspec = os.environ.get("COMSPEC") or ""
    candidates: list[Entry] = [
        ("PowerShell", _which("powershell.exe", "powershell") or "", []),
        ("PowerShell 7", _which("pwsh.exe", "pwsh") or "", []),
        ("Command Prompt", comspec if comspec and Path(comspec).is_file() else "", []),
        (
            "Git Bash",
            _which(
                "bash.exe",
                "bash",
                "C:/Program Files/Git/bin/bash.exe",
                "C:/Program Files (x86)/Git/bin/bash.exe",
                "C:/Program Files/Git/usr/bin/bash.exe",
            )
            or "",
            ["--login", "-i"],
        ),
        ("WSL", _which("wsl.exe") or "", []),
    ]
    for label, cmd, args in candidates:
        if cmd:
            found.append((label, cmd, args))
    return found


def _posix_shells() -> list[Entry]:
    found: list[Entry] = []
    seen: set[str] = set()

    def add(label: str, cmd: str | None, args: list[str]) -> None:
        if not cmd or cmd in seen:
            return
        seen.add(cmd)
        found.append((label, cmd, args))

    login = os.environ.get("SHELL") or ""
    if login and Path(login).is_file():
        add(Path(login).name.capitalize(), login, ["-l"])
    for name, args in (
        ("Zsh", ["-l"]),
        ("Bash", ["-l"]),
        ("Fish", ["-l"]),
        ("sh", []),
    ):
        add(name, _which(name.lower()), args)
    if not found:  # PATH-less fallback: sh is everywhere
        add("sh", "/bin/sh", [])
    return found


def find_shells() -> list[Entry]:
    """Installed shells, preference-ordered. Never empty in practice."""
    return _windows_shells() if os.name == "nt" else _posix_shells()


def find_shell(label: str) -> Entry | None:
    """Look one up by menu label (sidecar persistence uses labels)."""
    return next((e for e in find_shells() if e[0] == label), None)


def default_entry() -> Entry | None:
    """The platform default: first found in preference order."""
    shells = find_shells()
    return shells[0] if shells else None

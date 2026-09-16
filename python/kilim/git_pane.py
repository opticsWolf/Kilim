"""Git history dock: backend (git CLI, no new deps) + GitPane widget.

The graph comes from `git log --graph` itself — lane routing is git's
job; this module only splits its records (\\x1e-separated, \\0-separated
fields) and paints them. Rows are `{"sha", "parents", "author", "date",
"refs", "subject", "graph"}. Everything above GitPane is Qt-free, so the
backend unit-tests without a QApplication.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPalette, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from kilim.qt_bridge import Bridge
from kilim.qt_themes import cell_qcolor

HISTORY_LIMIT = 400  # rows per read; more is a scrollback, not a view
COMMIT_CAP = 300  # detail lines: message + stat, never a full patch

# Graph drawing chars are never hex: `*` nodes, `|` bars, `/` `\` slopes,
# `_` merge rails, padding spaces. The 40-hex sha right after is the split.
_RECORD = re.compile(r"^([^0-9a-f]*)([0-9a-f]{40})\x00(.*)$", re.DOTALL)


def find_git() -> str | None:
    """git binary, or None (pane degrades to an install hint)."""
    return shutil.which("git")


def _run_git(repo: str | None, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    kw: dict = {}
    if os.name == "nt":  # no console flash from the GUI process
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kw["startupinfo"] = si
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,  # plumbing: callers read returncode/stderr
        **kw,
    )


def repo_root(path: str | None) -> str | None:
    """Top-level of the repo containing `path` (follows worktrees)."""
    if not path or find_git() is None:
        return None
    try:
        out = _run_git(path, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    root = out.stdout.strip()
    return root or None


def git_dir(repo: str) -> str | None:
    """Absolute .git dir (watch target); None when unresolvable."""
    try:
        out = _run_git(repo, "rev-parse", "--absolute-git-dir")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    gd = out.stdout.strip()
    return gd or None


def read_history(
    repo: str, ref: str = "--all", limit: int = HISTORY_LIMIT
) -> tuple[list[dict], bool, str | None]:
    """(rows, truncated, error): newest-first commits with graph prefixes.

    One extra row is requested to detect truncation. `ref` is "--all" or
    a branch name (argv, never a shell — no quoting worries).
    """
    rows: list[dict] = []
    try:
        out = _run_git(
            repo,
            "log", "--graph", "--topo-order", ref,
            f"--max-count={limit + 1}",
            "--pretty=format:%H%x00%P%x00%an%x00%ae%x00%aI%x00%D%x00%s%x1e",
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return [], False, f"git log failed: {e}"
    if out.returncode != 0:
        err = out.stderr.strip()
        if "does not have any commits yet" in err:
            return [], False, None  # fresh repo: empty, not an error
        return [], False, err.splitlines()[0] if err else f"git log exited {out.returncode}"
    for record in out.stdout.split("\x1e"):
        record = record.strip("\n")
        if not record.strip():
            continue
        m = _RECORD.match(record)
        if m is None:
            continue  # never observed; a weird line must not kill the view
        graph, sha, rest = m.group(1), m.group(2), m.group(3)
        fields = rest.split("\x00")
        if len(fields) < 6:
            continue
        parents, author, date, refs, subject = fields[0], fields[1], fields[3], fields[4], fields[5]
        rows.append({
            "sha": sha,
            "parents": parents.split(),
            "author": author,
            "date": date,
            "refs": refs,
            "subject": subject,
            "graph": graph.rstrip(),
        })
    return rows[:limit], len(rows) > limit, None


def read_branches(repo: str) -> tuple[str, list[str], str | None]:
    """(current, locals, error) for the branch filter."""
    try:
        cur = _run_git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        lst = _run_git(repo, "branch", "--format=%(refname:short)")
    except (OSError, subprocess.TimeoutExpired) as e:
        return "", [], f"git branch failed: {e}"
    if cur.returncode != 0 or lst.returncode != 0:
        err = (cur.stderr or lst.stderr).strip().splitlines()
        return "", [], err[0] if err else "git branch failed"
    current = cur.stdout.strip()
    branches = [b.strip() for b in lst.stdout.splitlines() if b.strip()]
    return current, branches, None


def read_commit(repo: str, sha: str) -> str:
    """Full message + file stat for one commit, capped (no patch)."""
    try:
        out = _run_git(repo, "show", "--stat", "--format=fuller", sha, "--")
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"git show failed: {e}"
    if out.returncode != 0:
        err = out.stderr.strip().splitlines()
        return err[0] if err else f"git show exited {out.returncode}"
    lines = out.stdout.splitlines()
    if len(lines) > COMMIT_CAP:
        lines = lines[:COMMIT_CAP] + [f"… {len(lines) - COMMIT_CAP} more lines"]
    return "\n".join(lines).rstrip() or "(empty commit)"


def split_refs(refs: str) -> tuple[list[str], list[str], list[str]]:
    """%D decorations -> (head, branches, tags).

    "HEAD -> main, origin/main, tag: v1.2" — HEAD first so the current
    branch paints bold; remotes stay with branches (same color family).
    """
    head, branches, tags = [], [], []
    for deco in (d.strip() for d in refs.split(",")):
        if not deco:
            continue
        if deco.startswith("HEAD -> "):
            head.append(deco[len("HEAD -> "):])
        elif deco == "HEAD":
            head.append("HEAD")
        elif deco.startswith("tag: "):
            tags.append(deco[len("tag: "):])
        else:
            branches.append(deco)
    return head, branches, tags


class _HistoryView(QPlainTextEdit):
    """Read-only history with click-to-show (block -> commit callback)."""

    def __init__(self, on_pick):
        super().__init__()
        self._on_pick = on_pick
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            cur = self.cursorForPosition(e.pos())
            if cur.block().isValid():
                self._on_pick(cur.block().blockNumber())
        super().mousePressEvent(e)


class GitPane(QWidget):
    """Git history dock: graph list on top, commit detail below.

    Repo sources, in order: explicit path, the active terminal's live
    directory (OSC 7 snapshot cwd), the app directory — each walked up
    to its top-level. Otherwise an empty state with an Open button.
    A watcher on .git re-reads after commits/fetches (debounced); the
    Refresh button is the fallback. `on_repo_changed(path)` lets the
    window persist the choice (sidecar); may be None (tests).
    """

    def __init__(self, bridge: Bridge, repo: str | None = None):
        super().__init__()
        self.bridge = bridge
        self.repo: str | None = None
        self.on_repo_changed = None
        self._commits: list[dict] = []
        self._block_commits: list[int | None] = []  # block -> commit idx
        self._ref = "--all"
        self._refresh_queued = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.setContentsMargins(4, 4, 4, 0)
        self._repo_label = QLabel("No repository")
        self._repo_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bar.addWidget(self._repo_label, 1)
        self._branch_box = QComboBox()
        self._branch_box.setToolTip("Which refs to show")
        self._branch_box.currentIndexChanged.connect(self._on_branch_picked)
        bar.addWidget(self._branch_box)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(lambda _c=False: self.refresh())
        bar.addWidget(refresh)
        open_btn = QPushButton("Open…")
        open_btn.clicked.connect(lambda _c=False: self.choose_repo())
        bar.addWidget(open_btn)
        lay.addLayout(bar)

        split = QSplitter(Qt.Vertical)
        mono = QFont("Cascadia Mono", 10)
        self.history_view = _HistoryView(self._pick_row)
        self.history_view.setFont(mono)
        split.addWidget(self.history_view)
        self.detail_view = QPlainTextEdit()
        self.detail_view.setReadOnly(True)
        self.detail_view.setFont(mono)
        self.detail_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.detail_view.setPlaceholderText("Select a commit to see its message and files.")
        split.addWidget(self.detail_view)
        split.setSizes([300, 200])
        lay.addWidget(split, 1)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(lambda _p: self._schedule_refresh())
        self._watcher.fileChanged.connect(lambda _p: self._schedule_refresh())

        self._apply_theme_paper()
        if repo:
            self.set_repo(repo)

    # ── repo ──
    def set_repo(self, path: str | None) -> bool:
        """Point at the repo containing `path`; False = not a repo."""
        root = repo_root(path) if path else None
        if root is None:
            return False
        changed = root != self.repo
        self.repo = root
        self._repo_label.setText(Path(root).name or root)
        self._repo_label.setToolTip(root)
        self._arm_watcher()
        self.refresh()
        if changed and self.on_repo_changed is not None:
            try:
                self.on_repo_changed(root)
            except Exception:  # noqa: BLE001, S110 — persist must not break the view
                pass
        return True

    def choose_repo(self):
        """Pick a directory; walks up to its repo (stays put if none)."""
        picked = QFileDialog.getExistingDirectory(
            self, "Open git repository", self.repo or str(Path.home())
        )
        if picked and not self.set_repo(picked):
            self._repo_label.setText(f"Not a git repository: {picked}")
            self._repo_label.setToolTip(picked)

    # ── data ──
    def refresh(self):
        """Re-read branches + history (watcher calls this debounced)."""
        self._refresh_queued = False
        if find_git() is None:
            self._show_note("git not found on PATH — install git to use this view.")
            return
        if not self.repo:
            self._show_note("No repository — Open… a folder inside a git tree.")
            return
        current, branches, err = read_branches(self.repo)
        if err is not None:
            self._show_note(err)
            return
        self._fill_branches(current, branches)
        rows, truncated, err = read_history(self.repo, self._ref)
        if err is not None:
            self._show_note(err)
            return
        self._commits = rows
        self._paint(rows, truncated)
        self.detail_view.clear()

    def _fill_branches(self, current: str, branches: list[str]):
        """Rebuild the filter, keeping the selection when it survives."""
        box = self._branch_box
        want = self._ref
        box.blockSignals(True)
        try:
            box.clear()
            box.addItem("All branches", "--all")
            if current and current != "HEAD":
                box.addItem(f"Current: {current}", current)
            for b in branches:
                if b != current:
                    box.addItem(b, b)
            idx = box.findData(want)
            box.setCurrentIndex(max(idx, 0))
            self._ref = box.currentData() or "--all"
        finally:
            box.blockSignals(False)

    def _on_branch_picked(self, _idx: int):
        ref = self._branch_box.currentData() or "--all"
        if ref != self._ref:
            self._ref = ref
            self.refresh()

    def _schedule_refresh(self):
        """Watcher callback: coalesce bursts (checkout rewrites a lot)."""
        if self._refresh_queued:
            return
        self._refresh_queued = True
        QTimer.singleShot(1000, self.refresh)

    def _arm_watcher(self):
        """Watch the git dir + local branch tips (non-recursive: both)."""
        watched = self._watcher.directories() + self._watcher.files()
        if watched:
            self._watcher.removePaths(watched)
        if not self.repo:
            return
        gd = git_dir(self.repo)
        if gd is None:
            return
        paths = [gd]
        heads = Path(gd, "refs", "heads")
        if heads.is_dir():
            paths.append(str(heads))
        for name in ("HEAD", "packed-refs"):
            p = Path(gd, name)
            if p.is_file():
                paths.append(str(p))
        try:
            self._watcher.addPaths([p for p in paths if Path(p).exists()])
        except Exception:  # noqa: BLE001, S110 — watcher is best-effort
            pass

    # ── paint ──
    def _apply_theme_paper(self):
        """Full-bleed theme paper like FilePane (no widget-gray)."""
        from kilim import theme_background

        try:
            paper = theme_background(self.bridge.core.theme())
        except Exception:  # noqa: BLE001
            paper = None
        if not paper:
            return
        for view in (self.history_view, self.detail_view):
            pal = view.palette()
            pal.setColor(QPalette.Base, QColor(paper))
            view.setPalette(pal)

    def _show_note(self, text: str):
        self._commits = []
        self._block_commits = []
        self.history_view.setPlainText(text)
        self.detail_view.clear()

    def _visual_lines(self, rows: list[dict]) -> list[tuple[dict | None, str]]:
        """Records -> paint lines: `git log --graph` separates commits
        with graph-only connector rows (`|\\`, `|/`), so one record is
        one node line plus zero or more connector lines above it."""
        visual: list[tuple[dict | None, str]] = []
        for idx, row in enumerate(rows):
            parts = row["graph"].split("\n")
            for g in parts[:-1]:
                visual.append((None, g))
            visual.append((idx, parts[-1] if parts else ""))
        return visual

    def _paint(self, rows: list[dict], truncated: bool):
        """One node line per commit (+ graph-only connectors), then refs/meta."""
        view = self.history_view
        fg0 = view.palette().color(view.foregroundRole())
        bg0 = view.palette().color(view.backgroundRole())
        doc = view.document()
        cur = QTextCursor(doc)
        cur.beginEditBlock()
        try:
            doc.clear()
            visual = self._visual_lines(rows)
            self._block_commits = [idx for idx, _g in visual]
            for i, (idx, graph) in enumerate(visual):
                if i > 0:
                    cur.insertBlock()
                if idx is None:
                    fmt = QTextCharFormat()
                    fmt.setForeground(cell_qcolor("cyan", fg0))
                    cur.insertText(graph, fmt)
                else:
                    self._paint_row(cur, rows[idx], graph, fg0, bg0)
            if truncated:
                cur.insertBlock()
                fmt = QTextCharFormat()
                fmt.setForeground(cell_qcolor("gray", fg0))
                cur.insertText(f"… showing first {len(rows)} (narrow the branch filter)", fmt)
            if not rows and not truncated:
                self._block_commits = []
                cur.insertText("(no commits on this ref yet)")
        finally:
            cur.endEditBlock()

    def _paint_row(self, cur, row: dict, graph: str, fg0, bg0):
        def span(text: str, color: str, bold: bool = False):
            fmt = QTextCharFormat()
            fmt.setForeground(cell_qcolor(color, fg0))
            fmt.setBackground(cell_qcolor("default", bg0))
            if bold:
                fmt.setFontWeight(QFont.Bold)
            cur.insertText(text, fmt)

        span(graph + " " if graph else "", "cyan")
        span(row["subject"] or "(no message)", "default")
        head, branches, tags = split_refs(row["refs"])
        for name in head:
            span(f"  ● {name}", "brightgreen", bold=True)
        for name in branches:
            span(f"  {name}", "yellow")
        for name in tags:
            span(f"  tag: {name}", "brightmagenta")
        span(f"  {row['author']} · {row['date'][:10]}", "gray")

    # ── detail ──
    def _pick_row(self, block: int):
        if 0 <= block < len(self._block_commits):
            idx = self._block_commits[block]
            if idx is not None and 0 <= idx < len(self._commits):
                self._show_commit(self._commits[idx]["sha"])

    def _show_commit(self, sha: str):
        if not self.repo:
            return
        self.detail_view.setPlainText(read_commit(self.repo, sha))

"""Git history dock: backend (git CLI, no new deps) + GitPane widget.

The lanes come from layout_lanes (this module's own routing over the
commit DAG) — git only lists the commits, never draws.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QPointF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from kilim.qt_bridge import Bridge
from kilim.qt_themes import cell_qcolor

HISTORY_LIMIT = 400  # rows per read; more is a scrollback, not a view
COMMIT_CAP = 300  # diff lines: capped, never a full huge patch
HIGHLIGHT_CAP = 2000  # file lines per side: bigger keeps line colors
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # diff root vs this

LANE_COLORS = [  # vivid per-lane inks for dark paper (default theme)
    "#4da3ff", "#4dd06a", "#ffbe3c", "#ff6b6b",
    "#c586ff", "#4dd6d6", "#ff8ad4", "#a3e635",
]
LANE_COLORS_LIGHT = [  # same hues, darkened for light paper
    "#0b5fd0", "#0f7a3d", "#96590a", "#6d3bc7",
    "#007a87", "#a92e6c", "#6e5a00", "#b32626",
]
ROW_H = 24  # fixed row pitch: graph gutter + one text line
LANE_W = 14  # lane column pitch in the gutter
GUTTER_PAD = 10  # left margin before lane zero

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
            "log", "--topo-order", ref,
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
        _prefix, sha, rest = m.group(1), m.group(2), m.group(3)
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


def read_commit_files(repo: str, sha: str) -> tuple[list[tuple[str, str, str | None]], str | None]:
    """(files, error): (status, path, old_path) per changed file.

    First-parent diff, so merges show what they brought in and roots
    show every file as added. R/C rows carry the old path too. -M is
    explicit: rename detection must not depend on user git config."""
    try:
        out = _run_git(repo, "show", "--name-status", "--format=",
                       "--first-parent", "-M", sha, "--")
    except (OSError, subprocess.TimeoutExpired) as e:
        return [], f"git show failed: {e}"
    if out.returncode != 0:
        err = out.stderr.strip().splitlines()
        return [], err[0] if err else f"git show exited {out.returncode}"
    files: list[tuple[str, str, str | None]] = []
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0][:1]
        if len(parts) >= 3:  # R100/C100 old new
            files.append((status, parts[2], parts[1]))
        elif len(parts) == 2:
            files.append((status, parts[1], None))
    return files, None


def read_file_diff(repo: str, sha: str, parent: str | None, path: str) -> str:
    """Unified diff of one file in a commit (vs first parent / empty)."""
    base = parent or EMPTY_TREE
    try:
        out = _run_git(repo, "diff", "--no-color", "--no-ext-diff", "-M", base, sha, "--", path)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"git diff failed: {e}"
    if out.returncode != 0:
        err = out.stderr.strip().splitlines()
        return err[0] if err else f"git diff exited {out.returncode}"
    lines = out.stdout.splitlines()
    if len(lines) > COMMIT_CAP:
        lines = lines[:COMMIT_CAP] + [f"… {len(lines) - COMMIT_CAP} more lines"]
    return "\n".join(lines).rstrip() or "(no textual diff)"


def read_commit_body(repo: str, sha: str) -> str:
    """Full message body (subject stripped: the header shows it bold)."""
    try:
        out = _run_git(repo, "log", "-1", "--format=%B", sha, "--")
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if out.returncode != 0:
        return ""
    return "\n".join(out.stdout.strip("\n").splitlines()[1:]).strip("\n")


def _luminance(color) -> float:
    """Perceived brightness 0..1 (picks the dark/light ink set)."""
    return 0.2126 * color.redF() + 0.7152 * color.greenF() + 0.0722 * color.blueF()


def _mix(a, b, t: float):
    """Linear blend of two inks (dimmed text = text mixed toward paper)."""
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


_STATUS_DARK = {  # name-status letter -> ink on dark paper
    "M": "#e3b341", "A": "#56d364", "D": "#ff7b72", "R": "#39c5cf",
    "C": "#db61a2", "T": "#9aa4b2", "U": "#ffa657",
}
_STATUS_LIGHT = {  # same semantics on light paper (AA incl. mid-gray)
    "M": "#835700", "A": "#146c2e", "D": "#ba1e28", "R": "#006772",
    "C": "#a92e6c", "T": "#576171", "U": "#904b05",
}
_PILLS_DARK = {"head": "#56d364", "branch": "#e3b341", "tag": "#db61a2"}
_PILLS_LIGHT = {"head": "#146c2e", "branch": "#835700", "tag": "#a92e6c"}


def read_file_text(repo: str, sha: str, path: str) -> str:
    """`git show sha:path` (empty when that side lacks the file)."""
    try:
        out = _run_git(repo, "show", f"{sha}:{path}")
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout if out.returncode == 0 else ""


_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def layout_lanes(commits: list[dict]) -> list[dict]:
    """Newest-first commits -> paint rows for the graph gutter.

    Row: {"sha", "slot", "top", "bottom", "edges"}. `top`/`bottom`
    are the lane columns (sha|None) above/below the node; `edges` are
    (from, to) curves drawn from the node down into a lane. First
    parents continue straight in the child's slot, extra parents fork
    new lanes, already-reserved parents merge back with a curve, and a
    freed slot ends the line (roots, lane tips). Missing parents
    (shallow clones) simply run their lane to the bottom edge."""
    lanes: list[str | None] = []
    rows: list[dict] = []
    for c in commits:
        sha = c["sha"]
        if sha in lanes:
            s = lanes.index(sha)
        else:
            try:
                s = lanes.index(None)
            except ValueError:
                s = len(lanes)
                lanes.append(None)
            lanes[s] = sha
        top = list(lanes)
        lanes[s] = None
        edges: list[tuple[int, int]] = []
        for i, p in enumerate(c["parents"]):
            if p in lanes:
                j = lanes.index(p)
                if j != s:
                    edges.append((s, j))
            else:
                if i == 0:
                    j = s  # straight continuation, no curve
                else:
                    try:
                        j = lanes.index(None)
                    except ValueError:
                        j = len(lanes)
                        lanes.append(None)
                    edges.append((s, j))
                lanes[j] = p
        rows.append({"sha": sha, "slot": s, "top": top, "bottom": list(lanes), "edges": edges})
    return rows


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


class GraphView(QWidget):
    """Painted commit graph: lanes, bezier merges, dots, pills, hover.

    One fixed-height row per commit (layout_lanes output): verticals for
    passing lanes, gradient curves for forks/merges, a dot on the
    commit's slot, then subject + ref pills + dimmed meta. Hover tints
    the row, rings the node and every ancestor/descendant dot, and shows
    the full identity as a tooltip; click selects (Up/Down move it)."""

    def __init__(self, on_pick):
        super().__init__()
        self._on_pick = on_pick
        self._commits: list[dict] = []
        self._rows: list[dict] = []
        self._by_sha: dict[str, int] = {}
        self._children: dict[str, list[str]] = {}
        self._hover: int | None = None
        self._selected: str | None = None
        self._connected: set[str] = set()
        self._colors = [QColor(c) for c in LANE_COLORS]
        self._pills = {k: QColor(v) for k, v in _PILLS_DARK.items()}
        self._text = QColor("#e6e9f0")
        self._dim = QColor("#9aa4b2")
        self._link = QColor(LANE_COLORS[0])
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFont(QFont("Cascadia Mono", 10))

    # ── model ──
    def set_commits(self, commits: list[dict]):
        self._commits = commits
        self._rows = layout_lanes(commits)
        self._by_sha = {c["sha"]: i for i, c in enumerate(commits)}
        children: dict[str, list[str]] = {}
        for c in commits:
            for p in c["parents"]:
                children.setdefault(p, []).append(c["sha"])
        self._children = children
        self._hover = None
        self._connected = self._connected_shas(self._selected)
        self._resize_to_content()
        self.update()

    def _connected_shas(self, sha: str | None) -> set[str]:
        """sha + its ancestors + descendants (bounded: loaded rows only)."""
        if not sha:
            return set()
        seen = {sha}
        stack = [sha]
        by_sha = {c["sha"]: c for c in self._commits}
        while stack and len(seen) < 5000:
            cur = stack.pop()
            c = by_sha.get(cur)
            if c is None:
                continue
            for nxt in list(c["parents"]) + self._children.get(cur, []):
                if nxt not in seen and nxt in by_sha:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    def select(self, sha: str | None):
        self._selected = sha
        if self._hover is None:
            self._connected = self._connected_shas(sha)
        self.update()

    def row_of(self, sha: str) -> int | None:
        idx = self._by_sha.get(sha)
        return idx

    def ensure_row_visible(self, i: int):
        area = self.parentWidget()
        while area is not None and not isinstance(area, QScrollArea):
            area = area.parentWidget()
        if area is not None:
            area.ensureVisible(0, i * ROW_H + ROW_H // 2, 0, ROW_H * 2)

    def _resize_to_content(self):
        fm = QFontMetrics(self.font())
        lanes = max((max(len(r["top"]), len(r["bottom"])) for r in self._rows), default=0)
        gutter = GUTTER_PAD + lanes * LANE_W + 8
        widest = 0
        for c in self._commits:
            w = fm.horizontalAdvance(c["subject"] or "(no message)")
            head, branches, tags = split_refs(c["refs"])
            for name in head + branches + tags:
                w += fm.horizontalAdvance(name) + 18
            w += fm.horizontalAdvance(f"  {c['author']} · {c['date'][:10]}")
            widest = max(widest, w)
        self.setMinimumSize(gutter + widest + GUTTER_PAD, max(1, len(self._rows)) * ROW_H)

    # ── interaction ──
    def _row_at(self, y: int) -> int | None:
        i = y // ROW_H
        return i if 0 <= i < len(self._rows) else None

    def mouseMoveEvent(self, e):
        i = self._row_at(e.position().toPoint().y())
        if i != self._hover:
            self._hover = i
            self._connected = self._connected_shas(
                self._rows[i]["sha"] if i is not None else self._selected)
            self.update()
            if i is not None:
                c = self._commits[i]
                QToolTip.showText(
                    e.globalPosition().toPoint(),
                    f"{c['sha']}\n{c['subject']}\n{c['author']} · {c['date'][:10]}",
                    self,
                )
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._hover = None
        self._connected = self._connected_shas(self._selected)
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            i = self._row_at(e.position().toPoint().y())
            if i is not None:
                self._on_pick(i)
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        cur = self._by_sha.get(self._selected or "", None)
        if cur is None:
            cur = self._hover
        if e.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Home, Qt.Key_End):
            if not self._rows:
                return
            base = cur if cur is not None else 0
            if e.key() == Qt.Key_Up:
                nxt = max(0, base - 1)
            elif e.key() == Qt.Key_Down:
                nxt = min(len(self._rows) - 1, base + 1)
            elif e.key() == Qt.Key_Home:
                nxt = 0
            else:
                nxt = len(self._rows) - 1
            self._on_pick(nxt)
            self.ensure_row_visible(nxt)
            e.accept()
            return
        super().keyPressEvent(e)

    # ── paint ──
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)  # curves, dots, rings, pills
        p.setRenderHint(QPainter.TextAntialiasing)
        fm = p.fontMetrics()
        first = max(0, e.rect().top() // ROW_H - 1)
        last = min(len(self._rows), e.rect().bottom() // ROW_H + 2)
        for i in range(first, last):
            self._paint_row(p, fm, i)

    def _lane_x(self, j: int) -> int:
        return GUTTER_PAD + j * LANE_W

    def _paint_row(self, p, fm, i: int):
        row = self._rows[i]
        c = self._commits[i]
        y, cy = i * ROW_H, i * ROW_H + ROW_H // 2
        slot = row["slot"]
        if c["sha"] == self._selected:
            sel = QColor(self._link)
            sel.setAlpha(70)
            p.fillRect(0, y, self.width(), ROW_H, sel)
        elif i == self._hover:
            faint = QColor(self._link)
            faint.setAlpha(45)
            p.fillRect(0, y, self.width(), ROW_H, faint)
        top, bottom = row["top"], row["bottom"]
        for j in range(max(len(top), len(bottom))):
            x = self._lane_x(j)
            t = top[j] if j < len(top) else None
            b = bottom[j] if j < len(bottom) else None
            col = self._colors[j % len(self._colors)]
            p.setPen(QPen(col, 2))
            if j == slot:
                p.drawLine(x, y + 1, x, cy)
                if b is not None:
                    p.drawLine(x, cy, x, y + ROW_H - 1)
            else:
                if t is not None:
                    p.drawLine(x, y + 1, x, cy)
                if b is not None:
                    p.drawLine(x, cy, x, y + ROW_H - 1)
        for s, j in row["edges"]:
            if j == s:
                continue
            xs, xj = self._lane_x(s), self._lane_x(j)
            grad = QLinearGradient(xs, cy, xj, y + ROW_H)
            grad.setColorAt(0.0, self._colors[s % len(self._colors)])
            grad.setColorAt(1.0, self._colors[j % len(self._colors)])
            path = QPainterPath(QPointF(xs, cy))
            path.cubicTo(xs, cy + 8, xj, y + ROW_H - 8, xj, y + ROW_H - 1)
            p.strokePath(path, QPen(QBrush(grad), 2))
        dot = QColor(self._colors[slot % len(self._colors)])
        p.setPen(QPen(dot.darker(130), 1))
        p.setBrush(QBrush(dot))
        p.drawEllipse(QPointF(self._lane_x(slot), cy), 4.5, 4.5)
        if c["sha"] in self._connected:
            ring = QColor(dot).lighter(150)
            p.setPen(QPen(ring, 2 if c["sha"] == self._selected or i == self._hover else 1))
            p.setBrush(QBrush(Qt.NoBrush))
            extra = 3.5 if c["sha"] == self._selected or i == self._hover else 2.5
            p.drawEllipse(QPointF(self._lane_x(slot), cy), 4.5 + extra, 4.5 + extra)
        lanes = max(len(top), len(bottom))
        x = GUTTER_PAD + lanes * LANE_W + 8
        base = fm.ascent() + (ROW_H - fm.height()) // 2
        p.setPen(QPen(self._text))
        p.drawText(x, y + base, c["subject"] or "(no message)")
        x += fm.horizontalAdvance(c["subject"] or "(no message)") + 6
        head, branches, tags = split_refs(c["refs"])
        for name, kind, bold in (
            [(n, "head", True) for n in head]
            + [(n, "branch", False) for n in branches]
            + [(f"tag: {n}", "tag", False) for n in tags]
        ):
            w = fm.horizontalAdvance(name)
            pill_h = fm.height() + 2
            pill = QColor(self._pills[kind])
            fill = QColor(pill)
            fill.setAlpha(40)
            p.setPen(QPen(pill, 1))
            p.setBrush(QBrush(fill))
            p.drawRoundedRect(x, y + (ROW_H - pill_h) // 2, w + 12, pill_h, 7, 7)
            if bold:
                f = QFont(self.font())
                f.setBold(True)
                p.setFont(f)
            p.setPen(QPen(pill))
            p.drawText(x + 6, y + base, name)
            if bold:
                p.setFont(self.font())
            p.setPen(QPen(self._text))
            x += w + 18
        meta = f"{c['author']} · {c['date'][:10]}"
        p.setPen(QPen(self._dim))
        p.drawText(x, y + base, meta)


class GitPane(QWidget):
    """Git history dock: painted graph on top, commit inspector below.

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
        self._selected_sha: str | None = None
        self._selected_idx: int | None = None
        self._files: list[tuple[str, str, str | None]] = []
        self._file_row: int | None = None
        self._ref = "--all"
        self._refresh_queued = False
        self._status: dict[str, QColor] = {}
        self._ink = QColor("#e6e9f0")
        self._dim = QColor("#9aa4b2")
        self._link = QColor(LANE_COLORS[0])

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
        self._more_label = QLabel("")
        self._more_label.setToolTip("History is capped: narrow the branch filter to see more")
        bar.addWidget(self._more_label)
        open_btn = QPushButton("Open…")
        open_btn.clicked.connect(lambda _c=False: self.choose_repo())
        bar.addWidget(open_btn)
        lay.addLayout(bar)

        split = QSplitter(Qt.Vertical)
        mono = QFont("Cascadia Mono", 10)
        self.graph_scroll = QScrollArea()
        self.graph_scroll.setWidgetResizable(True)
        self.graph = GraphView(self._pick_commit)
        self.graph.setFont(mono)
        self.graph_scroll.setWidget(self.graph)
        split.addWidget(self.graph_scroll)
        self.msg_view = QPlainTextEdit()
        self.msg_view.setReadOnly(True)
        self.msg_view.setFont(mono)
        self.msg_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        split.addWidget(self.msg_view)
        files_box = QWidget()
        files_lay = QVBoxLayout(files_box)
        files_lay.setContentsMargins(0, 0, 0, 0)
        self._files_label = QLabel("Files")
        files_lay.addWidget(self._files_label)
        self.files_list = QListWidget()
        self.files_list.setFont(mono)
        self.files_list.currentRowChanged.connect(self._on_file_picked)
        files_lay.addWidget(self.files_list, 1)
        split.addWidget(files_box)
        self.diff_view = QPlainTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setFont(mono)
        self.diff_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.diff_view.setPlaceholderText("Select a file to preview its diff.")
        split.addWidget(self.diff_view)
        split.setSizes([320, 90, 120, 240])
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
        self._more_label.setText(f"{len(rows)}+ shown" if truncated else "")
        self.graph.set_commits(rows)
        if rows:
            shas = {c["sha"] for c in rows}
            keep = self._selected_sha if self._selected_sha in shas else rows[0]["sha"]
            i = next(k for k, c in enumerate(rows) if c["sha"] == keep)
            self._selected_sha = keep
            self._selected_idx = i
            self.graph.select(keep)
            self._show_commit(i)
        else:
            self._selected_sha = None
            self._selected_idx = None
            self.graph.select(None)
            self.msg_view.setPlainText("(no commits on this ref yet)")
            self._files_label.setText("Files")
            self.files_list.clear()
            self.diff_view.clear()

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
        """Full-bleed theme paper like FilePane (no widget-gray).

        Also refreshes every ink the dock paints with: lanes, pills,
        status, text, dim, and link accent. All come from the theme
        registry synchronously — never from widget palettes, which Lace
        re-applies asynchronously and would otherwise win the race and
        leave one-shot paints (message header, diff) stale."""
        from kilim import theme_background, theme_foreground

        try:
            paper = theme_background(self.bridge.core.theme())
        except Exception:  # noqa: BLE001
            paper = None
        if not paper:
            return
        for view in (self.msg_view, self.diff_view, self.files_list):
            pal = view.palette()
            pal.setColor(QPalette.Base, QColor(paper))
            view.setPalette(pal)
        pal = self.graph.palette()
        pal.setColor(QPalette.Window, QColor(paper))
        self.graph.setPalette(pal)
        self.graph.setAutoFillBackground(True)
        paper_q = QColor(paper)
        dark = _luminance(paper_q) < 0.5
        lanes = LANE_COLORS if dark else LANE_COLORS_LIGHT
        self.graph._colors = [QColor(c) for c in lanes]
        pills = _PILLS_DARK if dark else _PILLS_LIGHT
        self.graph._pills = {k: QColor(v) for k, v in pills.items()}
        inks = _STATUS_DARK if dark else _STATUS_LIGHT
        self._status = {k: QColor(v) for k, v in inks.items()}
        self._link = QColor(lanes[0])
        if fg := theme_foreground(self.bridge.core.theme()):
            self._ink = QColor(fg)
        self._dim = _mix(self._ink, paper_q, 0.45)
        self.graph._text = QColor(self._ink)
        self.graph._dim = QColor(self._dim)
        self.graph._link = QColor(self._link)
        self.graph.update()

    def _show_note(self, text: str):
        self._commits = []
        self._selected_sha = None
        self._selected_idx = None
        self._more_label.setText("")
        self.graph.set_commits([])
        self.msg_view.setPlainText(text)
        self._files_label.setText("Files")
        self.files_list.clear()
        self.diff_view.clear()

    # ── inspector: message + files + diff ──
    def _pick_commit(self, i: int):
        if 0 <= i < len(self._commits):
            self._selected_sha = self._commits[i]["sha"]
            self._selected_idx = i
            self.graph.select(self._selected_sha)
            self._show_commit(i)

    def _show_message(self, i: int):
        """Message header alone (theme refresh repaints Link/dim inks)."""
        c = self._commits[i]
        body = read_commit_body(self.repo, c["sha"])
        view = self.msg_view
        cur = QTextCursor(view.document())
        cur.beginEditBlock()
        try:
            view.clear()
            self._span(cur, c["subject"] or "(no message)", self._ink, self._ink, bold=True)
            cur.insertBlock()
            self._span(cur, c["sha"][:12], self._link, self._ink)
            self._span(cur, f" · {c['author']} · {c['date'][:10]}", self._dim, self._ink)
            if body:
                cur.insertBlock()
                self._span(cur, body, self._ink, self._ink)
        finally:
            cur.endEditBlock()

    def _show_commit(self, i: int):
        """Message header + file list; the first file's diff shows at once."""
        if not self.repo or not (0 <= i < len(self._commits)):
            return
        self._show_message(i)
        c = self._commits[i]
        files, err = read_commit_files(self.repo, c["sha"])
        self._files = files if err is None else []
        self.files_list.clear()
        if err is not None:
            self._files_label.setText("Files")
            bad = QListWidgetItem(err)
            bad.setFlags(bad.flags() & ~Qt.ItemIsSelectable)
            self.files_list.addItem(bad)
            self.diff_view.clear()
            return
        self._files_label.setText(f"Files ({len(files)})")
        for status, path, old in files:
            label = f"{status}  {old} → {path}" if old else f"{status}  {path}"
            item = QListWidgetItem(label)
            item.setForeground(QBrush(self._status.get(status, self._ink)))
            self.files_list.addItem(item)
        if files:
            self.files_list.setCurrentRow(0)  # fires _on_file_picked -> diff
        else:
            self._file_row = None
            empty = QListWidgetItem("(no files changed)")
            empty.setFlags(empty.flags() & ~Qt.ItemIsSelectable)
            self.files_list.addItem(empty)
            self.diff_view.clear()

    def refresh_theme(self):
        """Theme switch: re-paper, repaint header, re-highlight diff."""
        self._apply_theme_paper()
        idx = self._selected_idx
        if (self._selected_sha is not None and idx is not None
                and 0 <= idx < len(self._commits)
                and self._commits[idx]["sha"] == self._selected_sha):
            self._show_message(idx)
        if self._selected_sha is not None and self._file_row is not None:
            self._show_file(self._file_row)
        self.graph.update()

    def _on_file_picked(self, row: int):
        self._file_row = row if 0 <= row < len(self._files) else None
        if self._file_row is not None:
            self._show_file(row)

    def _show_file(self, row: int):
        """Unified diff of one file (vs first parent, empty tree at root)."""
        if not self.repo or not (0 <= row < len(self._files)):
            return
        _status, path, old = self._files[row]
        parent = None
        for c in self._commits:
            if c["sha"] == self._selected_sha and c["parents"]:
                parent = c["parents"][0]
                break
        text = read_file_diff(self.repo, self._selected_sha, parent, path)
        self._paint_diff(text, path, self._highlighted_sides(path, old, parent))

    def _highlighted_sides(self, path: str, old: str | None, parent: str | None):
        """(old_rows, new_rows): syntect spans per side, ([], []) when big.

        Renames look up the old side under the old path (it never
        existed under the new one)."""
        base = path.rsplit("/", 1)[-1]
        ext = base.rsplit(".", 1)[-1] if "." in base else ""
        old_text = read_file_text(self.repo, parent or EMPTY_TREE, old or path)
        new_text = read_file_text(self.repo, self._selected_sha, path)
        if max(len(old_text.splitlines()), len(new_text.splitlines())) > HIGHLIGHT_CAP:
            return [], []
        try:
            old_rows = self.bridge.core.highlight_code(ext, old_text)
            new_rows = self.bridge.core.highlight_code(ext, new_text)
        except Exception:  # noqa: BLE001 — highlight never breaks the diff
            return [], []
        return old_rows, new_rows

    @staticmethod
    def _span(cur, text: str, color, fg0, bold: bool = False):
        fmt = QTextCharFormat()
        fmt.setForeground(color if isinstance(color, QColor) else cell_qcolor(color, fg0))
        if bold:
            fmt.setFontWeight(QFont.Bold)
        cur.insertText(text, fmt)

    def _paint_diff(self, text: str, path: str, sides):
        """Unified diff: headers/hunks line-colored, code syntect-spanned.

        `sides` = (old_rows, new_rows) of (text, fg, bg) spans indexed by
        0-based file line; hunk headers track both counters. Added lines
        get a green wash, deleted a red wash, context stays on paper.
        Missing rows (cap, binary, huge) fall back to whole-line colors.
        """
        old_rows, new_rows = sides
        view = self.diff_view
        cur = QTextCursor(view.document())
        cur.beginEditBlock()
        try:
            view.clear()
            old_no = new_no = 0
            for n, line in enumerate(text.splitlines()):
                if n > 0:
                    cur.insertBlock()
                chunks = self._diff_chunks(line, old_rows, new_rows, old_no, new_no)
                if line.startswith("@@"):
                    m = _HUNK.match(line)
                    if m:
                        old_no, new_no = int(m.group(1)), int(m.group(2))
                elif line.startswith("+") and not line.startswith("+++"):
                    new_no += 1
                elif line.startswith("-") and not line.startswith("---"):
                    old_no += 1
                elif line.startswith(" "):
                    old_no += 1
                    new_no += 1
                for chunk, color, bold, bg in chunks:
                    fmt = QTextCharFormat()
                    fmt.setForeground(color)
                    if bold:
                        fmt.setFontWeight(QFont.Bold)
                    if bg is not None:
                        fmt.setBackground(bg)
                    cur.insertText(chunk, fmt)
        finally:
            cur.endEditBlock()

    def _diff_chunks(self, line, old_rows, new_rows, old_no, new_no):
        """One diff line -> (text, QColor, bold, bg) chunks, theme-aware."""
        ink = self._status.get
        if line.startswith("@@"):
            return [(line or " ", ink("R", self._ink), True, None)]
        if line.startswith(("diff --git", "index ", "--- ", "+++ ",
                            "new file", "deleted", "similarity",
                            "rename ", "old mode", "new mode")):
            return [(line or " ", self._ink, True, None)]
        if line.startswith("\\"):
            return [(line or " ", self._dim, False, None)]
        add, delete = ink("A", self._ink), ink("D", self._ink)
        if line.startswith("+") and not line.startswith("+++"):
            rows, no, color = new_rows, new_no, add
            wash = QColor(color)
            wash.setAlpha(26)
        elif line.startswith("-") and not line.startswith("---"):
            rows, no, color = old_rows, old_no, delete
            wash = QColor(color)
            wash.setAlpha(30)
        elif line.startswith(" "):
            rows, no, color, wash = new_rows, new_no, self._ink, None
        else:
            return [(line or " ", self._ink, False, None)]
        marker, code = line[:1], line[1:]
        chunks = [(marker, color, True, wash)]
        spans = rows[no - 1] if 1 <= no <= len(rows) else []
        if spans:
            for text, fg, _bg in spans:
                text = text.rstrip("\r\n")  # syntect keeps line endings
                if text:
                    chunks.append((text, cell_qcolor(fg, self._ink), False, wash))
        else:
            chunks.append((code, color, False, wash))
        return chunks

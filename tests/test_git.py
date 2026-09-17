"""Git history dock: backend parsing on a fixture repo + Qt open/click/close."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git CLI required")

from kilim.git_pane import (
    find_git,
    layout_lanes,
    read_branches,
    read_commit_body,
    read_commit_files,
    read_file_diff,
    read_history,
    repo_root,
    split_refs,
)


def _git(repo, *args):
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1")
    base = [
        "git",
        "-c", "user.name=Kilim Test",
        "-c", "user.email=kilim@test.invalid",
        "-c", "init.defaultBranch=main",
        "-c", "commit.gpgsign=false",
    ]
    return subprocess.run(
        base + list(args), cwd=str(repo), capture_output=True,
        text=True, timeout=60, env=env, check=True,
    )


@pytest.fixture()
def repo(tmp_path):
    """main with a merged feature branch + tag (graph has a real fork)."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-m", "first commit")
    (root / "b.txt").write_text("two\n")
    _git(root, "add", "b.txt")
    _git(root, "commit", "-m", "second commit")
    _git(root, "checkout", "-b", "feature")
    (root / "c.txt").write_text("three\n")
    _git(root, "add", "c.txt")
    _git(root, "commit", "-m", "feature work", "-m", "the body")
    _git(root, "checkout", "main")
    (root / "d.txt").write_text("four\n")
    (root / "f.py").write_text('def hello(name):\n    return f"hi {name}"\n')
    _git(root, "add", "d.txt", "f.py")
    _git(root, "commit", "-m", "third commit")
    _git(root, "merge", "--no-ff", "-m", "merge feature", "feature")
    _git(root, "tag", "v0.1")
    return str(root)


def test_repo_root_walks_up_and_rejects_plain_dirs(repo, tmp_path):
    sub = os.path.join(repo, "sub", "deep")
    os.makedirs(sub)
    assert os.path.normcase(repo_root(sub)) == os.path.normcase(repo)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert repo_root(str(plain)) is None
    assert repo_root(None) is None


def test_history_parses_merge_refs_and_graph(repo):
    rows, truncated, err = read_history(repo)
    assert err is None and truncated is False
    assert next(r["subject"] for r in rows) == "merge feature"
    assert {r["subject"] for r in rows} == {
        "merge feature", "third commit", "feature work",
        "second commit", "first commit",
    }
    by_sha = {r["sha"]: r for r in rows}
    assert len(by_sha) == 5 and all(len(s) == 40 for s in by_sha)
    merge = rows[0]
    assert len(merge["parents"]) == 2
    assert all(p in by_sha for p in merge["parents"])
    assert "main" in merge["refs"] and "tag: v0.1" in merge["refs"]


def test_history_limit_reports_truncation(repo):
    rows, truncated, err = read_history(repo, limit=2)
    assert err is None and truncated is True
    assert len(rows) == 2 and rows[0]["subject"] == "merge feature"


def test_history_branch_filter_hides_other_branches(repo):
    rows, truncated, err = read_history(repo, ref="feature")
    assert err is None and truncated is False
    assert {r["subject"] for r in rows} == {"feature work", "second commit", "first commit"}


def test_branches_lists_current_and_locals(repo):
    current, branches, err = read_branches(repo)
    assert err is None
    assert current == "main"
    assert set(branches) == {"main", "feature"}


def test_commit_files_lists_statuses(repo):
    """Merge shows first-parent files; the root shows everything added."""
    rows, _, _ = read_history(repo)
    by_subject = {r["subject"]: r for r in rows}
    files, err = read_commit_files(repo, by_subject["merge feature"]["sha"])
    assert err is None
    assert ("A", "c.txt", None) in files
    assert "d.txt" not in {f[1] for f in files}  # already in first parent
    files, err = read_commit_files(repo, by_subject["first commit"]["sha"])
    assert err is None
    assert files == [("A", "a.txt", None)]


def test_commit_files_detects_renames(tmp_path, monkeypatch):
    """Renames come back as one R row (not D+A), with the old path.

    Rename detection is forced off via config so the test proves the
    explicit -M flag (not ambient user settings) finds the rename."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "diff.renames")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
    root = tmp_path / "ren"
    root.mkdir()
    _git(root, "init")
    (root / "old.py").write_text("a = 1\nb = 2\nc = 3\nd = 4\n")
    _git(root, "add", "old.py")
    _git(root, "commit", "-m", "base")
    (root / "old.py").rename(root / "new.py")
    (root / "new.py").write_text("a = 1\nb = 2\nc = 3\nd = 5\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "rename")
    rows, _, _ = read_history(str(root))
    sha = next(r["sha"] for r in rows if r["subject"] == "rename")
    files, err = read_commit_files(str(root), sha)
    assert err is None
    assert files == [("R", "new.py", "old.py")]


def test_file_diff_unified_against_first_parent(repo):
    rows, _, _ = read_history(repo)
    by_subject = {r["subject"]: r for r in rows}
    merge = by_subject["merge feature"]
    text = read_file_diff(repo, merge["sha"], merge["parents"][0], "c.txt")
    assert "@@" in text and "+three" in text


def test_commit_body_strips_subject(repo):
    rows, _, _ = read_history(repo)
    by_subject = {r["subject"]: r for r in rows}
    assert read_commit_body(repo, by_subject["feature work"]["sha"]) == "the body"
    assert read_commit_body(repo, by_subject["first commit"]["sha"]) == ""


def test_layout_lanes_linear_chain():
    """Straight history: one lane, no curves, slots stay zero."""
    cs = [{"sha": s, "parents": p} for s, p in
          [("C", ["B"]), ("B", ["A"]), ("A", [])]]
    rows = layout_lanes(cs)
    assert [(r["slot"], r["edges"]) for r in rows] == [(0, []), (0, []), (0, [])]
    assert [r["bottom"] for r in rows] == [["B"], ["A"], [None]]


def test_layout_lanes_fork_and_merge():
    """D and C fork off B: C's row curves back, B continues straight."""
    cs = [{"sha": s, "parents": p} for s, p in
          [("D", ["B"]), ("C", ["B"]), ("B", ["A"]), ("A", [])]]
    rows = layout_lanes(cs)
    assert [(r["slot"], r["edges"]) for r in rows] == [
        (0, []), (1, [(1, 0)]), (0, []), (0, [])]
    assert [r["bottom"] for r in rows] == [
        ["B"], ["B", None], ["A", None], [None, None]]


def test_layout_lanes_octopus_merge():
    """Three parents: first continues, two fork out and curve back."""
    cs = [{"sha": s, "parents": p} for s, p in
          [("M", ["A", "B", "C"]), ("A", ["R"]), ("B", ["R"]),
           ("C", ["R"]), ("R", [])]]
    rows = layout_lanes(cs)
    assert [(r["slot"], r["edges"]) for r in rows] == [
        (0, [(0, 1), (0, 2)]), (0, []), (1, [(1, 0)]), (2, [(2, 0)]), (0, [])]
    assert [r["bottom"][-1] for r in rows] == ["C", "C", "C", None, None]


def test_empty_repo_reads_empty_not_error(tmp_path):
    root = tmp_path / "fresh"
    root.mkdir()
    _git(root, "init")
    rows, truncated, err = read_history(str(root))
    assert err is None and rows == [] and truncated is False


def test_split_refs_sorts_head_branches_tags():
    assert split_refs("HEAD -> main, origin/main, tag: v1.2") == (
        ["main"], ["origin/main"], ["v1.2"])
    assert split_refs("") == ([], [], [])
    assert split_refs("HEAD") == (["HEAD"], [], [])


def test_find_git_missing_without_path(monkeypatch):
    monkeypatch.setenv("PATH", "")
    assert find_git() is None


PySide6 = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("lace")


@pytest.fixture()
def scene(tmp_path):
    """Hermetic (layout, sidecar) pair: tests never write the repo files."""
    import json

    from _util import copy_layout

    layout = copy_layout("layouts/default.json", tmp_path / "l.json")
    # Neutralize term spawns: they inherit the pytest cwd (the Kilim
    # repo), which would let focus-follow yank the dock off the fixture
    # repo. tmp_path is never a repo, so follow keeps the current one.
    doc = json.loads(layout.read_text(encoding="utf-8"))
    for p in doc.get("panes", []):
        if p.get("kind") == "term":
            p["cwd"] = str(tmp_path)
    layout.write_text(json.dumps(doc), encoding="utf-8")
    return str(layout), str(tmp_path / "l.perspective.json")


def _window(layout, sidecar):
    from kilim.qt_app import KilimWindow
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    w = KilimWindow(layout, sidecar)
    w.show()
    return app, w


def _gclick(view, pos):
    """Left click on a plain QWidget (position + global, press/release)."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    for typ in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        buttons = Qt.LeftButton if typ == QEvent.MouseButtonPress else Qt.NoButton
        ev = QMouseEvent(
            typ, QPointF(pos), QPointF(view.mapToGlobal(pos)),
            Qt.LeftButton, buttons, Qt.NoModifier,
        )
        if typ == QEvent.MouseButtonPress:
            view.mousePressEvent(ev)
        else:
            view.mouseReleaseEvent(ev)


def _row_center(pane, i):
    """Widget coords of a graph row's middle (TextCenter of the node)."""
    from kilim.git_pane import ROW_H
    from PySide6.QtCore import QPoint

    return QPoint(30, i * ROW_H + ROW_H // 2)


def test_git_dock_opens_graph_and_autoshows_merge_inspector(scene, repo):
    """Five painted rows, newest auto-selected: message + files + diff."""
    layout, sidecar = scene
    app, w = _window(layout, sidecar)
    try:
        w._open_git_dock(repo=repo)
        assert "git" in w.pane_docks
        pane = w.git_pane
        assert pane.repo and os.path.normcase(pane.repo) == os.path.normcase(repo)
        assert len(pane._commits) == 5 and len(pane.graph._rows) == 5
        slots = [r["slot"] for r in pane.graph._rows]
        assert slots[0] == 0 and max(slots) >= 1  # merge forked a lane
        assert "merge feature" in pane.msg_view.toPlainText()
        assert pane._files_label.text() == f"Files ({pane.files_list.count()})"
        assert pane.files_list.count() >= 1
        assert "+three" in pane.diff_view.toPlainText()  # c.txt vs 1st parent
    finally:
        w.close()
        app.processEvents()


def test_git_click_selects_and_hover_tracks(scene, repo):
    """Click moves the selection (message follows); hover tracks the row."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    layout, sidecar = scene
    app, w = _window(layout, sidecar)
    try:
        w._open_git_dock(repo=repo)
        pane = w.git_pane
        _gclick(pane.graph, _row_center(pane, 3))
        app.processEvents()
        assert pane._selected_sha == pane._commits[3]["sha"]
        assert "second commit" in pane.msg_view.toPlainText()
        ev = QMouseEvent(
            QEvent.MouseMove, QPointF(_row_center(pane, 1)),
            QPointF(pane.graph.mapToGlobal(_row_center(pane, 1))),
            Qt.NoButton, Qt.NoButton, Qt.NoModifier,
        )
        pane.graph.mouseMoveEvent(ev)
        assert pane.graph._hover == 1
        assert pane._commits[1]["sha"] in pane.graph._connected
    finally:
        w.close()
        app.processEvents()


def test_git_keyboard_moves_selection(scene, repo):
    """Down-arrow steps the inspector to the next commit."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    layout, sidecar = scene
    app, w = _window(layout, sidecar)
    try:
        w._open_git_dock(repo=repo)
        pane = w.git_pane
        assert pane._selected_sha == pane._commits[0]["sha"]
        pane.graph.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))
        app.processEvents()
        assert pane._selected_sha == pane._commits[1]["sha"]
        assert "feature work" in pane.msg_view.toPlainText()
    finally:
        w.close()
        app.processEvents()


def test_git_file_pick_shows_its_diff(scene, repo):
    """The file list drives the diff preview (c.txt: added with +three)."""
    from PySide6.QtCore import Qt

    layout, sidecar = scene
    app, w = _window(layout, sidecar)
    try:
        w._open_git_dock(repo=repo)
        pane = w.git_pane
        hits = pane.files_list.findItems("c.txt", Qt.MatchEndsWith)
        assert hits, "merge must list c.txt"
        pane.files_list.setCurrentRow(pane.files_list.row(hits[0]))
        app.processEvents()
        diff = pane.diff_view.toPlainText()
        assert "@@" in diff and "+three" in diff
    finally:
        w.close()
        app.processEvents()


def test_git_branch_filter_narrows_rows(scene, repo):
    """Picking `feature` drops the merge + main-only commit from the list."""
    layout, sidecar = scene
    app, w = _window(layout, sidecar)
    try:
        w._open_git_dock(repo=repo)
        pane = w.git_pane
        idx = pane._branch_box.findData("feature")
        assert idx >= 0
        pane._branch_box.setCurrentIndex(idx)
        app.processEvents()
        assert len(pane._commits) == 3
        subjects = [c["subject"] for c in pane._commits]
        assert not any(s in ("merge feature", "third commit") for s in subjects)
    finally:
        w.close()
        app.processEvents()


def test_git_focus_follows_terminal_repo(scene, repo, tmp_path):
    """Focusing another terminal re-points the dock; viewers keep last."""
    import os
    import sys
    import time

    from kilim.terminal_pane import TerminalPane

    def norm(p):
        return os.path.normcase(p)

    layout, sidecar = scene
    app, w = _window(layout, sidecar)
    try:
        w._open_git_dock(repo=repo)
        assert norm(w.git_pane.repo) == norm(repo)
        root2 = tmp_path / "repo2"
        root2.mkdir()
        _git(root2, "init")
        w.bridge.call(lambda: w.bridge.core.spawn_term(
            "t2", "t2", sys.executable, ["-c", "import time; time.sleep(60)"],
            24, 80, 5000, cwd=str(root2)))
        pane2 = TerminalPane(w.bridge, "t2", w.terminal_theme)
        w.term_panes["t2"] = pane2
        try:
            deadline = time.time() + 10
            while norm(w.git_pane.repo or "") != norm(str(root2)):
                w._on_focus_changed(w.git_pane.graph, pane2.view)
                app.processEvents()
                if time.time() > deadline:
                    break
            assert norm(w.git_pane.repo) == norm(str(root2))
            # Viewer (and git) focus keeps the last terminal's repo.
            w._on_focus_changed(pane2.view, w.git_pane.graph)
            assert norm(w.git_pane.repo) == norm(str(root2))
            # Same-repo refocus is a no-op too.
            w._on_focus_changed(w.git_pane.graph, pane2.view)
            assert norm(w.git_pane.repo) == norm(str(root2))
        finally:
            w.term_panes.pop("t2", None)
            try:
                w.bridge.call(lambda: w.bridge.core.terminate_term("t2", 1.0))
            except Exception:  # noqa: BLE001, S110 — closing anyway
                pass
            pane2.setParent(None)
            pane2.deleteLater()
    finally:
        w.close()
        app.processEvents()


def test_git_follows_code_theme(scene, repo):
    """Theme switch re-papers the graph, lists, and message views."""
    from kilim import list_themes, theme_background
    from PySide6.QtGui import QColor, QPalette

    layout, sidecar = scene
    app, w = _window(layout, sidecar)
    try:
        w._open_git_dock(repo=repo)
        current = w.bridge.core.theme()
        other = next(
            n for n in list_themes()
            if theme_background(n) and theme_background(n) != theme_background(current)
        )
        w.apply_code_theme(other)
        paper = QColor(theme_background(other))
        assert w.git_pane.graph.palette().color(QPalette.Window) == paper
        assert w.git_pane.diff_view.palette().color(QPalette.Base) == paper
        assert w.git_pane.msg_view.palette().color(QPalette.Base) == paper
    finally:
        w.close()
        app.processEvents()


def test_git_colors_follow_theme(scene, repo):
    """Dark/light paper swaps the lane, pill, and status ink sets."""
    from kilim import list_themes, theme_background
    from PySide6.QtGui import QColor

    def lum(hexstr):
        c = QColor(hexstr)
        return 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()

    layout, sidecar = scene
    app, w = _window(layout, sidecar)
    try:
        w._open_git_dock(repo=repo)
        pane = w.git_pane
        papers = {n: theme_background(n) for n in list_themes()}
        dark = next(n for n, p in papers.items() if p and lum(p) < 0.4)
        light = next(n for n, p in papers.items() if p and lum(p) > 0.6)
        seen = {}
        for name in (dark, light):
            w.apply_code_theme(name)
            app.processEvents()
            paper = QColor(theme_background(name))
            seen[name] = (
                pane._status["M"], pane._status["D"],
                pane.graph._colors[0], pane.graph._pills["head"],
            )
            for ink in seen[name]:
                assert abs(lum(ink.name()) - lum(paper.name())) > 0.15, name
        assert seen[dark] != seen[light]
        # Message header: sha in the link accent, rest dimmed (two spans).
        doc = pane.msg_view.document()
        block = doc.begin().next()
        assert "·" in block.text()
        it, frags = block.begin(), []
        while not it.atEnd():
            frags.append(it.fragment())
            it += 1
        assert len(frags) == 2
        assert frags[0].charFormat().foreground().color() == pane._link
        assert frags[1].charFormat().foreground().color() == pane._dim
    finally:
        w.close()
        app.processEvents()


def test_git_inks_contrast_all_themes(scene):
    """Every dock ink passes contrast on every theme's paper.

    Status letters + body text hit WCAG AA (4.5); graphical inks
    (lanes, pills, dim, link) hit 3.0. Guards future themes too."""
    from kilim import list_themes, theme_background
    from PySide6.QtGui import QColor

    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    def luminance(q):
        return 0.2126 * lin(q.red()) + 0.7152 * lin(q.green()) + 0.0722 * lin(q.blue())

    def ratio(a, b):
        l1, l2 = luminance(a), luminance(b)
        if l1 < l2:
            l1, l2 = l2, l1
        return (l1 + 0.05) / (l2 + 0.05)

    layout, sidecar = scene
    app, w = _window(layout, sidecar)
    try:
        w._open_git_dock(repo=None)  # inks need no repo, zero git I/O
        pane = w.git_pane
        names = list_themes()
        assert len(names) >= 2
        for name in names:
            w.apply_code_theme(name)
            app.processEvents()
            paper = QColor(theme_background(name))
            text_inks = {f"status-{k}": v for k, v in pane._status.items()}
            text_inks["text"] = pane._ink
            for label, ink in text_inks.items():
                assert ratio(ink, paper) >= 4.5, f"{name} {label} {ink.name()}"
            gfx_inks = {"dim": pane._dim, "link": pane._link}
            gfx_inks.update({f"pill-{k}": v for k, v in pane.graph._pills.items()})
            gfx_inks.update({f"lane-{j}": c for j, c in enumerate(pane.graph._colors)})
            for label, ink in gfx_inks.items():
                assert ratio(ink, paper) >= 3.0, f"{name} {label} {ink.name()}"
    finally:
        w.close()
        app.processEvents()


def test_graph_paints_antialiased(monkeypatch):
    """The graph paint path enables AA for curves, dots, and text."""
    import kilim.git_pane as gp
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    seen = []
    real = gp.QPainter

    class Spy(real):
        def setRenderHint(self, hint, on=True):
            seen.append(hint)
            return super().setRenderHint(hint, on)

    monkeypatch.setattr(gp, "QPainter", Spy)
    view = gp.GraphView(lambda i: None)
    view.set_commits([
        {"sha": "m" * 40, "parents": ["a" * 40, "b" * 40],
         "author": "t", "date": "2026-01-01T00:00:00+00:00",
         "refs": "HEAD -> main", "subject": "merge"},
        {"sha": "a" * 40, "parents": [],
         "author": "t", "date": "2026-01-01T00:00:00+00:00",
         "refs": "", "subject": "first"},
        {"sha": "b" * 40, "parents": [],
         "author": "t", "date": "2026-01-01T00:00:00+00:00",
         "refs": "", "subject": "second"},
    ])
    view.resize(400, 200)
    view.grab()
    assert QPainter.Antialiasing in seen
    assert QPainter.TextAntialiasing in seen


def test_highlight_code_bridge_spans_tokens(scene):
    """highlight_code: syntect spans through the session theme."""
    layout, sidecar = scene
    app, w = _window(layout, sidecar)
    try:
        rows = w.bridge.core.highlight_code("py", 'def hello(name):\n    pass\n')
        assert len(rows) == 2
        first_fgs = {fg for _t, fg, _bg in rows[0]}
        assert len(rows[0]) >= 3 and len(first_fgs) >= 2
        plain = w.bridge.core.highlight_code("nope", "just text\n")
        assert "".join(t for t, _fg, _bg in plain[0]).rstrip("\n") == "just text"
    finally:
        w.close()
        app.processEvents()


def test_diff_highlights_code_spans(scene, repo):
    """The +def line paints marker + syntect spans (not one flat line)."""
    from PySide6.QtCore import Qt

    layout, sidecar = scene
    app, w = _window(layout, sidecar)
    try:
        w._open_git_dock(repo=repo)
        pane = w.git_pane
        idx = next(k for k, c in enumerate(pane._commits)
                   if c["subject"] == "third commit")
        pane._pick_commit(idx)
        app.processEvents()
        hits = pane.files_list.findItems("f.py", Qt.MatchEndsWith)
        assert hits, "third commit must list f.py"
        pane.files_list.setCurrentRow(pane.files_list.row(hits[0]))
        app.processEvents()
        doc = pane.diff_view.document()
        block = doc.begin()
        target = None
        while block.isValid():
            if block.text().startswith("+def hello"):
                target = block
                break
            block = block.next()
        assert target is not None
        it, count = target.begin(), 0
        while not it.atEnd():
            count += 1
            it += 1
        assert count >= 3, "marker + at least two code spans"
    finally:
        w.close()
        app.processEvents()


def test_git_focus_follows_live_cd(scene, repo, tmp_path):
    """Focus follows the shell's live dir, not its spawn dir."""
    import os
    import shutil
    import time

    from kilim.terminal_pane import TerminalPane

    if shutil.which("cmd.exe") is None:
        pytest.skip("cmd.exe required")

    def norm(p):
        return os.path.normcase(p)

    layout, sidecar = scene
    app, w = _window(layout, sidecar)
    try:
        w._open_git_dock(repo=repo)
        assert norm(w.git_pane.repo) == norm(repo)
        root2 = tmp_path / "repo2"
        root2.mkdir()
        _git(root2, "init")
        w.bridge.call(lambda: w.bridge.core.spawn_term(
            "t3", "t3", "cmd.exe", [], 24, 80, 5000, cwd=str(repo)))
        pane3 = TerminalPane(w.bridge, "t3", w.terminal_theme)
        w.term_panes["t3"] = pane3
        try:
            w.bridge.submit(lambda: w.bridge.core.write_term(
                "t3", ("cd /d " + str(root2).replace("/", "\\") + "\r").encode()))
            deadline = time.time() + 30
            while norm(w.git_pane.repo or "") != norm(str(root2)):
                w._on_focus_changed(w.git_pane.graph, pane3.view)
                app.processEvents()
                time.sleep(0.5)
                if time.time() > deadline:
                    break
            assert norm(w.git_pane.repo) == norm(str(root2))
        finally:
            w.term_panes.pop("t3", None)
            try:
                w.bridge.call(lambda: w.bridge.core.terminate_term("t3", 1.0))
            except Exception:  # noqa: BLE001, S110 — closing anyway
                pass
            pane3.setParent(None)
            pane3.deleteLater()
    finally:
        w.close()
        app.processEvents()


def test_git_close_clears_sidecar_flag(scene, repo):
    """Toggling off disposes the dock (no session pane) and persists shut."""
    layout, sidecar = scene
    app, w = _window(layout, sidecar)
    try:
        w._open_git_dock(repo=repo)
        assert "git" in w.pane_docks
        w.toggle_git_history()
        app.processEvents()
        assert "git" not in w.pane_docks and w.git_pane is None
        saved = json.loads(Path(sidecar).read_text(encoding="utf-8"))
        assert saved.get("git_open") is False
    finally:
        w.close()
        app.processEvents()


def test_git_restores_open_from_sidecar(scene, repo):
    """A sidecar left with git_open reopens the dock at launch on the repo."""
    layout, sidecar = scene
    Path(sidecar).write_text(
        json.dumps({"git_open": True, "git_repo": repo}), encoding="utf-8")
    app, w = _window(layout, sidecar)
    try:
        assert w.git_pane is not None and "git" in w.pane_docks
        assert os.path.normcase(w.git_pane.repo) == os.path.normcase(repo)
        assert len(w.git_pane._commits) == 5
    finally:
        w.close()
        app.processEvents()

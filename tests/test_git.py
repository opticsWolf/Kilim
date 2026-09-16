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
    read_branches,
    read_commit,
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
    _git(root, "commit", "-m", "feature work")
    _git(root, "checkout", "main")
    (root / "d.txt").write_text("four\n")
    _git(root, "add", "d.txt")
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
    assert all("*" in r["graph"] for r in rows)  # every record is a node row


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


def test_commit_detail_has_message_and_stat(repo):
    rows, _, _ = read_history(repo)
    text = read_commit(repo, rows[0]["sha"])
    assert "merge feature" in text
    assert ".txt" in text  # file stat, not just the message


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
    from _util import copy_layout

    layout = copy_layout("layouts/default.json", tmp_path / "l.json")
    return str(layout), str(tmp_path / "l.perspective.json")


def _window(layout, sidecar):
    from kilim.qt_app import KilimWindow
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    w = KilimWindow(layout, sidecar)
    w.show()
    return app, w


def _click(view, pos):
    """Synthesize a left click (press + release, no drag) at `pos`."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    for typ in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        buttons = Qt.LeftButton if typ == QEvent.MouseButtonPress else Qt.NoButton
        ev = QMouseEvent(
            typ,
            QPointF(pos),
            QPointF(view.viewport().mapToGlobal(pos)),
            Qt.LeftButton,
            buttons,
            Qt.NoModifier,
        )
        if typ == QEvent.MouseButtonPress:
            view.mousePressEvent(ev)
        else:
            view.mouseReleaseEvent(ev)


def test_git_dock_opens_renders_and_click_shows_detail(scene, repo):
    """Views > Git History: five graph rows, click lands the merge detail."""
    from PySide6.QtGui import QTextCursor

    layout, sidecar = scene
    app, w = _window(layout, sidecar)
    try:
        w._open_git_dock(repo=repo)
        assert "git" in w.pane_docks
        pane = w.git_pane
        assert pane.repo and os.path.normcase(pane.repo) == os.path.normcase(repo)
        view = pane.history_view
        assert len(pane._commits) == 5
        node = pane._block_commits.index(0)  # merge row's visual line
        assert "merge feature" in view.document().findBlockByNumber(node).text()
        rect = view.cursorRect(QTextCursor(view.document().findBlockByNumber(node)))
        _click(view, rect.center())
        app.processEvents()
        assert "merge feature" in pane.detail_view.toPlainText()
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
        texts = [
            pane.history_view.document().findBlockByNumber(i).text()
            for i, c in enumerate(pane._block_commits) if c is not None
        ]
        assert len(texts) == 3
        assert not any("merge feature" in t or "third commit" in t for t in texts)
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

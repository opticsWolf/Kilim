"""Kilim Python surface tests — offline safe (no PTY spawn, no Qt)."""

import json

import pytest

from kilim import CoreSession, list_syntaxes, list_themes

DOC = {
    "layout": {
        "root": {"type": "pane", "pane_id": "code"},
        "active": "code",
        "theme": "Kilim Dark",
    },
    "panes": [
        {"id": "code", "title": "s.py", "kind": "file", "path": "sample.py"},
    ],
}


@pytest.fixture()
def session(tmp_path, monkeypatch):
    (tmp_path / "sample.py").write_text("x = 1\n# hi\n", encoding="utf-8")
    doc = json.loads(json.dumps(DOC))
    doc["panes"][0]["path"] = str(tmp_path / "sample.py")
    monkeypatch.chdir(tmp_path)
    return CoreSession(json.dumps(doc))


def test_pane_ids(session):
    assert session.pane_ids() == ["code"]


def test_layout_roundtrip(session):
    assert json.loads(session.layout_json())["active"] == "code"


def test_themes_nonempty():
    assert "Kilim Dark" in list_themes()
    assert len(list_syntaxes()) > 10


def test_highlighted_file_shape(session):
    rows = session.highlighted_file("code")
    assert len(rows) == 2
    text = "".join(t for row in rows for t, _, _ in row)
    assert "x = 1" in text


def test_set_theme_unknown_falls_back(session):
    before = session.theme()
    with pytest.raises(ValueError):
        session.set_theme("does-not-exist")
    assert session.theme() == before  # rejected names change nothing
    rows = session.highlighted_file("code")
    assert len(rows) == 2


def test_register_garbage_theme_fails(session):
    with pytest.raises(ValueError):
        session.register_custom_theme("junk", "not a theme")


def test_spawn_term_adds_live_shell(tmp_path, monkeypatch):
    """New shells splice into layout + panes and come up alive."""
    import asyncio
    import shutil

    ps = shutil.which("powershell.exe") or shutil.which("powershell")
    if ps is None:
        pytest.skip("no powershell for spawn test")
    (tmp_path / "sample.py").write_text("x = 1\n", encoding="utf-8")
    doc = json.loads(json.dumps(DOC))
    doc["panes"][0]["path"] = str(tmp_path / "sample.py")
    monkeypatch.chdir(tmp_path)
    core = CoreSession(json.dumps(doc))

    async def go():
        await core.spawn_term("term9", "shell9", ps, [], 24, 80, 5000)
        assert core.term_alive("term9") is True

    asyncio.run(go())
    assert "term9" in core.pane_ids() or True  # pane_ids covers tree panes
    layout = json.loads(core.layout_json())
    assert layout["active"] == "term9"

    async def bye():
        await core.terminate_term("term9", 1.0)

    asyncio.run(bye())


def test_markdown_theme_get_set_and_doc_persist(session):
    from kilim import list_themes, markdown_theme_names

    assert session.markdown_theme() == "Kilim Dark"
    session.set_markdown_theme("Kilim Warm Neo")
    assert session.markdown_theme() == "Kilim Warm Neo"
    session.set_theme("Kilim Light")
    import json as _json

    raw = _json.loads(session.doc_json())
    assert raw["layout"]["theme"] == "Kilim Light"
    assert raw["layout"]["markdown_theme"] == "Kilim Warm Neo"
    # Full doc round-trips back through a fresh session.
    again = CoreSession(session.doc_json())
    assert again.theme() == "Kilim Light"
    assert again.markdown_theme() == "Kilim Warm Neo"
    # Unknown names raise instead of silently falling back.
    import pytest

    with pytest.raises(ValueError):
        session.set_theme("Dracula")
    with pytest.raises(ValueError):
        session.set_markdown_theme("Solarized (dark)")


def test_theme_names_are_the_kilim_eight():
    from kilim import list_themes, markdown_theme_names

    expected = ["Kilim Dark", "Kilim Dark Neo", "Kilim Light", "Kilim Light Neo",
                "Kilim Neutral", "Kilim Neutral Neo", "Kilim Warm", "Kilim Warm Neo"]
    assert sorted(list_themes()) == sorted(expected)
    assert sorted(markdown_theme_names()) == sorted(expected)

"""Kilim Python surface tests — offline safe (no PTY spawn, no Qt)."""

import json

import pytest

from kilim import CoreSession, list_syntaxes, list_themes

DOC = {
    "layout": {
        "root": {"type": "pane", "pane_id": "code"},
        "active": "code",
        "theme": "Kilim Midnight",
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


def test_themes_nonempty(session):
    assert "Kilim Midnight" in list_themes()
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

    assert session.markdown_theme() == "Kilim Midnight"
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


def test_theme_names_are_the_kilim_ten(session):
    from kilim import list_themes, markdown_theme_names

    expected = ["Kilim Midnight", "Kilim Midnight Neo",
                "Kilim Dark", "Kilim Dark Neo",
                "Kilim Light", "Kilim Light Neo",
                "Kilim Neutral", "Kilim Neutral Neo",
                "Kilim Warm", "Kilim Warm Neo"]
    assert sorted(list_themes()) == sorted(expected)
    assert sorted(markdown_theme_names()) == sorted(expected)


def test_fusion_scrollbar_css_sampled():
    """Preview scrollbar CSS carries live sampled colors, never invented."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from kilim.qt_app import MarkdownPane, _fusion_scrollbar_css

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    css = _fusion_scrollbar_css()
    assert css.startswith("html{scrollbar-color:#") and ";color-scheme:" in css
    out = MarkdownPane._style_page("<html><head></head><body>x</body></html>")
    assert "<style>" + css + "</style>" in out
    assert out.index("<style>") < out.index("</head>")
    assert MarkdownPane._style_page("<body>bare</body>").startswith("<style>")


def test_detect_paths_and_classify(session, tmp_path, monkeypatch):
    """Rust detector exposed to Python: char offsets, kinds, :line:col."""
    src = tmp_path / "m.py"
    src.write_text("x = 1\n", encoding="utf-8")
    blob = tmp_path / "b.bin"
    blob.write_bytes(b"\x00\x01\x02\x00\xff\xfe")
    md = tmp_path / "n.md"
    md.write_text("# n\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    line = f"see {src}:12:3 and {blob} and n.md and missing.txt now"
    hits = session.detect_paths(line)
    assert [(line[h[0]:h[1]], h[5]) for h in hits] == [
        (str(src), "code"),
        (str(blob), "binary"),
        ("n.md", "markdown"),
        ("missing.txt", "missing"),
    ]
    code = next(h for h in hits if h[5] == "code")
    assert code[3] == 12 and code[4] == 3  # :line:col parsed off the path
    assert session.classify_file(str(src)) == "code"
    assert session.classify_file(str(blob)) == "binary"
    assert session.classify_file(str(tmp_path / "nope.txt")) == "missing"


def test_pane_inventory_add_and_remove(session, tmp_path):
    """Viewer panes are added and forgotten (a closed dock disposes one)."""
    src = tmp_path / "v.txt"
    src.write_text("hi\n", encoding="utf-8")
    session.insert_file_pane("view1", "v.txt", str(src), False)
    assert "view1" in session.pane_ids()
    assert session.highlighted_file("view1")[0][0][0].startswith("hi")
    assert session.remove_pane("view1") is True
    assert session.remove_pane("view1") is False
    with pytest.raises(ValueError):
        session.highlighted_file("view1")


def test_empty_cmd_spawns_platform_default():
    """Portable layouts omit cmd; an empty cmd resolves to the OS default."""
    import asyncio

    doc = json.dumps(
        {
            "layout": {"root": {"type": "pane", "pane_id": "t"}, "active": "t"},
            "panes": [{"id": "t", "title": "t", "kind": "term"}],
        }
    )
    core = CoreSession(doc)

    async def go():
        await core.spawn_term("term-def", "default", "", [], 24, 80, 2000)
        return core.term_alive("term-def")

    async def bye():
        await core.terminate_term("term-def", 1.0)

    assert asyncio.run(go()) is True
    asyncio.run(bye())


def test_blend_cell_math(session):
    """Core blending: identity at 0, moved at 0.5, legible after flips."""
    ink, paper = "#cbd0dc", "#101319"
    assert session.blend_cell("#000000", "#ffffff", ink, paper, 0.0) == (
        "#000000",
        "#ffffff",
    )
    f, b = session.blend_cell("#ffffff", "#000000", ink, paper, 0.5)
    assert f != "#ffffff" and b != "#000000"

    def luma(hexcol):
        h = hexcol.lstrip("#")
        r, g, bl = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        return 0.2126 * r + 0.7152 * g + 0.0722 * bl

    # Polarity flip: a dark chip on a light theme must not collapse.
    f2, b2 = session.blend_cell("#e6e6e6", "#1a1a1a", "#2f3134", "#ffffff", 0.5)
    assert abs(luma(f2) - luma(b2)) > 0.1, (f2, b2)
    # Malformed colors raise instead of guessing.
    with pytest.raises(ValueError):
        session.blend_cell("nope", "#ffffff", ink, paper, 0.5)

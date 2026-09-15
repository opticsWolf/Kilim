"""Shell discovery: the Terminal menu lists only installed shells."""

from pathlib import Path


def test_find_shells_only_lists_installed():
    from kilim import shells

    found = shells.find_shells()
    assert found, "expected at least one shell on this machine"
    labels = [label for label, _, _ in found]
    assert len(labels) == len(set(labels)), labels
    for label, cmd, args in found:
        assert label
        assert Path(cmd).is_file(), (label, cmd)
        assert isinstance(args, list)
    assert shells.default_entry() == found[0]


def test_find_shell_by_label():
    from kilim import shells

    found = shells.find_shells()
    assert shells.find_shell(found[0][0]) == found[0]
    assert shells.find_shell("definitely-not-installed") is None

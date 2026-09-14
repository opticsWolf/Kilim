"""Suite hygiene: several tests open the repo's default layout (spawning real
shells, switching themes, closing windows). Those runs rewrite
layouts/default.json (theme persistence) and
layouts/default.perspective.json (dock geometry + pins on close).
Back both up once per session and restore at the end so the suite never
leaks state into the repo — or into later tests via restored pins."""

import shutil

import pytest

REPO_FILES = [
    "layouts/default.json",
    "layouts/default.perspective.json",
]


@pytest.fixture(scope="session", autouse=True)
def _preserve_repo_layouts():
    backups = {}
    for path in REPO_FILES:
        try:
            with open(path, "rb") as f:
                backups[path] = f.read()
        except OSError:
            backups[path] = None
    yield
    for path, content in backups.items():
        try:
            if content is None:
                continue
            with open(path, "wb") as f:
                f.write(content)
        except OSError:
            pass

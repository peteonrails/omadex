"""Installing and removing the overlay and its launcher entry.

The entry is installed with the overlay rather than by the package, so the
two have to arrive and leave together — an entry left behind after a removal
offers an application that no longer opens.
"""
from __future__ import annotations

import pytest

from omadex import cli

ENTRY = "applications/omadex.desktop"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A machine whose config, data and plugin source are all under tmp_path."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    source = tmp_path / "share" / "omadex" / "plugin"
    source.mkdir(parents=True)
    for name in ("manifest.json", "OmaDex.qml", "omadex.desktop"):
        (source / name).write_text(f"{name} contents", encoding="utf-8")
    monkeypatch.setattr(cli, "_plugin_source", lambda: source)
    return tmp_path


def test_installing_the_overlay_also_lists_it_in_the_launcher(home) -> None:
    assert cli.main(["plugin", "install"]) == 0

    plugin = home / "config" / "omarchy" / "plugins" / "io.github.peteonrails.omadex"
    assert (plugin / "OmaDex.qml").is_file()
    assert (home / "data" / ENTRY).read_text(encoding="utf-8") == (
        "omadex.desktop contents")


def test_removing_the_overlay_takes_the_entry_with_it(home) -> None:
    cli.main(["plugin", "install"])

    assert cli.main(["plugin", "remove"]) == 0

    assert not (home / "data" / ENTRY).exists()


def test_an_install_without_a_desktop_file_still_installs_the_overlay(home) -> None:
    """Older layouts have no entry to copy, which is not a failure."""
    (home / "share" / "omadex" / "plugin" / "omadex.desktop").unlink()

    assert cli.main(["plugin", "install"]) == 0
    assert not (home / "data" / ENTRY).exists()


def test_removing_what_was_never_installed_is_not_an_error(home) -> None:
    assert cli.main(["plugin", "remove"]) == 0

"""Opening a contact in the application its record came from."""
from __future__ import annotations

import pytest

from omadex import launch
from omadex.config import DEFAULTS, Settings
from omadex.launch import LaunchError, target_for


@pytest.fixture
def settings():
    return Settings(DEFAULTS)


@pytest.fixture(autouse=True)
def installed(monkeypatch):
    """Pretend the usual tools exist; the suite must not need them."""
    monkeypatch.setattr(
        launch.shutil, "which",
        lambda name: f"/usr/bin/{name}" if name != "gnome-contacts" else None,
    )
    monkeypatch.setenv("TERMINAL", "xdg-terminal-exec")


def identity(**overrides) -> dict:
    base = {"name": "Alice Example", "emails": ["mailto:alice@example.com"],
            "phones": ["tel:5551234567"], "key": "mailto:alice@example.com"}
    return {**base, **overrides}


def record(source, **overrides) -> dict:
    base = {"source": source, "source_id": "0", "name": "Alice Example",
            "emails": ["alice@example.com"], "phones": ["5551234567"],
            "postal": []}
    return {**base, **overrides}


def test_neomutt_is_given_the_address_as_its_own_argument(settings) -> None:
    found = target_for("neomutt", identity(), record("neomutt"), settings)

    assert found.argv == ["omarchy-launch-terminal", "neomutt",
                          "alice@example.com"]
    assert found.preloads_contact


def test_notmuch_passes_the_address_through_argv_not_the_script(settings) -> None:
    """The query text must never contain the address."""
    found = target_for("notmuch", identity(), record("notmuch"), settings)

    script = found.argv[3]
    assert "alice@example.com" not in script
    assert '"$1"' in script
    assert found.argv[-1] == "alice@example.com"


def test_abook_is_marked_as_opening_the_application_only(settings) -> None:
    """abook has no option to open at a record, and must not pretend to."""
    found = target_for("abook", identity(), record("abook"), settings)

    assert found.preloads_contact is False
    assert "--datafile" in found.argv


def test_a_source_needing_an_address_the_contact_lacks_is_refused(settings) -> None:
    contact = identity(emails=[], phones=[])

    with pytest.raises(LaunchError, match="email"):
        target_for("neomutt", contact,
                   record("neomutt", emails=[], phones=[]), settings)


def test_an_uninstalled_application_is_refused_before_launching(settings) -> None:
    with pytest.raises(LaunchError, match="not installed"):
        target_for("eds", identity(), record("eds"), settings)


def test_a_source_with_no_launch_command_is_refused(settings) -> None:
    bare = Settings({"sources": {"custom": {"enabled": True}}})

    with pytest.raises(LaunchError, match="no launch command"):
        target_for("custom", identity(), record("custom"), bare)


def test_neomutt_gets_a_fresh_terminal_rather_than_a_focused_one() -> None:
    """Focusing an existing neomutt would silently drop the address."""
    found = target_for("neomutt", identity(), record("neomutt"), Settings(DEFAULTS))

    assert found.argv[0] == "omarchy-launch-terminal"
    assert found.argv[-1] == "alice@example.com"


def test_vdir_opens_the_vcard_file_the_record_came_from(tmp_path, settings) -> None:
    collection = tmp_path / "default"
    collection.mkdir()
    card = collection / "alice.vcf"
    card.write_text("BEGIN:VCARD\nFN:Alice\nEND:VCARD\n", encoding="utf-8")
    configured = Settings({
        **DEFAULTS,
        "sources": {**DEFAULTS["sources"],
                    "vdir": {**DEFAULTS["sources"]["vdir"], "path": str(tmp_path)}},
    })

    found = target_for("vdir", identity(),
                       record("vdir", source_id="alice:0"), configured)

    assert found.argv == ["uwsm-app", "--", "xdg-open", str(card)]
    assert found.preloads_contact


def test_a_missing_vcard_file_is_refused_rather_than_opened_empty(
    tmp_path, settings
) -> None:
    configured = Settings({
        **DEFAULTS,
        "sources": {**DEFAULTS["sources"],
                    "vdir": {**DEFAULTS["sources"]["vdir"], "path": str(tmp_path)}},
    })

    with pytest.raises(LaunchError, match="file"):
        target_for("vdir", identity(),
                   record("vdir", source_id="gone:0"), configured)


def test_a_source_is_labelled_for_people_but_keyed_for_machines() -> None:
    from omadex.config import label, source_for_label

    assert label("blueferry") == "iPhone"
    assert label("abook") == "abook"
    # Whatever a person saw in the interface is what they will type back.
    assert source_for_label("iPhone") == "blueferry"
    assert source_for_label("iphone") == "blueferry"
    assert source_for_label("blueferry") == "blueferry"
    assert source_for_label("nonsense") is None


def test_launch_uses_omarchy_helpers_not_a_bare_spawn(settings) -> None:
    """A bare xdg-terminal-exec does not survive uwsm; the helpers wrap it."""
    found = target_for("abook", identity(), record("abook"), settings)

    assert found.argv[0] == "omarchy-launch-or-focus-tui"
    assert "--app-id=abook" in found.argv


def test_the_application_is_checked_not_the_launcher(settings, monkeypatch) -> None:
    """Every Omarchy helper exists whether or not the app it starts does."""
    monkeypatch.setattr(
        launch.shutil, "which",
        lambda name: None if name == "abook" else f"/usr/bin/{name}",
    )

    with pytest.raises(LaunchError, match="abook is not installed"):
        target_for("abook", identity(), record("abook"), settings)


def test_the_launcher_runs_detached_from_omadex(settings, monkeypatch) -> None:
    """omadex must not own or outlive the application it starts."""
    captured = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs

    monkeypatch.setattr(launch.subprocess, "Popen", FakePopen)

    launch.open_source("abook", identity(), record("abook"), settings)

    assert captured["kwargs"]["start_new_session"] is True
    assert captured["argv"][0] == "omarchy-launch-or-focus-tui"

"""Source readiness, and the onboarding decision that depends on it."""
from __future__ import annotations

import json
import subprocess

import pytest

from omadex import readiness
from omadex.config import DEFAULTS, Settings
from omadex.readiness import (
    BLOCKED,
    EMPTY,
    MISSING,
    READY,
    Readiness,
    check_abook,
    check_blueferry,
    check_notmuch,
    check_vdir,
    needs_onboarding,
)

BLUEFERRY_XML = (
    '<node><interface name="io.weirdware.BlueFerry.Messages1">'
    '<method name="FindContacts"><arg name="query" type="s"/></method>'
    "{extra}</interface></node>"
)
LIST_CONTACTS = '<method name="ListContacts"><arg name="offset" type="u"/></method>'


def settings_with(**sources) -> Settings:
    merged = {name: {**DEFAULTS["sources"][name], **changes}
              for name, changes in sources.items()}
    return Settings({**DEFAULTS, "sources": {**DEFAULTS["sources"], **merged}})


def test_a_missing_address_book_says_what_to_install(tmp_path) -> None:
    found = check_abook(settings_with(abook={"path": str(tmp_path / "nope")}))

    assert found.state == MISSING
    assert not found.usable
    assert "abook" in found.hint


def test_an_unpatched_blueferry_backend_is_named_as_the_problem(monkeypatch) -> None:
    """The failure that looks like nothing is wrong: paired phone, no contacts."""
    monkeypatch.setattr(readiness.shutil, "which", lambda _name: "/usr/bin/blueferry")
    monkeypatch.setattr(
        readiness, "_introspect_blueferry",
        lambda: BLUEFERRY_XML.format(extra=""),
        raising=False,
    )

    found = check_blueferry(Settings(DEFAULTS))

    assert found.state == BLOCKED
    assert "ListContacts" in found.detail
    assert "fork" in found.hint


def test_a_patched_backend_is_ready(monkeypatch) -> None:
    monkeypatch.setattr(readiness.shutil, "which", lambda _name: "/usr/bin/blueferry")
    monkeypatch.setattr(
        readiness, "_introspect_blueferry",
        lambda: BLUEFERRY_XML.format(extra=LIST_CONTACTS),
        raising=False,
    )

    assert check_blueferry(Settings(DEFAULTS)).state == READY


def test_blueferry_absent_entirely_points_at_the_fork(monkeypatch) -> None:
    monkeypatch.setattr(readiness.shutil, "which", lambda _name: None)

    found = check_blueferry(Settings(DEFAULTS))

    assert found.state == MISSING
    assert "fork" in found.hint


def test_notmuch_exiting_zero_with_no_database_is_not_ready(monkeypatch) -> None:
    """An unconfigured notmuch exits 0 and prints nothing."""
    monkeypatch.setattr(readiness.shutil, "which", lambda _name: "/usr/bin/notmuch")
    monkeypatch.setattr(
        readiness.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""),
    )

    assert check_notmuch(Settings(DEFAULTS)).state == BLOCKED


def test_an_empty_vdir_is_usable_but_not_ready(tmp_path) -> None:
    found = check_vdir(settings_with(vdir={"path": str(tmp_path)}))

    assert found.state == EMPTY
    assert found.usable          # configured correctly, simply not synced yet
    assert "vdirsyncer sync" in found.hint


@pytest.mark.parametrize("states,onboarding", [
    ([READY, MISSING, MISSING], False),
    ([EMPTY, MISSING], True),        # reachable but nothing to show is not enough
    ([MISSING, BLOCKED], True),
    ([], True),
])
def test_onboarding_is_shown_only_when_nothing_can_supply_a_contact(
    states, onboarding
) -> None:
    checks = [Readiness(f"s{index}", state, "") for index, state in enumerate(states)]

    assert needs_onboarding(checks) is onboarding


def test_a_saved_setting_does_not_freeze_todays_defaults(tmp_path, monkeypatch) -> None:
    """Otherwise a later version's better default never reaches anyone."""
    from omadex import config as config_module

    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "settings.json")

    config_module.update_source("abook", enabled=False)

    written = json.loads((tmp_path / "settings.json").read_text())
    assert written == {"sources": {"abook": {"enabled": False}}}
    # The default is still supplied on read, not shadowed by a stored copy.
    assert config_module.load().option("abook", "launch")[0].startswith("omarchy")

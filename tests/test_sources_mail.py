"""neomutt and notmuch adapters.

Neither tool is exercised for real here: the suite must pass on a machine with
no aliases file and no mail index, which is exactly the machine this was
written on.
"""
from __future__ import annotations

import pytest

from omadex.sources.neomutt import load_neomutt
from omadex.sources.notmuch import is_machine_address, load_notmuch


def fake_notmuch(output: str):
    def runner(_argv):
        return output
    return runner


def test_aliases_become_records_with_names(tmp_path) -> None:
    config = tmp_path / "neomuttrc"
    config.write_text(
        "# a comment\n"
        "set alias_file = ~/.neomutt-aliases\n"
        "alias ae Alice Example <alice@example.com>\n"
        "alias bob bob@example.com\n",
        encoding="utf-8",
    )

    records = load_neomutt((config,))

    assert [(record.name, record.emails) for record in records] == [
        ("Alice Example", ("alice@example.com",)),
        ("", ("bob@example.com",)),
    ]
    assert {record.source for record in records} == {"neomutt"}


def test_a_group_alias_becomes_one_record_per_person(tmp_path) -> None:
    config = tmp_path / "neomuttrc"
    config.write_text(
        "alias team Alice <a@x.com>, Bob <b@y.com>\n", encoding="utf-8"
    )

    records = load_neomutt((config,))

    assert [record.name for record in records] == ["Alice", "Bob"]


def test_a_comma_inside_a_name_does_not_split_the_person(tmp_path) -> None:
    config = tmp_path / "neomuttrc"
    config.write_text(
        'alias ae Example, Alice <alice@example.com>\n', encoding="utf-8"
    )

    records = load_neomutt((config,))

    assert len(records) == 1
    assert records[0].emails == ("alice@example.com",)


def test_a_missing_neomutt_config_is_empty_not_fatal(tmp_path) -> None:
    assert load_neomutt((tmp_path / "nothing-here",)) == []


def test_correspondents_below_the_floor_are_left_out() -> None:
    output = (
        "  12\tAlice Example <alice@example.com>\n"
        "   1\tStranger <stranger@example.com>\n"
        "   3\tBob Other <bob@example.com>\n"
    )

    records = load_notmuch(runner=fake_notmuch(output))

    assert [record.name for record in records] == ["Alice Example", "Bob Other"]


def test_machine_senders_never_become_contacts() -> None:
    output = (
        " 400\tGitHub <noreply@github.com>\n"
        "  90\t<mailer-daemon@example.com>\n"
        "  12\tAlice Example <alice@example.com>\n"
    )

    records = load_notmuch(runner=fake_notmuch(output))

    assert [record.emails for record in records] == [("alice@example.com",)]


@pytest.mark.parametrize("address,machine", [
    ("noreply@github.com", True),
    ("no-reply@example.com", True),
    ("notifications@slack.com", True),
    ("reply+abc123@example.com", True),
    ("alice@example.com", False),
    ("norman@example.com", False),
])
def test_machine_address_detection(address, machine) -> None:
    assert is_machine_address(address) is machine


def test_notmuch_failure_is_raised_for_the_loader_to_report() -> None:
    def failing(_argv):
        raise RuntimeError("could not locate database")

    with pytest.raises(RuntimeError, match="database"):
        load_notmuch(runner=failing)

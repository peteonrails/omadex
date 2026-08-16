"""What the database file gives up to someone who reads it.

These tests are about the file on disk, not the API. The API returning the
right answers says nothing about what is legible in the bytes behind it, and
the first attempt at encrypting this store passed every functional test while
leaving every address in the clear in its lookup columns.
"""
from __future__ import annotations

import sqlite3

import pytest

from omadex import store as store_module
from omadex.crypto import KeyRing
from omadex.models import VERDICT_DISTINCT, Identity, Override, RawRecord
from tests.conftest import TEST_KEY

SECRETS = ("Grelling", "quine@nelson.example", "5095550172", "12 Paradox Lane")


def on_disk(path) -> bytes:
    """Everything the database occupies, not just the main file.

    In WAL mode a recent write lives in the sidecar until a checkpoint, so
    reading only contacts.sqlite would let plaintext pass unnoticed.
    """
    return b"".join(
        sidecar.read_bytes()
        for sidecar in (path, *(path.with_name(path.name + suffix)
                                for suffix in ("-wal", "-shm")))
        if sidecar.exists()
    )


def person(name, keys, address=None) -> Identity:
    record = RawRecord(
        source="abook", source_id="the-source-id", name=name,
        emails=tuple(k.removeprefix("mailto:") for k in keys if "@" in k),
        postal=(address,) if address else (),
    )
    return Identity(key=sorted(keys)[0], display_name=name,
                    records=[record], keys=set(keys))


@pytest.fixture
def sealed(tmp_path):
    path = tmp_path / "contacts.sqlite"
    with store_module.open_store(path, keyring=KeyRing(TEST_KEY)) as opened:
        yield opened, path


def test_no_part_of_a_contact_is_legible_in_the_file(sealed) -> None:
    store, path = sealed
    store.replace([person(SECRETS[0], [f"mailto:{SECRETS[1]}",
                                       f"tel:{SECRETS[2]}"], SECRETS[3])], [])

    raw = on_disk(path)
    for secret in SECRETS:
        assert secret.encode() not in raw, f"{secret} is readable on disk"


def test_a_lookup_column_holds_a_digest_not_an_address(sealed) -> None:
    store, path = sealed
    store.replace([person("Grelling", [f"mailto:{SECRETS[1]}"])], [])

    with sqlite3.connect(path) as db:
        stored = [row[0] for row in db.execute("SELECT address_key FROM addresses")]
    assert stored and all(SECRETS[1] not in value for value in stored)
    # Still deterministic, or the joins it exists for would not work.
    assert stored == [KeyRing(TEST_KEY).blind(f"mailto:{SECRETS[1]}")]


def test_a_different_key_cannot_match_the_same_address() -> None:
    other = KeyRing(bytes(32))
    assert KeyRing(TEST_KEY).blind("mailto:a@b.example") != other.blind(
        "mailto:a@b.example")


def test_an_unencrypted_store_is_replaced_and_decisions_are_kept(tmp_path) -> None:
    path = tmp_path / "contacts.sqlite"
    decision = Override("mailto:a@x.example", "mailto:b@y.example", VERDICT_DISTINCT)
    with store_module.open_store(path, encrypt=False) as plain:
        plain.set_override(decision)
        plain.replace([person("Grelling", [f"mailto:{SECRETS[1]}"])], [])
    assert SECRETS[1].encode() in on_disk(path)

    with store_module.open_store(path, keyring=KeyRing(TEST_KEY)) as reopened:
        assert reopened.overrides() == [decision]
        # The people are gone until the next sync rebuilds them, which is the
        # trade for not leaving the old rows behind in the file.
        assert reopened.counts()["identities"] == 0

    assert SECRETS[1].encode() not in on_disk(path)


def test_an_already_encrypted_store_is_left_alone(sealed) -> None:
    store, path = sealed
    store.replace([person("Grelling", [f"mailto:{SECRETS[1]}"])], [])

    with store_module.open_store(path, keyring=KeyRing(TEST_KEY)) as reopened:
        assert reopened.counts()["identities"] == 1


def test_pages_are_alphabetical_even_though_the_name_column_is_not(store) -> None:
    names = ["Zeno", "aristotle", "Mill", "boole"]
    store.replace([person(name, [f"mailto:{name}@x.example"]) for name in names], [])

    first = store.list(offset=0, limit=2)
    second = store.list(offset=2, limit=2)

    assert [p["name"] for p in first + second] == ["aristotle", "boole", "Mill", "Zeno"]

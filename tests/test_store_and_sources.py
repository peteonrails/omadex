"""Store durability and source parsing."""
from __future__ import annotations

import pytest

from omadex.models import VERDICT_DISTINCT, Identity, Override, RawRecord, ReviewItem
from omadex.normalize import email_key, names_agree, phone_key
from omadex.sources.abook import load_abook


def identity(name, keys, sources=("abook",)) -> Identity:
    records = [RawRecord(source, "0", name) for source in sources]
    return Identity(key=sorted(keys)[0], display_name=name,
                    records=records, keys=set(keys))


def test_a_sync_rebuilds_people_but_never_clears_decisions(store) -> None:
    store.set_override(Override("mailto:a@x.com", "mailto:b@y.com", VERDICT_DISTINCT))
    store.replace([identity("Alice", ["mailto:a@x.com"])], [])

    store.replace([identity("Bob", ["mailto:b@y.com"])], [])

    assert store.counts()["identities"] == 1
    assert len(store.overrides()) == 1


def test_search_matches_name_prefix_and_address(store) -> None:
    store.replace([
        identity("Alice Example", ["mailto:alice@example.com", "tel:5551234567"]),
        identity("Bob Other", ["mailto:bob@other.example"]),
    ], [])

    assert [found["name"] for found in store.search("ali")] == ["Alice Example"]
    assert [found["name"] for found in store.search("5551234567")] == ["Alice Example"]
    assert store.search("nobody") == []


def test_search_ignores_fts_operators_rather_than_erroring(store) -> None:
    store.replace([identity("Alice Example", ["mailto:alice@example.com"])], [])

    assert [found["name"] for found in store.search('alice OR "')] == ["Alice Example"]
    assert store.search("*") == []


def test_get_resolves_by_any_of_a_persons_addresses(store) -> None:
    store.replace([
        identity("Alice Example", ["mailto:alice@example.com", "tel:5551234567"])
    ], [])

    assert store.get("tel:5551234567")["name"] == "Alice Example"
    assert store.get("mailto:alice@example.com")["name"] == "Alice Example"
    assert store.get("tel:5550000000") is None


def test_review_items_survive_a_round_trip(store) -> None:
    store.replace([], [ReviewItem(
        "mailto:a@x.com", "mailto:b@y.com", "Alice", "Bob",
        "tel:5551112222", "shared phone, names disagree",
    )])

    held = store.review_items(10)

    assert held[0]["left_name"] == "Alice"
    assert held[0]["shared"] == "tel:5551112222"


def test_abook_entries_collect_every_phone_field(tmp_path) -> None:
    path = tmp_path / "addressbook"
    path.write_text(
        "# abook addressbook file\n\n[format]\nprogram=abook\nversion=0.6.1\n\n"
        "[0]\nname=Alice Example\nemail=alice@example.com,alice@work.example\n"
        "phone=555-111-2222\nmobile=555-333-4444\nworkphone=555-555-6666\n\n"
        "[1]\nname=Bob Other\n",
        encoding="utf-8",
    )

    records = load_abook(path)

    assert len(records) == 2
    assert records[0].emails == ("alice@example.com", "alice@work.example")
    assert records[0].phones == ("555-111-2222", "555-333-4444", "555-555-6666")
    assert records[1].name == "Bob Other"


def test_missing_abook_is_empty_not_fatal(tmp_path) -> None:
    assert load_abook(tmp_path / "nothing-here") == []


@pytest.mark.parametrize("raw,expected", [
    ("+1 (555) 123-4567", "tel:5551234567"),
    ("15551234567", "tel:5551234567"),
    ("555-123-4567", "tel:5551234567"),
    ("5551234", "tel:5551234"),
    ("123", None),
    ("Mom", None),
])
def test_phone_keys_ignore_formatting(raw, expected) -> None:
    assert phone_key(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("Alice@Example.COM", "mailto:alice@example.com"),
    ("  alice@example.com  ", "mailto:alice@example.com"),
    ("not an email", None),
    ("@example.com", None),
])
def test_email_keys_are_lowercased(raw, expected) -> None:
    assert email_key(raw) == expected


@pytest.mark.parametrize("left,right,agrees", [
    ("Alice Example", "Alice", True),
    ("Mr Alex Bartholomaus", "Phil Manno", False),
    ("Kevin and Molly Burns", "Kevin Burns", True),
    ("Alice Example", "", True),
    ("Dr Smith", "Mr Jones", False),
])
def test_name_agreement_needs_a_shared_identifying_word(left, right, agrees) -> None:
    assert names_agree(left, right) is agrees


def test_abook_postal_fields_are_composed_into_one_line(tmp_path) -> None:
    path = tmp_path / "addressbook"
    path.write_text(
        "[0]\nname=Alice Example\naddress=2485 Rockville Pike\ncity=Rockville\n"
        "state=MD\nzip=20852\ncountry=USA\n\n"
        "[1]\nname=Bob Other\ncity=Baltimore\nstate=MD\n",
        encoding="utf-8",
    )

    records = load_abook(path)

    assert records[0].postal == ("2485 Rockville Pike, Rockville, MD 20852, USA",)
    assert records[1].postal == ("Baltimore, MD",)


def test_an_entry_with_only_a_postal_address_is_still_a_record(tmp_path) -> None:
    path = tmp_path / "addressbook"
    path.write_text("[0]\nname=Alice\naddress=1 Main St\ncity=Town\n", encoding="utf-8")

    assert load_abook(path)[0].postal == ("1 Main St, Town",)


def test_stored_records_keep_every_field_on_the_way_back(store) -> None:
    """A round trip that drops a field is invisible until someone looks."""
    person = Identity(
        key="mailto:a@x.com", display_name="Alice",
        records=[RawRecord("abook", "0", "Alice", ("5551234567",), ("a@x.com",),
                           ("1 Main St, Town",))],
        keys={"mailto:a@x.com", "tel:5551234567"},
    )
    store.replace([person], [])

    restored = store.records_for("mailto:a@x.com")[0]

    assert restored.phones == ("5551234567",)
    assert restored.emails == ("a@x.com",)
    assert restored.postal == ("1 Main St, Town",)


@pytest.mark.parametrize("raw,expected", [
    # An extension belongs to the desk, not the line. Keeping it shifted the
    # digits into a number belonging to nobody.
    ("555-234-1500 x208", "tel:5552341500"),
    ("(800) 555-4332 Ext. 502", "tel:8005554332"),
    ("(555) 652-2818 x220", "tel:5556522818"),
    ("555-482-1300 x114", "tel:5554821300"),
    # An extension with no line in front of it identifies nothing.
    ("3282", None),
    # A handle is not a phone number, however many digits it contains.
    ("person:9b959c70f9b9c3e4", None),
    ("1-800-FLOWERS", None),
])
def test_extensions_are_stripped_and_non_numbers_rejected(raw, expected) -> None:
    assert phone_key(raw) == expected

"""vCard parsing — one adapter for every CardDAV-backed account."""
from __future__ import annotations

from omadex.sources.vdir import load_vdir, parse_vcards

SIMPLE = """BEGIN:VCARD
VERSION:3.0
FN:Alice Example
N:Example;Alice;;;
EMAIL;TYPE=INTERNET;TYPE=HOME:alice@example.com
EMAIL;TYPE=WORK:alice@work.example
TEL;TYPE=CELL:+1 555 123 4567
END:VCARD
"""


def test_a_vcard_becomes_one_record_with_every_address() -> None:
    records = parse_vcards(SIMPLE, "alice")

    assert len(records) == 1
    assert records[0].name == "Alice Example"
    assert records[0].emails == ("alice@example.com", "alice@work.example")
    assert records[0].phones == ("+1 555 123 4567",)
    assert records[0].source == "vdir"


def test_folded_lines_are_rejoined_before_parsing() -> None:
    folded = (
        "BEGIN:VCARD\nFN:Alexandra Bartholomew-Fitz\n"
        "EMAIL:alexandra.bartholomew\n .fitz@averylongdomainname.example\nEND:VCARD\n"
    )

    records = parse_vcards(folded, "folded")

    assert records[0].emails == ("alexandra.bartholomew.fitz@averylongdomainname.example",)


def test_apple_style_item_grouping_is_understood() -> None:
    grouped = (
        "BEGIN:VCARD\nFN:Bob Other\n"
        "item1.EMAIL;type=INTERNET:bob@example.com\n"
        "item1.X-ABLabel:_$!<Home>!$_\nEND:VCARD\n"
    )

    records = parse_vcards(grouped, "bob")

    assert records[0].emails == ("bob@example.com",)


def test_structured_name_is_used_when_there_is_no_display_name() -> None:
    records = parse_vcards(
        "BEGIN:VCARD\nN:Other;Bob;;;\nTEL:5551234567\nEND:VCARD\n", "bob"
    )

    assert records[0].name == "Bob Other"


def test_several_vcards_in_one_file_stay_separate() -> None:
    records = parse_vcards(SIMPLE + SIMPLE.replace("Alice", "Carol"), "pair")

    assert [record.name for record in records] == ["Alice Example", "Carol Example"]
    assert records[0].source_id != records[1].source_id


def test_encoded_payloads_are_skipped_rather_than_mangled() -> None:
    with_photo = (
        "BEGIN:VCARD\nFN:Bob Other\n"
        "PHOTO;ENCODING=b;TYPE=JPEG:/9j/4AAQSkZJRgABAQ\n"
        "EMAIL:bob@example.com\nEND:VCARD\n"
    )

    records = parse_vcards(with_photo, "bob")

    assert records[0].emails == ("bob@example.com",)
    assert records[0].name == "Bob Other"


def test_escapes_are_unwound() -> None:
    records = parse_vcards(
        "BEGIN:VCARD\nFN:Example\\, Alice\nEMAIL:a@x.com\nEND:VCARD\n", "esc"
    )

    assert records[0].name == "Example, Alice"


def test_a_missing_vdir_is_empty_not_fatal(tmp_path) -> None:
    assert load_vdir(tmp_path / "nothing-here") == []


def test_every_vcf_under_the_vdir_is_read(tmp_path) -> None:
    collection = tmp_path / "default"
    collection.mkdir()
    (collection / "one.vcf").write_text(SIMPLE, encoding="utf-8")
    (collection / "two.vcf").write_text(SIMPLE.replace("Alice", "Carol"), encoding="utf-8")
    (collection / "ignored.txt").write_text("not a vcard", encoding="utf-8")

    records = load_vdir(tmp_path)

    assert sorted(record.name for record in records) == ["Alice Example", "Carol Example"]


def test_postal_addresses_are_composed_from_adr() -> None:
    card = (
        "BEGIN:VCARD\nFN:Alice Example\n"
        "ADR;TYPE=HOME:;;2485 Rockville Pike;Rockville;MD;20852;USA\n"
        "END:VCARD\n"
    )

    records = parse_vcards(card, "alice")

    assert records[0].postal == ("2485 Rockville Pike, Rockville, MD 20852, USA",)


def test_a_partial_adr_does_not_leave_stray_commas() -> None:
    card = "BEGIN:VCARD\nFN:Bob\nADR:;;;Baltimore;MD;;\nEND:VCARD\n"

    records = parse_vcards(card, "bob")

    assert records[0].postal == ("Baltimore, MD",)


def test_a_postal_address_is_never_an_identity_key() -> None:
    """Housemates share an address; merging on one would be the hub bug again."""
    card = (
        "BEGIN:VCARD\nFN:Alice\nADR:;;1 Shared St;Town;MD;20852;USA\nEND:VCARD\n"
    )

    record = parse_vcards(card, "alice")[0]

    assert record.postal
    assert record.keys == set()

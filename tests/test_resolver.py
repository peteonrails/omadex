"""The merge rules, stated as the failures they prevent."""
from __future__ import annotations

from omadex.limits import HUB_RECORD_THRESHOLD
from omadex.models import VERDICT_DISTINCT, VERDICT_SAME, Override, RawRecord
from omadex.resolver import resolve


def record(name, phones=(), emails=(), source="abook", source_id="0") -> RawRecord:
    return RawRecord(source, source_id, name, tuple(phones), tuple(emails))


def test_shared_email_merges_even_when_names_look_unrelated() -> None:
    """Maiden names, nicknames, and initials must not block a strong signal."""
    resolution = resolve([
        record("Alice Example", emails=["alice@example.com"]),
        record("A. Barnes", emails=["ALICE@example.com"], source="blueferry"),
    ])

    assert len(resolution.identities) == 1
    assert not resolution.review


def test_shared_phone_merges_when_names_agree() -> None:
    resolution = resolve([
        record("Alice Example", phones=["+1 (555) 123-4567"]),
        record("Alice", phones=["5551234567"], source="blueferry"),
    ])

    assert len(resolution.identities) == 1


def test_shared_office_line_does_not_merge_two_colleagues() -> None:
    """The Phase 0 failure: one switchboard, two unrelated people."""
    resolution = resolve([
        record("Priya Nair", phones=["5551112222"], emails=["pn@northwind.example"]),
        record("Marcus Webb", phones=["5551112222"], emails=["mw@northwind.example"]),
    ])

    assert len(resolution.identities) == 2
    assert len(resolution.review) == 1
    assert resolution.review[0].shared == "tel:5551112222"
    assert resolution.review[0].reason == "shared phone, names disagree"


def test_one_persons_address_on_many_records_is_not_a_switchboard() -> None:
    """Several sources plus their duplicates easily exceed the record count.

    Suppressing that address would split one popular person into pieces.
    """
    shared = "dana@example.com"
    records = [
        record("Dana Ellis", emails=[shared], source=source, source_id=str(index))
        for index, source in enumerate(
            ["abook", "blueferry", "eds", "vdir", "neomutt"]
        )
    ]

    resolution = resolve(records)

    assert resolution.hubs == []
    assert len(resolution.identities) == 1
    assert len(resolution.identities[0].records) == 5


def test_a_switchboard_on_many_records_never_merges_anyone() -> None:
    shared = "5559998888"
    records = [
        record(f"Person {index}", phones=[shared], source_id=str(index))
        for index in range(HUB_RECORD_THRESHOLD)
    ]

    resolution = resolve(records)

    assert len(resolution.identities) == HUB_RECORD_THRESHOLD
    assert resolution.hubs == [(f"tel:{shared}", HUB_RECORD_THRESHOLD)]
    # A hub is suppressed outright, not queued as N-squared review noise.
    assert not resolution.review


def test_unnamed_records_still_merge_on_a_shared_phone() -> None:
    """A number-only contact carries no name evidence either way."""
    resolution = resolve([
        record("", phones=["5551234567"]),
        record("Alice Example", phones=["5551234567"], source="blueferry"),
    ])

    assert len(resolution.identities) == 1
    assert resolution.identities[0].display_name == "Alice Example"


def test_distinct_override_keeps_two_people_apart() -> None:
    records = [
        record("Alice Example", phones=["5551234567"]),
        record("Alice Other", phones=["5551234567"], source="blueferry"),
    ]
    assert len(resolve(records).identities) == 1

    split = resolve(records, [Override(
        "tel:5551234567", "tel:5551234567", VERDICT_DISTINCT
    )])

    # Same key on both sides cannot separate them; the override must name the
    # two distinct handles, which is what the service layer enforces.
    assert len(split.identities) == 1


def test_distinct_override_on_separable_addresses_splits_them() -> None:
    records = [
        record("Alice Example", phones=["5551234567"], emails=["a@x.com"]),
        record("Alice Other", phones=["5551234567"], emails=["b@y.com"],
               source="blueferry"),
    ]
    assert len(resolve(records).identities) == 1

    split = resolve(records, [Override("mailto:a@x.com", "mailto:b@y.com",
                                       VERDICT_DISTINCT)])

    assert len(split.identities) == 2
    assert not split.conflicts


def test_same_override_merges_records_with_nothing_in_common() -> None:
    records = [
        record("Alice Example", emails=["alice@work.example"]),
        record("Alice at home", emails=["alice@home.example"], source="blueferry"),
    ]
    assert len(resolve(records).identities) == 2

    merged = resolve(records, [Override(
        "mailto:alice@home.example", "mailto:alice@work.example", VERDICT_SAME
    )])

    assert len(merged.identities) == 1


def test_a_defeated_distinct_override_is_reported_not_ignored() -> None:
    """If another link re-joins them, the user must hear about it."""
    records = [
        record("Alice Example", emails=["a@x.com"], phones=["5551110000"]),
        record("Alice Other", emails=["b@y.com"], phones=["5551110000"],
               source="blueferry"),
        record("Alice Example", emails=["a@x.com", "b@y.com"], source="vdir"),
    ]

    resolution = resolve(records, [Override("mailto:a@x.com", "mailto:b@y.com",
                                            VERDICT_DISTINCT)])

    assert len(resolution.identities) == 1
    assert resolution.conflicts == [("mailto:a@x.com", "mailto:b@y.com")]


def test_people_sharing_only_a_switchboard_get_distinct_handles() -> None:
    """A hub key identifies no one, so it must not name anyone either."""
    shared = "5559998888"
    records = [
        record(f"Person {index}", phones=[shared], source_id=str(index))
        for index in range(HUB_RECORD_THRESHOLD)
    ]

    identities = resolve(records).identities

    assert len({identity.key for identity in identities}) == len(records)
    assert all(identity.key.startswith("person:") for identity in identities)


def test_records_with_no_address_at_all_stay_separate_people() -> None:
    """abook entries that are a name and a postal address are still contacts."""
    records = [record("Alice Example"), record("Bob Other", source_id="1")]

    identities = resolve(records).identities

    assert len(identities) == 2
    assert len({identity.key for identity in identities}) == 2


def test_an_addressless_record_folds_into_the_same_name_with_addresses() -> None:
    """Otherwise both rows show in every search for the rest of time."""
    records = [
        record("Robin Vance"),
        record("Robin Vance", phones=["5550149271"], source="blueferry"),
    ]

    identities = resolve(records).identities

    assert len(identities) == 1
    assert len(identities[0].records) == 2
    assert identities[0].phones == ["tel:5550149271"]


def test_an_addressless_record_with_an_ambiguous_name_is_left_alone() -> None:
    """Two candidates means guessing, and a name is not identity."""
    records = [
        record("Chris Smith"),
        record("Chris Smith", phones=["5551110000"], source="blueferry"),
        record("Chris Smith", emails=["chris@example.com"], source="vdir"),
    ]

    identities = resolve(records).identities

    assert len(identities) == 3


def test_carrier_shortcodes_are_dropped_even_when_they_have_a_number() -> None:
    records = [
        record("#Balance Check", phones=["8005550142"]),
        record("*611", phones=["5551110000"], source_id="1"),
        record("Alice Example", phones=["5552223333"], source_id="2"),
    ]

    identities = resolve(records).identities

    assert [identity.display_name for identity in identities] == ["Alice Example"]


def test_identity_handle_prefers_email_and_is_stable() -> None:
    records = [record("Alice", phones=["5551234567"], emails=["z@example.com"])]

    first = resolve(records).identities[0]
    second = resolve(list(records)).identities[0]

    assert first.key == "mailto:z@example.com"
    assert first.key == second.key


def test_two_addressless_records_of_one_name_become_one_person() -> None:
    """One source knows the name, another knows the name and a street.

    Neither carries a destination, so nothing can be wrongly associated —
    and left apart they show as two identical rows, one of them empty.
    """
    records = [
        record("Casey Lund", source="blueferry"),
        RawRecord("abook", "1", "Casey Lund", (), (), ("18 Larkspur Way",)),
    ]

    identities = resolve(records).identities

    assert len(identities) == 1
    assert identities[0].postal == ["18 Larkspur Way"]


def test_addressless_records_still_defer_to_an_addressed_namesake() -> None:
    records = [
        record("Chris Smith"),
        record("Chris Smith", phones=["5551110000"], source="blueferry"),
        record("Chris Smith", emails=["chris@example.com"], source="vdir"),
    ]

    assert len(resolve(records).identities) == 3

"""Weighted identity resolution.

Phase 0 measured the failure mode this exists to prevent: two colleagues at one
company share a switchboard or fax line, and a naive union-find welds them into
one person. 306 of 925 merge edges rested on a shared phone alone.

The rule, in one line: **a shared email merges; a shared phone merges only when
the names also agree.** Everything else is held for a human.

Every edge records the evidence that created it, so a merge can always be
explained, and so an override has something specific to contradict.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256

from omadex.limits import HUB_NAME_THRESHOLD, HUB_RECORD_THRESHOLD
from omadex.models import (
    VERDICT_DISTINCT,
    VERDICT_SAME,
    Identity,
    Override,
    RawRecord,
    ReviewItem,
)
from omadex.normalize import is_person, key_kind, names_agree, pair_key


@dataclass(frozen=True, slots=True)
class Resolution:
    identities: list[Identity]
    review: list[ReviewItem]
    hubs: list[tuple[str, int]]
    edges: int
    conflicts: list[tuple[str, str]]


class _UnionFind:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, item: int) -> int:
        while self._parent[item] != item:
            self._parent[item] = self._parent[self._parent[item]]
            item = self._parent[item]
        return item

    def union(self, left: int, right: int) -> bool:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return False
        self._parent[right_root] = left_root
        return True


def _index_keys(records: list[RawRecord]) -> dict[str, list[int]]:
    index: dict[str, list[int]] = defaultdict(list)
    for position, record in enumerate(records):
        for key in record.keys:
            index[key].append(position)
    return index


def _identity_handle(
    keys: set[str], hubs: frozenset[str] = frozenset(), name: str = ""
) -> str:
    """Content-addressed handle: stable across re-sync for unchanged data.

    Emails are preferred because they survive number changes and are the
    evidence the resolver trusts most. A hub key is disqualified for the same
    reason it cannot merge anyone: a switchboard identifies no one, and using
    it here would give every colleague on that line the same handle.

    Records with no identifying address at all — an abook entry that is a name
    and a postal address — get a digest of what they do have.
    """
    usable = keys - hubs
    for prefix in ("mailto:", "tel:"):
        candidates = sorted(key for key in usable if key.startswith(prefix))
        if candidates:
            return candidates[0]
    digest = sha256(
        "\n".join([name, *sorted(keys)]).encode("utf-8")
    ).hexdigest()[:16]
    return f"person:{digest}"


def resolve(
    records: list[RawRecord], overrides: list[Override] | None = None
) -> Resolution:
    overrides = overrides or []
    index = _index_keys(records)
    union = _UnionFind(len(records))

    # An address is never distinct from itself, and never needs linking to
    # itself; degenerate pairs are dropped rather than acted on.
    forced = {
        pair_key(item.left_key, item.right_key)
        for item in overrides
        if item.verdict == VERDICT_SAME and item.left_key != item.right_key
    }
    claimed_distinct = {
        pair_key(item.left_key, item.right_key)
        for item in overrides
        if item.verdict == VERDICT_DISTINCT and item.left_key != item.right_key
    }

    # A single record carrying both addresses is the source itself insisting
    # they are one person. That outranks a stale "distinct" ruling: honour the
    # data, drop the rule, and report the contradiction.
    contradicted = {
        pair for pair in claimed_distinct
        if any(pair[0] in record.keys and pair[1] in record.keys for record in records)
    }
    denied = claimed_distinct - contradicted

    def denies(left: int, right: int) -> bool:
        """True when the user has said these two records are different people.

        Only the specific pair of addresses named in the ruling blocks a
        merge — a record that owns neither is unaffected.
        """
        left_keys, right_keys = records[left].keys, records[right].keys
        return any(
            (first in left_keys and second in right_keys)
            or (second in left_keys and first in right_keys)
            for first, second in denied
        )

    hubs: list[tuple[str, int]] = []
    review: dict[tuple[str, str], ReviewItem] = {}
    edges = 0

    key_owner: dict[str, int] = {}
    for position, record in enumerate(records):
        for key in record.keys:
            key_owner.setdefault(key, position)

    # User assertions are applied first, so a decision already made is never
    # re-raised as a question during the scan below.
    for left_key, right_key in sorted(forced):
        left, right = key_owner.get(left_key), key_owner.get(right_key)
        if left is not None and right is not None and union.union(left, right):
            edges += 1

    for key, positions in sorted(index.items()):
        # A destination on many records under many different names is a
        # switchboard, a support inbox or a family line: real, but not
        # identifying. Never merge through it. One name repeated across
        # sources is the opposite — that is the person themselves.
        if len(positions) >= HUB_RECORD_THRESHOLD:
            distinct_names = {
                records[position].name.strip().casefold()
                for position in positions
                if records[position].name.strip()
            }
            if len(distinct_names) >= HUB_NAME_THRESHOLD:
                hubs.append((key, len(positions)))
                continue

        strong = key_kind(key) == "mailto"
        anchor = positions[0]
        for other in positions[1:]:
            left, right = records[anchor], records[other]
            if denies(anchor, other):
                continue
            if strong or names_agree(left.name, right.name):
                if union.union(anchor, other):
                    edges += 1
                continue
            if union.find(anchor) == union.find(other):
                # Already one person on better evidence — nothing to ask.
                continue
            # Weak evidence and disagreeing names: this is the Phase 0 failure.
            handle = pair_key(
                _identity_handle(left.keys), _identity_handle(right.keys)
            )
            review.setdefault(handle, ReviewItem(
                left_key=handle[0],
                right_key=handle[1],
                left_name=left.name,
                right_name=right.name,
                shared=key,
                reason="shared phone, names disagree",
            ))

    grouped: dict[int, Identity] = {}
    for position, record in enumerate(records):
        root = union.find(position)
        identity = grouped.get(root)
        if identity is None:
            identity = grouped[root] = Identity(key="", display_name="")
        identity.records.append(record)
        identity.keys |= record.keys

    hub_keys = frozenset(key for key, _ in hubs)
    identities: list[Identity] = []
    for identity in grouped.values():
        identity.display_name = _display_name(identity)
        identity.key = _identity_handle(
            identity.keys, hub_keys, identity.display_name
        )
        if is_person(identity.display_name, identity.keys):
            identities.append(identity)
    identities = _absorb_addressless(identities)
    identities.sort(key=lambda item: (item.display_name.casefold(), item.key))
    _ensure_unique_handles(identities)

    # A "distinct" ruling can still be defeated — by a record that carries both
    # addresses, or by a transitive path through other records. Surface it
    # rather than silently ignoring the user.
    conflicts = sorted(contradicted | {
        (left_key, right_key)
        for left_key, right_key in denied
        if (left := key_owner.get(left_key)) is not None
        and (right := key_owner.get(right_key)) is not None
        and union.find(left) == union.find(right)
    })

    return Resolution(
        identities=identities,
        review=sorted(review.values(), key=lambda item: item.left_name.casefold()),
        hubs=sorted(hubs, key=lambda hub: -hub[1]),
        edges=edges,
        conflicts=conflicts,
    )


def _absorb_addressless(identities: list[Identity]) -> list[Identity]:
    """Fold a record with no addresses into the one person of the same name.

    This is the single place a name is allowed to link records, and it is safe
    for a narrow reason: the absorbed record contributes no addresses, so it
    cannot attach a wrong number or mailbox to anyone. Without it, an abook
    entry holding only a name sits forever beside the same person's iPhone
    entry, and both show in every search.

    An ambiguous name — two people who could absorb it — leaves it alone.
    Guessing there would merge two different people on a name alone, which is
    exactly what the rest of the resolver refuses to do.
    """
    by_name: dict[str, list[Identity]] = defaultdict(list)
    for identity in identities:
        if identity.keys:
            by_name[identity.display_name.casefold()].append(identity)

    kept: list[Identity] = []
    # Where no addressed contact owns the name, the addressless records still
    # belong together: one source knows a name, another knows the same name
    # and a street address, and neither has a destination to link them by.
    # Nothing can be wrongly associated, because none of them carry one.
    absorbed: dict[str, Identity] = {}

    for identity in identities:
        if identity.keys:
            kept.append(identity)
            continue
        name = identity.display_name.casefold()
        candidates = by_name.get(name, [])
        if len(candidates) == 1:
            candidates[0].records.extend(identity.records)
        elif candidates:
            # More than one addressed contact shares the name; guessing which
            # one owns this record would merge on a name alone.
            kept.append(identity)
        elif name in absorbed:
            absorbed[name].records.extend(identity.records)
        else:
            absorbed[name] = identity
            kept.append(identity)
    return kept


def _ensure_unique_handles(identities: list[Identity]) -> None:
    """A handle is a primary key; two people may never share one.

    Preferred handles can still collide — an override can split two records
    that share the very email one of them would be named by. The first in
    sorted order keeps the readable handle; the rest fall back to a digest, so
    the outcome does not depend on iteration order.
    """
    seen: set[str] = set()
    for identity in identities:
        if identity.key not in seen:
            seen.add(identity.key)
            continue
        digest = sha256(
            "\n".join([
                identity.key, identity.display_name, *sorted(identity.keys),
                *sorted(f"{item.source}:{item.source_id}" for item in identity.records),
            ]).encode("utf-8")
        ).hexdigest()[:16]
        identity.key = f"person:{digest}"
        seen.add(identity.key)


def _display_name(identity: Identity) -> str:
    """Longest name wins: "Alice Example" beats "Alice", which beats "".

    Ties break alphabetically so the choice is stable between runs.
    """
    names = identity.names
    if not names:
        return next(iter(sorted(identity.keys)), "")
    return sorted(names, key=lambda name: (-len(name), name))[0]

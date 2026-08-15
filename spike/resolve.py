"""Phase 0 identity resolution — the part the whole product rests on.

Records are linked when they share a normalized destination (phone or email).
Names are never a merge key: they are display data, not identity. Two people
called "David Smith" are two people; one person with a work and a personal
address is one person.

The interesting output is not the merged set, it is the *evidence*: which key
merged which records, so false merges can be found instead of assumed away.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from sources import RawRecord

_DIGITS = re.compile(r"[^0-9]")
_EMAIL_SHAPED = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_phone(raw: str) -> str | None:
    """Match key for a phone number.

    Last 10 digits for anything NANP-sized, so +1-555-123-4567, 15551234567,
    and (555) 123-4567 collapse to one key. Shorter strings keep their digits
    if there are at least 7, which is the shortest thing plausibly dialable.
    """
    digits = _DIGITS.sub("", raw or "")
    if len(digits) >= 10:
        return digits[-10:]
    return digits if len(digits) >= 7 else None


def normalize_email(raw: str) -> str | None:
    value = (raw or "").strip().lower()
    return value if _EMAIL_SHAPED.match(value) else None


def name_tokens(name: str) -> set[str]:
    return {token for token in re.split(r"[^\w']+", (name or "").casefold()) if token}


@dataclass
class Cluster:
    records: list[RawRecord] = field(default_factory=list)
    keys: set[str] = field(default_factory=set)

    @property
    def sources(self) -> set[str]:
        return {record.source for record in self.records}

    @property
    def names(self) -> list[str]:
        seen: list[str] = []
        for record in self.records:
            name = record.name.strip()
            if name and name not in seen:
                seen.append(name)
        return seen

    def names_disagree(self) -> bool:
        """True when no two names in the cluster share a token.

        A cluster of "Alice Example" + "Alice" agrees. A cluster of
        "Alice Example" + "Bob Other" does not, and is either a shared line
        or a genuine mistake — either way a human should look.
        """
        token_sets = [tokens for tokens in map(name_tokens, self.names) if tokens]
        if len(token_sets) < 2:
            return False
        return not any(
            left & right
            for index, left in enumerate(token_sets)
            for right in token_sets[index + 1:]
        )


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, item: int) -> int:
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def resolve(records: list[RawRecord], hub_threshold: int = 4) -> tuple[list[Cluster], dict]:
    """Link records by shared destination.

    `hub_threshold` guards the classic failure: a shared office line or a
    family landline appears on many unrelated people and, left alone, welds
    them into one giant identity. Keys at or above the threshold are treated
    as non-identifying and excluded from linking, then reported.
    """
    key_to_records: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        for raw in record.phones:
            key = normalize_phone(raw)
            if key:
                key_to_records[f"tel:{key}"].append(index)
        for raw in record.emails:
            key = normalize_email(raw)
            if key:
                key_to_records[f"mailto:{key}"].append(index)

    hubs: list[tuple[str, int, list[str]]] = []
    union = _UnionFind()
    for index in range(len(records)):
        union.find(index)

    for key, indexes in key_to_records.items():
        distinct_names = {
            records[index].name.strip() for index in indexes if records[index].name.strip()
        }
        if len(indexes) >= hub_threshold and len(distinct_names) >= hub_threshold:
            hubs.append((key, len(indexes), sorted(distinct_names)[:8]))
            continue
        first = indexes[0]
        for other in indexes[1:]:
            union.union(first, other)

    grouped: dict[int, Cluster] = defaultdict(Cluster)
    for index, record in enumerate(records):
        grouped[union.find(index)].records.append(record)
    for key, indexes in key_to_records.items():
        if indexes:
            grouped[union.find(indexes[0])].keys.add(key)

    clusters = sorted(
        grouped.values(), key=lambda cluster: (-len(cluster.records), cluster.names[:1])
    )
    stats = {
        "raw_records": len(records),
        "distinct_keys": len(key_to_records),
        "clusters": len(clusters),
        "hubs_excluded": len(hubs),
        "hub_detail": sorted(hubs, key=lambda hub: -hub[1])[:10],
    }
    return clusters, stats

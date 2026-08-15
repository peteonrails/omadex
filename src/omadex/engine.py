"""Sync pipeline: sources → resolver → store.

One entry point so the daemon, the CLI, and the tests all take the same path.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from omadex.models import Override
from omadex.resolver import resolve
from omadex.sources import load_all
from omadex.store import Store

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SyncResult:
    identities: int
    records: int
    review: int
    hubs: int
    edges: int
    conflicts: list[tuple[str, str]] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    elapsed: float = 0.0

    def to_dict(self) -> dict:
        return {
            "identities": self.identities,
            "records": self.records,
            "review": self.review,
            "hubs": self.hubs,
            "edges": self.edges,
            "conflicts": [list(pair) for pair in self.conflicts],
            "errors": self.errors,
            "elapsed": round(self.elapsed, 3),
        }


def sync(store: Store, overrides: list[Override] | None = None) -> SyncResult:
    """Rebuild the derived projection from every readable source.

    Overrides are read from the store unless supplied, so a caller cannot
    accidentally rebuild without the user's decisions applied.
    """
    started = time.monotonic()
    records, errors = load_all()
    decisions = store.overrides() if overrides is None else overrides
    resolution = resolve(records, decisions)
    store.replace(resolution.identities, resolution.review)

    if resolution.conflicts:
        log.warning(
            "%d 'distinct' overrides are defeated by a transitive path",
            len(resolution.conflicts),
        )

    return SyncResult(
        identities=len(resolution.identities),
        records=len(records),
        review=len(resolution.review),
        hubs=len(resolution.hubs),
        edges=resolution.edges,
        conflicts=resolution.conflicts,
        errors=errors,
        elapsed=time.monotonic() - started,
    )

"""Value types shared by sources, resolver, and store."""
from __future__ import annotations

from dataclasses import dataclass, field

from omadex.normalize import address_key, email_key, phone_key

# Evidence strengths, strongest first. A merge is justified by its strongest
# supporting evidence; review is justified by the absence of any.
EVIDENCE_OVERRIDE = "override"
EVIDENCE_EMAIL = "email"
EVIDENCE_PHONE = "phone"

VERDICT_SAME = "same"
VERDICT_DISTINCT = "distinct"


@dataclass(frozen=True, slots=True)
class RawRecord:
    """One entry exactly as a source stated it, before any merging."""

    source: str
    source_id: str
    name: str
    phones: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    # Display only. A postal address is deliberately absent from `keys`:
    # housemates, spouses and colleagues share one, so merging on it would
    # recreate the switchboard failure in a worse form. Same rule as names.
    postal: tuple[str, ...] = ()

    @property
    def keys(self) -> set[str]:
        found = {phone_key(value) for value in self.phones}
        found |= {email_key(value) for value in self.emails}
        return {key for key in found if key}

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "name": self.name,
            "phones": list(self.phones),
            "emails": list(self.emails),
            "postal": list(self.postal),
        }


@dataclass(slots=True)
class Identity:
    """One resolved person: the records that agree they are the same."""

    key: str
    display_name: str
    records: list[RawRecord] = field(default_factory=list)
    keys: set[str] = field(default_factory=set)

    @property
    def sources(self) -> list[str]:
        return sorted({record.source for record in self.records})

    @property
    def phones(self) -> list[str]:
        return sorted({key for key in self.keys if key.startswith("tel:")})

    @property
    def emails(self) -> list[str]:
        return sorted({key for key in self.keys if key.startswith("mailto:")})

    @property
    def postal(self) -> list[str]:
        seen: list[str] = []
        for record in self.records:
            for address in record.postal:
                if address and address not in seen:
                    seen.append(address)
        return seen

    @property
    def names(self) -> list[str]:
        seen: list[str] = []
        for record in self.records:
            name = record.name.strip()
            if name and name not in seen:
                seen.append(name)
        return seen

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.display_name,
            "names": self.names,
            "phones": self.phones,
            "emails": self.emails,
            "postal": self.postal,
            "sources": self.sources,
            "record_count": len(self.records),
        }


@dataclass(frozen=True, slots=True)
class ReviewItem:
    """A merge the resolver declined to make on its own."""

    left_key: str
    right_key: str
    left_name: str
    right_name: str
    shared: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "left": self.left_key,
            "right": self.right_key,
            "left_name": self.left_name,
            "right_name": self.right_name,
            "shared": self.shared,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class Override:
    """A human decision, which always outranks computed evidence."""

    left_key: str
    right_key: str
    verdict: str

    @staticmethod
    def normalize_handle(raw: str) -> str | None:
        """Accept either a full key or a bare address from the caller."""
        value = (raw or "").strip()
        if value.startswith(("tel:", "mailto:")):
            kind, _, rest = value.partition(":")
            return phone_key(rest) if kind == "tel" else email_key(rest)
        return address_key(value)

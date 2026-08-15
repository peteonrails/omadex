"""Phase 0 source adapters — read-only, no writes to any address book.

Each adapter yields RawRecord: whatever the source actually said, before
normalization. Keeping the raw form separate from the normalized keys is what
lets the resolver explain *why* two records merged.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ABOOK_PATH = Path.home() / ".abook" / "addressbook"

# abook stores multi-valued fields as one comma-separated line.
_ABOOK_SECTION = re.compile(r"^\[(\d+)\]$")
_ABOOK_PHONE_FIELDS = ("phone", "mobile", "workphone", "fax", "otherphone")


@dataclass
class RawRecord:
    source: str
    source_id: str
    name: str
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)


def load_abook(path: Path = ABOOK_PATH) -> list[RawRecord]:
    """Parse abook's ini-ish addressbook. Sections are [0], [1], ... entries."""
    records: list[RawRecord] = []
    current: dict[str, str] | None = None
    section = ""

    def flush() -> None:
        if current is None:
            return
        phones = [
            value.strip()
            for key in _ABOOK_PHONE_FIELDS
            for value in current.get(key, "").split(",")
            if value.strip()
        ]
        emails = [
            value.strip()
            for value in current.get("email", "").split(",")
            if value.strip()
        ]
        name = current.get("name", "").strip()
        if name or phones or emails:
            records.append(RawRecord("abook", section, name, phones, emails))

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        match = _ABOOK_SECTION.match(line)
        if match:
            flush()
            section, current = match.group(1), {}
            continue
        if not line or line.startswith("#") or current is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        current[key.strip()] = value.strip()
    flush()
    return records


def load_blueferry() -> list[RawRecord]:
    """Enumerate the iPhone phonebook over D-Bus.

    Deliberately not reading contacts.sqlite: records there are encrypted
    under a key the BlueFerry daemon owns in the Secret Service wallet, and
    that key is a trust boundary the daemon defends. ListContacts is the
    supported way in (added on the feature/list-contacts branch).
    """
    import dbus

    bus = dbus.SessionBus()
    iface = dbus.Interface(
        bus.get_object("io.weirdware.BlueFerry", "/io/weirdware/BlueFerry"),
        "io.weirdware.BlueFerry.Messages1",
    )

    records: list[RawRecord] = []
    offset, page = 0, 250
    while True:
        batch = json.loads(str(iface.ListContacts(dbus.UInt32(offset), dbus.UInt32(page))))
        if not batch:
            break
        for index, item in enumerate(batch):
            records.append(RawRecord(
                "blueferry",
                str(offset + index),
                str(item.get("name", "")),
                [str(value) for value in item.get("phones", [])],
                [str(value) for value in item.get("emails", [])],
            ))
        if len(batch) < page:
            break
        offset += len(batch)
    return records

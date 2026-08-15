"""BlueFerry — the contacts already on the paired iPhone.

Read over D-Bus, never from `contacts.sqlite`. Those records are encrypted
under a key BlueFerry owns in the Secret Service wallet, and that key is a
trust boundary the daemon actively defends; reaching around it would mean
either lifting another application's secret or reimplementing its crypto.

`ListContacts` (feature/list-contacts) exists because `FindContacts` answers
"who owns this destination" and cannot answer "who is in the phonebook".
"""
from __future__ import annotations

import json
import logging

from omadex.limits import MAX_PAGE
from omadex.models import RawRecord

log = logging.getLogger(__name__)

# A backend that ignored `offset` would page forever; stop well past any
# plausible phonebook rather than trusting it to terminate.
MAX_ENUMERATED_CONTACTS = 100_000

BUS_NAME = "io.weirdware.BlueFerry"
OBJECT_PATH = "/io/weirdware/BlueFerry"
MESSAGES_IFACE = f"{BUS_NAME}.Messages1"
CALL_TIMEOUT_SECONDS = 30


def load_blueferry(page: int = MAX_PAGE) -> list[RawRecord]:
    import dbus

    bus = dbus.SessionBus()
    iface = dbus.Interface(
        bus.get_object(BUS_NAME, OBJECT_PATH), MESSAGES_IFACE
    )

    records: list[RawRecord] = []
    offset = 0
    while True:
        batch = json.loads(str(iface.ListContacts(
            dbus.UInt32(offset), dbus.UInt32(page), timeout=CALL_TIMEOUT_SECONDS
        )))
        if not batch:
            break
        for position, item in enumerate(batch):
            records.append(RawRecord(
                source="blueferry",
                source_id=str(offset + position),
                name=str(item.get("name", "")),
                phones=tuple(str(value) for value in item.get("phones", [])),
                emails=tuple(str(value) for value in item.get("emails", [])),
            ))
        offset += len(batch)
        # A short page does not mean the end: the backend clamps `limit` to
        # its own maximum, so asking for more than it allows returns a full
        # page that merely looks partial. Only an empty reply ends the walk.
        if len(records) >= MAX_ENUMERATED_CONTACTS:
            log.warning("stopped after %d contacts", len(records))
            break
    return records

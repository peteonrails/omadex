"""Evolution Data Server — the store behind GNOME Contacts.

EDS is worth having as a source for what it is *connected to* rather than what
it holds locally: with GNOME Online Accounts attached, a Google or CardDAV
account appears here as an ordinary address book, and OmaDex reads it without
implementing OAuth or CardDAV itself.

Nothing is imported at module scope. EDS is optional, and a machine without it
must still load every other source.
"""
from __future__ import annotations

import logging

from omadex.models import RawRecord
from omadex.sources.vdir import structured_postal

log = logging.getLogger(__name__)

# `wait_for_connected_seconds` is a fixed settle delay, not a ceiling: this
# call sleeps for the whole value whether or not the backend is ready (30 →
# 30.07s, 1 → 1.05s), and 0 blocks indefinitely rather than skipping the wait.
# The query itself takes no measurable time, so keep the delay small and pay
# it once per address book.
CONNECT_SETTLE_SECONDS = 1
# EDS's own "match anything" query. Empty substring against the synthetic
# any-field index is how a full enumeration is expressed.
MATCH_EVERYTHING = '(contains "x-evolution-any-field" "")'


def load_eds() -> list[RawRecord]:
    import gi

    gi.require_version("EDataServer", "1.2")
    gi.require_version("EBook", "1.2")
    gi.require_version("EBookContacts", "1.2")
    from gi.repository import EBook, EBookContacts, EDataServer

    registry = EDataServer.SourceRegistry.new_sync(None)
    books = registry.list_sources(EDataServer.SOURCE_EXTENSION_ADDRESS_BOOK)

    records: list[RawRecord] = []
    for book in books:
        if not book.get_enabled():
            continue
        try:
            client = EBook.BookClient.connect_sync(book, CONNECT_SETTLE_SECONDS, None)
            found, contacts = client.get_contacts_sync(MATCH_EVERYTHING, None)
        except Exception as error:  # noqa: BLE001 - one bad book must not end the sync
            log.warning("eds book %s failed: %s", book.get_uid(), error)
            continue
        if not found:
            continue

        for contact in contacts:
            record = _to_record(contact, book.get_uid(), EBookContacts)
            if record is not None:
                records.append(record)
    return records


def _to_record(contact, book_uid: str, EBookContacts) -> RawRecord | None:
    # EContact inherits EVCard.get_attributes(), which takes no field argument
    # and returns everything; the per-field accessor in the C API is a macro
    # that introspection does not expose. Group the attributes by name here
    # rather than asking for one field at a time.
    grouped: dict[str, list[str]] = {}
    for attribute in contact.get_attributes() or []:
        value = attribute.get_value()
        if value:
            grouped.setdefault(attribute.get_name().upper(), []).append(value)

    name = contact.get_property("full-name") or ""
    emails = tuple(grouped.get("EMAIL", ()))
    phones = tuple(grouped.get("TEL", ()))
    # EDS hands ADR back in its raw semicolon form, the same as any vCard.
    postal = tuple(
        composed for composed in
        (structured_postal(value) for value in grouped.get("ADR", ()))
        if composed
    )
    if not name and not emails and not phones and not postal:
        return None

    uid = contact.get_property("id") or ""
    return RawRecord(
        source="eds",
        source_id=f"{book_uid}:{uid}",
        name=str(name),
        phones=phones,
        emails=emails,
        postal=postal,
    )

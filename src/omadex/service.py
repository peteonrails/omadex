"""D-Bus surface: io.omadex.Contacts1.

Payloads are JSON in a string, the same shape BlueFerry uses, so the QML client
can stay declarative and the wire contract can grow without new signatures.
Replies are bounded; a caller that wants everything pages for it.
"""
from __future__ import annotations

import json
import logging
import os

import dbus
import dbus.service

from omadex.engine import sync
from omadex.limits import (
    MAX_DBUS_JSON_BYTES,
    MAX_PAGE,
    MAX_REVIEW_ITEMS,
    MAX_SEARCH_QUERY_CHARS,
    MAX_SEARCH_RESULTS,
)
from omadex.models import VERDICT_DISTINCT, VERDICT_SAME, Override
from omadex.store import Store

log = logging.getLogger(__name__)

BUS_NAME = "io.omadex.Contacts"
OBJECT_PATH = "/io/omadex/Contacts"
IFACE = "io.omadex.Contacts1"
ERROR_PREFIX = "io.omadex.Contacts.Error"


class InvalidArguments(dbus.DBusException):
    _dbus_error_name = f"{ERROR_PREFIX}.InvalidArgs"


class ResponseTooLarge(dbus.DBusException):
    _dbus_error_name = f"{ERROR_PREFIX}.ResponseTooLarge"


class NotAuthorized(dbus.DBusException):
    _dbus_error_name = f"{ERROR_PREFIX}.NotAuthorized"


class CallerGuard:
    """Only this user's own processes may read the address book.

    The session bus is reachable by anything the user runs, including
    sandboxed applications that were never granted access to the filesystem.
    Checking the caller's uid stops the service handing an entire address book
    to a process that could not otherwise open the database.
    """

    def __init__(self, bus: dbus.Bus) -> None:
        self._bus = bus
        self._owner = os.getuid()

    def authorize(self, sender: str | None) -> None:
        if sender is None:
            raise NotAuthorized("caller could not be identified")
        try:
            caller = int(self._bus.get_unix_user(sender))
        except Exception as error:  # noqa: BLE001 - unknown caller is refused
            raise NotAuthorized(f"caller could not be identified: {error}") from error
        if caller != self._owner:
            raise NotAuthorized("only the owning user may read these contacts")


class ContactsService(dbus.service.Object):
    def __init__(
        self, bus: dbus.Bus, store: Store, guard: CallerGuard | None = None
    ) -> None:
        super().__init__(dbus.service.BusName(BUS_NAME, bus), OBJECT_PATH)
        self.store = store
        self._guard = guard or CallerGuard(bus)

    @staticmethod
    def _json(value) -> str:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_DBUS_JSON_BYTES:
            raise ResponseTooLarge("result is too large; request a smaller page")
        return encoded

    @staticmethod
    def _handle(raw: str) -> str:
        key = Override.normalize_handle(str(raw))
        if key is None:
            raise InvalidArguments(f"not an address: {raw!r}")
        return key

    @dbus.service.method(
        IFACE, sender_keyword="sender", in_signature="su", out_signature="s")
    def Search(self, query: str, limit: dbus.UInt32, sender=None) -> str:
        self._guard.authorize(sender)
        text = str(query)
        if len(text) > MAX_SEARCH_QUERY_CHARS:
            raise InvalidArguments("query is too long")
        bounded = max(1, min(int(limit) or MAX_SEARCH_RESULTS, MAX_SEARCH_RESULTS))
        return self._json(self.store.search(text, bounded))

    @dbus.service.method(
        IFACE, sender_keyword="sender", in_signature="s", out_signature="s")
    def Get(self, handle: str, sender=None) -> str:
        self._guard.authorize(sender)
        # An identity key is a valid handle too — a contact with no phone or
        # email is named by a digest, which is not an address to normalize.
        found = self.store.get(Override.normalize_handle(handle) or str(handle))
        return self._json(found if found is not None else {})

    @dbus.service.method(
        IFACE, sender_keyword="sender", in_signature="uu", out_signature="s")
    def List(self, offset: dbus.UInt32, limit: dbus.UInt32, sender=None) -> str:
        self._guard.authorize(sender)
        return self._json(self.store.list(
            int(offset), max(1, min(int(limit) or MAX_PAGE, MAX_PAGE))
        ))

    @dbus.service.method(
        IFACE, sender_keyword="sender", in_signature="u", out_signature="s")
    def Review(self, limit: dbus.UInt32, sender=None) -> str:
        self._guard.authorize(sender)
        return self._json(self.store.review_items(
            max(1, min(int(limit) or MAX_REVIEW_ITEMS, MAX_REVIEW_ITEMS))
        ))

    @dbus.service.method(
        IFACE, sender_keyword="sender", in_signature="ss", out_signature="s")
    def Link(self, left: str, right: str, sender=None) -> str:
        """Assert that two addresses belong to the same person."""
        self._guard.authorize(sender)
        return self._decide(left, right, VERDICT_SAME)

    @dbus.service.method(
        IFACE, sender_keyword="sender", in_signature="ss", out_signature="s")
    def Unlink(self, left: str, right: str, sender=None) -> str:
        """Assert that two addresses belong to different people."""
        self._guard.authorize(sender)
        return self._decide(left, right, VERDICT_DISTINCT)

    @dbus.service.method(
        IFACE, sender_keyword="sender", in_signature="ss", out_signature="b")
    def ForgetDecision(self, left: str, right: str, sender=None) -> bool:
        self._guard.authorize(sender)
        left_key, right_key = sorted((self._handle(left), self._handle(right)))
        cleared = self.store.clear_override(left_key, right_key)
        if cleared:
            sync(self.store)
            self.ContactsChanged()
        return cleared

    def _decide(self, left: str, right: str, verdict: str) -> str:
        left_key, right_key = sorted((self._handle(left), self._handle(right)))
        if left_key == right_key:
            raise InvalidArguments("an address cannot be linked to itself")
        self.store.set_override(Override(left_key, right_key, verdict))
        result = sync(self.store)
        self.ContactsChanged()
        return self._json(result.to_dict())

    @dbus.service.method(
        IFACE, sender_keyword="sender", in_signature="", out_signature="s")
    def Refresh(self, sender=None) -> str:
        self._guard.authorize(sender)
        result = sync(self.store)
        self.ContactsChanged()
        return self._json(result.to_dict())

    @dbus.service.method(
        IFACE, sender_keyword="sender", in_signature="", out_signature="s")
    def Counts(self, sender=None) -> str:
        self._guard.authorize(sender)
        return self._json(self.store.counts())

    @dbus.service.signal(IFACE)
    def ContactsChanged(self) -> None:
        """Content-free invalidation; clients re-read what they display."""

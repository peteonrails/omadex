"""D-Bus surface: io.omadex.Contacts1.

Payloads are JSON in a string, the same shape BlueFerry uses, so the QML client
can stay declarative and the wire contract can grow without new signatures.
Replies are bounded; a caller that wants everything pages for it.
"""
from __future__ import annotations

import json
import logging

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


class ContactsService(dbus.service.Object):
    def __init__(self, bus: dbus.Bus, store: Store) -> None:
        super().__init__(dbus.service.BusName(BUS_NAME, bus), OBJECT_PATH)
        self.store = store

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

    @dbus.service.method(IFACE, in_signature="su", out_signature="s")
    def Search(self, query: str, limit: dbus.UInt32) -> str:
        text = str(query)
        if len(text) > MAX_SEARCH_QUERY_CHARS:
            raise InvalidArguments("query is too long")
        bounded = max(1, min(int(limit) or MAX_SEARCH_RESULTS, MAX_SEARCH_RESULTS))
        return self._json(self.store.search(text, bounded))

    @dbus.service.method(IFACE, in_signature="s", out_signature="s")
    def Get(self, handle: str) -> str:
        # An identity key is a valid handle too — a contact with no phone or
        # email is named by a digest, which is not an address to normalize.
        found = self.store.get(Override.normalize_handle(handle) or str(handle))
        return self._json(found if found is not None else {})

    @dbus.service.method(IFACE, in_signature="uu", out_signature="s")
    def List(self, offset: dbus.UInt32, limit: dbus.UInt32) -> str:
        return self._json(self.store.list(
            int(offset), max(1, min(int(limit) or MAX_PAGE, MAX_PAGE))
        ))

    @dbus.service.method(IFACE, in_signature="u", out_signature="s")
    def Review(self, limit: dbus.UInt32) -> str:
        return self._json(self.store.review_items(
            max(1, min(int(limit) or MAX_REVIEW_ITEMS, MAX_REVIEW_ITEMS))
        ))

    @dbus.service.method(IFACE, in_signature="ss", out_signature="s")
    def Link(self, left: str, right: str) -> str:
        """Assert that two addresses belong to the same person."""
        return self._decide(left, right, VERDICT_SAME)

    @dbus.service.method(IFACE, in_signature="ss", out_signature="s")
    def Unlink(self, left: str, right: str) -> str:
        """Assert that two addresses belong to different people."""
        return self._decide(left, right, VERDICT_DISTINCT)

    @dbus.service.method(IFACE, in_signature="ss", out_signature="b")
    def ForgetDecision(self, left: str, right: str) -> bool:
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

    @dbus.service.method(IFACE, in_signature="", out_signature="s")
    def Refresh(self) -> str:
        result = sync(self.store)
        self.ContactsChanged()
        return self._json(result.to_dict())

    @dbus.service.method(IFACE, in_signature="", out_signature="s")
    def Counts(self) -> str:
        return self._json(self.store.counts())

    @dbus.service.signal(IFACE)
    def ContactsChanged(self) -> None:
        """Content-free invalidation; clients re-read what they display."""

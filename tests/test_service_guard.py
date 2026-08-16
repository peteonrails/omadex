"""Who is allowed to ask the service for contacts.

The service publishes an entire address book on the session bus, which every
process the user runs can reach — including sandboxed applications that were
never given access to the files behind it.
"""
from __future__ import annotations

import os

import pytest

from omadex.service import CallerGuard, NotAuthorized


class Bus:
    """A session bus that reports whatever uid the test needs it to."""

    def __init__(self, uid) -> None:
        self._uid = uid

    def get_unix_user(self, sender):
        if self._uid is None:
            raise RuntimeError("no such connection")
        return self._uid


def test_the_owner_is_allowed() -> None:
    CallerGuard(Bus(os.getuid())).authorize(":1.42")


def test_another_user_is_refused() -> None:
    with pytest.raises(NotAuthorized):
        CallerGuard(Bus(os.getuid() + 1)).authorize(":1.42")


def test_a_caller_the_bus_cannot_identify_is_refused() -> None:
    """An unanswerable question is not a yes."""
    with pytest.raises(NotAuthorized):
        CallerGuard(Bus(None)).authorize(":1.42")


def test_a_call_with_no_sender_is_refused() -> None:
    with pytest.raises(NotAuthorized):
        CallerGuard(Bus(os.getuid())).authorize(None)

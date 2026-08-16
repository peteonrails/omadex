"""Shared fixtures.

The store is encrypted in normal use, so the tests exercise it that way. They
must not reach for the desktop wallet to do it: a suite that needs an unlocked
keyring passes on the developer's machine and fails everywhere else. A fixed
key stands in, which also keeps failures reproducible.
"""
from __future__ import annotations

import pytest

from omadex import store as store_module
from omadex.crypto import KeyRing

TEST_KEY = bytes(range(32))


@pytest.fixture(params=["encrypted", "plaintext"])
def keyring(request):
    """Both storage modes, since either is reachable in production."""
    return KeyRing(TEST_KEY) if request.param == "encrypted" else None


@pytest.fixture
def open_store(keyring):
    """open_store bound to the fixture's mode, never to the real wallet."""
    def opener(path):
        return store_module.open_store(
            path, keyring=keyring, encrypt=keyring is not None)
    return opener


@pytest.fixture
def store(tmp_path, open_store):
    with open_store(tmp_path / "omadex" / "contacts.sqlite") as opened:
        yield opened

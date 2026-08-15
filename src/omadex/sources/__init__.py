"""Source adapters, and the settings that decide which of them run.

Every adapter takes the same argument — the settings — so a source can be
turned off or repointed without the caller knowing anything about it.

Write-back (Phase 4) goes to CardDAV, never to a source that cannot accept it:
BlueFerry's phonebook arrives over PBAP, which has no write operation in the
profile at all.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from omadex import config as config_module
from omadex.config import Settings
from omadex.models import RawRecord
from omadex.sources.abook import ABOOK_PATH, load_abook
from omadex.sources.blueferry import load_blueferry
from omadex.sources.eds import load_eds
from omadex.sources.neomutt import CONFIG_PATHS, load_neomutt
from omadex.sources.notmuch import MIN_MESSAGES, load_notmuch
from omadex.sources.vdir import load_vdir

log = logging.getLogger(__name__)


def _abook(settings: Settings) -> list[RawRecord]:
    return load_abook(settings.path_for("abook") or ABOOK_PATH)


def _blueferry(settings: Settings) -> list[RawRecord]:
    return load_blueferry()


def _eds(settings: Settings) -> list[RawRecord]:
    return load_eds()


def _neomutt(settings: Settings) -> list[RawRecord]:
    configured = settings.path_for("neomutt")
    return load_neomutt((configured,) if configured else CONFIG_PATHS)


def _notmuch(settings: Settings) -> list[RawRecord]:
    try:
        floor = int(settings.option("notmuch", "min_messages", MIN_MESSAGES))
    except (TypeError, ValueError):
        floor = MIN_MESSAGES
    return load_notmuch(max(1, floor))


def _vdir(settings: Settings) -> list[RawRecord]:
    return load_vdir(settings.path_for("vdir"))


SOURCES: dict[str, Callable[[Settings], list[RawRecord]]] = {
    "abook": _abook,
    "blueferry": _blueferry,
    "eds": _eds,
    "neomutt": _neomutt,
    "notmuch": _notmuch,
    "vdir": _vdir,
}


def load_all(
    settings: Settings | None = None,
) -> tuple[list[RawRecord], dict[str, str]]:
    """Load every enabled source, tolerating individual failures.

    One unreachable source must not empty the address book, so failures are
    reported alongside whatever did load. A source the user turned off is not
    a failure and is simply absent.
    """
    settings = settings or config_module.load()
    records: list[RawRecord] = []
    errors: dict[str, str] = {}
    for name, loader in SOURCES.items():
        if not settings.enabled(name):
            log.debug("source %s is disabled", name)
            continue
        try:
            loaded = loader(settings)
        except Exception as error:  # noqa: BLE001 - a source is untrusted input
            log.warning("source %s failed: %s", name, error)
            errors[name] = f"{type(error).__name__}: {error}"
            continue
        log.info("source %s returned %d records", name, len(loaded))
        records.extend(loaded)
    return records, errors


__all__ = [
    "SOURCES",
    "load_abook",
    "load_all",
    "load_blueferry",
    "load_eds",
    "load_neomutt",
    "load_notmuch",
    "load_vdir",
]

"""omadexd — owns the store and serves io.omadex.Contacts1."""
from __future__ import annotations

import logging
import signal
import sys
from contextlib import suppress

from omadex.engine import sync
from omadex.store import open_store

log = logging.getLogger("omadexd")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s: %(message)s"
    )

    import dbus
    import dbus.mainloop.glib
    from gi.repository import GLib

    from omadex.service import ContactsService

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    loop = GLib.MainLoop()

    with open_store() as store:
        result = sync(store)
        log.info(
            "%d identities from %d records in %.2fs (%d for review)",
            result.identities, result.records, result.elapsed, result.review,
        )
        for source, error in result.errors.items():
            log.warning("source %s unavailable: %s", source, error)

        service = ContactsService(dbus.SessionBus(), store)
        service.ContactsChanged()

        for received in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(
                GLib.PRIORITY_HIGH, received, lambda: (loop.quit(), False)[1]
            )
        log.info("serving %s", "io.omadex.Contacts")
        with suppress(KeyboardInterrupt):
            loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

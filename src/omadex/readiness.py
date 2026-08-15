"""Is each source actually usable, and if not, what would fix it.

Two audiences. Someone opening OmaDex for the first time with nothing set up
needs to be told what to install; someone whose iPhone source stopped working
needs to know it is a missing backend method rather than a bug.

Every probe is cheap and read-only. Nothing here starts a sync.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from omadex import config as config_module
from omadex.config import DESCRIPTIONS, Settings, label

# The BlueFerry method OmaDex needs. It exists only on the patched backend
# until the pull request lands upstream.
REQUIRED_BLUEFERRY_METHOD = "ListContacts"
BLUEFERRY_FORK_HINT = (
    "Install blueferry-backend from the OmaDex fork: it adds ListContacts, "
    "which upstream does not have yet. The GTK, Qt and Quickshell clients are "
    "unaffected — only the backend package needs replacing."
)

READY = "ready"          # configured, reachable, has something to give
EMPTY = "empty"          # reachable but holds nothing yet
MISSING = "missing"      # not installed or not configured
BLOCKED = "blocked"      # installed, but something must be done first


@dataclass(frozen=True, slots=True)
class Readiness:
    source: str
    state: str
    detail: str
    hint: str = ""

    @property
    def usable(self) -> bool:
        return self.state in (READY, EMPTY)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "label": label(self.source),
            "description": DESCRIPTIONS.get(self.source, ""),
            "state": self.state,
            "detail": self.detail,
            "hint": self.hint,
            "usable": self.usable,
        }


def _file_source(source: str, path: Path | None, what: str, install: str) -> Readiness:
    if path is None:
        return Readiness(source, MISSING, f"no {what} configured", install)
    if not path.exists():
        return Readiness(source, MISSING, f"{path} does not exist", install)
    return Readiness(source, READY, str(path))


def check_abook(settings: Settings) -> Readiness:
    return _file_source(
        "abook", settings.path_for("abook"), "address book",
        "Install abook and create an address book, or point OmaDex at an "
        "existing one in settings.",
    )


def check_neomutt(settings: Settings) -> Readiness:
    from omadex.sources.neomutt import CONFIG_PATHS, load_neomutt

    configured = settings.path_for("neomutt")
    paths = (configured,) if configured else CONFIG_PATHS
    if not any(path.exists() for path in paths):
        return Readiness(
            "neomutt", MISSING, "no neomutt configuration found",
            "Install neomutt, or point OmaDex at your config directory.",
        )
    try:
        found = load_neomutt(paths)
    except Exception as error:  # noqa: BLE001
        return Readiness("neomutt", BLOCKED, str(error))
    if not found:
        return Readiness(
            "neomutt", EMPTY, "configuration found, no aliases defined",
            "Add `alias` lines to your neomutt configuration.",
        )
    return Readiness("neomutt", READY, f"{len(found)} aliases")


def check_notmuch(settings: Settings) -> Readiness:
    if not shutil.which("notmuch"):
        return Readiness(
            "notmuch", MISSING, "notmuch is not installed",
            "Install notmuch, sync mail to a local maildir (isync or "
            "offlineimap), then run `notmuch setup`.",
        )
    probe = subprocess.run(
        ["notmuch", "config", "get", "database.path"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    # An unconfigured notmuch exits 0 with nothing on stdout, so the exit
    # status alone reports a working database that does not exist.
    database = probe.stdout.strip()
    if probe.returncode != 0 or not database:
        return Readiness(
            "notmuch", BLOCKED, "no notmuch database",
            "notmuch indexes local mail; it does not fetch any. Sync a "
            "maildir with isync or offlineimap, then run `notmuch setup`.",
        )
    if not (Path(database) / ".notmuch").exists():
        return Readiness(
            "notmuch", BLOCKED, f"{database} has not been indexed",
            "Run `notmuch new` to index the maildir.",
        )
    return Readiness("notmuch", READY, database)


def check_eds(settings: Settings) -> Readiness:
    try:
        import gi

        gi.require_version("EDataServer", "1.2")
        from gi.repository import EDataServer
    except Exception:  # noqa: BLE001 - absence is the answer, not an error
        return Readiness(
            "eds", MISSING, "Evolution Data Server is not installed",
            "Install evolution-data-server, and gnome-online-accounts to "
            "attach a Google or CardDAV account.",
        )
    try:
        registry = EDataServer.SourceRegistry.new_sync(None)
        books = registry.list_sources(EDataServer.SOURCE_EXTENSION_ADDRESS_BOOK)
    except Exception as error:  # noqa: BLE001
        return Readiness("eds", BLOCKED, str(error))
    if not books:
        return Readiness(
            "eds", EMPTY, "no address books",
            "Attach an account with gnome-online-accounts.",
        )
    return Readiness("eds", READY, f"{len(books)} address books")


def check_vdir(settings: Settings) -> Readiness:
    path = settings.path_for("vdir")
    if path is None or not path.is_dir():
        return Readiness(
            "vdir", MISSING, "no vCard directory",
            "Configure a contacts pair in vdirsyncer and run "
            "`vdirsyncer discover && vdirsyncer sync`.",
        )
    cards = sum(1 for _ in path.rglob("*.vcf"))
    if not cards:
        return Readiness(
            "vdir", EMPTY, f"{path} holds no vCards",
            "Run `vdirsyncer sync` to populate it.",
        )
    return Readiness("vdir", READY, f"{cards} vCards")


def _introspect_blueferry() -> str:
    """Ask the running backend what methods it exports. Seam for tests."""
    import dbus

    bus = dbus.SessionBus()
    return str(dbus.Interface(
        bus.get_object("io.weirdware.BlueFerry", "/io/weirdware/BlueFerry"),
        "org.freedesktop.DBus.Introspectable",
    ).Introspect(timeout=10))


def check_blueferry(settings: Settings) -> Readiness:
    """Distinguish 'not installed' from 'installed but unpatched'.

    The second is the interesting one: everything looks healthy, the phone is
    paired, and contacts silently never arrive because the backend has no way
    to enumerate them.
    """
    if not shutil.which("blueferry"):
        return Readiness(
            "blueferry", MISSING, "BlueFerry is not installed",
            "Install blueferry-backend from the OmaDex fork and pair your "
            "iPhone. Upstream BlueFerry cannot enumerate contacts yet.",
        )
    try:
        introspection = _introspect_blueferry()
    except Exception as error:  # noqa: BLE001
        return Readiness(
            "blueferry", BLOCKED, f"backend unreachable: {error}",
            "Start it with `systemctl --user start blueferry`, or open a "
            "BlueFerry client to pair your iPhone.",
        )
    if f'name="{REQUIRED_BLUEFERRY_METHOD}"' not in str(introspection):
        return Readiness(
            "blueferry", BLOCKED,
            f"backend has no {REQUIRED_BLUEFERRY_METHOD} method",
            BLUEFERRY_FORK_HINT,
        )
    return Readiness("blueferry", READY, "backend supports ListContacts")


CHECKS = {
    "abook": check_abook,
    "blueferry": check_blueferry,
    "eds": check_eds,
    "neomutt": check_neomutt,
    "notmuch": check_notmuch,
    "vdir": check_vdir,
}


def check_all(settings: Settings | None = None) -> list[Readiness]:
    settings = settings or config_module.load()
    found: list[Readiness] = []
    for source, check in CHECKS.items():
        try:
            found.append(check(settings))
        except Exception as error:  # noqa: BLE001 - a probe must never throw
            found.append(Readiness(source, BLOCKED, f"{type(error).__name__}: {error}"))
    return found


def needs_onboarding(checks: list[Readiness]) -> bool:
    """True when no source can supply a single contact.

    Nothing to show and nothing to search: the only useful screen is one that
    says what to install.
    """
    return not any(check.state == READY for check in checks)

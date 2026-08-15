"""User settings: which backends are on, and where their data lives.

JSON rather than TOML because Python can only read TOML, not write it, and a
settings pane has to write. The file is merged over the defaults on every read,
so a key that does not appear behaves as the default rather than as missing —
and a hand-edited file with a typo loses one setting instead of all of them.

Unknown keys are preserved on save. A newer OmaDex writing a field this
version does not understand must not have it erased by an older one.
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path(
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
) / "omadex"
CONFIG_PATH = CONFIG_DIR / "settings.json"
DIR_MODE = 0o700

# How each source's own application is opened for a contact. Placeholders are
# substituted as separate argv entries, never into a shell string — a contact
# is other people's data and must not reach a command line as text.
#
# Only some of these can genuinely preload a contact. abook has no option to
# open at a given record (only --datafile and --mutt-query), and BlueFerry's
# clients expose no thread selector, so those two open the application and
# nothing more. Where a placeholder cannot be filled the entry is offered as
# unavailable rather than launched half-formed.
DEFAULTS: dict = {
    "terminal": "",
    "sources": {
        "abook": {
            "enabled": True,
            "path": "~/.abook/addressbook",
            "requires": "abook",
            # Focus an abook already on screen rather than stacking another.
            "launch": ["omarchy-launch-or-focus-tui", "--app-id=abook",
                       "abook", "--datafile", "{path}"],
        },
        "blueferry": {
            "enabled": True,
            "requires": "blueferry-gtk",
            "launch": ["omarchy-launch-or-focus",
                       "io.weirdware.BlueFerry.Gtk", "blueferry-gtk"],
        },
        "eds": {
            "enabled": True,
            "requires": "gnome-contacts",
            "launch": ["omarchy-launch-or-focus",
                       "org.gnome.Contacts", "gnome-contacts"],
        },
        "neomutt": {
            "enabled": True,
            "path": "~/.config/neomutt",
            "requires": "neomutt",
            # A fresh instance: focusing an existing one would drop the
            # address, and composing to someone is the whole point.
            "launch": ["omarchy-launch-terminal", "neomutt", "{email}"],
        },
        "notmuch": {
            "enabled": True,
            "min_messages": 3,
            "requires": "notmuch",
            # $1 keeps the address in argv; the script text never contains it.
            "launch": ["omarchy-launch-terminal", "sh", "-c",
                       "notmuch search from:\"$1\" or to:\"$1\" | less -R",
                       "sh", "{email}"],
        },
        "vdir": {
            "enabled": True,
            "path": "~/.local/share/vdirsyncer/contacts",
            "requires": "xdg-open",
            # uwsm-app puts the handler in the session's systemd scope, which
            # a bare spawn from a detached process does not survive.
            "launch": ["uwsm-app", "--", "xdg-open", "{file}"],
        },
    },
    "store": {
        "path": "~/.local/state/omadex/contacts.sqlite",
    },
}

# What a settings pane may offer for each source, and how to render it. A
# source with no path is one OmaDex reaches through a service rather than a
# file, and there is nothing to browse for.
FIELDS: dict[str, list[tuple[str, str]]] = {
    "abook": [("path", "Address book file")],
    "blueferry": [],
    "eds": [],
    "neomutt": [("path", "Config directory")],
    "notmuch": [("min_messages", "Minimum messages exchanged")],
    "vdir": [("path", "vCard directory")],
}

# What a source is called in front of a person. The keys stay as they are —
# they name config sections, database rows and --source arguments — but nobody
# reading a contact card should have to know that "blueferry" is their phone.
LABELS: dict[str, str] = {
    "abook": "abook",
    "blueferry": "iPhone",
    "eds": "Evolution",
    "neomutt": "neomutt",
    "notmuch": "Mail",
    "vdir": "CardDAV",
}


def label(source: str) -> str:
    return LABELS.get(source, source)


def source_for_label(text: str) -> str | None:
    """Resolve a label back to its source key, case-insensitively.

    Someone who sees "iPhone" in the interface will type "iPhone".
    """
    wanted = (text or "").strip().casefold()
    if wanted in LABELS:
        return wanted
    for source, shown in LABELS.items():
        if shown.casefold() == wanted:
            return source
    return None


DESCRIPTIONS: dict[str, str] = {
    "abook": "Plain-text address book",
    "blueferry": "iPhone phonebook over Bluetooth",
    "eds": "Evolution Data Server, including any connected account",
    "neomutt": "Mail aliases you wrote by hand",
    "notmuch": "People you exchange mail with",
    "vdir": "CardDAV contacts synced to disk by vdirsyncer",
}


def expand(value: str | os.PathLike[str]) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(value))))


def _merge(defaults: dict, overrides: dict) -> dict:
    merged = deepcopy(defaults)
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


@dataclass(frozen=True, slots=True)
class Settings:
    data: dict

    @property
    def sources(self) -> dict:
        return self.data.get("sources", {})

    def enabled(self, source: str) -> bool:
        return bool(self.sources.get(source, {}).get("enabled", True))

    def option(self, source: str, key: str, fallback=None):
        return self.sources.get(source, {}).get(key, fallback)

    def path_for(self, source: str) -> Path | None:
        raw = self.option(source, "path")
        return expand(raw) if raw else None

    @property
    def store_path(self) -> Path:
        return expand(
            self.data.get("store", {}).get("path", DEFAULTS["store"]["path"])
        )


def _stored() -> dict:
    """Only what the user changed, without the defaults merged in."""
    try:
        stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return stored if isinstance(stored, dict) else {}


def load() -> Settings:
    """Defaults, with the user's file merged over them.

    An unreadable or malformed file yields the defaults: a broken settings
    file must not take the address book down with it.
    """
    return Settings(_merge(DEFAULTS, _stored()))


def save(settings: Settings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    temporary = CONFIG_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(settings.data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(CONFIG_PATH)
    CONFIG_PATH.chmod(0o600)


def update_source(source: str, **changes) -> Settings:
    """Change one source's settings, leaving every other key untouched.

    Only the change is written. Saving the merged result would freeze today's
    defaults into the file, and a later version's improved default — a new
    launch command, say — would never reach anyone who had ever touched a
    setting.
    """
    data = deepcopy(_stored())
    data.setdefault("sources", {}).setdefault(source, {}).update(changes)
    save(Settings(data))
    return load()

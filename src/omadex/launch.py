"""Open a contact in the application the record came from.

The point is provenance you can act on: a record says it came from abook, so
open abook; from neomutt, so compose to them. What "open" can mean varies by
application, and the honest answer is sometimes "launch it, that is all it
supports".

Placeholders are filled into separate argv entries. A contact is other
people's data, and it must never be pasted into a command line as text — the
notmuch entry passes the address as `$1` for exactly that reason.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from omadex.config import Settings

# Applications that cannot open at a given contact. abook offers only
# --datafile and --mutt-query; BlueFerry's clients expose no thread selector.
# Recorded so a UI can say so rather than implying more than happens.
OPENS_APPLICATION_ONLY = frozenset({"abook", "blueferry", "eds"})

_TERMINAL_CANDIDATES = ("xdg-terminal-exec", "alacritty", "ghostty", "foot", "kitty")


class LaunchError(RuntimeError):
    """Raised when a source cannot be opened for this contact."""


@dataclass(frozen=True, slots=True)
class LaunchTarget:
    source: str
    argv: list[str]
    preloads_contact: bool

    @property
    def program(self) -> str:
        return self.argv[0] if self.argv else ""


def terminal(settings: Settings) -> str | None:
    """The terminal to run console applications in.

    $TERMINAL first — Omarchy sets it to xdg-terminal-exec, the freedesktop
    default-terminal launcher — then whatever is installed.
    """
    configured = (settings.data.get("terminal") or "").strip()
    if configured:
        return configured
    from_environment = (os.environ.get("TERMINAL") or "").strip()
    if from_environment and shutil.which(from_environment):
        return from_environment
    for candidate in _TERMINAL_CANDIDATES:
        if shutil.which(candidate):
            return candidate
    return None


def _bare(key: str) -> str:
    _, separator, rest = key.partition(":")
    return rest if separator else key


def _vcard_file(settings: Settings, record: dict) -> str | None:
    """vdir's source_id is '<file stem>:<index within the file>'."""
    root = settings.path_for("vdir")
    stem = str(record.get("source_id", "")).rsplit(":", 1)[0]
    if not root or not stem:
        return None
    candidate = root / f"{stem}.vcf"
    if candidate.exists():
        return str(candidate)
    matches = sorted(root.rglob(f"{stem}.vcf"))
    return str(matches[0]) if matches else None


def _values(settings: Settings, source: str, identity: dict, record: dict) -> dict:
    emails = record.get("emails") or identity.get("emails") or []
    phones = record.get("phones") or identity.get("phones") or []
    path = settings.path_for(source)
    values = {
        "name": identity.get("name", ""),
        "email": _bare(emails[0]) if emails else "",
        "phone": _bare(phones[0]) if phones else "",
        "path": str(path) if path else "",
        "source_id": str(record.get("source_id", "")),
    }
    if source == "vdir":
        values["file"] = _vcard_file(settings, record) or ""
    return values


def target_for(
    source: str, identity: dict, record: dict, settings: Settings
) -> LaunchTarget:
    template = settings.option(source, "launch")
    if not template:
        raise LaunchError(f"{source} has no launch command configured")

    values = _values(settings, source, identity, record)
    resolved: list[str] = []
    for part in template:
        if part == "{terminal}":
            found = terminal(settings)
            if not found:
                raise LaunchError("no terminal found; set \"terminal\" in settings")
            resolved.append(found)
            continue
        filled = part
        for key, value in values.items():
            token = "{" + key + "}"
            if token in filled:
                if not value:
                    raise LaunchError(
                        f"{source} needs {key} to open this contact, "
                        "and this one has none"
                    )
                filled = filled.replace(token, value)
        resolved.append(filled)

    # Check the application, not the launcher wrapping it: every Omarchy
    # helper exists whether or not the thing it starts does.
    needed = settings.option(source, "requires") or resolved[0]
    if not shutil.which(needed) and not Path(needed).exists():
        raise LaunchError(f"{needed} is not installed")

    return LaunchTarget(
        source=source,
        argv=resolved,
        preloads_contact=source not in OPENS_APPLICATION_ONLY,
    )


def open_source(
    source: str, identity: dict, record: dict, settings: Settings
) -> LaunchTarget:
    """Launch detached: OmaDex must not outlive or own the application."""
    found = target_for(source, identity, record, settings)
    subprocess.Popen(  # noqa: S603 - argv list, never a shell
        found.argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return found

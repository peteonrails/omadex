"""notmuch — the people you actually correspond with.

This is the one source that is *evidence* rather than a list someone
maintained: an address you have exchanged mail with repeatedly belongs in your
address book whether or not you ever added it.

That strength is also the risk. A mail index contains every no-reply sender,
mailing list, and one-off notification you have received, and importing all of
it would bury the real contacts. Two defences: a minimum correspondence count,
and a machine-address filter. Both are deliberately conservative — a contact
missing from search is a smaller failure than a search full of robots.
"""
from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence

from omadex.models import RawRecord

# Someone you have exchanged this many messages with is a correspondent, not
# an accident. One-off senders stay out.
MIN_MESSAGES = 3
QUERY_TIMEOUT_SECONDS = 120

# `notmuch address --output=count` emits: "<count>\tName <address>"
_COUNTED = re.compile(r"^\s*(?P<count>\d+)\s+(?P<rest>.+?)\s*$")
_RECIPIENT = re.compile(r"^(?P<name>.*?)\s*<(?P<address>[^>]+)>$")

_MACHINE_LOCALPARTS = (
    "noreply", "no-reply", "donotreply", "do-not-reply", "notifications",
    "notification", "mailer-daemon", "postmaster", "bounce", "bounces",
    "automated", "autoreply", "support+", "reply+", "notify",
)


def is_machine_address(address: str) -> bool:
    """True for addresses that answer no one."""
    local = address.split("@", 1)[0].casefold()
    return any(local.startswith(marker) for marker in _MACHINE_LOCALPARTS)


def _run(argv: Sequence[str]) -> str:
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=QUERY_TIMEOUT_SECONDS, check=False
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "notmuch failed").strip().split("\n")[0])
    return result.stdout


def load_notmuch(min_messages: int = MIN_MESSAGES, runner=_run) -> list[RawRecord]:
    """Addresses seen in sent or received mail, above the correspondence floor.

    Both sides are queried: `from` alone misses everyone who only ever reads
    what you send them.
    """
    output = runner([
        "notmuch", "address", "--output=count", "--deduplicate=address", "*",
    ])

    records: list[RawRecord] = []
    for line in output.splitlines():
        counted = _COUNTED.match(line)
        if not counted:
            continue
        if int(counted.group("count")) < min_messages:
            continue
        recipient = _RECIPIENT.match(counted.group("rest"))
        if recipient:
            name = recipient.group("name").strip().strip('"')
            address = recipient.group("address").strip()
        else:
            name, address = "", counted.group("rest").strip()
        if "@" not in address or is_machine_address(address):
            continue
        records.append(RawRecord(
            source="notmuch",
            source_id=address.casefold(),
            name=name,
            emails=(address,),
        ))
    return records

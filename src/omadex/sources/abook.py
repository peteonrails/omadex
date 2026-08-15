"""abook — plain-text address book at ~/.abook/addressbook.

Section numbers are positions in the file, not identities: abook renumbers on
edit. Nothing downstream may treat `source_id` as stable, which is why
overrides are keyed by address instead.
"""
from __future__ import annotations

import re
from pathlib import Path

from omadex.models import RawRecord

ABOOK_PATH = Path.home() / ".abook" / "addressbook"

_SECTION = re.compile(r"^\[(\d+)\]$")
_PHONE_FIELDS = ("phone", "mobile", "workphone", "fax", "otherphone")
# abook scatters one postal address over five keys; the display form is a
# single line, assembled in the order a person would write it.
_POSTAL_FIELDS = ("address", "address2", "city", "state", "zip", "country")


def _compose_postal(fields: dict[str, str]) -> tuple[str, ...]:
    def value(key: str) -> str:
        # abook stores a second address line as a literal backslash-n.
        return fields.get(key, "").replace("\\n", ", ").strip()

    street = ", ".join(
        value(key) for key in ("address", "address2") if value(key)
    )
    city = value("city")
    region = " ".join(value(key) for key in ("state", "zip") if value(key))
    country = value("country")
    line = ", ".join(part for part in (street, city, region, country) if part)
    return (line,) if line else ()


def load_abook(path: Path | None = None) -> list[RawRecord]:
    source = path or ABOOK_PATH
    if not source.exists():
        return []

    records: list[RawRecord] = []
    fields: dict[str, str] | None = None
    section = ""

    def flush() -> None:
        if fields is None:
            return
        phones = tuple(
            value.strip()
            for field in _PHONE_FIELDS
            for value in fields.get(field, "").split(",")
            if value.strip()
        )
        emails = tuple(
            value.strip()
            for value in fields.get("email", "").split(",")
            if value.strip()
        )
        postal = _compose_postal(fields)
        name = fields.get("name", "").strip()
        if name or phones or emails or postal:
            records.append(
                RawRecord("abook", section, name, phones, emails, postal)
            )

    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        match = _SECTION.match(line)
        if match:
            flush()
            section, fields = match.group(1), {}
            continue
        if not line or line.startswith("#") or fields is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip()
    flush()
    return records

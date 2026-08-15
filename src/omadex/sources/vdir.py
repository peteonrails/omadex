"""A vdir of vCards — the shape vdirsyncer writes and CardDAV speaks.

One adapter covers every CardDAV-backed account: Google, iCloud, Fastmail,
Nextcloud. vdirsyncer owns the network and the OAuth token; OmaDex reads the
directory it leaves behind. That is the whole reason to prefer it over talking
to Google's People API directly — the contacts scope is "sensitive", and a
distributable plugin doing its own OAuth needs Google app verification.

The parser is deliberately small: real address books use a handful of vCard
properties, and anything exotic is better ignored than half-understood.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from omadex.models import RawRecord

VDIR_PATH = Path(
    os.environ.get(
        "OMADEX_VDIR_PATH",
        Path.home() / ".local" / "share" / "vdirsyncer" / "contacts",
    )
)

# "ITEM1.EMAIL;TYPE=work:alice@example.com" -> name EMAIL, value alice@…
_PROPERTY = re.compile(
    r"^(?:[A-Za-z0-9-]+\.)?(?P<name>[A-Za-z0-9-]+)(?P<params>;[^:]*)?:(?P<value>.*)$"
)


def _unfold(text: str) -> list[str]:
    """Join vCard continuation lines, which begin with a space or tab."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    return (
        value.replace("\\n", " ").replace("\\N", " ")
        .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
        .strip()
    )


def structured_postal(value: str) -> str:
    """ADR is PO;extended;street;locality;region;postcode;country.

    Rendered as one display line. Empty components are dropped rather than
    leaving the commas of an address nobody wrote.
    """
    parts = [_unescape(part) for part in value.split(";")]
    parts += [""] * (7 - len(parts))
    street = ", ".join(part for part in (parts[2], parts[1], parts[0]) if part)
    region = " ".join(part for part in (parts[4], parts[5]) if part)
    return ", ".join(
        part for part in (street, parts[3], region, parts[6]) if part
    )


def _structured_name(value: str) -> str:
    """N is Family;Given;Additional;Prefix;Suffix — render it readably."""
    parts = [_unescape(part) for part in value.split(";")]
    family = parts[0] if parts else ""
    given = parts[1] if len(parts) > 1 else ""
    return " ".join(part for part in (given, family) if part)


def parse_vcards(text: str, source_id: str) -> list[RawRecord]:
    records: list[RawRecord] = []
    name = structured = ""
    emails: list[str] = []
    phones: list[str] = []
    postal: list[str] = []
    index = 0

    def flush() -> None:
        nonlocal name, structured, emails, phones, postal, index
        display = name or structured
        if display or emails or phones or postal:
            records.append(RawRecord(
                source="vdir",
                source_id=f"{source_id}:{index}",
                name=display,
                phones=tuple(phones),
                emails=tuple(emails),
                postal=tuple(postal),
            ))
            index += 1
        name = structured = ""
        emails, phones, postal = [], [], []

    for line in _unfold(text):
        match = _PROPERTY.match(line.strip())
        if not match:
            continue
        key = match.group("name").upper()
        value = match.group("value")
        params = (match.group("params") or "").upper()
        # A base64 photo or a quoted-printable body is not worth decoding here.
        if "ENCODING=" in params or "B64" in params:
            continue

        if key == "END" and value.strip().upper() == "VCARD":
            flush()
        elif key == "FN":
            name = _unescape(value)
        elif key == "N":
            structured = _structured_name(value)
        elif key == "EMAIL":
            address = _unescape(value)
            if "@" in address:
                emails.append(address)
        elif key == "ADR":
            composed = structured_postal(value)
            if composed:
                postal.append(composed)
        elif key == "TEL":
            number = _unescape(value).replace("tel:", "")
            if number:
                phones.append(number)

    flush()
    return records


def load_vdir(path: Path | None = None) -> list[RawRecord]:
    """Read every .vcf under a vdir, including per-collection subdirectories."""
    root = path or VDIR_PATH
    if not root.is_dir():
        return []

    records: list[RawRecord] = []
    for vcf in sorted(root.rglob("*.vcf")):
        try:
            text = vcf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        records.extend(parse_vcards(text, vcf.stem))
    return records

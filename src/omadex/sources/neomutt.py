"""neomutt aliases — small, hand-curated, and therefore high quality.

An alias is someone you deliberately wrote down, so these records deserve more
trust than anything harvested automatically. The file is plain text:

    alias ae Alice Example <alice@example.com>
    alias bob bob@example.com
    alias team Alice <a@x.com>, Bob <b@y.com>

A single alias may name several people; each becomes its own record, because
"team" is a distribution list rather than a person.
"""
from __future__ import annotations

import re
from pathlib import Path

from omadex.models import RawRecord

CONFIG_PATHS = (
    Path.home() / ".config" / "neomutt",
    Path.home() / ".neomuttrc",
    Path.home() / ".muttrc",
    Path.home() / ".mutt",
)

_ALIAS = re.compile(r"^\s*alias\s+(?P<key>\S+)\s+(?P<targets>.+?)\s*$")
_ALIAS_FILE = re.compile(r"^\s*set\s+alias_file\s*=\s*[\"']?(?P<path>[^\"'\s]+)")
# "Alice Example <alice@example.com>" or a bare address.
_RECIPIENT = re.compile(r"^(?P<name>.*?)\s*<(?P<address>[^>]+)>$")


def _candidate_files(paths=CONFIG_PATHS) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(child for child in path.rglob("*") if child.is_file()))
        elif path.is_file():
            files.append(path)
    return files


def _split_targets(raw: str) -> list[str]:
    """Split on commas that separate recipients, not commas inside a name.

    "Example, Alice <a@x.com>, Bob <b@y.com>" is two people, and the first
    one's surname-first form must survive.
    """
    parts, depth, current = [], 0, ""
    for character in raw:
        if character == "<":
            depth += 1
        elif character == ">":
            depth = max(0, depth - 1)
        # Only a comma that follows a complete address separates two people;
        # anything earlier is punctuation inside a name.
        if character == "," and depth == 0 and "@" in current:
            parts.append(current)
            current = ""
            continue
        current += character
    if current.strip():
        parts.append(current)
    return [part.strip() for part in parts if part.strip()]


def load_neomutt(paths=CONFIG_PATHS) -> list[RawRecord]:
    files = list(_candidate_files(paths))
    seen_alias_files: set[Path] = set()

    # `set alias_file` often points somewhere outside the config directory.
    for path in list(files):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            match = _ALIAS_FILE.match(line)
            if match:
                extra = Path(match.group("path")).expanduser()
                if extra.is_file() and extra not in seen_alias_files:
                    seen_alias_files.add(extra)
                    files.append(extra)

    records: list[RawRecord] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines()):
            if line.lstrip().startswith("#"):
                continue
            match = _ALIAS.match(line)
            if not match:
                continue
            for position, target in enumerate(_split_targets(match.group("targets"))):
                recipient = _RECIPIENT.match(target)
                if recipient:
                    name = recipient.group("name").strip().strip('"')
                    address = recipient.group("address").strip()
                else:
                    name, address = "", target.strip()
                if "@" not in address:
                    continue
                records.append(RawRecord(
                    source="neomutt",
                    source_id=f"{path.name}:{number}:{position}",
                    name=name,
                    emails=(address,),
                ))
    return records

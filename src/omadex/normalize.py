"""Address normalization — the vocabulary the whole resolver speaks.

A *key* is a normalized, comparable destination: `mailto:alice@example.com` or
`tel:5551234567`. Keys are the only thing OmaDex treats as identity. Names are
display data; they are used to *withhold* a merge, never to make one.
"""
from __future__ import annotations

import re

_DIGITS = re.compile(r"[^0-9]")
# What a written phone number may contain. Anything with a letter in it is a
# name, a handle, or a URI — not a number someone would dial.
_PHONE_SHAPED = re.compile(r"[0-9+()\-.\s/]+")
_EXTENSION = re.compile(r"\s*(?:x|ext|ext\.|extension|#)\s*\.?\s*\d+\s*$", re.I)
_EMAIL_SHAPED = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NAME_SPLIT = re.compile(r"[^\w']+")

# Tokens that carry no identifying weight when comparing two display names.
_NAME_NOISE = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "jr", "sr", "ii", "iii", "iv",
    "and", "the", "of", "&",
})


def phone_key(raw: str | None) -> str | None:
    """Last ten digits for NANP-sized numbers, so formatting cannot matter.

    +1 (555) 123-4567, 15551234567 and 5551234567 are one key. Shorter
    strings keep their digits when there are at least seven, the shortest
    plausibly dialable thing; anything less is an extension or a typo.

    The shape is checked before the digits are extracted. Stripping
    non-digits from anything at all is how "person:9b959c70f9b9c3e4" became
    a plausible-looking phone number — a hex digest has plenty of digits in it.
    """
    # An extension belongs to the desk, not the line. Strip it before keying:
    # left in, "555-234-1500 x208" keys as 2341500208 — a shifted number
    # belonging to nobody — and rejecting it outright loses the contact.
    value = _EXTENSION.sub("", (raw or "").strip()).strip()
    if not value or not _PHONE_SHAPED.fullmatch(value):
        return None
    digits = _DIGITS.sub("", value)
    if len(digits) >= 10:
        return f"tel:{digits[-10:]}"
    return f"tel:{digits}" if len(digits) >= 7 else None


def email_key(raw: str | None) -> str | None:
    value = (raw or "").strip().lower()
    return f"mailto:{value}" if _EMAIL_SHAPED.match(value) else None


def address_key(raw: str | None) -> str | None:
    """Best-effort key for an address of unknown kind."""
    return email_key(raw) or phone_key(raw)


def key_kind(key: str) -> str:
    return key.partition(":")[0]


def name_tokens(name: str | None) -> set[str]:
    """Identifying words in a display name, lowercased, noise removed."""
    return {
        token
        for token in _NAME_SPLIT.split((name or "").casefold())
        if token and token not in _NAME_NOISE and len(token) > 1
    }


def names_agree(left: str | None, right: str | None) -> bool:
    """True when two display names share an identifying word.

    Unknown names agree with everything: an unnamed record carries no
    evidence either way, and refusing to merge on missing data would strand
    every number-only contact in its own island.
    """
    left_tokens, right_tokens = name_tokens(left), name_tokens(right)
    if not left_tokens or not right_tokens:
        return True
    return bool(left_tokens & right_tokens)


def is_person(name: str | None, keys: set[str] | None = None) -> bool:
    """False for carrier shortcodes and other non-human phonebook entries.

    iPhones ship entries like "#BAL - Check Balance" and "*611"; they are
    dial codes, not people, and they pollute every search. The test is
    deliberately narrow — a leading # or * is what carriers use and what no
    real name starts with. Anything with a real address is kept regardless,
    because a wrong exclusion is worse than a stray row.
    """
    stripped = (name or "").strip()
    if stripped.startswith(("#", "*")):
        return False
    return bool(stripped or keys)


def pair_key(left: str, right: str) -> tuple[str, str]:
    """Order-independent handle for a pair of address keys."""
    return (left, right) if left <= right else (right, left)

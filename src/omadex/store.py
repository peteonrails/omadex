"""SQLite store: a rebuildable projection plus one table that is not.

Everything derived from sources — records, identities, the search index — is
disposable and rebuilt on every sync. `overrides` is the exception: it holds
human decisions, is never cleared by a sync, and is keyed by *address* rather
than by row id because source ids are not stable (abook renumbers on edit).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from omadex import config as config_module
from omadex.crypto import KeyRing, StorageUnavailable, is_encrypted
from omadex.limits import MAX_PAGE, MAX_SEARCH_RESULTS
from omadex.models import Identity, Override, RawRecord, ReviewItem

log = logging.getLogger(__name__)

STATE_DIR = Path(
    os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")
) / "omadex"
DB_PATH = STATE_DIR / "contacts.sqlite"
DIR_MODE = 0o700

_SCHEMA = """
CREATE TABLE IF NOT EXISTS identities (
    key          TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    payload      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
    identity_key TEXT NOT NULL REFERENCES identities(key) ON DELETE CASCADE,
    source       TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    payload      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS addresses (
    identity_key TEXT NOT NULL REFERENCES identities(key) ON DELETE CASCADE,
    address_key  TEXT NOT NULL,
    UNIQUE(identity_key, address_key)
);
CREATE TABLE IF NOT EXISTS review (
    left_key   TEXT NOT NULL,
    right_key  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    UNIQUE(left_key, right_key)
);
CREATE TABLE IF NOT EXISTS overrides (
    left_key   TEXT NOT NULL,
    right_key  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (unixepoch('subsec')),
    UNIQUE(left_key, right_key)
);
CREATE INDEX IF NOT EXISTS idx_addresses_key ON addresses(address_key);
CREATE INDEX IF NOT EXISTS idx_records_identity ON records(identity_key);
"""


def _connect(path: Path | None = None) -> sqlite3.Connection:
    database = path or config_module.load().store_path
    directory = database.parent
    directory.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    if directory.stat().st_mode & 0o777 != DIR_MODE:
        directory.chmod(DIR_MODE)
    first_time = not database.exists()
    connection = sqlite3.connect(database)
    if first_time:
        database.chmod(0o600)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def open_store(
    path: Path | None = None,
    keyring: KeyRing | None = None,
    *,
    encrypt: bool | None = None,
) -> Iterator[Store]:
    """Open the configured database, or an explicit one for tests.

    Encryption is on unless the user turns it off. A wallet that cannot supply
    a key is fatal rather than a silent downgrade to plaintext: contacts are
    exactly the data someone would be dismayed to find unprotected.

    `encrypt=False` asks for plaintext outright. Passing no keyring cannot
    mean that, because the common call passes none and must still be
    encrypted — so the two possible intentions behind an absent keyring are
    kept apart here rather than guessed at.
    """
    settings = config_module.load()
    if encrypt is None:
        encrypt = settings.encrypt_store
    if keyring is None and encrypt:
        try:
            keyring = KeyRing.open()
        except StorageUnavailable as error:
            raise StorageUnavailable(
                f"{error}. Set store.encrypt to false in settings.json to "
                "store contacts in the clear instead."
            ) from error
    carried: list[Override] = []
    if keyring is not None:
        carried = _discard_plaintext_store(path or settings.store_path)
    connection = _connect(path)
    try:
        with connection:
            connection.executescript(_SCHEMA)
        store = Store(connection, keyring)
        for override in carried:
            store.set_override(override)
        yield store
    finally:
        connection.close()


def _carry_overrides(probe: sqlite3.Connection) -> list[Override]:
    """Read merge decisions out of a store in whichever shape it was written.

    Two unencrypted shapes exist: releases before encryption kept the verdict
    in its own column, and this version with encryption turned off keeps the
    whole decision in a plaintext payload. Both are somebody's hand-made
    decisions and neither is worth losing to a schema change.
    """
    columns = {column[1] for column in probe.execute(
        "PRAGMA table_info(overrides)")}
    if "verdict" in columns:
        return [
            Override(row["left_key"], row["right_key"], row["verdict"])
            for row in probe.execute(
                "SELECT left_key, right_key, verdict FROM overrides")
        ]
    if "payload" not in columns:
        return []
    carried = []
    for row in probe.execute("SELECT payload FROM overrides"):
        try:
            decision = json.loads(row["payload"])
            carried.append(Override(
                decision["left"], decision["right"], decision["verdict"]))
        except (ValueError, KeyError, TypeError):
            continue  # Encrypted or malformed; nothing to carry.
    return carried


def _discard_plaintext_store(database: Path) -> list[Override]:
    """Replace a store written before encryption was turned on.

    Rewriting the rows in place would leave the old values behind in the
    file's free pages, so the file itself goes. Nothing is lost by that:
    everything in it is rebuilt from the sources on the next sync, except
    the merge decisions the user made by hand, which are returned here to be
    written back into the encrypted store.
    """
    if not database.exists():
        return []
    carried: list[Override] = []
    try:
        with closing(sqlite3.connect(database)) as probe:
            probe.row_factory = sqlite3.Row
            names = {row["name"] for row in probe.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "identities" not in names:
                return []
            row = probe.execute(
                "SELECT payload FROM identities LIMIT 1").fetchone()
            if row is not None and is_encrypted(row["payload"]):
                return []
            if "overrides" in names:
                carried = _carry_overrides(probe)
    except sqlite3.Error:
        # Unreadable is not a reason to delete someone's file.
        return []

    log.warning("replacing the unencrypted store at %s", database)
    for suffix in ("", "-wal", "-shm"):
        Path(str(database) + suffix).unlink(missing_ok=True)
    return carried


class Store:
    def __init__(
        self, connection: sqlite3.Connection, keyring: KeyRing | None = None
    ) -> None:
        self._db = connection
        self._keyring = keyring

    def _seal(self, value: str) -> str:
        return self._keyring.encrypt(value) if self._keyring else value

    def _open(self, value: str) -> str:
        return self._keyring.decrypt(value) if self._keyring else value

    def _blind(self, value: str) -> str:
        """Match on a keyed digest so lookup columns hold no addresses."""
        return self._keyring.blind(value) if self._keyring else value

    # ---- overrides: the durable half -----------------------------------

    def overrides(self) -> list[Override]:
        rows = self._db.execute("SELECT payload FROM overrides").fetchall()
        found = []
        for row in rows:
            data = json.loads(self._open(row["payload"]))
            found.append(Override(data["left"], data["right"], data["verdict"]))
        return found

    def set_override(self, override: Override) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO overrides(left_key, right_key, payload)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(left_key, right_key)"
                " DO UPDATE SET payload = excluded.payload",
                (self._blind(override.left_key), self._blind(override.right_key),
                 self._seal(json.dumps({
                     "left": override.left_key, "right": override.right_key,
                     "verdict": override.verdict,
                 }))),
            )

    def clear_override(self, left_key: str, right_key: str) -> bool:
        with self._db:
            cursor = self._db.execute(
                "DELETE FROM overrides WHERE left_key = ? AND right_key = ?",
                (self._blind(left_key), self._blind(right_key)),
            )
        return cursor.rowcount > 0

    # ---- the rebuildable projection ------------------------------------

    def replace(
        self,
        identities: Iterable[Identity],
        review: Iterable[ReviewItem],
    ) -> int:
        count = 0
        with self._db:
            self._db.execute("DELETE FROM identities")
            self._db.execute("DELETE FROM records")
            self._db.execute("DELETE FROM addresses")
            self._db.execute("DELETE FROM review")
            for identity in identities:
                count += 1
                # Deliberately not an upsert: two identities sharing a handle
                # is a resolver bug, and silently overwriting one person with
                # another is the worst possible way to find out.
                self._db.execute(
                    "INSERT INTO identities(key, display_name, payload)"
                    " VALUES (?, ?, ?)",
                    (self._blind(identity.key), self._seal(identity.display_name),
                     self._seal(json.dumps(identity.to_dict(), ensure_ascii=False))),
                )
                self._db.executemany(
                    "INSERT INTO records(identity_key, source, source_id, payload)"
                    " VALUES (?, ?, ?, ?)",
                    [
                        (self._blind(identity.key), record.source,
                         self._seal(record.source_id),
                         self._seal(json.dumps(record.to_dict(), ensure_ascii=False)))
                        for record in identity.records
                    ],
                )
                self._db.executemany(
                    "INSERT OR IGNORE INTO addresses(identity_key, address_key)"
                    " VALUES (?, ?)",
                    [(self._blind(identity.key), self._blind(key))
                     for key in sorted(identity.keys)],
                )
            self._db.executemany(
                "INSERT OR IGNORE INTO review(left_key, right_key, payload)"
                " VALUES (?, ?, ?)",
                [
                    (self._blind(item.left_key), self._blind(item.right_key),
                     self._seal(json.dumps(item.to_dict(), ensure_ascii=False)))
                    for item in review
                ],
            )
        return count

    # ---- reads ----------------------------------------------------------

    def search(self, query: str, limit: int = MAX_SEARCH_RESULTS) -> list[dict]:
        """Match names and addresses, best match first.

        The scan happens here rather than in an index because an index of
        plaintext names and addresses would defeat the encryption it sits
        beside. A few thousand people is small enough that it does not matter.
        """
        terms = [term.casefold() for term in _search_terms(query) if term]
        if not terms:
            return []

        found: list[tuple[int, str, dict]] = []
        for row in self._db.execute("SELECT payload FROM identities"):
            person = json.loads(self._open(row["payload"]))
            name = str(person.get("name", ""))
            folded = name.casefold()
            fields = [folded]
            fields += [
                key.partition(":")[2].casefold()
                for key in person.get("phones", []) + person.get("emails", [])
            ]
            fields += [str(line).casefold() for line in person.get("postal", [])]
            if not all(any(term in field for field in fields) for term in terms):
                continue
            # A name starting with the query outranks one merely containing
            # it, which in turn outranks a match on an address alone.
            first = terms[0]
            rank = 0 if folded.startswith(first) else (1 if first in folded else 2)
            found.append((rank, folded, person))

        found.sort(key=lambda item: (item[0], item[1]))
        bounded = max(1, min(int(limit), MAX_SEARCH_RESULTS))
        return [person for _, _, person in found[:bounded]]

    def get(self, address_key: str) -> dict | None:
        row = self._db.execute(
            "SELECT i.payload FROM identities i"
            " WHERE i.key = ?"
            " OR EXISTS (SELECT 1 FROM addresses a"
            "            WHERE a.identity_key = i.key AND a.address_key = ?)"
            " LIMIT 1",
            (self._blind(address_key), self._blind(address_key)),
        ).fetchone()
        return json.loads(self._open(row["payload"])) if row else None

    def list(self, offset: int = 0, limit: int = MAX_PAGE) -> list[dict]:
        """One page, ordered by name.

        The database cannot order this: the name column is ciphertext, and
        sorting ciphertext would page people in an order that means nothing.
        So the set is decrypted and ordered here, which is affordable at this
        size and keeps paging stable between calls.
        """
        return self._page(
            self._db.execute("SELECT payload FROM identities"), offset, limit
        )

    def _page(self, rows, offset: int, limit: int) -> list[dict]:
        people = [json.loads(self._open(row["payload"])) for row in rows]
        people.sort(key=lambda person: (str(person.get("name", "")).casefold(),
                                        person.get("key", "")))
        start = max(0, int(offset))
        return people[start:start + max(1, min(int(limit), MAX_PAGE))]

    def list_by_source(
        self, source: str, offset: int = 0, limit: int = MAX_PAGE
    ) -> list[dict]:
        """Everyone a single source contributed to, however they merged.

        A person is listed whether that source supplied all of their detail or
        one alias line, which is what makes it useful for judging whether a
        source is pulling its weight.
        """
        rows = self._db.execute(
            "SELECT DISTINCT i.payload, i.display_name, i.key FROM identities i"
            " JOIN records r ON r.identity_key = i.key"
            " WHERE r.source = ?"
            " ORDER BY i.display_name COLLATE NOCASE, i.key"
            " LIMIT ? OFFSET ?",
            (source, max(1, min(int(limit), MAX_PAGE)), max(0, int(offset))),
        ).fetchall()
        return [json.loads(self._open(row["payload"])) for row in rows]

    def source_counts(self) -> dict[str, int]:
        """Records and people contributed, per source."""
        rows = self._db.execute(
            "SELECT source, COUNT(*) AS records,"
            " COUNT(DISTINCT identity_key) AS people"
            " FROM records GROUP BY source ORDER BY source"
        ).fetchall()
        return {row["source"]: (row["records"], row["people"]) for row in rows}

    def review_items(self, limit: int) -> list[dict]:
        rows = self._db.execute(
            "SELECT payload FROM review LIMIT ?", (max(1, int(limit)),)
        ).fetchall()
        return [json.loads(self._open(row["payload"])) for row in rows]

    def counts(self) -> dict[str, int]:
        def scalar(sql: str) -> int:
            return int(self._db.execute(sql).fetchone()[0])

        return {
            "identities": scalar("SELECT COUNT(*) FROM identities"),
            "records": scalar("SELECT COUNT(*) FROM records"),
            "addresses": scalar("SELECT COUNT(DISTINCT address_key) FROM addresses"),
            "review": scalar("SELECT COUNT(*) FROM review"),
            "overrides": scalar("SELECT COUNT(*) FROM overrides"),
        }

    def records_for(self, identity_key: str) -> list[RawRecord]:
        rows = self._db.execute(
            "SELECT payload FROM records WHERE identity_key = ?",
            (self._blind(identity_key),)
        ).fetchall()
        out = []
        for row in rows:
            data = json.loads(self._open(row["payload"]))
            out.append(RawRecord(
                source=data["source"],
                source_id=data["source_id"],
                name=data["name"],
                phones=tuple(data.get("phones", ())),
                emails=tuple(data.get("emails", ())),
                # Keyword arguments from here on: this rebuild silently dropped
                # postal addresses when the field was added positionally.
                postal=tuple(data.get("postal", ())),
            ))
        return out


_QUERY_NOISE = frozenset({"and", "or", "not", "near"})


def _search_terms(query: str) -> list[str]:
    """Split a query into terms; a contact search is not a query language.

    Bare boolean keywords are dropped. Someone typing "smith or jones" wants
    both names, not an operator.
    """
    cleaned = "".join(
        character if character.isalnum() or character in "@.+-_ " else " "
        for character in (query or "")
    )
    return [term for term in cleaned.split() if term.lower() not in _QUERY_NOISE]


def wipe(path: Path | None = None) -> None:
    """Drop the derived projection but keep overrides. Used by tests and reset."""
    with closing(_connect(path)) as connection, connection:
        connection.executescript(_SCHEMA)
        for table in ("identities", "records", "addresses", "review", "search"):
            connection.execute(f"DELETE FROM {table}")

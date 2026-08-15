"""SQLite store: a rebuildable projection plus one table that is not.

Everything derived from sources — records, identities, the search index — is
disposable and rebuilt on every sync. `overrides` is the exception: it holds
human decisions, is never cleared by a sync, and is keyed by *address* rather
than by row id because source ids are not stable (abook renumbers on edit).
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from contextlib import closing, contextmanager
from pathlib import Path

from omadex import config as config_module
from omadex.limits import MAX_PAGE, MAX_SEARCH_RESULTS
from omadex.models import Identity, Override, RawRecord, ReviewItem

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
    verdict    TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (unixepoch('subsec')),
    UNIQUE(left_key, right_key)
);
CREATE INDEX IF NOT EXISTS idx_addresses_key ON addresses(address_key);
CREATE INDEX IF NOT EXISTS idx_records_identity ON records(identity_key);
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
    identity_key UNINDEXED,
    name,
    addresses,
    tokenize = "unicode61 remove_diacritics 2"
);
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
def open_store(path: Path | None = None):
    """Open the configured database, or an explicit one for tests."""
    connection = _connect(path)
    try:
        with connection:
            connection.executescript(_SCHEMA)
        yield Store(connection)
    finally:
        connection.close()


class Store:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    # ---- overrides: the durable half -----------------------------------

    def overrides(self) -> list[Override]:
        rows = self._db.execute(
            "SELECT left_key, right_key, verdict FROM overrides"
        ).fetchall()
        return [Override(row["left_key"], row["right_key"], row["verdict"]) for row in rows]

    def set_override(self, override: Override) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO overrides(left_key, right_key, verdict) VALUES (?, ?, ?)"
                " ON CONFLICT(left_key, right_key)"
                " DO UPDATE SET verdict = excluded.verdict",
                (override.left_key, override.right_key, override.verdict),
            )

    def clear_override(self, left_key: str, right_key: str) -> bool:
        with self._db:
            cursor = self._db.execute(
                "DELETE FROM overrides WHERE left_key = ? AND right_key = ?",
                (left_key, right_key),
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
            self._db.execute("DELETE FROM search")
            for identity in identities:
                count += 1
                # Deliberately not an upsert: two identities sharing a handle
                # is a resolver bug, and silently overwriting one person with
                # another is the worst possible way to find out.
                self._db.execute(
                    "INSERT INTO identities(key, display_name, payload)"
                    " VALUES (?, ?, ?)",
                    (identity.key, identity.display_name,
                     json.dumps(identity.to_dict(), ensure_ascii=False)),
                )
                self._db.executemany(
                    "INSERT INTO records(identity_key, source, source_id, payload)"
                    " VALUES (?, ?, ?, ?)",
                    [
                        (identity.key, record.source, record.source_id,
                         json.dumps(record.to_dict(), ensure_ascii=False))
                        for record in identity.records
                    ],
                )
                self._db.executemany(
                    "INSERT OR IGNORE INTO addresses(identity_key, address_key)"
                    " VALUES (?, ?)",
                    [(identity.key, key) for key in sorted(identity.keys)],
                )
                self._db.execute(
                    "INSERT INTO search(identity_key, name, addresses)"
                    " VALUES (?, ?, ?)",
                    (
                        identity.key,
                        " ".join(identity.names),
                        " ".join([
                            *(key.partition(":")[2] for key in sorted(identity.keys)),
                            *identity.postal,
                        ]),
                    ),
                )
            self._db.executemany(
                "INSERT OR IGNORE INTO review(left_key, right_key, payload)"
                " VALUES (?, ?, ?)",
                [
                    (item.left_key, item.right_key,
                     json.dumps(item.to_dict(), ensure_ascii=False))
                    for item in review
                ],
            )
        return count

    # ---- reads ----------------------------------------------------------

    def search(self, query: str, limit: int = MAX_SEARCH_RESULTS) -> list[dict]:
        """Prefix search across names and addresses, best match first."""
        terms = [term for term in _fts_terms(query) if term]
        if not terms:
            return []
        expression = " AND ".join(f'"{term}"*' for term in terms)
        try:
            rows = self._db.execute(
                "SELECT i.payload FROM search"
                " JOIN identities i ON i.key = search.identity_key"
                " WHERE search MATCH ? ORDER BY rank LIMIT ?",
                (expression, max(1, min(int(limit), MAX_SEARCH_RESULTS))),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [json.loads(row["payload"]) for row in rows]

    def get(self, address_key: str) -> dict | None:
        row = self._db.execute(
            "SELECT i.payload FROM identities i"
            " WHERE i.key = ?"
            " OR EXISTS (SELECT 1 FROM addresses a"
            "            WHERE a.identity_key = i.key AND a.address_key = ?)"
            " LIMIT 1",
            (address_key, address_key),
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list(self, offset: int = 0, limit: int = MAX_PAGE) -> list[dict]:
        rows = self._db.execute(
            "SELECT payload FROM identities ORDER BY display_name COLLATE NOCASE, key"
            " LIMIT ? OFFSET ?",
            (max(1, min(int(limit), MAX_PAGE)), max(0, int(offset))),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

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
        return [json.loads(row["payload"]) for row in rows]

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
        return [json.loads(row["payload"]) for row in rows]

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
            "SELECT payload FROM records WHERE identity_key = ?", (identity_key,)
        ).fetchall()
        out = []
        for row in rows:
            data = json.loads(row["payload"])
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


_FTS_KEYWORDS = frozenset({"and", "or", "not", "near"})


def _fts_terms(query: str) -> list[str]:
    """Strip FTS5 syntax; a contact search is not a query language.

    Bare keywords are dropped too. Someone typing "smith or jones" wants both
    names, not a boolean — and leaving "or" in as a literal term matches
    nothing at all.
    """
    cleaned = "".join(
        character if character.isalnum() or character in "@.+-_ " else " "
        for character in (query or "")
    )
    return [term for term in cleaned.split() if term.lower() not in _FTS_KEYWORDS]


def wipe(path: Path | None = None) -> None:
    """Drop the derived projection but keep overrides. Used by tests and reset."""
    with closing(_connect(path)) as connection, connection:
        connection.executescript(_SCHEMA)
        for table in ("identities", "records", "addresses", "review", "search"):
            connection.execute(f"DELETE FROM {table}")

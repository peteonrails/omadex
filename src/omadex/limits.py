"""Bounds. A daemon that aggregates other people's data stays bounded."""
from __future__ import annotations

# A destination shared by this many records *under this many different names*
# is a switchboard or a shared inbox, not a person. Phase 0 found one number on
# 28 records under 28 names.
#
# Both halves are needed. Record count alone misidentifies a popular person:
# one address can appear on nine records once several sources and their
# duplicates are counted, and suppressing it splits that person into pieces.
# A switchboard is distinguished by the *names* on it, not the volume.
HUB_RECORD_THRESHOLD = 4
HUB_NAME_THRESHOLD = 3

MAX_SEARCH_QUERY_CHARS = 256
MAX_SEARCH_RESULTS = 200
MAX_PAGE = 250
MAX_REVIEW_ITEMS = 500
MAX_DBUS_JSON_BYTES = 8 * 1024 * 1024

# Sources are read fresh on request, but never faster than this.
MIN_REFRESH_INTERVAL_SECONDS = 5.0

"""Perzistentní stav v SQLite — které inzeráty už známe + cache detailů.

Dvě tabulky:
  listings  — všechny viděné byty (kvůli detekci NOVÝCH a historii)
  cache     — obecná key/value cache (stažené detaily bytů), šetří požadavky
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Listing, now_iso

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "state.db"


class Store:
    def __init__(self, path: str | Path = DEFAULT_DB):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS listings (
                key        TEXT PRIMARY KEY,
                source     TEXT NOT NULL,
                source_id  TEXT NOT NULL,
                data       TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen  TEXT NOT NULL,
                notified   INTEGER NOT NULL DEFAULT 0,
                active     INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS cache (
                key        TEXT PRIMARY KEY,
                data       TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    # --- listings ---------------------------------------------------------

    def is_known(self, key: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM listings WHERE key = ?", (key,))
        return cur.fetchone() is not None

    def upsert(self, listing: Listing) -> bool:
        """Uloží/aktualizuje inzerát. Vrací True, pokud je NOVÝ (dosud neviděný)."""
        ts = now_iso()
        row = self.conn.execute(
            "SELECT first_seen FROM listings WHERE key = ?", (listing.key,)
        ).fetchone()
        is_new = row is None
        first_seen = listing.first_seen or (ts if is_new else row["first_seen"])
        listing.first_seen = first_seen
        listing.last_seen = ts
        self.conn.execute(
            """
            INSERT INTO listings (key, source, source_id, data, first_seen, last_seen, notified, active)
            VALUES (?, ?, ?, ?, ?, ?, 0, 1)
            ON CONFLICT(key) DO UPDATE SET
                data = excluded.data,
                last_seen = excluded.last_seen,
                active = 1
            """,
            (listing.key, listing.source, listing.source_id,
             json.dumps(listing.to_dict(), ensure_ascii=False), first_seen, ts),
        )
        self.conn.commit()
        return is_new

    def mark_inactive_except(self, active_keys: set[str]) -> int:
        """Označí jako neaktivní inzeráty, které v tomto běhu nebyly nalezeny.
        Vrací počet nově zmizelých. Prázdný active_keys nechá vše být (bezpečnost)."""
        if not active_keys:
            return 0
        placeholders = ",".join("?" for _ in active_keys)
        cur = self.conn.execute(
            f"UPDATE listings SET active = 0 WHERE active = 1 AND key NOT IN ({placeholders})",
            tuple(active_keys),
        )
        self.conn.commit()
        return cur.rowcount

    def unnotified(self) -> list[Listing]:
        rows = self.conn.execute(
            "SELECT data FROM listings WHERE notified = 0 AND active = 1"
        ).fetchall()
        return [Listing.from_dict(json.loads(r["data"])) for r in rows]

    def mark_notified(self, keys: list[str]) -> None:
        if not keys:
            return
        self.conn.executemany(
            "UPDATE listings SET notified = 1 WHERE key = ?", [(k,) for k in keys]
        )
        self.conn.commit()

    def active_listings(self) -> list[Listing]:
        rows = self.conn.execute(
            "SELECT data FROM listings WHERE active = 1"
        ).fetchall()
        return [Listing.from_dict(json.loads(r["data"])) for r in rows]

    def total_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]

    # --- cache ------------------------------------------------------------

    def cache_get(self, key: str, max_age_days: float | None = None) -> dict | None:
        row = self.conn.execute(
            "SELECT data, fetched_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return None
        if max_age_days is not None:
            fetched = datetime.fromisoformat(row["fetched_at"])
            age = (datetime.now(timezone.utc) - fetched).total_seconds() / 86400
            if age > max_age_days:
                return None
        return json.loads(row["data"])

    def cache_set(self, key: str, data: dict) -> None:
        self.conn.execute(
            "INSERT INTO cache (key, data, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET data = excluded.data, fetched_at = excluded.fetched_at",
            (key, json.dumps(data, ensure_ascii=False), now_iso()),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

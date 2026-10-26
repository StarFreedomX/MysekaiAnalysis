"""One latest snapshot per hashed access key, committed atomically."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    key_hash TEXT PRIMARY KEY,
                    uid TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS maps (
                    key_hash TEXT NOT NULL REFERENCES snapshots(key_hash) ON DELETE CASCADE,
                    site_id INTEGER NOT NULL,
                    image BLOB NOT NULL,
                    PRIMARY KEY (key_hash, site_id)
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, key_hash, uid, payload, metadata, images):
        with self.connect() as db:
            db.execute("""
                INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key_hash) DO UPDATE SET
                    uid=excluded.uid, updated_at=excluded.updated_at,
                    metadata=excluded.metadata, payload=excluded.payload
            """, (key_hash, uid, metadata["updated_at"], json.dumps(metadata), payload))
            db.execute("DELETE FROM maps WHERE key_hash=?", (key_hash,))
            db.executemany("INSERT INTO maps VALUES (?, ?, ?)",
                           [(key_hash, sid, image) for sid, image in images.items()])

    def metadata(self, key_hash):
        with self.connect() as db:
            row = db.execute("SELECT metadata FROM snapshots WHERE key_hash=?", (key_hash,)).fetchone()
        return json.loads(row[0]) if row else None

    def image(self, key_hash, site_id):
        with self.connect() as db:
            row = db.execute("SELECT image FROM maps WHERE key_hash=? AND site_id=?", (key_hash, site_id)).fetchone()
        return row[0] if row else None

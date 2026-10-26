"""One latest snapshot per hashed access key, committed atomically."""

import json
import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


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

    def archive_failure(self, uid, region, payload, status, code, message, expected_bytes, exc=None):
        """Keep failed bytes independently of SQLite, including database failures."""
        now = datetime.now(timezone.utc)
        archive_id = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}_{uid}_{uuid4().hex[:8]}"
        directory = self.path.parent / "failed_uploads"
        directory.mkdir(parents=True, exist_ok=True)
        binary_path = directory / f"{archive_id}.bin"
        with binary_path.open("xb") as stream:
            stream.write(payload)
        chain = []
        seen = set()
        while exc is not None and id(exc) not in seen:
            seen.add(id(exc))
            chain.append(type(exc).__name__)
            exc = exc.__cause__ or exc.__context__
        metadata = {
            "id": archive_id, "uid": uid, "received_at": now.isoformat(), "region": region,
            "status": status, "error": code, "message": message,
            "received_bytes": len(payload), "expected_bytes": expected_bytes,
            "sha256": hashlib.sha256(payload).hexdigest(), "exception_types": chain,
        }
        metadata_path = directory / f"{archive_id}.json"
        temporary_path = directory / f"{archive_id}.json.tmp"
        temporary_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary_path.replace(metadata_path)
        return archive_id

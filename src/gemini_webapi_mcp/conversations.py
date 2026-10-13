"""Durable, account-scoped local conversation storage."""

from __future__ import annotations

import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path


class ConversationStore:
    """SQLite store for local IDs, Gemini metadata, and safe local history."""

    def __init__(self, path: str | Path | None = None, *, ttl_seconds: int | None = None):
        self.path = Path(path or os.environ.get(
            "GEMINI_CONVERSATIONS_PATH", "/var/lib/gemini-mcp/conversations.sqlite3"
        )).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else int(
            os.environ.get("GEMINI_CONVERSATION_TTL_SECONDS", "2592000")
        )
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _initialize(self) -> None:
        with self._lock, self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    session_id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    account TEXT NOT NULL,
                    model TEXT NOT NULL,
                    cid TEXT,
                    rid TEXT,
                    rcid TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    closed_at REAL
                );
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES conversations(session_id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_owner
                    ON conversations(subject, account, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON conversation_messages(session_id, id);
            """)
            if version < 1:
                db.execute("PRAGMA user_version=1")
                version = 1
            # Version 2 retains the public thinking selection alongside the
            # stable public model ID. Existing conversations keep NULL, which
            # means no verified thinking control was selected.
            if version < 2:
                columns = {row[1] for row in db.execute("PRAGMA table_info(conversations)")}
                if "thinking_level" not in columns:
                    db.execute("ALTER TABLE conversations ADD COLUMN thinking_level TEXT")
                db.execute("PRAGMA user_version=2")

    def create(self, subject: str, account: str, model: str, thinking_level: str | None = None) -> str:
        now = time.time()
        session_id = secrets.token_urlsafe(24)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO conversations
                   (session_id, subject, account, model, cid, rid, rcid, created_at,
                    updated_at, expires_at, closed_at, thinking_level)
                   VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, NULL, ?)""",
                (session_id, subject, account, model, now, now, now + self.ttl_seconds, thinking_level),
            )
            db.commit()
        return session_id

    def _row(self, db, subject: str, account: str, session_id: str):
        row = db.execute(
            "SELECT * FROM conversations WHERE session_id=? AND subject=? AND account=?",
            (session_id, subject, account),
        ).fetchone()
        if row is None:
            raise KeyError("session_not_found")
        if row["closed_at"] is not None or row["expires_at"] <= time.time():
            raise TimeoutError("session_expired")
        return row

    def get(self, subject: str, account: str, session_id: str) -> dict:
        with self._lock, self._connect() as db:
            row = self._row(db, subject, account, session_id)
            return dict(row)

    def update_metadata(self, subject: str, account: str, session_id: str, metadata: list[str | None]) -> None:
        values = (metadata + [None, None, None])[:3]
        with self._lock, self._connect() as db:
            self._row(db, subject, account, session_id)
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE conversations SET cid=?, rid=?, rcid=?, updated_at=?, expires_at=? WHERE session_id=?",
                (*values, time.time(), time.time() + self.ttl_seconds, session_id),
            )
            db.commit()

    def update_model(self, subject: str, account: str, session_id: str, model: str,
                     thinking_level: str | None = None) -> None:
        with self._lock, self._connect() as db:
            self._row(db, subject, account, session_id)
            db.execute("UPDATE conversations SET model=?, thinking_level=?, updated_at=?, expires_at=? WHERE session_id=?",
                       (model, thinking_level, time.time(), time.time() + self.ttl_seconds, session_id))

    def add_messages(self, subject: str, account: str, session_id: str, messages: list[tuple[str, str]]) -> None:
        with self._lock, self._connect() as db:
            self._row(db, subject, account, session_id)
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            db.executemany(
                "INSERT INTO conversation_messages(session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                [(session_id, role, content, now) for role, content in messages],
            )
            db.execute("UPDATE conversations SET updated_at=?, expires_at=? WHERE session_id=?",
                       (now, now + self.ttl_seconds, session_id))
            db.commit()

    def list(self, subject: str, account: str, limit: int = 50, offset: int = 0) -> list[dict]:
        with self._lock, self._connect() as db:
            self.cleanup(db)
            return [dict(row) for row in db.execute(
                "SELECT * FROM conversations WHERE subject=? AND account=? AND closed_at IS NULL ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (subject, account, max(1, min(limit, 100)), max(0, offset)),
            )]

    def count_for_account(self, subject: str, account: str) -> int:
        """Count retained local conversations without exposing their content."""
        with self._lock, self._connect() as db:
            self.cleanup(db)
            return int(db.execute(
                "SELECT COUNT(*) FROM conversations WHERE subject=? AND account=?",
                (subject, account),
            ).fetchone()[0])

    def last_activity_for_account(self, subject: str, account: str) -> float | None:
        """Return the newest retained local-conversation timestamp, if any."""
        with self._lock, self._connect() as db:
            self.cleanup(db)
            value = db.execute(
                "SELECT MAX(updated_at) FROM conversations WHERE subject=? AND account=?",
                (subject, account),
            ).fetchone()[0]
            return float(value) if value is not None else None

    def history(self, subject: str, account: str, session_id: str, limit: int = 100) -> list[dict]:
        with self._lock, self._connect() as db:
            self._row(db, subject, account, session_id)
            return [dict(row) for row in db.execute(
                "SELECT role, content, created_at FROM conversation_messages WHERE session_id=? ORDER BY id LIMIT ?",
                (session_id, max(1, min(limit, 500))),
            )]

    def close(self, subject: str, account: str, session_id: str) -> None:
        with self._lock, self._connect() as db:
            self._row(db, subject, account, session_id)
            db.execute("UPDATE conversations SET closed_at=?, updated_at=? WHERE session_id=?",
                       (time.time(), time.time(), session_id))

    def delete(self, subject: str, account: str, session_id: str) -> None:
        with self._lock, self._connect() as db:
            self._row(db, subject, account, session_id)
            db.execute("DELETE FROM conversations WHERE session_id=?", (session_id,))

    def cleanup(self, db=None) -> int:
        owned = db is None
        db = db or self._connect()
        try:
            cur = db.execute("DELETE FROM conversations WHERE expires_at<=? OR closed_at IS NOT NULL", (time.time(),))
            return cur.rowcount
        finally:
            if owned:
                db.close()

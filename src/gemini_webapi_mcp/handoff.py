"""One-time browser handoff sessions for remote Gemini login bootstrapping."""
from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass


@dataclass
class HandoffSession:
    token_hash: str
    subject: str
    account: str
    created_at: float
    expires_at: float
    state: str = "pending"


class HandoffManager:
    """Security-neutral state machine for a gateway's login handoff UI."""
    def __init__(self, ttl_seconds: int = 600):
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, HandoffSession] = {}

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def create(self, subject: str, account: str, now: float | None = None) -> str:
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        self._sessions[self._hash(token)] = HandoffSession(
            self._hash(token), subject, account, now, now + self.ttl_seconds
        )
        return token

    def inspect(self, token: str, now: float | None = None) -> HandoffSession:
        now = time.time() if now is None else now
        session = self._sessions.get(self._hash(token))
        if session is None or session.expires_at <= now or session.state in {"expired", "revoked", "used"}:
            if session is not None and session.state == "pending":
                session.state = "expired"
            raise PermissionError("login handoff is invalid or expired")
        return session

    def approve(self, token: str, now: float | None = None) -> HandoffSession:
        session = self.inspect(token, now)
        if session.state != "verified":
            raise PermissionError("Gemini session must be verified before approval")
        session.state = "approved"
        return session

    def mark_verified(self, token: str, now: float | None = None) -> HandoffSession:
        session = self.inspect(token, now)
        session.state = "verified"
        return session

    def consume(self, token: str, now: float | None = None) -> HandoffSession:
        session = self.inspect(token, now)
        if session.state != "approved":
            raise PermissionError("login handoff has not been approved")
        session.state = "used"
        return session

    def revoke(self, token: str) -> None:
        session = self._sessions.get(self._hash(token))
        if session is not None:
            session.state = "revoked"

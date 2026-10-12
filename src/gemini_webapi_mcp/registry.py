"""Encrypted dynamic Gemini account registration.

The registry stores account metadata only. Browser profiles and cookies remain
in the session store and are never serialized into this file.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if os.name == "nt":
    import msvcrt
else:
    import fcntl

_ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def generate_key() -> str:
    """Return a URL-safe Base64 representation of a fresh AES-256 key."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def _decode_key(value: str) -> bytes:
    try:
        normalized = value.strip() + "=" * (-len(value.strip()) % 4)
        key = base64.urlsafe_b64decode(normalized.encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("registry key must be URL-safe Base64") from exc
    if len(key) != 32:
        raise ValueError("registry key must decode to exactly 32 bytes")
    return key


class DynamicAccountRegistry:
    """Atomically persisted, encrypted account metadata registry."""

    def __init__(self, path: str | Path, key: str | bytes, *, create: bool = True):
        self.path = Path(path)
        self._key = _decode_key(key) if isinstance(key, str) else key
        if len(self._key) != 32:
            raise ValueError("registry key must be exactly 32 bytes")
        self._data: dict[str, Any] = {"version": 1, "accounts": {}, "selected": {}}
        if self.path.exists():
            self._load()
        elif create:
            with self._locked():
                if self.path.exists():
                    self._load()
                else:
                    self._save()

    @contextmanager
    def _locked(self):
        """Serialize registry operations across worker processes.

        The lock is separate from the encrypted data file so atomic replacement
        of the latter never invalidates the lock handle.  POSIX uses flock;
        Windows uses the native byte-range lock.  The lock file contains no
        secrets and can safely remain beside the registry after a crash.
        """
        lock_path = self.path.with_name(self.path.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            if os.name == "nt":
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _reload_if_present(self) -> None:
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        if envelope.get("version") != 1 or envelope.get("algorithm") != "AES-256-GCM":
            raise ValueError("unsupported registry envelope")
        try:
            nonce = base64.urlsafe_b64decode(envelope["nonce"])
            ciphertext = base64.urlsafe_b64decode(envelope["ciphertext"])
            plain = AESGCM(self._key).decrypt(nonce, ciphertext, b"gemini-webapi-mcp/registry/v1")
        except (KeyError, ValueError, InvalidTag) as exc:
            raise ValueError("cannot decrypt registry; check GEMINI_REGISTRY_KEY") from exc
        self._data = json.loads(plain.decode("utf-8"))
        if self._data.get("version") != 1 or not isinstance(self._data.get("accounts"), dict):
            raise ValueError("invalid registry contents")
        # Version 1 files created before persistent selection existed are
        # upgraded in memory without changing their encryption format.
        if not isinstance(self._data.get("selected"), dict):
            self._data["selected"] = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._key).encrypt(
            nonce, json.dumps(self._data, separators=(",", ":")).encode("utf-8"),
            b"gemini-webapi-mcp/registry/v1",
        )
        envelope = {"version": 1, "algorithm": "AES-256-GCM", "nonce": base64.urlsafe_b64encode(nonce).decode(), "ciphertext": base64.urlsafe_b64encode(ciphertext).decode()}
        fd, temp_name = tempfile.mkstemp(prefix=".registry-", dir=self.path.parent)
        try:
            os.chmod(temp_name, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(envelope, handle, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def _validate(account: str) -> None:
        if not _ACCOUNT.fullmatch(account):
            raise ValueError("account must contain only letters, numbers, dot, underscore, or dash")

    def register(self, *, subject: str, account: str, profile: str | None = None, scopes: set[str] | frozenset[str] = frozenset({"chat"}), requests_per_minute: int = 30, state: str = "ready") -> dict[str, Any]:
        self._validate(account)
        if not subject or not isinstance(subject, str):
            raise ValueError("subject is required")
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute must be positive")
        if state not in {"pending", "ready", "disabled", "revoked"}:
            raise ValueError("unsupported account state")
        with self._locked():
            self._reload_if_present()
            current = self._data["accounts"].get(account)
            if current and current.get("owner_subject") != subject:
                raise PermissionError("account is already registered to another subject")
            now = int(time.time())
            record = {
                "owner_subject": subject,
                "profile": profile or (current or {}).get("profile") or account,
                "scopes": sorted(scopes),
                "requests_per_minute": requests_per_minute,
                "state": state,
                "created_at": int((current or {}).get("created_at", now)),
                "updated_at": now,
            }
            self._data["accounts"][account] = record
            self._save()
            return dict(record, account=account)

    def set_state(self, account: str, state: str) -> None:
        self._validate(account)
        if state not in {"pending", "ready", "disabled", "revoked"}:
            raise ValueError("unsupported account state")
        with self._locked():
            self._reload_if_present()
            if account not in self._data["accounts"]:
                raise KeyError(account)
            self._data["accounts"][account]["state"] = state
            self._data["accounts"][account]["updated_at"] = int(time.time())
            self._save()

    def selection(self, subject: str) -> str | None:
        """Return the persisted account name without selecting or mutating it."""
        with self._locked():
            self._reload_if_present()
            selected = self._data.setdefault("selected", {}).get(subject)
            if not isinstance(selected, str):
                return None
            record = self._data["accounts"].get(selected)
            if not record or record.get("owner_subject") != subject or record.get("state") != "ready":
                return None
            return selected

    def select(self, subject: str, account: str | None = None) -> dict[str, Any]:
        with self._locked():
            self._reload_if_present()
            matches = [(name, record) for name, record in self._data["accounts"].items() if record.get("owner_subject") == subject and record.get("state") == "ready"]
            if account:
                matches = [(name, record) for name, record in matches if name == account]
            if len(matches) != 1:
                raise PermissionError("explicit Gemini account selection is required or account is unavailable")
            name, record = matches[0]
            self._data.setdefault("selected", {})[subject] = name
            self._save()
            return dict(record, account=name)

    def selected(self, subject: str) -> dict[str, Any]:
        """Return the subject's persisted selection, failing closed."""
        with self._locked():
            self._reload_if_present()
            account = self._data.setdefault("selected", {}).get(subject)
        return self.select(subject, account)

    def list_safe(self, subject: str | None = None) -> list[dict[str, Any]]:
        with self._locked():
            self._reload_if_present()
            result = []
            for name, record in self._data["accounts"].items():
                if subject is None or record.get("owner_subject") == subject:
                    result.append({
                        "account": name,
                        "profile": record.get("profile"),
                        "scopes": record.get("scopes", []),
                        "state": record.get("state"),
                        "requests_per_minute": record.get("requests_per_minute"),
                        "created_at": record.get("created_at"),
                        "updated_at": record.get("updated_at"),
                    })
            return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the encrypted Gemini account registry")
    sub = parser.add_subparsers(dest="command", required=True)
    keygen = sub.add_parser("keygen")
    keygen.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "keygen":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(generate_key() + "\n", encoding="ascii")
        try:
            os.chmod(args.output, 0o600)
        except OSError:
            pass
        print(f"wrote registry key to {args.output}")


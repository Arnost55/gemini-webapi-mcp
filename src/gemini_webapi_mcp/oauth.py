"""Small, local OAuth 2.1 authorization server for the MCP endpoint.

This authenticates MCP clients to this server. It deliberately does not claim
to be Google OAuth: Gemini accounts are linked separately through the browser
handoff flow.
"""
from __future__ import annotations

import base64
import hashlib
import html
import secrets
import time
from dataclasses import dataclass
from threading import RLock
from urllib.parse import urlencode


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce(verifier: str) -> str:
    return _b64(hashlib.sha256(verifier.encode("ascii")).digest())


@dataclass
class Client:
    client_id: str
    client_name: str
    redirect_uris: tuple[str, ...]
    token_endpoint_auth_method: str
    created_at: int


@dataclass
class AuthorizationCode:
    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    subject: str
    expires_at: float
    used: bool = False


class LocalOAuth:
    """In-memory OAuth state; persistent client storage can be added later."""

    def __init__(self, issuer: str, ttl: int = 300):
        self.issuer = issuer.rstrip("/")
        self.ttl = ttl
        self._clients: dict[str, Client] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._tokens: dict[str, tuple[str, str, float]] = {}
        self._lock = RLock()

    def register(self, body: dict) -> dict:
        redirects = tuple(body.get("redirect_uris") or ())
        if not body.get("client_name") or not redirects:
            raise ValueError("client_name and redirect_uris are required")
        if any(not u.startswith("https://") and not u.startswith("http://localhost") for u in redirects):
            raise ValueError("redirect_uris must use HTTPS or localhost")
        method = body.get("token_endpoint_auth_method", "none")
        if method not in {"none", "client_secret_post", "client_secret_basic"}:
            raise ValueError("unsupported token_endpoint_auth_method")
        client_id = _b64(secrets.token_bytes(24))
        client = Client(client_id, str(body["client_name"]), redirects, method, int(time.time()))
        with self._lock:
            self._clients[client_id] = client
        result = {
            "client_id": client_id,
            "client_name": client.client_name,
            "redirect_uris": list(client.redirect_uris),
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": method,
        }
        if method != "none":
            result["client_secret"] = _b64(secrets.token_bytes(32))
        return result

    def begin(self, client_id: str, redirect_uri: str, challenge: str, subject: str = "pending") -> str:
        with self._lock:
            client = self._clients.get(client_id)
            if not client or redirect_uri not in client.redirect_uris:
                raise ValueError("unknown client or redirect URI")
            if not challenge:
                raise ValueError("PKCE code_challenge is required")
            code = _b64(secrets.token_bytes(32))
            self._codes[code] = AuthorizationCode(code, client_id, redirect_uri, challenge, subject, time.time() + self.ttl)
            return code

    def complete(self, code: str, subject: str) -> None:
        """Complete a pending browser handoff before releasing its code."""
        with self._lock:
            item = self._codes.get(code)
            if not item or item.expires_at <= time.time() or item.used:
                raise ValueError("invalid or expired authorization request")
            item.subject = subject

    def exchange(self, code: str, client_id: str, redirect_uri: str, verifier: str) -> dict:
        with self._lock:
            item = self._codes.get(code)
            if not item or item.used or item.expires_at <= time.time():
                raise ValueError("invalid or expired authorization code")
            if item.client_id != client_id or item.redirect_uri != redirect_uri or _pkce(verifier) != item.code_challenge:
                raise ValueError("client, redirect URI, or PKCE verifier mismatch")
            item.used = True
            access = _b64(secrets.token_bytes(32))
            refresh = _b64(secrets.token_bytes(32))
            self._tokens[access] = (item.subject, "access", time.time() + 3600)
            self._tokens[refresh] = (item.subject, "refresh", time.time() + 2592000)
            return {"access_token": access, "token_type": "Bearer", "expires_in": 3600, "refresh_token": refresh, "scope": "openid profile"}

    def subject_for(self, token: str) -> str | None:
        with self._lock:
            value = self._tokens.get(token)
            if not value or value[2] <= time.time() or value[1] != "access":
                return None
            return value[0]

    def authorize_page(self, client_id: str, redirect_uri: str, challenge: str, state: str) -> str:
        code = self.begin(client_id, redirect_uri, challenge, subject="local-user")
        finish = self.issuer + "/oauth/complete?code=" + code + "&state=" + state
        return "<html><body><h1>Connect Gemini account</h1><p>Complete Gemini login in Chromium, then continue.</p><a href='" + html.escape(finish, quote=True) + "'>Continue after login</a></body></html>"

    def completion_redirect(self, code: str, subject: str) -> str:
        with self._lock:
            item = self._codes.get(code)
            if not item:
                raise ValueError("invalid authorization request")
            self.complete(code, subject)
            return item.redirect_uri + ("&" if "?" in item.redirect_uri else "?") + urlencode({"code": code})


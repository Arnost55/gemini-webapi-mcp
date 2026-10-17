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
import os
import json
import re
import inspect
from pathlib import Path
from dataclasses import dataclass
from threading import RLock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_STATE_AAD = b"gemini-webapi-mcp/oauth-state/v1"
_PKCE_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_PKCE_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce(verifier: str) -> str:
    return _b64(hashlib.sha256(verifier.encode("ascii")).digest())


def _state_key(value: str | bytes | None) -> bytes | None:
    """Read a URL-safe base64 encoded, 32-byte state-encryption key."""
    if value is None:
        value = os.environ.get("GEMINI_OAUTH_STATE_KEY")
        key_file = os.environ.get("GEMINI_OAUTH_STATE_KEY_FILE")
        if not value and key_file:
            value = Path(key_file).read_text(encoding="ascii").strip()
    if not value:
        return None
    encoded = value.encode("ascii") if isinstance(value, str) else value
    try:
        decoded = base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("OAuth state key must be URL-safe base64") from exc
    if len(decoded) != 32:
        raise ValueError("OAuth state key must decode to 32 bytes")
    return decoded


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
    state: str
    account: str | None = None
    handoff_token: str | None = None
    used: bool = False


class LocalOAuth:
    """Local OAuth 2.1 state with durable DCR and refresh-token records."""

    def __init__(self, issuer: str, ttl: int = 300, state_path: str | None = None,
                 state_key: str | bytes | None = None,
                 handoff_url_factory: Callable[..., tuple[str, str]] | None = None,
                 handoff_ready: Callable[[str], bool] | None = None,
                 handoff_consume: Callable[[str, str, str], None] | None = None):
        self.issuer = issuer.rstrip("/")
        self.ttl = ttl
        self.state_path = Path(state_path or os.environ.get("GEMINI_OAUTH_STATE_PATH", "")) if (state_path or os.environ.get("GEMINI_OAUTH_STATE_PATH")) else None
        self._state_key = _state_key(state_key)
        self._clients: dict[str, Client] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._tokens: dict[str, tuple[str, str, float, str]] = {}
        self._lock = RLock()
        self._handoff_url_factory = handoff_url_factory
        self._handoff_ready = handoff_ready
        self._handoff_consume = handoff_consume
        self._load_state()

    def _load_state(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if data.get("algorithm") == "AES-256-GCM":
                if self._state_key is None:
                    raise RuntimeError("persisted OAuth state is encrypted; configure GEMINI_OAUTH_STATE_KEY_FILE")
                nonce = base64.urlsafe_b64decode(data["nonce"])
                ciphertext = base64.urlsafe_b64decode(data["ciphertext"])
                data = json.loads(AESGCM(self._state_key).decrypt(nonce, ciphertext, _STATE_AAD))
            for item in data.get("clients", []):
                client = Client(**item)
                self._clients[client.client_id] = client
            for token, item in data.get("tokens", {}).items():
                self._tokens[token] = (item["subject"], item["kind"], float(item["expires_at"]), item.get("client_id", ""))
        except (OSError, ValueError, TypeError, KeyError, InvalidTag) as exc:
            raise RuntimeError(f"invalid persisted OAuth state: {self.state_path}") from exc

    def _save_state(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "clients": [
                {"client_id": c.client_id, "client_name": c.client_name, "redirect_uris": list(c.redirect_uris),
                 "token_endpoint_auth_method": c.token_endpoint_auth_method, "created_at": c.created_at}
                for c in self._clients.values()
            ],
            "tokens": {
                token: {"subject": subject, "kind": kind, "expires_at": expires, "client_id": client_id}
                for token, (subject, kind, expires, client_id) in self._tokens.items()
            },
        }
        serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if self._state_key is not None:
            nonce = secrets.token_bytes(12)
            ciphertext = AESGCM(self._state_key).encrypt(nonce, serialized, _STATE_AAD)
            serialized = json.dumps({
                "version": 1,
                "algorithm": "AES-256-GCM",
                "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"),
                "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("ascii"),
            }, separators=(",", ":")).encode("utf-8")
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_bytes(serialized)
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.state_path)
        os.chmod(self.state_path, 0o600)

    def register(self, body: dict) -> dict:
        redirects = tuple(body.get("redirect_uris") or ())
        if not body.get("client_name") or not redirects:
            raise ValueError("client_name and redirect_uris are required")
        if any(not self._valid_redirect_uri(u) for u in redirects):
            raise ValueError("redirect_uris must use HTTPS or localhost")
        method = body.get("token_endpoint_auth_method", "none")
        # Dynamic clients for browser and desktop MCP flows are public clients
        # and must use PKCE. Returning an unverified client secret would be a
        # misleading, weaker authentication mode.
        if method != "none":
            raise ValueError("only public PKCE clients (token_endpoint_auth_method=none) are supported")
        client_id = _b64(secrets.token_bytes(24))
        client = Client(client_id, str(body["client_name"]), redirects, method, int(time.time()))
        with self._lock:
            self._clients[client_id] = client
            self._save_state()
        result = {
            "client_id": client_id,
            "client_name": client.client_name,
            "redirect_uris": list(client.redirect_uris),
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": method,
        }
        return result

    @staticmethod
    def _valid_redirect_uri(value: str) -> bool:
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.netloc and not parsed.username and not parsed.password:
            return True
        return parsed.scheme == "http" and parsed.hostname == "localhost" and not parsed.username and not parsed.password

    def begin(self, client_id: str, redirect_uri: str, challenge: str, subject: str = "pending") -> str:
        with self._lock:
            client = self._clients.get(client_id)
            if not client or redirect_uri not in client.redirect_uris:
                raise ValueError("unknown client or redirect URI")
            if not _PKCE_CHALLENGE.fullmatch(challenge):
                raise ValueError("PKCE code_challenge must be a base64url S256 value")
            code = _b64(secrets.token_bytes(32))
            self._codes[code] = AuthorizationCode(code, client_id, redirect_uri, challenge, subject, time.time() + self.ttl, "")
            return code

    def set_state(self, code: str, state: str) -> None:
        with self._lock:
            item = self._codes.get(code)
            if not item or item.expires_at <= time.time() or item.used:
                raise ValueError("invalid or expired authorization request")
            item.state = state

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
            if not _PKCE_VERIFIER.fullmatch(verifier):
                raise ValueError("invalid PKCE verifier")
            if item.client_id != client_id or item.redirect_uri != redirect_uri or _pkce(verifier) != item.code_challenge:
                raise ValueError("client, redirect URI, or PKCE verifier mismatch")
            item.used = True
            access = _b64(secrets.token_bytes(32))
            refresh = _b64(secrets.token_bytes(32))
            self._tokens[access] = (item.subject, "access", time.time() + 3600, item.client_id)
            self._tokens[refresh] = (item.subject, "refresh", time.time() + 2592000, item.client_id)
            self._save_state()
            return {"access_token": access, "token_type": "Bearer", "expires_in": 3600, "refresh_token": refresh, "scope": "openid profile"}

    def subject_for(self, token: str) -> str | None:
        with self._lock:
            value = self._tokens.get(token)
            if not value or value[2] <= time.time() or value[1] != "access":
                return None
            return value[0]

    def refresh(self, refresh_token: str, client_id: str | None = None) -> dict:
        """Rotate a valid refresh token without extending client trust."""
        with self._lock:
            value = self._tokens.get(refresh_token)
            if not value or value[1] != "refresh" or value[2] <= time.time():
                raise ValueError("invalid or expired refresh token")
            subject, _, _, bound_client = value
            if client_id and bound_client and client_id != bound_client:
                raise ValueError("refresh token is bound to another client")
            self._tokens.pop(refresh_token, None)
            access = _b64(secrets.token_bytes(32))
            refresh = _b64(secrets.token_bytes(32))
            bound_client = bound_client or client_id or ""
            self._tokens[access] = (subject, "access", time.time() + 3600, bound_client)
            self._tokens[refresh] = (subject, "refresh", time.time() + 2592000, bound_client)
            self._save_state()
            return {"access_token": access, "token_type": "Bearer", "expires_in": 3600,
                    "refresh_token": refresh, "scope": "openid profile"}

    def authorize_page(self, client_id: str, redirect_uri: str, challenge: str, state: str) -> str:
        # A public DCR registration is the only stable caller boundary this
        # local authorization server has. Keep account registry ownership
        # separate per registered client rather than collapsing every caller
        # into a shared "local-user" identity.
        code = self.begin(client_id, redirect_uri, challenge, subject="client:" + client_id)
        self.set_state(code, state)
        self._codes[code].account = "gemini-" + client_id[:12] + "-" + code[:10]
        finish = self.issuer + "/oauth/complete?" + urlencode({"code": code, "state": state})
        browser_url = ""
        if self._handoff_url_factory:
            # Keep zero-argument factories compatible with existing callers,
            # while allowing the browser broker to bind the handoff to the
            # initiating OAuth principal and account before it starts.
            try:
                accepts_owner = bool(inspect.signature(self._handoff_url_factory).parameters)
            except (TypeError, ValueError):
                accepts_owner = True
            if accepts_owner:
                token, browser_url = self._handoff_url_factory(
                    self._codes[code].subject, self._codes[code].account or ""
                )
            else:
                token, browser_url = self._handoff_url_factory()
            self._codes[code].handoff_token = token
            browser_url = browser_url + ("&" if "?" in browser_url else "?") + urlencode({"complete": finish})
        else:
            # Compatibility for deployments which have not yet enabled the
            # isolated broker.  This never puts the OAuth code in the URL.
            browser_url = os.environ.get("GEMINI_NOVNC_PUBLIC_URL", "")
        browser_link = (
            "<a class='button secondary' href='" + html.escape(browser_url, quote=True) + "'>Open Chromium login</a>"
            if browser_url else
            "<div class='notice'>The embedded Chromium viewer is not configured yet. Set <code>GEMINI_PUBLIC_URL</code> and <code>GEMINI_NOVNC_WS_URL</code>, or complete login in the container browser profile.</div>"
        )
        browser_auto = "<script>window.location.replace(" + json.dumps(browser_url) + ")</script>" if browser_url else ""
        return """<!doctype html>
<html><head><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Connect Gemini account</title><style>
body{margin:0;background:#0b1020;color:#eef2ff;font:16px system-ui,sans-serif}.card{max-width:560px;margin:10vh auto;padding:32px;border:1px solid #2b3655;border-radius:18px;background:#121a2d;box-shadow:0 20px 60px #0006}h1{margin-top:0;font-size:28px}p{color:#b8c2dc;line-height:1.5}.steps{margin:24px 0;padding:0;list-style:none}.steps li{padding:12px 0;border-bottom:1px solid #26304a}.button{display:inline-block;padding:12px 16px;border-radius:10px;background:#8ab4ff;color:#08101f;text-decoration:none;font-weight:700;margin:8px 8px 8px 0}.secondary{background:#273653;color:#eef2ff}.notice{margin-top:18px;padding:14px;border-radius:10px;background:#211d13;color:#f4d58a;line-height:1.45}code{font-family:ui-monospace,monospace}.small{font-size:13px;color:#8793b0}</style></head>
<body><main class='card'><h1>Connect Gemini account</h1>
<p>This authorizes the MCP connection. Gemini Web login is completed in the isolated Chromium profile on your server.</p>
<ol class='steps'><li><b>1.</b> Open the isolated Chromium login.</li><li><b>2.</b> Sign in to the Google account you want to connect.</li><li><b>3.</b> Wait for both required Gemini session cookies to be detected. The server will continue automatically.</li></ol>
""" + browser_auto + browser_link + "<a class='button' href='" + html.escape(finish, quote=True) + "'>Check login and continue</a>" + """
<p class='small'>A visible Google page is not enough to confirm login. This service checks only whether the required session cookies are present; it never displays or transmits their values. The authorization link expires after a short time and can be used once.</p></main></body></html>"""

    def completion_redirect(self, code: str, state: str, subject: str | None = None) -> str:
        with self._lock:
            item = self._codes.get(code)
            if not item:
                raise ValueError("invalid authorization request")
            if item.state != state:
                raise ValueError("OAuth state mismatch")
            if item.handoff_token and self._handoff_ready and not self._handoff_ready(item.handoff_token):
                raise ValueError(
                    "Gemini login is not ready: the isolated browser must contain both required "
                    "Gemini session cookies. Finish sign-in in that browser and try again."
                )
            self.complete(code, subject or item.subject)
            if item.handoff_token and self._handoff_consume:
                self._handoff_consume(item.handoff_token, item.subject, item.account or ("gemini-" + item.code[:10]))
            parsed = urlsplit(item.redirect_uri)
            query = parse_qsl(parsed.query, keep_blank_values=True)
            query.append(("code", code))
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


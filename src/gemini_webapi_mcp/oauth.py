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
        browser_url = os.environ.get("GEMINI_NOVNC_PUBLIC_URL", "")
        browser_link = (
            "<a class='button secondary' target='_blank' rel='noopener' href='" + html.escape(browser_url, quote=True) + "'>Open Chromium login</a>"
            if browser_url else
            "<div class='notice'>Chromium display is not published yet. Set <code>GEMINI_NOVNC_PUBLIC_URL</code> to your protected noVNC URL, or complete login in the container browser profile.</div>"
        )
        return """<!doctype html>
<html><head><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Connect Gemini account</title><style>
body{margin:0;background:#0b1020;color:#eef2ff;font:16px system-ui,sans-serif}.card{max-width:560px;margin:10vh auto;padding:32px;border:1px solid #2b3655;border-radius:18px;background:#121a2d;box-shadow:0 20px 60px #0006}h1{margin-top:0;font-size:28px}p{color:#b8c2dc;line-height:1.5}.steps{margin:24px 0;padding:0;list-style:none}.steps li{padding:12px 0;border-bottom:1px solid #26304a}.button{display:inline-block;padding:12px 16px;border-radius:10px;background:#8ab4ff;color:#08101f;text-decoration:none;font-weight:700;margin:8px 8px 8px 0}.secondary{background:#273653;color:#eef2ff}.notice{margin-top:18px;padding:14px;border-radius:10px;background:#211d13;color:#f4d58a;line-height:1.45}code{font-family:ui-monospace,monospace}.small{font-size:13px;color:#8793b0}</style></head>
<body><main class='card'><h1>Connect Gemini account</h1>
<p>This authorizes the MCP connection. Gemini Web login is completed in the isolated Chromium profile on your server.</p>
<ol class='steps'><li><b>1.</b> Open Chromium login.</li><li><b>2.</b> Sign in to the Google account you want to connect.</li><li><b>3.</b> Return here and continue.</li></ol>
""" + browser_link + "<a class='button' href='" + html.escape(finish, quote=True) + "'>Continue after login</a>" + """
<p class='small'>This page never displays or transmits your Google cookies. The authorization link expires after a short time and can be used once.</p></main></body></html>"""

    def completion_redirect(self, code: str, subject: str) -> str:
        with self._lock:
            item = self._codes.get(code)
            if not item:
                raise ValueError("invalid authorization request")
            self.complete(code, subject)
            return item.redirect_uri + ("&" if "?" in item.redirect_uri else "?") + urlencode({"code": code})


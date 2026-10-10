"""Deployment URL configuration shared by gateway integrations."""
from __future__ import annotations

import os
from urllib.parse import urlparse


def public_url(value: str | None = None) -> str | None:
    """Return a normalized external HTTPS origin, or None when unset."""
    raw = (value if value is not None else os.getenv("GEMINI_PUBLIC_URL", "")).strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("GEMINI_PUBLIC_URL must be an HTTPS origin, for example https://gemini.example.com")
    return raw.rstrip("/")


def external_url(path: str, base: str | None = None) -> str:
    origin = public_url(base)
    if origin is None:
        raise RuntimeError("GEMINI_PUBLIC_URL is required for external OAuth and handoff URLs")
    return f"{origin}/{path.lstrip('/')}"

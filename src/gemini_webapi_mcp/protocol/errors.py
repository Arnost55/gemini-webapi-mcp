"""Stable error taxonomy; messages never include prompts, cookies, or tokens."""

from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    AUTHENTICATION = "authentication"
    ENTITLEMENT = "entitlement"
    TRANSPORT = "transport"
    SERIALIZATION = "serialization"
    PROTOCOL_REJECTION = "protocol_rejection"
    PARSING = "parsing"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    UPSTREAM = "upstream"
    UNSUPPORTED = "unsupported"


class ProtocolError(RuntimeError):
    def __init__(self, category: ErrorCategory, code: str, safe_message: str, *, retryable: bool = False):
        super().__init__(safe_message)
        self.category = category
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable

    def public(self) -> dict[str, object]:
        return {"category": self.category.value, "code": self.code,
                "message": self.safe_message, "retryable": self.retryable}


def classify_exception(exc: Exception) -> ProtocolError:
    """Map upstream/library failures without importing it at module import time."""
    name = type(exc).__name__.lower()
    detail = str(exc).lower()
    if "auth" in name or any(x in detail for x in ("cookie", "sign in", "logged in")):
        return ProtocolError(ErrorCategory.AUTHENTICATION, "authentication_error", "Gemini authentication is unavailable")
    if "ratelimit" in name or "usage" in name or "429" in detail:
        return ProtocolError(ErrorCategory.RATE_LIMIT, "rate_limited", "Gemini rate limited the request", retryable=True)
    if "timeout" in name or "timed out" in detail or "stalled" in detail:
        return ProtocolError(ErrorCategory.TIMEOUT, "request_timeout", "Gemini did not complete the request in time", retryable=True)
    if "model" in name or any(x in detail for x in ("model invalid", "not available", "not enabled")):
        return ProtocolError(ErrorCategory.ENTITLEMENT, "model_unavailable", "The selected model is unavailable for this account")
    if any(x in detail for x in ("parse", "truncated", "invalid frame")):
        return ProtocolError(ErrorCategory.PARSING, "response_parse_error", "Gemini returned an incomplete or invalid response")
    if any(x in name for x in ("connection", "request")):
        return ProtocolError(ErrorCategory.TRANSPORT, "transport_error", "The Gemini transport failed", retryable=True)
    return ProtocolError(ErrorCategory.UPSTREAM, "upstream_error", "Gemini rejected or failed the request")

"""Production-safe diagnostics for image-tool response delivery.

The diagnostic stream is deliberately metadata-only.  It never records tool
arguments, prompts, cookies, media bytes, base64 strings, or filesystem paths.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("gemini_mcp.image_delivery")

_CORRELATION_PATTERN = re.compile(
    rb'"correlation_id"\s*:\s*"([0-9a-f]{32})"'
)
_SAFE_FIELDS = {
    "artifact_count",
    "correlation_id",
    "disconnect_observed",
    "duration_ms",
    "encoded_bytes",
    "exception_category",
    "http_status",
    "image_blocks",
    "mime_types",
    "model",
    "response_bytes",
    "stage",
    "status",
    "text_blocks",
    "transport_completed",
}


def diagnostics_enabled() -> bool:
    return os.environ.get("GEMINI_IMAGE_DELIVERY_DIAGNOSTICS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def _safe_value(key: str, value: Any) -> Any:
    if key == "mime_types":
        return [str(item)[:64] for item in list(value)[:16]]
    if key in {"model", "exception_category", "status", "stage"}:
        return str(value)[:128]
    if key == "correlation_id":
        value = str(value)
        return value if re.fullmatch(r"[0-9a-f]{32}", value) else "invalid"
    if isinstance(value, bool | int | float) or value is None:
        return value
    return str(value)[:128]


def image_delivery_event(stage: str, **fields: Any) -> None:
    """Emit one allow-listed JSON event when diagnostics are enabled."""
    if not diagnostics_enabled():
        return
    payload = {"stage": stage}
    payload.update(
        (key, _safe_value(key, value))
        for key, value in fields.items()
        if key in _SAFE_FIELDS
    )
    logger.info("image_delivery %s", json.dumps(payload, sort_keys=True, separators=(",", ":")))


class ImageDeliveryTransportDiagnostics:
    """Observe ASGI response completion without buffering media payloads.

    A small rolling window is searched for the response correlation identifier.
    Media/base64 data is neither retained nor logged.  ``transport_completed``
    means the ASGI application completed its send sequence; it does not claim
    that a remote connector acknowledged or displayed the result.
    """

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if (
            not diagnostics_enabled()
            or scope.get("type") != "http"
            or scope.get("method") != "POST"
        ):
            await self.app(scope, receive, send)
            return

        started = time.monotonic()
        status: int | None = None
        response_bytes = 0
        correlation_id: str | None = None
        rolling = b""
        disconnected = False

        async def observed_receive():
            nonlocal disconnected
            message = await receive()
            if message.get("type") == "http.disconnect":
                disconnected = True
            return message

        async def observed_send(message: dict[str, Any]) -> None:
            nonlocal status, response_bytes, correlation_id, rolling
            if message.get("type") == "http.response.start":
                status = int(message.get("status", 0))
            elif message.get("type") == "http.response.body":
                body = message.get("body", b"")
                response_bytes += len(body)
                if correlation_id is None and body:
                    sample = rolling + body
                    match = _CORRELATION_PATTERN.search(sample)
                    if match:
                        correlation_id = match.group(1).decode("ascii")
                    rolling = sample[-96:]
            await send(message)

        try:
            await self.app(scope, observed_receive, observed_send)
        except BaseException as exc:
            if correlation_id:
                image_delivery_event(
                    "http_response_failed",
                    correlation_id=correlation_id,
                    status="failed",
                    http_status=status,
                    response_bytes=response_bytes,
                    transport_completed=False,
                    disconnect_observed=disconnected,
                    exception_category=type(exc).__name__,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
            raise
        if correlation_id:
            image_delivery_event(
                "http_response_completed",
                correlation_id=correlation_id,
                status="complete",
                http_status=status,
                response_bytes=response_bytes,
                transport_completed=True,
                disconnect_observed=disconnected,
                duration_ms=round((time.monotonic() - started) * 1000),
            )

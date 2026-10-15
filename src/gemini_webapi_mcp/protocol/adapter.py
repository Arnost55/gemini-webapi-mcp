"""Replaceable adapter contract and quarantined legacy implementation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from .contracts import GeminiRequest, GeminiResponse, ModelDescriptor


class GeminiProtocolAdapter(Protocol):
    revision: str

    async def discover_models(self) -> tuple[ModelDescriptor, ...]: ...
    async def execute(self, request: GeminiRequest) -> GeminiResponse: ...
    async def close(self) -> None: ...


class LegacyClientAdapter:
    """Compatibility quarantine for xob0t/Gemini-API.

    The MCP layer should migrate to ``execute``. ``native`` remains available
    during Phase 5 so image/download paths retain behavior without a flag day.
    """

    revision = "xob0t-4402e523-compat1"

    def __init__(self, native: Any, discover: Callable[[Any], Awaitable[list[Any]]]):
        self.native = native
        self._discover = discover

    def __getattr__(self, name: str) -> Any:
        return getattr(self.native, name)

    async def discover_models(self) -> tuple[ModelDescriptor, ...]:
        from .contracts import CapabilityState, Operation
        found = await self._discover(self.native)
        return tuple(ModelDescriptor(
            public_id=item.id,
            display_name=item.label,
            upstream_id=str(item.upstream_model) if item.upstream_model is not None else None,
            operations=frozenset({Operation.CHAT}),
            state=CapabilityState.VERIFIED if item.available == "yes" and item.upstream_model is not None else CapabilityState.DISCOVERED,
            protocol_revision=item.protocol_version or self.revision,
            evidence_sha256=item.evidence_sha256,
            route={"headers": dict(item.request_headers), "fields": dict(item.request_fields)},
        ) for item in found)

    async def execute(self, request: GeminiRequest) -> GeminiResponse:
        import time
        started = time.monotonic()
        response = await self.native.generate_content(
            request.prompt, model=request.model, files=list(request.files) or None, current_retry=0,
        )
        metadata = getattr(response, "metadata", None)
        conversation = tuple((metadata or [None, None, None])[:3]) if metadata else None
        return GeminiResponse(
            text=getattr(response, "text", "") or "", conversation=conversation,
            images=tuple(getattr(response, "images", ()) or ()),
            protocol_revision=self.revision,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def close(self) -> None:
        await self.native.close()

"""Typed, content-free contracts shared by MCP and protocol adapters."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CapabilityState(str, Enum):
    DISCOVERED = "discovered"
    SUPPORTED = "supported"
    VERIFIED = "verified"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ThinkingLevel(str, Enum):
    AUTO = "auto"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Operation(str, Enum):
    CHAT = "chat"
    IMAGE_GENERATION = "image_generation"
    IMAGE_EDIT = "image_edit"
    UPLOAD = "upload"
    URL_ANALYSIS = "url_analysis"


class ModelDescriptor(BaseModel):
    """One account-specific model route and its evidence state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    public_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)
    upstream_id: str | None = Field(default=None, max_length=512)
    operations: frozenset[Operation] = frozenset({Operation.CHAT})
    thinking_levels: frozenset[ThinkingLevel] = frozenset()
    state: CapabilityState = CapabilityState.UNKNOWN
    protocol_revision: str
    evidence_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    route: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @field_validator("thinking_levels")
    @classmethod
    def auto_is_not_an_upstream_selector(cls, value: frozenset[ThinkingLevel]):
        # `auto` means omit an independent control; it is always safe to expose.
        return value | {ThinkingLevel.AUTO}

    @property
    def routable(self) -> bool:
        return self.state is CapabilityState.VERIFIED and self.upstream_id is not None


class GeminiRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: Operation
    model: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1)
    thinking_level: ThinkingLevel = ThinkingLevel.AUTO
    files: tuple[str, ...] = ()
    conversation: tuple[str | None, str | None, str | None] | None = None
    request_id: str


class GeminiResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = ""
    follow_ups: tuple[dict[str, str | None], ...] = ()
    reasoning_available: bool = False
    conversation: tuple[str | None, str | None, str | None] | None = None
    images: tuple[Any, ...] = ()
    completed: bool = True
    protocol_revision: str
    duration_ms: int = Field(ge=0)

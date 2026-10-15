"""Versioned boundary around Gemini's undocumented web protocol."""

from .contracts import (
    CapabilityState,
    GeminiRequest,
    GeminiResponse,
    ModelDescriptor,
    Operation,
    ThinkingLevel,
)
from .errors import ErrorCategory, ProtocolError, classify_exception
from .registry import AccountModelRegistry
from .serializer import SERIALIZER_REVISION, bind_stream_request

__all__ = [
    "AccountModelRegistry",
    "CapabilityState",
    "ErrorCategory",
    "GeminiRequest",
    "GeminiResponse",
    "ModelDescriptor",
    "Operation",
    "ProtocolError",
    "SERIALIZER_REVISION",
    "ThinkingLevel",
    "bind_stream_request",
    "classify_exception",
]

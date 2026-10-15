"""Bounded stream progress/completion tracking independent of HTTP transport."""

from __future__ import annotations

from dataclasses import dataclass, field
import time

from .errors import ErrorCategory, ProtocolError


@dataclass
class StreamTracker:
    stall_timeout: float
    started_at: float = field(default_factory=time.monotonic)
    last_progress_at: float = field(default_factory=time.monotonic)
    last_sequence: int = -1
    completed: bool = False

    def observe(self, sequence: int, *, progress: bool, completed: bool = False, now: float | None = None) -> None:
        if sequence <= self.last_sequence:
            raise ProtocolError(ErrorCategory.PARSING, "stream_sequence_invalid", "Gemini returned an out-of-order stream frame")
        self.last_sequence = sequence
        instant = time.monotonic() if now is None else now
        if progress:
            self.last_progress_at = instant
        self.completed = self.completed or completed

    def assert_live(self, now: float | None = None) -> None:
        instant = time.monotonic() if now is None else now
        if not self.completed and instant - self.last_progress_at > self.stall_timeout:
            raise ProtocolError(ErrorCategory.TIMEOUT, "stream_stalled", "Gemini's stream stopped making progress", retryable=True)

    def assert_complete(self) -> None:
        if not self.completed:
            raise ProtocolError(ErrorCategory.PARSING, "stream_incomplete", "Gemini closed the stream before completion")

"""Single source of truth for discovery, routing, and verification state."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import time

from .contracts import CapabilityState, ModelDescriptor, Operation, ThinkingLevel
from .errors import ErrorCategory, ProtocolError


@dataclass(frozen=True)
class RegistrySnapshot:
    account: str
    discovered_at: float
    models: tuple[ModelDescriptor, ...]


class AccountModelRegistry:
    def __init__(self) -> None:
        self._snapshots: dict[str, RegistrySnapshot] = {}

    def replace(self, account: str, models: Iterable[ModelDescriptor], *, discovered_at: float | None = None) -> RegistrySnapshot:
        if not account:
            raise ValueError("account is required")
        unique: dict[str, ModelDescriptor] = {}
        for model in models:
            if model.public_id in unique:
                raise ValueError(f"duplicate public model identifier: {model.public_id}")
            unique[model.public_id] = model
        snapshot = RegistrySnapshot(account, discovered_at or time.time(), tuple(unique.values()))
        self._snapshots[account] = snapshot
        return snapshot

    def snapshot(self, account: str) -> RegistrySnapshot | None:
        return self._snapshots.get(account)

    def resolve(self, account: str, public_id: str, operation: Operation,
                thinking: ThinkingLevel = ThinkingLevel.AUTO) -> ModelDescriptor:
        snapshot = self._snapshots.get(account)
        model = next((m for m in snapshot.models if m.public_id == public_id), None) if snapshot else None
        if model is None:
            raise ProtocolError(ErrorCategory.ENTITLEMENT, "model_unavailable", "The selected model is not in this account's catalog")
        if model.state is CapabilityState.UNAVAILABLE:
            raise ProtocolError(ErrorCategory.ENTITLEMENT, "model_unavailable", "The selected model is unavailable for this account")
        if not model.routable:
            raise ProtocolError(ErrorCategory.UNSUPPORTED, "model_route_unverified", "The selected model has no verified request route")
        if operation not in model.operations:
            raise ProtocolError(ErrorCategory.UNSUPPORTED, "operation_not_supported", "The selected model has not been verified for this operation")
        # AUTO means omit an independent upstream control and accept the
        # model/account default. Only explicit levels require route evidence.
        if thinking is not ThinkingLevel.AUTO and thinking not in model.thinking_levels:
            raise ProtocolError(ErrorCategory.UNSUPPORTED, "thinking_not_supported", "The requested thinking level has not been verified for this model")
        return model

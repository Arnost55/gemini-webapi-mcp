"""Account-scoped Gemini model discovery and safe model selection.

The web client has no stable public model-discovery API. This module separates
*observed* model metadata from models that have a verified request route. A
model shown by the Gemini UI is never silently mapped to an opaque web-client
identifier: it must be exposed by the installed client or explicitly configured
by an operator who has captured a compatible route.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import asdict, dataclass
import json
import os
import time
from typing import Any, Iterable, Mapping


class ModelSelectionError(ValueError):
    """A requested model or thinking level cannot be used safely."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ModelCapability:
    """Public model metadata; route details are deliberately not serialized."""

    id: str
    label: str
    text: bool = True
    image: bool = False
    video_input: bool = False
    video_output: bool = False
    reasoning: bool = False
    thinking_levels: tuple[str, ...] = ()
    default_thinking_level: str | None = None
    source: str = "observed"
    confidence: str = "observed"
    available: str = "unknown"
    upstream_model: Any = None
    request_headers: tuple[tuple[str, str], ...] = ()
    thinking_request_headers: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()
    request_fields: tuple[tuple[int, Any], ...] = ()
    thinking_request_fields: tuple[tuple[str, tuple[tuple[int, Any], ...]], ...] = ()
    protocol_version: str | None = None
    evidence_sha256: str | None = None

    def public(self, *, discovered_at: float | None = None) -> dict[str, Any]:
        value = asdict(self)
        value.pop("upstream_model", None)
        value.pop("request_headers", None)
        value.pop("thinking_request_headers", None)
        value.pop("request_fields", None)
        value.pop("thinking_request_fields", None)
        value["thinking_levels"] = list(self.thinking_levels)
        if discovered_at is not None:
            value["discovered_at"] = discovered_at
        return value


# These names and request routes are present in the pinned gemini-webapi
# package. They are legacy compatibility entries, not proof the account has
# current access. The client exposes no verified thinking-level parameter.
FALLBACKS = (
    ModelCapability("gemini-3.0-flash", "Gemini Flash (legacy web client)",
                    source="fallback", confidence="low", upstream_model="gemini-3.0-flash"),
    ModelCapability("gemini-3.0-pro", "Gemini Pro (legacy web client)",
                    source="fallback", confidence="low", upstream_model="gemini-3.0-pro"),
    ModelCapability("gemini-3.0-flash-thinking", "Gemini Flash Thinking (legacy web client)",
                    reasoning=True, source="fallback", confidence="low",
                    upstream_model="gemini-3.0-flash-thinking"),
)

_VALID_THINKING_LEVELS = frozenset({"low", "medium", "high"})
_request_headers: ContextVar[Mapping[str, str] | None] = ContextVar(
    "gemini_model_request_headers", default=None
)
_request_fields: ContextVar[Mapping[int, Any] | None] = ContextVar(
    "gemini_model_request_fields", default=None
)


def current_request_headers() -> Mapping[str, str]:
    """Request-local verified headers for the selected configured route."""
    return _request_headers.get() or {}


def current_request_fields() -> Mapping[int, Any]:
    """Request-local, allow-listed f.req fields from verified evidence."""
    return _request_fields.get() or {}


class model_request_context:
    """Apply a verified configured route to exactly one request."""

    def __init__(self, selection: "ModelSelection"):
        self.selection = selection
        self._token = None
        self._fields_token = None

    def __enter__(self):
        self._token = _request_headers.set(dict(self.selection.request_headers))
        self._fields_token = _request_fields.set(dict(self.selection.request_fields))
        return self

    def __exit__(self, *_exc):
        _request_headers.reset(self._token)
        _request_fields.reset(self._fields_token)


@dataclass(frozen=True)
class ModelSelection:
    id: str
    upstream_model: Any
    thinking_level: str | None
    request_headers: tuple[tuple[str, str], ...] = ()
    request_fields: tuple[tuple[int, Any], ...] = ()


def _candidate(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("id", "name", "model", "model_name"):
            found = _candidate(value.get(key))
            if found:
                return found
    return None


def _as_bool(value: Any, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _as_levels(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(level for level in value if isinstance(level, str) and level in _VALID_THINKING_LEVELS)


_ALLOWED_PROTOCOL_FIELDS = frozenset({17, 31, 79, 80, 91, 96})


def _as_fields(value: Any) -> tuple[tuple[int, Any], ...]:
    """Accept only known model/thinking selector slots, never prompt/session slots."""
    if not isinstance(value, Mapping):
        return ()
    fields: list[tuple[int, Any]] = []
    for raw_index, field_value in value.items():
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            continue
        if index in _ALLOWED_PROTOCOL_FIELDS and isinstance(field_value, (int, str, bool, list, type(None))):
            fields.append((index, field_value))
    return tuple(sorted(fields))


def _from_mapping(raw: Mapping[str, Any], *, source: str, confidence: str) -> ModelCapability | None:
    model_id = _candidate(raw)
    if not model_id:
        return None
    levels = _as_levels(raw.get("thinking_levels"))
    default = raw.get("default_thinking_level")
    if default not in levels:
        default = None
    label = raw.get("label") if isinstance(raw.get("label"), str) else model_id
    # An observed object may say its own model name, but that does not prove the
    # legacy client's Model.from_name can route it. Only configured entries may
    # provide an explicit upstream route.
    upstream = raw.get("upstream_model") if source == "configured" else None
    headers = raw.get("request_headers", {}) if source == "configured" else {}
    if not isinstance(headers, Mapping) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        headers = {}
    raw_thinking_headers = raw.get("thinking_request_headers", {}) if source == "configured" else {}
    thinking_headers: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    if isinstance(raw_thinking_headers, Mapping):
        for level, value in raw_thinking_headers.items():
            if level not in levels or not isinstance(value, Mapping):
                continue
            if all(isinstance(key, str) and isinstance(header, str) for key, header in value.items()):
                thinking_headers.append((level, tuple(sorted(value.items()))))
    fields = _as_fields(raw.get("request_fields")) if source == "configured" else ()
    raw_thinking_fields = raw.get("thinking_request_fields", {}) if source == "configured" else {}
    thinking_fields: list[tuple[str, tuple[tuple[int, Any], ...]]] = []
    if isinstance(raw_thinking_fields, Mapping):
        for level, value in raw_thinking_fields.items():
            mapped = _as_fields(value)
            if level in levels and mapped:
                thinking_fields.append((level, mapped))
    protocol_version = raw.get("protocol_version") if isinstance(raw.get("protocol_version"), str) else None
    evidence_sha256 = raw.get("evidence_sha256") if isinstance(raw.get("evidence_sha256"), str) else None
    # Modern private-protocol fields are accepted only with a versioned,
    # fingerprinted capture. Legacy header-only catalogs remain compatible.
    if (fields or thinking_fields) and not (
        protocol_version and evidence_sha256 and len(evidence_sha256) == 64
    ):
        fields = ()
        thinking_fields = []
    return ModelCapability(
        id=model_id,
        label=label,
        text=_as_bool(raw.get("text"), True),
        image=_as_bool(raw.get("image")),
        video_input=_as_bool(raw.get("video_input")),
        video_output=_as_bool(raw.get("video_output")),
        reasoning=_as_bool(raw.get("reasoning")) or bool(levels),
        thinking_levels=levels,
        default_thinking_level=default,
        source=source,
        confidence=confidence,
        available=raw.get("available") if raw.get("available") in {"yes", "no", "unknown"} else "unknown",
        upstream_model=upstream,
        request_headers=tuple(sorted(headers.items())),
        thinking_request_headers=tuple(thinking_headers),
        request_fields=fields,
        thinking_request_fields=tuple(thinking_fields),
        protocol_version=protocol_version,
        evidence_sha256=evidence_sha256,
    )


def _configured_catalog() -> list[ModelCapability]:
    """Read an evidence-backed operator catalog without accepting user input."""
    raw = os.environ.get("GEMINI_MODEL_CATALOG_JSON", "").strip()
    if raw:
        try:
            decoded = json.loads(raw)
            entries = decoded.get("models", []) if isinstance(decoded, Mapping) else decoded
            if isinstance(entries, list):
                return [item for entry in entries if isinstance(entry, Mapping)
                        for item in [_from_mapping(entry, source="configured", confidence="configured")] if item]
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    # Preserve the original simple setting for listings. These entries cannot
    # be routed without an explicit verified catalog mapping.
    return [ModelCapability(item.strip(), item.strip(), source="configured_name_only", confidence="low")
            for item in os.environ.get("GEMINI_MODELS", "").split(",") if item.strip()]


def discover_models(client: Any | None = None) -> list[ModelCapability]:
    """Collect configured and client-exposed metadata without network probing."""
    capabilities = _configured_catalog()
    if client is not None:
        for attr in ("models", "available_models", "model_list", "supported_models"):
            try:
                raw = getattr(client, attr, None)
                raw = raw() if callable(raw) else raw
                if isinstance(raw, Mapping):
                    raw = raw.get("models", raw.values())
                if raw:
                    for item in raw:
                        if isinstance(item, Mapping):
                            capability = _from_mapping(item, source="observed", confidence="observed")
                        else:
                            model_id = _candidate(item)
                            capability = ModelCapability(model_id, model_id) if model_id else None
                        if capability:
                            capabilities.append(capability)
            except Exception:
                continue
    unique: dict[str, ModelCapability] = {}
    for capability in capabilities:
        unique.setdefault(capability.id, capability)
    return list(unique.values()) or list(FALLBACKS)


def discovery_metadata(client: Any | None = None) -> dict[str, Any]:
    """Explain exactly what the local client can (and cannot) discover.

    ``gemini-webapi`` currently ships static request routes only. Its model
    enum is useful evidence that legacy routes are installed, but it is not an
    authenticated account-capability endpoint and must never be presented as
    one. This keeps a fallback catalog from looking like successful UI
    discovery.
    """
    inspected_attributes = ("models", "available_models", "model_list", "supported_models")
    exposed = []
    if client is not None:
        for attr in inspected_attributes:
            try:
                value = getattr(client, attr, None)
                value = value() if callable(value) else value
                if value:
                    exposed.append(attr)
            except Exception:
                # Observation must not cause a network probe or affect chat.
                continue
    return {
        "mode": "client_attribute_observation" if exposed else "legacy_route_fallback",
        "client_attributes_with_values": exposed,
        "client_attributes_checked": list(inspected_attributes),
        "authenticated_account_capability_endpoint": False,
        "thinking_level_protocol_verified": False,
        "reason": (
            "the installed gemini-webapi client exposes no authenticated model "
            "enumeration or independent thinking-level control"
            if not exposed
            else "model metadata was observed from an installed client attribute; routes still require verification"
        ),
    }


class ModelCatalogCache:
    """Small per-account TTL cache; client lifecycle invalidates it explicitly."""

    def __init__(self, ttl_seconds: float | None = None):
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else max(
            1.0, float(os.environ.get("GEMINI_MODEL_DISCOVERY_TTL_SECONDS", "300"))
        )
        self._values: dict[tuple[str, str], tuple[float, list[ModelCapability]]] = {}

    def get(self, key: tuple[str, str], client: Any | None) -> tuple[list[ModelCapability], float]:
        now = time.time()
        cached = self._values.get(key)
        if cached and now - cached[0] < self.ttl_seconds:
            return cached[1], cached[0]
        values = discover_models(client)
        self._values[key] = (now, values)
        return values, now

    def invalidate(self, key: tuple[str, str] | None = None) -> None:
        if key is None:
            self._values.clear()
        else:
            self._values.pop(key, None)


def resolve_model(capabilities: Iterable[ModelCapability], model: str, thinking_level: str | None = None) -> ModelSelection:
    """Resolve only a model with a locally verified request route."""
    capability = next((item for item in capabilities if item.id == model), None)
    if capability is None:
        raise ModelSelectionError("model_unavailable", "requested model is not present in this account's model catalog")
    if capability.upstream_model is None:
        raise ModelSelectionError(
            "model_route_unverified",
            "requested model was observed but has no verified gemini-webapi routing configuration",
        )
    if thinking_level is not None:
        if thinking_level not in _VALID_THINKING_LEVELS:
            raise ModelSelectionError("invalid_thinking_level", "thinking_level must be low, medium, or high")
        if thinking_level not in capability.thinking_levels:
            raise ModelSelectionError(
                "thinking_not_supported",
                "the installed client has no verified thinking-level request mapping for this model",
            )
    effective_level = thinking_level or capability.default_thinking_level
    headers = dict(capability.request_headers)
    fields = dict(capability.request_fields)
    if effective_level:
        headers.update(dict(capability.thinking_request_headers).get(effective_level, ()))
        fields.update(dict(capability.thinking_request_fields).get(effective_level, ()))
    return ModelSelection(
        id=capability.id,
        upstream_model=capability.upstream_model,
        thinking_level=effective_level,
        request_headers=tuple(sorted(headers.items())),
        request_fields=tuple(sorted(fields.items())),
    )


def capabilities_json(client: Any | None = None) -> list[dict[str, Any]]:
    return [item.public() for item in discover_models(client)]

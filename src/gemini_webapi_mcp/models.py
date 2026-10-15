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
import hashlib
import json
import os
import time
from typing import Any, Iterable, Mapping


_GET_USER_STATUS_RPC = "otAQ7b"
_MODEL_CAPABILITIES = (4, 5, 6, 8)


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
PROTOCOL_VERSION = "gemini-web-streamgenerate-v1"
MODEL_ROUTE_HEADER = "x-goog-ext-525001261-jspb"
_request_headers: ContextVar[Mapping[str, str] | None] = ContextVar(
    "gemini_model_request_headers", default=None
)
_request_fields: ContextVar[Mapping[int, Any] | None] = ContextVar(
    "gemini_model_request_fields", default=None
)
_request_model_id: ContextVar[str | None] = ContextVar(
    "gemini_model_request_id", default=None
)


def current_request_headers() -> Mapping[str, str]:
    """Request-local verified headers for the selected configured route."""
    return _request_headers.get() or {}


def current_request_fields() -> Mapping[int, Any]:
    """Request-local, allow-listed f.req fields from verified evidence."""
    return _request_fields.get() or {}


def current_request_model_id() -> str | None:
    """Public, account-scoped model name selected for this request."""
    return _request_model_id.get()


class model_request_context:
    """Apply a verified configured route to exactly one request."""

    def __init__(self, selection: "ModelSelection"):
        self.selection = selection
        self._token = None
        self._fields_token = None
        self._model_token = None

    def __enter__(self):
        self._token = _request_headers.set(dict(self.selection.request_headers))
        self._fields_token = _request_fields.set(dict(self.selection.request_fields))
        self._model_token = _request_model_id.set(self.selection.id)
        return self

    def __exit__(self, *_exc):
        _request_headers.reset(self._token)
        _request_fields.reset(self._fields_token)
        _request_model_id.reset(self._model_token)


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


def _model_header(value: Any) -> str | None:
    """Return a bounded jspb model header, or fail closed."""
    if not isinstance(value, str) or len(value) > 8192:
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(decoded, list) else None


def selector_evidence_sha256(
    levels: Iterable[str],
    thinking_headers: Mapping[str, Mapping[str, str]],
    thinking_fields: Mapping[str, Mapping[int | str, Any]],
) -> str:
    """Fingerprint exactly the per-level selector metadata the adapter applies."""
    evidence: dict[str, dict[str, Any]] = {}
    for level in ("low", "medium", "high"):
        if level not in levels:
            continue
        headers = thinking_headers.get(level, {})
        header = _model_header(headers.get(MODEL_ROUTE_HEADER))
        fields = _as_fields(thinking_fields.get(level, {}))
        evidence[level] = {
            "header": header or "",
            "fields": {str(index): value for index, value in fields},
        }
    canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


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
    raw_headers = raw.get("request_headers", {}) if source == "configured" else {}
    headers: dict[str, str] = {}
    if isinstance(raw_headers, Mapping):
        header = _model_header(raw_headers.get(MODEL_ROUTE_HEADER))
        if header is not None:
            headers[MODEL_ROUTE_HEADER] = header
    raw_thinking_headers = raw.get("thinking_request_headers", {}) if source == "configured" else {}
    thinking_headers: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    if isinstance(raw_thinking_headers, Mapping):
        for level, value in raw_thinking_headers.items():
            if level not in levels or not isinstance(value, Mapping):
                continue
            header = _model_header(value.get(MODEL_ROUTE_HEADER))
            if header is not None:
                thinking_headers.append((level, ((MODEL_ROUTE_HEADER, header),)))
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
    # Modern private-protocol selectors are accepted only when their exact
    # normalized values match the capture fingerprint.
    normalized_thinking_headers = dict(thinking_headers)
    normalized_thinking_fields = dict(thinking_fields)
    computed_fingerprint = selector_evidence_sha256(
        levels,
        {level: dict(value) for level, value in normalized_thinking_headers.items()},
        {level: dict(value) for level, value in normalized_thinking_fields.items()},
    )
    evidence_valid = (
        protocol_version == PROTOCOL_VERSION
        and evidence_sha256 == computed_fingerprint
        and bool(levels)
    )
    selector_present = bool(headers or thinking_headers or fields or thinking_fields)
    if selector_present and not evidence_valid:
        # Do not leave a configured modern model looking routable after its
        # rejected selector data has been stripped.
        upstream = None
        headers = {}
        thinking_headers = []
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


def _nested(value: Any, path: Iterable[int], default: Any = None) -> Any:
    for index in path:
        if not isinstance(value, list) or index >= len(value):
            return default
        value = value[index]
    return value


def _model_slug(model_id: str, category: str, display_name: str) -> str:
    """Use the account-provided category as the stable public selector."""
    name = category.strip() or display_name.strip() or model_id
    slug = "-".join(name.lower().replace("_", "-").split())
    return slug if slug.startswith("gemini-") else f"gemini-{slug}"


def _account_capacity(tier_flags: Any, capability_flags: Any) -> tuple[int, int]:
    tiers = tier_flags if isinstance(tier_flags, list) else []
    capabilities = capability_flags if isinstance(capability_flags, list) else []
    if 21 in tiers:
        return 1, 13
    if 22 in tiers:
        return 2, 13
    if 115 in capabilities:
        return 4, 12
    if 16 in tiers or 106 in capabilities:
        return 3, 12
    return ((2, 12) if 8 in tiers or 19 in capabilities else (1, 12))


def parse_authenticated_models(payload: str | list[Any]) -> list[ModelCapability]:
    """Parse the authenticated GetUserStatus response into verified routes.

    This is a deliberately small backport of maintained upstream's dynamic
    ``AvailableModel`` parser. The response itself supplies model IDs, model
    numbers and account capacity; no static modern identifiers are accepted.
    """
    if isinstance(payload, str):
        try:
            from gemini_webapi.utils import extract_json_from_response
            frames = extract_json_from_response(payload)
        except (ImportError, ValueError, TypeError):
            return []
    elif isinstance(payload, list):
        frames = payload
    else:
        return []

    found: list[ModelCapability] = []
    for frame in frames:
        if not isinstance(frame, list) or _nested(frame, [1]) != _GET_USER_STATUS_RPC:
            continue
        body = _nested(frame, [2])
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        models = _nested(body, [15])
        if not isinstance(models, list):
            continue
        capacity, capacity_field = _account_capacity(_nested(body, [16], []), _nested(body, [17], []))
        for raw in models:
            if not isinstance(raw, list):
                continue
            model_id = _nested(raw, [0])
            if not isinstance(model_id, str) or not model_id:
                continue
            category = str(_nested(raw, [1], "") or _nested(raw, [10], "") or "")
            display = str(_nested(raw, [11], "") or _nested(raw, [19], "") or category or model_id)
            number = _nested(raw, [17])
            if not isinstance(number, int):
                number = _nested(raw, [9], 1)
            if not isinstance(number, int):
                number = 1
            header_values: list[Any] = [
                1, None, None, None, model_id, None, None, 0,
                list(_MODEL_CAPABILITIES), None, None,
            ]
            if capacity_field == 13:
                header_values.append(None)
            header_values.extend([capacity, None, None, number])
            header = json.dumps(header_values, separators=(",", ":"))
            route_headers = {
                MODEL_ROUTE_HEADER: header,
                "x-goog-ext-73010989-jspb": "[0]",
                "x-goog-ext-73010990-jspb": "[0,0,0]",
            }
            found.append(ModelCapability(
                id=_model_slug(model_id, category, display),
                label=display,
                reasoning="think" in (category + " " + display).lower(),
                source="authenticated_rpc",
                confidence="verified",
                available="yes",
                # The old client cannot resolve modern names. Let it build an
                # unspecified request while our request-local patch applies
                # the exact account-derived selector headers.
                upstream_model="unspecified",
                request_headers=tuple(sorted(route_headers.items())),
                protocol_version=PROTOCOL_VERSION,
            ))
        break
    unique: dict[str, ModelCapability] = {}
    for capability in found:
        unique.setdefault(capability.id, capability)
    return list(unique.values())


async def discover_authenticated_models(client: Any) -> list[ModelCapability]:
    """Query GetUserStatus through the already-authenticated client session."""
    batch = getattr(client, "_batch_execute", None)
    if not callable(batch):
        return []

    class _RPC:
        rpcid = _GET_USER_STATUS_RPC

        @staticmethod
        def serialize() -> list[Any]:
            return [_GET_USER_STATUS_RPC, "[]", None, "generic"]

    response = await batch([_RPC()])
    return parse_authenticated_models(getattr(response, "text", ""))


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

    def set(self, key: tuple[str, str], values: list[ModelCapability]) -> None:
        if values:
            self._values[key] = (time.time(), values)


def resolve_model(capabilities: Iterable[ModelCapability], model: str, thinking_level: str | None = None) -> ModelSelection:
    """Resolve only a model with a locally verified request route."""
    available = list(capabilities)
    capability = next((item for item in available if item.id == model), None)
    # Preserve the public legacy Flash/Pro selectors while replacing their
    # stale hardcoded route IDs with the authenticated account's same-family
    # route. This is identifier rotation, not cross-model substitution.
    authenticated_aliases = {
        "gemini-3.0-flash": "gemini-flash",
        "gemini-3.0-pro": "gemini-pro",
    }
    if capability is None and model in authenticated_aliases:
        target = authenticated_aliases[model]
        capability = next(
            (item for item in available if item.source == "authenticated_rpc" and item.id == target),
            None,
        )
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

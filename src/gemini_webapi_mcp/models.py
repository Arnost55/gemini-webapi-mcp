"""Dynamic model capability discovery with safe, explicit fallbacks.

The Gemini web application changes internal model identifiers. This module keeps
the MCP contract stable by describing capabilities separately from identifiers.
It never invents a model: identifiers are accepted only when observed from the
client or explicitly configured by the operator.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Any, Iterable


@dataclass(frozen=True)
class ModelCapability:
    id: str
    label: str
    text: bool = True
    image: bool = False
    video_input: bool = False
    video_output: bool = False
    reasoning: bool = False
    source: str = "observed"


FALLBACKS = (
    ModelCapability("gemini-3.0-flash", "Gemini Flash", source="fallback"),
    ModelCapability("gemini-3.0-pro", "Gemini Pro", source="fallback"),
    ModelCapability("gemini-3.0-flash-thinking", "Gemini Flash Thinking", reasoning=True, source="fallback"),
)


def _candidate(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("id", "name", "model", "model_name"):
            found = _candidate(value.get(key))
            if found:
                return found
    return None


def discover_models(client: Any | None = None) -> list[ModelCapability]:
    """Return observed models, falling back only when discovery is unavailable."""
    values: list[str] = []
    configured = os.getenv("GEMINI_MODELS", "")
    values.extend(x.strip() for x in configured.split(",") if x.strip())
    if client is not None:
        for attr in ("models", "available_models", "model_list", "supported_models"):
            try:
                raw = getattr(client, attr, None)
                raw = raw() if callable(raw) else raw
                if raw:
                    values.extend(x for item in raw for x in [_candidate(item)] if x)
            except Exception:
                continue
    if not values:
        return list(FALLBACKS)
    unique = dict.fromkeys(values)
    return [ModelCapability(model_id, model_id, source="observed") for model_id in unique]


def capabilities_json(client: Any | None = None) -> list[dict[str, Any]]:
    return [asdict(item) for item in discover_models(client)]

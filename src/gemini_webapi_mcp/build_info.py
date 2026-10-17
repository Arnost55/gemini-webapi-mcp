"""Runtime build provenance with an image-baked fallback."""

from __future__ import annotations

import json
import os
from importlib.resources import files


def build_provenance() -> dict[str, str]:
    baked: dict[str, str] = {}
    try:
        payload = files("gemini_webapi_mcp").joinpath("_build.json").read_text(encoding="utf-8")
        decoded = json.loads(payload)
        if isinstance(decoded, dict):
            baked = {str(key): str(value) for key, value in decoded.items() if value is not None}
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        pass

    def value(env_name: str, key: str) -> str:
        override = os.environ.get(env_name, "").strip()
        if override and override.lower() != "unknown":
            return override
        embedded = baked.get(key, "").strip()
        return embedded if embedded else "unknown"

    commit = os.environ.get("GEMINI_BUILD_COMMIT", "").strip()
    source = "GEMINI_BUILD_COMMIT"
    if not commit or commit.lower() == "unknown":
        commit = os.environ.get("GITHUB_SHA", "").strip()
        source = "GITHUB_SHA"
    if not commit or commit.lower() == "unknown":
        commit = baked.get("commit", "").strip()
        source = "image_metadata"
    if not commit:
        commit = "unknown"
        source = "unavailable"

    return {
        "commit": commit,
        "timestamp": value("GEMINI_BUILD_TIMESTAMP", "timestamp"),
        "version": value("GEMINI_BUILD_VERSION", "version"),
        "source": source,
    }

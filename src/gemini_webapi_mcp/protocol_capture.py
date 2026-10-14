"""Sanitize authorized Gemini StreamGenerate metadata into a model catalog.

Input stays local. Output contains only the model header and allow-listed
selector slots; prompts, cookies, tokens, URLs, and conversation metadata are
never copied. The result still requires authenticated behavioral validation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from .models import (
    _ALLOWED_PROTOCOL_FIELDS,
    MODEL_ROUTE_HEADER,
    PROTOCOL_VERSION,
    selector_evidence_sha256,
)

MODEL_HEADER = MODEL_ROUTE_HEADER


def _inner_fields(f_req: str) -> dict[str, Any]:
    outer = json.loads(f_req)
    if not isinstance(outer, list) or len(outer) < 2 or not isinstance(outer[1], str):
        raise ValueError("capture has an invalid f.req envelope")
    inner = json.loads(outer[1])
    if not isinstance(inner, list):
        raise ValueError("capture has an invalid inner request")
    return {str(index): inner[index] for index in sorted(_ALLOWED_PROTOCOL_FIELDS) if index < len(inner)}


def sanitize_capture(raw: Mapping[str, Any]) -> dict[str, Any]:
    model_id = raw.get("model_id")
    label = raw.get("label")
    captures = raw.get("captures")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("model_id is required")
    if not isinstance(label, str) or not label.strip():
        raise ValueError("label is required")
    if not isinstance(captures, list) or not captures:
        raise ValueError("captures must contain at least one request")

    by_level: dict[str, dict[str, Any]] = {}
    for capture in captures:
        if not isinstance(capture, Mapping):
            raise ValueError("each capture must be an object")
        level = capture.get("thinking_level")
        header = capture.get("model_header")
        f_req = capture.get("f_req")
        if level not in {"low", "medium", "high"}:
            raise ValueError("thinking_level must be low, medium, or high")
        if level in by_level:
            raise ValueError(f"duplicate capture for thinking_level {level}")
        if not isinstance(header, str) or not isinstance(f_req, str):
            raise ValueError("model_header and f_req are required strings")
        # Parsing also proves this is JSON; never preserve unrelated headers.
        if len(header) > 8192:
            raise ValueError("model_header is too large")
        parsed_header = json.loads(header)
        if not isinstance(parsed_header, list):
            raise ValueError("model_header must be a JSON array")
        by_level[level] = {"header": header, "fields": _inner_fields(f_req)}

    levels = [level for level in ("low", "medium", "high") if level in by_level]
    fingerprint = selector_evidence_sha256(
        levels,
        {level: {MODEL_HEADER: by_level[level]["header"]} for level in levels},
        {level: by_level[level]["fields"] for level in levels},
    )
    first = by_level[levels[0]]
    return {
        "protocol_version": PROTOCOL_VERSION,
        "evidence_sha256": fingerprint,
        "models": [{
            "id": model_id.strip(),
            "label": label.strip(),
            "upstream_model": {"model_name": model_id.strip(), "model_header": {}},
            "reasoning": True,
            "thinking_levels": levels,
            "request_headers": {MODEL_HEADER: first["header"]},
            "request_fields": first["fields"],
            "thinking_request_headers": {
                level: {MODEL_HEADER: by_level[level]["header"]} for level in levels
            },
            "thinking_request_fields": {
                level: by_level[level]["fields"] for level in levels
            },
            "protocol_version": PROTOCOL_VERSION,
            "evidence_sha256": fingerprint,
            "available": "unknown",
        }],
        "warning": "Captured routing metadata; live account validation is still required before marking available=yes.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sanitize Gemini StreamGenerate selector metadata")
    parser.add_argument("input", type=Path, help="local JSON capture description")
    parser.add_argument("output", type=Path, help="sanitized catalog JSON")
    args = parser.parse_args()
    raw = json.loads(args.input.read_text(encoding="utf-8"))
    sanitized = sanitize_capture(raw)
    args.output.write_text(json.dumps(sanitized, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote sanitized protocol metadata to {args.output}")


if __name__ == "__main__":
    main()

"""Pure serializer for one captured StreamGenerate protocol revision."""

from __future__ import annotations

import json
import uuid
from typing import Any

MODEL_ROUTE_HEADER = "x-goog-ext-525001261-jspb"
REQUEST_UUID_HEADER = "x-goog-ext-525005358-jspb"
SERIALIZER_REVISION = "streamgenerate-2026-10-r1"


class SerializationError(ValueError):
    pass


def bind_stream_request(headers: dict[str, str], data: dict[str, Any], *, selector_session_id: str,
                        language: str = "en", request_uuid: str | None = None) -> tuple[dict[str, str], dict[str, Any]]:
    """Bind runtime values to a discovery-produced selector; never invent a route."""
    result_headers, result_data = dict(headers), dict(data)
    header_value = result_headers.get(MODEL_ROUTE_HEADER)
    if not header_value:
        raise SerializationError("verified model selector header is missing")
    try:
        model_header = json.loads(header_value)
        outer = json.loads(result_data["f.req"])
        inner = json.loads(outer[1])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SerializationError("invalid StreamGenerate request envelope") from exc
    if not isinstance(model_header, list) or not isinstance(inner, list):
        raise SerializationError("invalid StreamGenerate selector or body")
    already_bound = len(model_header) >= 17 and isinstance(model_header[-1], str)
    raw_model_number = model_header[-3] if already_bound else model_header[-1]
    model_number = raw_model_number if isinstance(raw_model_number, int) else 1
    if already_bound:
        model_header[-2:] = [1, selector_session_id]
    else:
        model_header.extend([1, selector_session_id])
    if len(inner) < 81:
        inner.extend([None] * (81 - len(inner)))
    for index, value in {1: [language], 6: [1], 7: 1, 10: 1, 11: 0, 17: [[0]], 18: 0,
                         27: 1, 30: [4], 41: [1], 53: 0, 61: [], 68: 1,
                         79: model_number, 80: 1}.items():
        if inner[index] is None:
            inner[index] = value
    bound_uuid = request_uuid or str(uuid.uuid4()).upper()
    inner[59] = bound_uuid
    result_headers[MODEL_ROUTE_HEADER] = json.dumps(model_header, separators=(",", ":"))
    result_headers[REQUEST_UUID_HEADER] = json.dumps([bound_uuid, 1], separators=(",", ":"))
    result_headers.setdefault("X-Same-Domain", "1")
    outer[1] = json.dumps(inner, separators=(",", ":"))
    result_data["f.req"] = json.dumps(outer, separators=(",", ":"))
    return result_headers, result_data

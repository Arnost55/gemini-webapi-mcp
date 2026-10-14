import json

import pytest

from gemini_webapi_mcp.protocol_capture import MODEL_HEADER, sanitize_capture


def _f_req(level, mode):
    inner = [None] * 97
    inner[0] = ["SECRET PROMPT MUST NOT LEAK"]
    inner[2] = ["secret-cid", "secret-rid"]
    inner[17] = [[level]]
    inner[59] = "secret-request-uuid"
    inner[79] = mode
    inner[80] = level
    return json.dumps([None, json.dumps(inner)])


def test_capture_is_minimal_versioned_and_deterministic():
    raw = {
        "model_id": "verified-model",
        "label": "Verified Model",
        "captures": [
            {"thinking_level": "low", "model_header": "[1,null,\"route\"]", "f_req": _f_req(1, 3)},
            {"thinking_level": "high", "model_header": "[1,null,\"route\"]", "f_req": _f_req(3, 3)},
        ],
        "cookie": "must-not-survive",
    }
    result = sanitize_capture(raw)
    encoded = json.dumps(result)
    model = result["models"][0]
    assert model["thinking_levels"] == ["low", "high"]
    assert model["thinking_request_fields"]["low"]["17"] == [[1]]
    assert model["thinking_request_fields"]["high"]["80"] == 3
    assert model["thinking_request_headers"]["low"][MODEL_HEADER] == '[1,null,"route"]'
    assert len(result["evidence_sha256"]) == 64
    assert "SECRET" not in encoded
    assert "secret-cid" not in encoded
    assert "secret-request" not in encoded
    assert "cookie" not in encoded


def test_capture_rejects_unknown_thinking_level():
    with pytest.raises(ValueError, match="thinking_level"):
        sanitize_capture({
            "model_id": "x", "label": "X",
            "captures": [{"thinking_level": "ultra", "model_header": "[]", "f_req": _f_req(1, 1)}],
        })


def test_capture_rejects_duplicate_thinking_level():
    capture = {"thinking_level": "low", "model_header": "[]", "f_req": _f_req(1, 1)}
    with pytest.raises(ValueError, match="duplicate capture"):
        sanitize_capture({
            "model_id": "x", "label": "X", "captures": [capture, dict(capture)],
        })

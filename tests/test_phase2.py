import base64
from pathlib import Path

import pytest

from gemini_webapi_mcp.models import capabilities_json
from gemini_webapi_mcp.server import (
    _decode_upload_bytes,
    _handle_error,
    _validate_upload_path,
)


def test_fallback_models_are_explicitly_low_confidence(monkeypatch):
    monkeypatch.delenv("GEMINI_MODELS", raising=False)
    models = capabilities_json()
    assert models
    assert all(item["source"] == "fallback" for item in models)
    assert all(item["confidence"] == "low" for item in models)


def test_observed_models_are_not_reported_as_fallback():
    class Client:
        available_models = ["model-seen-from-client"]

    item = capabilities_json(Client())[0]
    assert item["id"] == "model-seen-from-client"
    assert item["source"] == "observed"
    assert item["confidence"] == "observed"


def test_zombie_stream_is_structured_without_model_substitution():
    from gemini_webapi import APIError

    result = _handle_error(APIError("Response stalled (zombie stream)."))
    assert result.startswith("Error: upstream_stall")
    assert "unsupported_model" not in result


def test_upload_path_validation_rejects_unknown_type(tmp_path):
    path = tmp_path / "secret.bin"
    path.write_bytes(b"not an accepted upload")
    with pytest.raises(ValueError, match="unsupported file type"):
        _validate_upload_path(str(path))


def test_upload_bytes_are_bounded_and_basename_only(monkeypatch):
    monkeypatch.setenv("GEMINI_MAX_UPLOAD_BYTES", "32")
    name, data = _decode_upload_bytes("../../note.txt", base64.b64encode(b"hello").decode())
    assert name == "note.txt"
    assert data == b"hello"


def test_upload_bytes_reject_invalid_base64():
    with pytest.raises(ValueError, match="valid base64"):
        _decode_upload_bytes("note.txt", "not-base64!")


def test_upload_bytes_reject_oversized_payload(monkeypatch):
    monkeypatch.setenv("GEMINI_MAX_UPLOAD_BYTES", "3")
    encoded = base64.b64encode(b"four").decode()
    with pytest.raises(ValueError, match="exceeds"):
        _decode_upload_bytes("note.txt", encoded)

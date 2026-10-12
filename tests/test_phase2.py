import base64
import asyncio
import json
import io
import time
from pathlib import Path

import pytest

from gemini_webapi_mcp.models import capabilities_json
from gemini_webapi.utils import parse_response_by_frame
from gemini_webapi_mcp.server import mcp
from gemini_webapi_mcp.server import (
    _decode_upload_bytes,
    _account_name_for_login,
    _env_flag,
    _handle_error,
    _native_image_content,
    gemini_server_info,
    _validate_upload_path,
)


_ONE_PIXEL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0dIDAT"
    b"\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_upstream_auto_refresh_is_explicitly_opt_in(monkeypatch):
    monkeypatch.delenv("GEMINI_UPSTREAM_AUTO_REFRESH", raising=False)
    assert _env_flag("GEMINI_UPSTREAM_AUTO_REFRESH", False) is False
    monkeypatch.setenv("GEMINI_UPSTREAM_AUTO_REFRESH", "yes")
    assert _env_flag("GEMINI_UPSTREAM_AUTO_REFRESH", False) is True
    monkeypatch.setenv("GEMINI_UPSTREAM_AUTO_REFRESH", "unexpected")
    assert _env_flag("GEMINI_UPSTREAM_AUTO_REFRESH", False) is False


def test_image_tool_uses_unstructured_native_content_output():
    from mcp.server.fastmcp.utilities.types import Image
    from mcp.types import ImageContent, TextContent

    tool = mcp._tool_manager._tools["gemini_generate_image"]
    assert tool.output_schema is None
    converted = tool.fn_metadata.convert_result(["{\"images\": []}", Image(data=_ONE_PIXEL_PNG, format="png")])
    assert isinstance(converted[0], TextContent)
    assert isinstance(converted[1], ImageContent)
    assert converted[1].mimeType == "image/png"
    assert converted[1].data


def test_conversation_tools_are_registered():
    names = set(mcp._tool_manager._tools)
    assert {
        "gemini_list_chats",
        "gemini_get_chat",
        "gemini_get_chat_history",
        "gemini_delete_chat",
        "gemini_switch_model",
        "gemini_account_diagnostics",
        "gemini_deactivate_account",
    } <= names


def test_reauthentication_reuses_selected_account(monkeypatch, tmp_path):
    from gemini_webapi_mcp.registry import DynamicAccountRegistry, generate_key

    path = tmp_path / "registry.json.enc"
    key = generate_key()
    monkeypatch.setenv("GEMINI_REGISTRY_PATH", str(path))
    monkeypatch.setenv("GEMINI_REGISTRY_KEY", key)
    registry = DynamicAccountRegistry(path, key)
    registry.register(subject="client:test", account="existing")
    registry.select("client:test", "existing")

    assert _account_name_for_login("client:test", "random-code-account") == "existing"


def test_new_identity_gets_stable_account_name(monkeypatch, tmp_path):
    from gemini_webapi_mcp.registry import DynamicAccountRegistry, generate_key

    path = tmp_path / "registry.json.enc"
    key = generate_key()
    monkeypatch.setenv("GEMINI_REGISTRY_PATH", str(path))
    monkeypatch.setenv("GEMINI_REGISTRY_KEY", key)
    DynamicAccountRegistry(path, key)

    first = _account_name_for_login("client:new", "random-one")
    second = _account_name_for_login("client:new", "random-two")
    assert first == second
    assert first.startswith("gemini-")


def test_streamable_http_tools_list_exposes_hotfix_contract():
    """Exercise the actual Streamable HTTP initialize/tools/list exchange."""
    import httpx

    async def exchange():
        app = mcp.streamable_http_app()
        async with mcp.session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
            ) as client:
                headers = {
                    "accept": "application/json, text/event-stream",
                    "content-type": "application/json",
                }
                initialize = await client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "phase2-test", "version": "1"},
                        },
                    },
                )
                assert initialize.status_code == 200
                headers["mcp-session-id"] = initialize.headers["mcp-session-id"]
                await client.post(
                    "/mcp",
                    headers=headers,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                )
                listed = await client.post(
                    "/mcp",
                    headers=headers,
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                )
                assert listed.status_code == 200
                event = next(line[6:] for line in listed.text.splitlines() if line.startswith("data: "))
                tools = json.loads(event)["result"]["tools"]
                by_name = {item["name"]: item for item in tools}
                assert {
                    "gemini_list_chats",
                    "gemini_get_chat",
                    "gemini_get_chat_history",
                    "gemini_delete_chat",
                    "gemini_switch_model",
                    "gemini_server_info",
                    "gemini_account_diagnostics",
                    "gemini_deactivate_account",
                } <= by_name.keys()
                assert "outputSchema" not in by_name["gemini_generate_image"]

    asyncio.run(exchange())


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


def test_stream_failures_are_classified_without_retry_claims():
    from gemini_webapi import APIError

    assert _handle_error(APIError("Stream interrupted or truncated.")).startswith("Error: stream_interrupted")
    assert _handle_error(APIError("Connection reset by peer")).startswith("Error: upstream_connection_closed")


def test_pro_entitlement_error_is_not_reported_as_success():
    from gemini_webapi import APIError

    result = _handle_error(APIError("gemini-3.0-pro is not available for this account"))
    assert result.startswith("Error: model_entitlement")


def test_image_mime_is_derived_from_actual_bytes(tmp_path):
    from PIL import Image as PillowImage
    from mcp.server.fastmcp.utilities.func_metadata import _convert_to_content

    path = tmp_path / "misleading.png"
    PillowImage.new("RGB", (2, 2), (20, 30, 40)).save(path, format="JPEG")
    block = _native_image_content(str(path))
    content = _convert_to_content(block)[0]
    raw = base64.b64decode(content.data)
    assert content.mimeType == "image/jpeg"
    assert raw[:2] == b"\xff\xd8"
    with PillowImage.open(io.BytesIO(raw)) as decoded:
        assert decoded.format == "JPEG"


def test_server_info_is_non_sensitive_and_reports_registered_tools():
    info = asyncio.run(gemini_server_info())
    assert info["mcp_protocol_version"] == "2025-03-26"
    assert "gemini_server_info" in info["registered_tools"]
    assert "__Secure-1PSID" not in repr(info)
    assert "cookies" not in repr(info).lower()


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


def _frame(payload: str) -> str:
    units = len((payload + "\n").encode("utf-16-le")) // 2
    return f"{units}\n{payload}\n"


def test_upstream_frame_parser_preserves_split_utf16_frames():
    payload = '["partial 😀 frame"]'
    frame = _frame(payload)
    for split in range(1, len(frame)):
        parts = []
        remainder = ""
        for chunk in (frame[:split], frame[split:]):
            parsed, remainder = parse_response_by_frame(remainder + chunk)
            parts.extend(parsed)
        assert parts == ["partial 😀 frame"]
        assert remainder == ""


def test_upstream_frame_parser_retains_incomplete_frame_without_growth():
    payload = '["slow"]'
    frame = _frame(payload)
    parts, remainder = parse_response_by_frame(frame[:-2])
    assert parts == []
    assert remainder == frame[:-2]
    assert len(remainder) <= len(frame)


def test_account_request_slots_bound_concurrency(monkeypatch, tmp_path):
    from gemini_webapi_mcp.server import _AccountClientManager

    monkeypatch.setenv("GEMINI_CONVERSATIONS_PATH", str(tmp_path / "conversations.sqlite3"))
    manager = _AccountClientManager()
    manager._account = lambda ctx: ("subject", {"account": "account", "requests_per_minute": 30})

    async def run():
        active = 0
        peak = 0
        lock = asyncio.Lock()

        async def task():
            nonlocal active, peak
            async with manager.request_slot(object()):
                async with lock:
                    active += 1
                    peak = max(peak, active)
                await asyncio.sleep(0.01)
                async with lock:
                    active -= 1
        await asyncio.gather(*(task() for _ in range(8)))
        return peak

    assert asyncio.run(run()) == 2

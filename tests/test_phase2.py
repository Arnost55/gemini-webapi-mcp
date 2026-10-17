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
    _consume_browser_handoff,
    _env_flag,
    _handle_error,
    _image_mode,
    _native_image_content,
    _response_text,
    gemini_server_info,
    _validate_upload_path,
)


def test_upstream_auto_refresh_is_explicitly_opt_in(monkeypatch):
    monkeypatch.delenv("GEMINI_UPSTREAM_AUTO_REFRESH", raising=False)
    assert _env_flag("GEMINI_UPSTREAM_AUTO_REFRESH", False) is False
    monkeypatch.setenv("GEMINI_UPSTREAM_AUTO_REFRESH", "yes")
    assert _env_flag("GEMINI_UPSTREAM_AUTO_REFRESH", False) is True
    monkeypatch.setenv("GEMINI_UPSTREAM_AUTO_REFRESH", "unexpected")
    assert _env_flag("GEMINI_UPSTREAM_AUTO_REFRESH", False) is False


def test_image_tool_uses_unstructured_native_content_output():
    from mcp.types import CallToolResult, ImageContent, TextContent

    tool = mcp._tool_manager._tools["gemini_generate_image"]
    assert tool.output_schema is None
    native = CallToolResult(
        content=[
            TextContent(type="text", text="Generated image"),
            ImageContent(type="image", data="aW1hZ2U=", mimeType="image/png"),
        ],
        structuredContent={"artifacts": [{"artifact_id": "private-id", "mime_type": "image/png"}]},
    )
    converted = tool.fn_metadata.convert_result(native)
    assert converted is native
    assert isinstance(converted.content[0], TextContent)
    assert isinstance(converted.content[1], ImageContent)
    assert converted.content[1].mimeType == "image/png"
    assert converted.content[1].data not in converted.content[0].text
    assert "storage_path" not in converted.structuredContent["artifacts"][0]


def test_conversation_tools_are_registered():
    names = set(mcp._tool_manager._tools)
    assert {
        "gemini_list_chats",
        "gemini_get_chat",
        "gemini_get_chat_history",
        "gemini_delete_chat",
        "gemini_switch_model",
        "gemini_account_diagnostics",
        "gemini_get_account_info",
        "gemini_deactivate_account",
        "gemini_delete_account",
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


def test_handoff_promotes_into_selected_account_profile(monkeypatch, tmp_path):
    """A completed login must not be promoted into a random handoff name."""
    from gemini_webapi_mcp.registry import DynamicAccountRegistry, generate_key
    import gemini_webapi_mcp.server as server

    path = tmp_path / "registry.json.enc"
    profiles = tmp_path / "accounts"
    key = generate_key()
    monkeypatch.setenv("GEMINI_REGISTRY_PATH", str(path))
    monkeypatch.setenv("GEMINI_REGISTRY_KEY", key)
    monkeypatch.setenv("GEMINI_PROFILE_ROOT", str(profiles))
    registry = DynamicAccountRegistry(path, key)
    registry.register(subject="client:test", account="selected")
    registry.select("client:test", "selected")

    class Broker:
        destination = None

        def consume(self, token, promote_to):
            self.destination = promote_to

    broker = Broker()
    monkeypatch.setattr(server, "_handoff_broker", broker)

    _consume_browser_handoff("opaque-token", "client:test", "random-handoff-name")

    assert broker.destination == profiles.resolve() / "selected"


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


def test_streamable_http_tools_list_exposes_hotfix_contract(monkeypatch, tmp_path):
    """Exercise the actual Streamable HTTP initialize/tools/list exchange."""
    import httpx
    monkeypatch.setenv("GEMINI_CONVERSATIONS_PATH", str(tmp_path / "conversations.sqlite3"))

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
                    "gemini_get_account_info",
                    "gemini_deactivate_account",
                    "gemini_delete_account",
                } <= by_name.keys()
                assert "outputSchema" not in by_name["gemini_generate_image"]
                image_meta = by_name["gemini_generate_image"]["_meta"]
                assert image_meta["ui"]["resourceUri"].startswith("ui://")
                assert image_meta["openai/outputTemplate"] == image_meta["ui"]["resourceUri"]
                for name in ("gemini_start_chat", "gemini_chat", "gemini_generate_image", "gemini_upload_file", "gemini_analyze_url"):
                    assert "thinking_level" in by_name[name]["inputSchema"]["properties"]

    asyncio.run(exchange())


@pytest.mark.parametrize("image_count", [1, 2])
def test_streamable_http_tools_call_returns_native_image_blocks(tmp_path, image_count):
    """Prove FastMCP serializes native image blocks into the HTTP MCP result."""
    import httpx
    from mcp.server.fastmcp import FastMCP
    from mcp.types import CallToolResult, ImageContent, TextContent
    from PIL import Image
    import uvicorn

    images = []
    for index in range(image_count):
        image_path = tmp_path / f"generated-{index}.png"
        Image.new("RGB", (13 + index, 7), (index, 30, 200)).save(image_path, format="PNG")
        images.append(ImageContent(
            type="image",
            mimeType="image/png",
            data=base64.b64encode(image_path.read_bytes()).decode("ascii"),
        ))
    native_result = CallToolResult(
        content=[TextContent(type="text", text="Generated image."), *images],
        structuredContent={
            "artifacts": [{"artifact_id": f"artifact-{i}", "mime_type": "image/png"} for i in range(image_count)],
            "conversation_id": ["cid", "rid", "rcid"],
        },
    )

    wire_server = FastMCP("gemini-image-wire-test")

    @wire_server.tool(name="gemini_generate_image", structured_output=False)
    async def fake_generate_image(prompt: str):
        return native_result

    async def exchange():
        app = wire_server.streamable_http_app()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="critical"))
        server_task = asyncio.create_task(server.serve())
        try:
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.02)
            assert server.started
            port = server.servers[0].sockets[0].getsockname()[1]
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
                headers = {
                    "accept": "application/json, text/event-stream",
                    "content-type": "application/json",
                }
                initialized = await client.post("/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26", "capabilities": {},
                        "clientInfo": {"name": "image-wire-test", "version": "1"},
                    },
                })
                assert initialized.status_code == 200
                headers["mcp-session-id"] = initialized.headers["mcp-session-id"]
                await client.post("/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
                })
                response = await client.post("/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "gemini_generate_image", "arguments": {"prompt": "test"}},
                })
                assert response.status_code == 200
                event = next(line[6:] for line in response.text.splitlines() if line.startswith("data: "))
                result = json.loads(event)["result"]
                assert isinstance(result["content"], list)
                blocks = [item for item in result["content"] if item["type"] == "image"]
                assert len(blocks) == image_count
                assert [item["mimeType"] for item in blocks] == ["image/png"] * image_count
                for block, original in zip(blocks, images):
                    decoded = base64.b64decode(block["data"], validate=True)
                    assert decoded == base64.b64decode(original.data, validate=True)
                    with Image.open(io.BytesIO(decoded)) as image:
                        image.verify()
                assert result["content"][0] == {"type": "text", "text": "Generated image."}
                assert result["structuredContent"]["artifacts"] == native_result.structuredContent["artifacts"]
                assert result["structuredContent"]["conversation_id"] == ["cid", "rid", "rcid"]
                assert "content_items" not in result
                assert sum(item["type"] == "image" for item in result["content"]) == image_count
        finally:
            server.should_exit = True
            await server_task

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

    assert _handle_error(APIError("Stream interrupted or truncated.")).startswith("Error: response_parse_error")
    assert _handle_error(APIError("Connection reset by peer")).startswith("Error: upstream_rejected")


def test_pro_entitlement_error_is_not_reported_as_success():
    from gemini_webapi import APIError

    result = _handle_error(APIError("gemini-3.0-pro is not available for this account"))
    assert result.startswith("Error: model_unavailable")


def test_generic_upstream_candidate_is_not_returned_as_success():
    class Response:
        candidates = [object()]
        text = "Sorry, something went wrong. Please try your request again."

    with pytest.raises(RuntimeError, match="rejected"):
        _response_text(Response(), operation="chat")


def test_live_flash_generic_failure_phrase_is_not_returned_as_success():
    class Response:
        candidates = [object()]
        text = "I seem to be encountering an error. Can I try something else for you?"

    with pytest.raises(RuntimeError, match="rejected"):
        _response_text(Response(), operation="chat")


def test_upload_attribute_error_identifies_client_incompatibility():
    result = _handle_error(AttributeError("'Response' object has no attribute 'headers'"), operation="upload")
    assert result.startswith("Error: upload_client_incompatible")
    assert "headers" in result


def test_image_account_restriction_is_not_reported_as_implementation_failure():
    from gemini_webapi.exceptions import ImageGenerationBlocked

    result = _handle_error(ImageGenerationBlocked("image creation isn't available"))
    assert result.startswith("Error: image_generation_unavailable")


def test_missing_candidate_is_response_parse_error():
    class Response:
        candidates = []
        text = "A response that must not be accepted without a candidate"

    with pytest.raises(RuntimeError, match="no candidates"):
        _response_text(Response(), operation="upload")


def test_image_mode_is_task_local_not_process_global():
    async def verify():
        started = asyncio.Event()
        release = asyncio.Event()

        async def image_task():
            token = _image_mode.set(True)
            try:
                started.set()
                await release.wait()
                return _image_mode.get()
            finally:
                _image_mode.reset(token)

        async def flash_task():
            await started.wait()
            observed = _image_mode.get()
            release.set()
            return observed

        image, flash = await asyncio.gather(image_task(), flash_task())
        return image, flash

    assert asyncio.run(verify()) == (True, False)


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
    assert info["registered_tool_count"] == len(info["registered_tools"])
    assert info["model_discovery"]["thinking_level_protocol_verified"] is False
    assert "verified_thinking_controls" not in info["enabled_capabilities"]
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


def test_account_request_slots_serialize_mutable_upstream_client_by_default(monkeypatch, tmp_path):
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

    assert asyncio.run(run()) == 1

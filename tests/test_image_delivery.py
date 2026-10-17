import asyncio
import base64
import io
import json
import logging

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, ResourceLink, TextContent
from PIL import Image

from gemini_webapi_mcp.image_viewer import IMAGE_VIEWER_HTML, IMAGE_VIEWER_URI
from gemini_webapi_mcp.observability import (
    ImageDeliveryTransportDiagnostics,
    image_delivery_event,
)
from gemini_webapi_mcp.server import mcp


def _png_bytes(size: int = 0) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), (25, 80, 160)).save(output, format="PNG")
    payload = output.getvalue()
    if size:
        assert size >= len(payload)
        payload += b"\0" * (size - len(payload))
    return payload


def _jpeg_bytes() -> bytes:
    output = io.BytesIO()
    Image.effect_noise((1024, 1024), 90).convert("RGB").save(
        output, format="JPEG", quality=90
    )
    return output.getvalue()


def _image(payload: bytes, mime_type: str) -> ImageContent:
    return ImageContent(
        type="image",
        mimeType=mime_type,
        data=base64.b64encode(payload).decode("ascii"),
    )


def _matrix_result(case: str, size: int = 0) -> CallToolResult:
    tiny = _image(_png_bytes(), "image/png")
    if case == "A":
        return CallToolResult(content=[TextContent(type="text", text="ok")])
    if case == "B":
        return CallToolResult(content=[], structuredContent={"status": "ok", "count": 1})
    if case == "C":
        return CallToolResult(content=[tiny])
    if case == "D":
        return CallToolResult(content=[TextContent(type="text", text="done"), tiny])
    if case == "E":
        return CallToolResult(
            content=[TextContent(type="text", text="full"), _image(_jpeg_bytes(), "image/jpeg")]
        )
    if case == "F":
        return CallToolResult(content=[tiny, tiny])
    if case == "G":
        return CallToolResult(content=[tiny], structuredContent={"status": "ok"})
    if case == "H":
        return CallToolResult(
            content=[
                TextContent(type="text", text="private resource"),
                ResourceLink(
                    type="resource_link",
                    name="diagnostic-image",
                    uri="gemini-private://diagnostic/image",
                    mimeType="image/png",
                ),
            ]
        )
    if case == "I":
        return CallToolResult(
            content=[TextContent(type="text", text="diagnostic error")], isError=True
        )
    if case == "SIZE":
        return CallToolResult(
            content=[TextContent(type="text", text="sized"), _image(_png_bytes(size), "image/png")],
            structuredContent={"source_bytes": size},
        )
    raise ValueError("unknown diagnostic case")


def _diagnostic_server() -> FastMCP:
    server = FastMCP(
        "image-response-diagnostic",
        json_response=True,
        stateless_http=True,
    )

    @server.tool(name="diagnostic_image_result", structured_output=False)
    async def diagnostic_image_result(case: str, size: int = 0):
        return _matrix_result(case, size)

    return server


async def _wire_calls(calls: list[tuple[str, int]]) -> list[tuple[httpx.Response, dict]]:
    server = _diagnostic_server()
    app = server.streamable_http_app()
    results = []
    async with (
        server.session_manager.run(),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
        ) as client,
    ):
        for request_id, (case, size) in enumerate(calls, start=1):
            response = await client.post(
                "/mcp",
                headers={
                    "accept": "application/json, text/event-stream",
                    "content-type": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {
                        "name": "diagnostic_image_result",
                        "arguments": {"case": case, "size": size},
                    },
                },
            )
            results.append((response, response.json()))
    return results


def test_controlled_response_format_matrix_serializes_complete_results():
    exchanges = asyncio.run(_wire_calls([(case, 0) for case in "ABCDEFGHI"]))
    for case, (response, envelope) in zip("ABCDEFGHI", exchanges):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert int(response.headers["content-length"]) == len(response.content)
        assert envelope["jsonrpc"] == "2.0"
        assert envelope["id"] is not None
        assert isinstance(envelope["result"]["content"], list)
        if case == "I":
            assert envelope["result"]["isError"] is True

    results = {case: item[1]["result"] for case, item in zip("ABCDEFGHI", exchanges)}
    assert results["B"]["structuredContent"] == {"status": "ok", "count": 1}
    assert [item["type"] for item in results["D"]["content"]] == ["text", "image"]
    assert len([item for item in results["F"]["content"] if item["type"] == "image"]) == 2
    assert results["G"]["structuredContent"] == {"status": "ok"}
    assert results["H"]["content"][1]["type"] == "resource_link"
    jpeg = base64.b64decode(results["E"]["content"][1]["data"], validate=True)
    with Image.open(io.BytesIO(jpeg)) as image:
        assert image.format == "JPEG"
        image.verify()


@pytest.mark.parametrize(
    "source_size",
    [5 * 1024, 25 * 1024, 100 * 1024, 250 * 1024, 500 * 1024, 1024 * 1024, 2 * 1024 * 1024],
)
def test_payload_size_boundaries_complete_without_truncation(source_size):
    response, envelope = asyncio.run(_wire_calls([("SIZE", source_size)]))[0]
    result = envelope["result"]
    encoded = result["content"][1]["data"]
    decoded = base64.b64decode(encoded, validate=True)
    assert response.status_code == 200
    assert int(response.headers["content-length"]) == len(response.content)
    assert result["structuredContent"]["source_bytes"] == source_size
    assert len(decoded) == source_size
    assert len(response.content) > len(encoded) >= source_size
    with Image.open(io.BytesIO(decoded)) as image:
        assert image.format == "PNG"
        image.verify()


def test_official_mcp_client_parses_complete_native_image_result():
    import uvicorn

    async def exchange():
        server = _diagnostic_server()
        app = server.streamable_http_app()
        uvicorn_server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=0, log_level="critical")
        )
        task = asyncio.create_task(uvicorn_server.serve())
        try:
            for _ in range(100):
                if uvicorn_server.started:
                    break
                await asyncio.sleep(0.02)
            assert uvicorn_server.started
            port = uvicorn_server.servers[0].sockets[0].getsockname()[1]
            async with (
                streamable_http_client(f"http://127.0.0.1:{port}/mcp") as streams,
                ClientSession(streams[0], streams[1]) as session,
            ):
                await session.initialize()
                result = await session.call_tool(
                    "diagnostic_image_result", {"case": "D", "size": 0}
                )
                assert [block.type for block in result.content] == ["text", "image"]
                assert isinstance(result.content[1], ImageContent)
                assert base64.b64decode(result.content[1].data, validate=True) == _png_bytes()
        finally:
            uvicorn_server.should_exit = True
            await task

    asyncio.run(exchange())


def test_image_tool_advertises_private_mcp_app_and_resource_is_static():
    async def inspect_registration():
        tools = await mcp.list_tools()
        resources = await mcp.list_resources()
        content = await mcp.read_resource(IMAGE_VIEWER_URI)
        return tools, resources, content

    tools, resources, content = asyncio.run(inspect_registration())
    tool = next(item for item in tools if item.name == "gemini_generate_image")
    assert tool.meta["ui"]["resourceUri"] == IMAGE_VIEWER_URI
    assert tool.meta["openai/outputTemplate"] == IMAGE_VIEWER_URI
    resource = next(item for item in resources if str(item.uri) == IMAGE_VIEWER_URI)
    assert resource.mimeType == "text/html;profile=mcp-app"
    assert IMAGE_VIEWER_HTML in content[0].content
    assert "http://" not in IMAGE_VIEWER_HTML
    assert "https://" not in IMAGE_VIEWER_HTML


def test_transport_diagnostics_record_completion_without_payload(monkeypatch, caplog):
    correlation_id = "a" * 32

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send(
            {
                "type": "http.response.body",
                "body": json.dumps(
                    {"correlation_id": correlation_id, "data": "PRIVATE-BASE64"}
                ).encode(),
            }
        )

    async def run():
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        wrapped = ImageDeliveryTransportDiagnostics(app)
        await wrapped({"type": "http", "method": "POST"}, receive, send)
        return sent

    monkeypatch.setenv("GEMINI_IMAGE_DELIVERY_DIAGNOSTICS", "1")
    with caplog.at_level(logging.INFO, logger="gemini_mcp.image_delivery"):
        asyncio.run(run())
    assert correlation_id in caplog.text
    assert "http_response_completed" in caplog.text
    assert "PRIVATE-BASE64" not in caplog.text


def test_transport_diagnostics_classify_disconnect_and_timeout(monkeypatch, caplog):
    correlation_id = "b" * 32

    async def app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send(
            {
                "type": "http.response.body",
                "body": f'{{"correlation_id":"{correlation_id}"}}'.encode(),
            }
        )
        raise TimeoutError

    async def run():
        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            return None

        with pytest.raises(TimeoutError):
            await ImageDeliveryTransportDiagnostics(app)(
                {"type": "http", "method": "POST"}, receive, send
            )

    monkeypatch.setenv("GEMINI_IMAGE_DELIVERY_DIAGNOSTICS", "true")
    with caplog.at_level(logging.INFO, logger="gemini_mcp.image_delivery"):
        asyncio.run(run())
    assert '"disconnect_observed":true' in caplog.text
    assert '"exception_category":"TimeoutError"' in caplog.text
    assert '"transport_completed":false' in caplog.text


def test_diagnostic_allowlist_drops_prompts_credentials_and_media(monkeypatch, caplog):
    monkeypatch.setenv("GEMINI_IMAGE_DELIVERY_DIAGNOSTICS", "1")
    with caplog.at_level(logging.INFO, logger="gemini_mcp.image_delivery"):
        image_delivery_event(
            "test",
            correlation_id="c" * 32,
            status="ok",
            prompt="PRIVATE PROMPT",
            cookie="SECRET COOKIE",
            image_data="BASE64 PAYLOAD",
            path="C:/private/image.png",
        )
    assert "PRIVATE PROMPT" not in caplog.text
    assert "SECRET COOKIE" not in caplog.text
    assert "BASE64 PAYLOAD" not in caplog.text
    assert "C:/private" not in caplog.text

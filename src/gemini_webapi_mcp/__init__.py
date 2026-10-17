"""MCP Server for Google Gemini via browser cookies."""

import argparse
import asyncio
import os
from urllib.parse import parse_qs

from gemini_webapi_mcp.observability import ImageDeliveryTransportDiagnostics
from gemini_webapi_mcp.server import mcp


class _ChatGPTContentTypeCompat:
    """Accept JSON MCP requests mislabeled as application/octet-stream."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") == "http"
            and scope.get("method") == "POST"
            and scope.get("path") == mcp.settings.streamable_http_path
        ):
            headers = list(scope.get("headers", []))
            header_names = {name.lower() for name, _ in headers}
            if b"accept" in header_names:
                for index, (name, value) in enumerate(headers):
                    if name.lower() == b"accept":
                        accepted = value.decode("latin-1")
                        if "application/json" not in accepted:
                            accepted += ", application/json"
                        if "text/event-stream" not in accepted:
                            accepted += ", text/event-stream"
                        headers[index] = (name, accepted.encode("latin-1"))
                        break
            else:
                headers.append((b"accept", b"application/json, text/event-stream"))
            for index, (name, value) in enumerate(headers):
                if name.lower() == b"content-type" and value.lower().split(b";", 1)[0].strip() == b"application/octet-stream":
                    headers[index] = (name, b"application/json")
                    scope = dict(scope)
                    scope["headers"] = headers
                    break
        await self.app(scope, receive, send)


class _WebsockifyCompat:
    """Bridge the public WebSocket path to the token-checked websockify port.

    Some reverse proxies can forward the MCP HTTP origin only to port 8000.
    Keeping this bridge in the application makes that deployment work without
    publishing the raw websockify/VNC listener.  websockify remains the
    authority for the opaque token and target mapping.
    """

    def __init__(self, app, *, target_host: str = "127.0.0.1", target_port: int = 6080):
        self.app = app
        self.target_host = target_host
        self.target_port = target_port

    @staticmethod
    def _headers(scope) -> dict[bytes, bytes]:
        return {name.lower(): value for name, value in scope.get("headers", [])}

    @staticmethod
    def _allowed_origins() -> set[str]:
        configured = os.environ.get("GEMINI_VIEWER_ALLOWED_ORIGINS", "")
        origins = {item.strip().rstrip("/") for item in configured.split(",") if item.strip()}
        public_url = os.environ.get("GEMINI_PUBLIC_URL", "").strip().rstrip("/")
        if public_url:
            origins.add(public_url)
        return origins

    async def _bridge(self, scope, receive, send) -> None:
        from websockets import connect
        from websockets.exceptions import WebSocketException

        query = scope.get("query_string", b"").decode("ascii", "ignore")
        if not parse_qs(query).get("token"):
            await send({"type": "websocket.close", "code": 1008, "reason": "viewer token required"})
            return
        origin = self._headers(scope).get(b"origin", b"").decode("latin-1").rstrip("/")
        allowed_origins = self._allowed_origins()
        if allowed_origins and origin not in allowed_origins:
            await send({"type": "websocket.close", "code": 1008, "reason": "origin not allowed"})
            return

        await send({"type": "websocket.accept"})
        upstream_url = f"ws://{self.target_host}:{self.target_port}/websockify"
        if query:
            upstream_url += f"?{query}"
        try:
            async with connect(upstream_url) as upstream:
                async def client_to_upstream() -> None:
                    while True:
                        message = await receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        if message["type"] == "websocket.receive":
                            payload = message.get("bytes")
                            if payload is None:
                                payload = message.get("text", "").encode("utf-8")
                            await upstream.send(payload)

                async def upstream_to_client() -> None:
                    async for payload in upstream:
                        if isinstance(payload, str):
                            payload = payload.encode("utf-8")
                        await send({"type": "websocket.send", "bytes": payload})

                tasks = {
                    asyncio.create_task(client_to_upstream()),
                    asyncio.create_task(upstream_to_client()),
                }
                _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        except (OSError, WebSocketException):
            await send({"type": "websocket.close", "code": 1011, "reason": "viewer transport unavailable"})

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "websocket" and scope.get("path") == "/websockify":
            await self._bridge(scope, receive, send)
            return
        await self.app(scope, receive, send)


def main():
    """Run stdio by default; expose Streamable HTTP only when requested."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    # FastMCP.run() accepts the transport only in the installed MCP SDK.
    # Host and port are settings on the FastMCP instance, so configure them
    # before starting the server instead of passing unsupported run kwargs.
    if args.host is not None:
        mcp.settings.host = args.host
    if args.port is not None:
        mcp.settings.port = args.port
    if args.transport == "streamable-http":
        import uvicorn
        app = _WebsockifyCompat(
            ImageDeliveryTransportDiagnostics(
                _ChatGPTContentTypeCompat(mcp.streamable_http_app())
            )
        )
        config = uvicorn.Config(app, host=mcp.settings.host, port=mcp.settings.port, log_level=mcp.settings.log_level.lower())
        uvicorn.Server(config).run()
    else:
        mcp.run(transport=args.transport)

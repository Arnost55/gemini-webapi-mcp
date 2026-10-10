"""MCP Server for Google Gemini via browser cookies."""

import argparse
from urllib.parse import urlparse

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
        app = _ChatGPTContentTypeCompat(mcp.streamable_http_app())
        config = uvicorn.Config(app, host=mcp.settings.host, port=mcp.settings.port, log_level=mcp.settings.log_level.lower())
        uvicorn.Server(config).run()
    else:
        mcp.run(transport=args.transport)

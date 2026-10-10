"""MCP Server for Google Gemini via browser cookies."""

import argparse

from gemini_webapi_mcp.server import mcp


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
    mcp.run(transport=args.transport)

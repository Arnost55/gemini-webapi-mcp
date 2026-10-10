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
    kwargs = {key: value for key, value in {"host": args.host, "port": args.port}.items() if value is not None}
    mcp.run(transport=args.transport, **kwargs)

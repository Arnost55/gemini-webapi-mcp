import socket
import sys

from gemini_webapi_mcp import main
from gemini_webapi_mcp.server import mcp


def test_http_cli_configures_settings_before_run(monkeypatch):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    captured = []
    monkeypatch.setattr(sys, "argv", ["gemini-webapi-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", str(port)])
    monkeypatch.setattr("uvicorn.Server.run", lambda server: captured.append(server.config))
    main()
    assert mcp.settings.host == "0.0.0.0"
    assert mcp.settings.port == port
    assert captured and captured[0].host == "0.0.0.0"
    assert captured[0].port == port

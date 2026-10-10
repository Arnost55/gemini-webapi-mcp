import sys

from gemini_webapi_mcp import main
from gemini_webapi_mcp.server import mcp


def test_http_cli_configures_settings_before_run(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["gemini-webapi-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8123"])
    monkeypatch.setattr(mcp, "run", lambda **kwargs: calls.append(kwargs))
    main()
    assert mcp.settings.host == "0.0.0.0"
    assert mcp.settings.port == 8123
    assert calls == [{"transport": "streamable-http"}]

import json
from pathlib import Path

from gemini_webapi_mcp.browser_handoff import BrowserHandoffBroker
from gemini_webapi_mcp.viewer import DEFAULT_RFB_URL, content_security_policy, viewer_page


class FakeProcess:
    pid = 33123

    def poll(self):
        return 0

    def terminate(self):
        pass


def test_viewer_is_a_custom_rfb_surface_without_google_iframes():
    page = viewer_page(
        sid="opaque-capability",
        ws_url="wss://gemini.example/websockify",
        expires_at=1234,
    )
    assert "RFB" in page
    assert "accounts.google.com" not in page
    assert "gemini.google.com" not in page
    assert "iframe" not in page
    assert "cursor:none" in page
    assert "showDotCursor:false" in page
    assert '"sid":"opaque-capability"' in page
    assert DEFAULT_RFB_URL == "https://cdn.jsdelivr.net/gh/novnc/noVNC@v1.5.0/core/rfb.js"
    assert DEFAULT_RFB_URL in page
    assert "waiting for Chromium" in page
    assert "browser_process_exited" in page


def test_viewer_csp_allows_only_configured_transport_and_ancestors():
    csp = content_security_policy(
        "https://cdn.example/rfb.js",
        "wss://gemini.example/websockify",
        ["'self'", "https://chatgpt.com"],
    )
    assert "connect-src 'self' wss://gemini.example" in csp
    assert "frame-ancestors 'self' https://chatgpt.com" in csp
    assert "accounts.google.com" not in csp


def test_handoff_status_and_owner_are_safe(tmp_path: Path):
    broker = BrowserHandoffBroker(tmp_path, runner=lambda *args, **kwargs: FakeProcess())
    session = broker.create(owner_subject="client:abc", account="gemini-abc")
    assert broker.owner_for(session.token) == "client:abc"
    assert broker.status(session.token)["ready"] is False
    manifest = json.loads((tmp_path / "manifests" / f"{session.token}.json").read_text())
    assert manifest["owner_subject"] == "client:abc"
    assert manifest["account"] == "gemini-abc"
    assert "profile" not in broker.status(session.token)
    broker.shutdown()

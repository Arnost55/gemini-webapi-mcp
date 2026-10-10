import base64
import hashlib
from pathlib import Path

import pytest

from gemini_webapi_mcp.oauth import LocalOAuth


def challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def test_dynamic_registration_and_pkce_are_one_time():
    oauth = LocalOAuth("https://gemini.example")
    client = oauth.register({"client_name": "ChatGPT Gemini", "redirect_uris": ["https://chat.openai.com/aip/callback"], "token_endpoint_auth_method": "none"})
    verifier = "verifier-value-that-is-long-enough-for-pkce-123456"
    code = oauth.begin(client["client_id"], client["redirect_uris"][0], challenge(verifier))
    oauth.complete(code, "gemini-account-a")
    token = oauth.exchange(code, client["client_id"], client["redirect_uris"][0], verifier)
    assert oauth.subject_for(token["access_token"]) == "gemini-account-a"
    with pytest.raises(ValueError):
        oauth.exchange(code, client["client_id"], client["redirect_uris"][0], verifier)


def test_registration_rejects_untrusted_redirect_uri():
    oauth = LocalOAuth("https://gemini.example")
    with pytest.raises(ValueError):
        oauth.register({"client_name": "bad", "redirect_uris": ["http://evil.example/callback"]})


def test_each_registration_gets_a_distinct_client():
    oauth = LocalOAuth("https://gemini.example")
    args = {"client_name": "ChatGPT Gemini", "redirect_uris": ["https://chat.openai.com/aip/callback"]}
    assert oauth.register(args)["client_id"] != oauth.register(args)["client_id"]


def test_registration_and_tokens_survive_process_restart(tmp_path: Path):
    state_path = tmp_path / "oauth-state.json"
    args = {"client_name": "ChatGPT Gemini", "redirect_uris": ["https://chat.openai.com/aip/callback"]}
    first = LocalOAuth("https://gemini.example", state_path=str(state_path))
    client = first.register(args)
    verifier = "verifier-value-that-is-long-enough-for-persistence-123456"
    code = first.begin(client["client_id"], client["redirect_uris"][0], challenge(verifier))
    first.complete(code, "gemini-account-a")
    token = first.exchange(code, client["client_id"], client["redirect_uris"][0], verifier)

    restarted = LocalOAuth("https://gemini.example", state_path=str(state_path))
    assert client["client_id"] in restarted._clients
    assert restarted.subject_for(token["access_token"]) == "gemini-account-a"


def test_browser_handoff_link_is_bound_to_expiring_authorization_code(monkeypatch):
    monkeypatch.setenv("GEMINI_NOVNC_PUBLIC_URL", "https://novnc.example/vnc.html")
    oauth = LocalOAuth("https://gemini.example")
    client = oauth.register({"client_name": "ChatGPT", "redirect_uris": ["https://chat.openai.com/cb"]})
    page = oauth.authorize_page(client["client_id"], client["redirect_uris"][0], "challenge", "state")
    assert "sid=" in page
    assert "https://novnc.example/vnc.html?sid=" in page

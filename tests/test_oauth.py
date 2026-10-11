import base64
import hashlib
import os
from pathlib import Path

import pytest

from gemini_webapi_mcp.oauth import LocalOAuth
from gemini_webapi_mcp.registry import generate_key


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


def test_registration_rejects_localhost_lookalike_redirect_uri():
    oauth = LocalOAuth("https://gemini.example")
    with pytest.raises(ValueError):
        oauth.register({"client_name": "bad", "redirect_uris": ["http://localhost.evil.example/callback"]})


def test_registration_accepts_only_public_pkce_clients():
    oauth = LocalOAuth("https://gemini.example")
    with pytest.raises(ValueError, match="public PKCE"):
        oauth.register({"client_name": "bad", "redirect_uris": ["https://chat.openai.com/callback"],
                        "token_endpoint_auth_method": "client_secret_post"})


def test_pkce_and_state_are_strictly_bound():
    oauth = LocalOAuth("https://gemini.example")
    client = oauth.register({"client_name": "ChatGPT", "redirect_uris": ["https://chat.openai.com/callback"]})
    verifier = "verifier-value-that-is-long-enough-for-state-binding-123456"
    with pytest.raises(ValueError, match="code_challenge"):
        oauth.begin(client["client_id"], client["redirect_uris"][0], "too-short")
    code = oauth.begin(client["client_id"], client["redirect_uris"][0], challenge(verifier))
    oauth.set_state(code, "expected-state")
    with pytest.raises(ValueError, match="state"):
        oauth.completion_redirect(code, "wrong-state")
    oauth.complete(code, "owned-subject")
    with pytest.raises(ValueError, match="verifier"):
        oauth.exchange(code, client["client_id"], client["redirect_uris"][0], "short")


def test_refresh_token_cannot_be_used_by_another_client():
    oauth = LocalOAuth("https://gemini.example")
    first = oauth.register({"client_name": "first", "redirect_uris": ["https://chat.openai.com/first"]})
    second = oauth.register({"client_name": "second", "redirect_uris": ["https://chat.openai.com/second"]})
    verifier = "verifier-value-that-is-long-enough-for-refresh-binding-123456"
    code = oauth.begin(first["client_id"], first["redirect_uris"][0], challenge(verifier))
    oauth.complete(code, "owned-subject")
    tokens = oauth.exchange(code, first["client_id"], first["redirect_uris"][0], verifier)
    with pytest.raises(ValueError, match="another client"):
        oauth.refresh(tokens["refresh_token"], second["client_id"])


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
    if os.name != "nt":
        assert state_path.stat().st_mode & 0o077 == 0


def test_durable_state_is_encrypted_when_key_is_configured(tmp_path: Path):
    state_path = tmp_path / "oauth-state.json"
    key = generate_key()
    oauth = LocalOAuth("https://gemini.example", state_path=str(state_path), state_key=key)
    client = oauth.register({"client_name": "ChatGPT Gemini", "redirect_uris": ["https://chat.openai.com/aip/callback"]})
    saved = state_path.read_text(encoding="utf-8")
    assert '"algorithm":"AES-256-GCM"' in saved
    assert client["client_id"] not in saved
    restarted = LocalOAuth("https://gemini.example", state_path=str(state_path), state_key=key)
    assert client["client_id"] in restarted._clients


def test_refresh_token_is_rotated_and_survives_restart(tmp_path: Path):
    state_path = tmp_path / "oauth-state.json"
    oauth = LocalOAuth("https://gemini.example", state_path=str(state_path))
    client = oauth.register({"client_name": "ChatGPT Gemini", "redirect_uris": ["https://chat.openai.com/aip/callback"]})
    verifier = "verifier-value-that-is-long-enough-for-refresh-test-123456"
    code = oauth.begin(client["client_id"], client["redirect_uris"][0], challenge(verifier))
    oauth.complete(code, "gemini-account-a")
    tokens = oauth.exchange(code, client["client_id"], client["redirect_uris"][0], verifier)
    rotated = oauth.refresh(tokens["refresh_token"])
    assert oauth.subject_for(rotated["access_token"]) == "gemini-account-a"
    with pytest.raises(ValueError):
        oauth.refresh(tokens["refresh_token"])


def test_browser_handoff_link_uses_a_separate_opaque_token():
    oauth = LocalOAuth(
        "https://gemini.example",
        handoff_url_factory=lambda: ("handoff-token", "https://novnc.example/vnc.html?sid=handoff-token"),
    )
    client = oauth.register({"client_name": "ChatGPT", "redirect_uris": ["https://chat.openai.com/cb"]})
    page = oauth.authorize_page(client["client_id"], client["redirect_uris"][0], challenge("browser-handoff-verifier-123456789012345678901234567890"), "state")
    assert "sid=handoff-token" in page
    assert "handoff-token" != next(iter(oauth._codes))


def test_authorization_subject_is_isolated_per_dynamic_client():
    oauth = LocalOAuth("https://gemini.example")
    first = oauth.register({"client_name": "first", "redirect_uris": ["https://chat.openai.com/first"]})
    second = oauth.register({"client_name": "second", "redirect_uris": ["https://chat.openai.com/second"]})
    verifier = "verifier-value-that-is-long-enough-for-client-isolation-123456"
    first_code = oauth.begin(first["client_id"], first["redirect_uris"][0], challenge(verifier), subject="client:" + first["client_id"])
    second_code = oauth.begin(second["client_id"], second["redirect_uris"][0], challenge(verifier), subject="client:" + second["client_id"])
    oauth.complete(first_code, "client:" + first["client_id"])
    oauth.complete(second_code, "client:" + second["client_id"])
    first_tokens = oauth.exchange(first_code, first["client_id"], first["redirect_uris"][0], verifier)
    second_tokens = oauth.exchange(second_code, second["client_id"], second["redirect_uris"][0], verifier)
    assert oauth.subject_for(first_tokens["access_token"]) != oauth.subject_for(second_tokens["access_token"])

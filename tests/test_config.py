import pytest

from gemini_webapi_mcp.config import external_url, public_url


def test_public_url_requires_https_origin():
    assert public_url("https://gemini.example.com/") == "https://gemini.example.com"
    assert external_url("oauth/callback", "https://gemini.example.com") == "https://gemini.example.com/oauth/callback"
    with pytest.raises(ValueError):
        public_url("http://127.0.0.1:8000")


def test_external_url_requires_configuration():
    with pytest.raises(RuntimeError):
        external_url("mcp")

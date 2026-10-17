import asyncio
import json

import pytest

import gemini_webapi_mcp.server as server
from gemini_webapi_mcp.models import ModelSelection, model_request_context


class _HTTPResponse:
    status_code = 200


class _Session:
    def __init__(self):
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _HTTPResponse()


class _Client:
    def __init__(self):
        self.client = _Session()
        self.cookies = {}


def _selection():
    header = '[1,null,null,null,"flash-route",null,null,0,[4,5,6,8],null,null,2,null,null,1]'
    return ModelSelection(
        "gemini-flash", "unspecified", None,
        request_headers=(("x-goog-ext-525001261-jspb", header),),
    )


def test_stream_patch_binds_locale_and_emits_only_safe_diagnostics(monkeypatch):
    client = _Client()
    server._patch_client(client)
    inner = [None] * 73
    inner[0] = ["prompt is intentionally not diagnostic"]
    data = {"f.req": json.dumps([None, json.dumps(inner)])}

    async def run():
        with model_request_context(_selection()):
            await client.client.request(
                "POST", "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
                params={"_reqid": "123"}, data=data, headers={},
            )
            return server._request_diagnostics.get()

    diagnostics = asyncio.run(run())
    _, _, kwargs = client.client.calls[-1]
    assert kwargs["params"] == {"_reqid": "123", "hl": "en"}
    assert diagnostics == {
        "model": "gemini-flash",
        "serializer": "modern-v2-locale-bound",
        "model_number": 1,
        "field_count": 81,
        "locale_bound": True,
        "selector_session_bound": True,
        "request_uuid_bound": True,
        "http_status": 200,
    }
    assert "prompt" not in repr(diagnostics).lower()


def test_rejection_includes_sanitized_protocol_stage():
    class Response:
        candidates = [object()]
        text = "I cannot fulfill this request. Can I help you with something else instead?"

    token = server._request_diagnostics.set({
        "model": "gemini-pro", "serializer": "modern-v2-locale-bound",
        "locale_bound": True, "http_status": 200,
    })
    try:
        with pytest.raises(server._UpstreamRejectedError) as error:
            server._response_text(Response(), operation="chat")
    finally:
        server._request_diagnostics.reset(token)
    detail = str(error.value)
    assert "model=gemini-pro" in detail
    assert "parser_stage=candidate_text" in detail
    assert "cookie" not in detail.lower()


def test_server_info_reports_build_provenance(monkeypatch):
    monkeypatch.setenv("GEMINI_BUILD_COMMIT", "phase45-test")
    monkeypatch.setenv("GEMINI_BUILD_TIMESTAMP", "2026-10-17T12:00:00Z")
    monkeypatch.setenv("GEMINI_BUILD_VERSION", "0.2.0-test")
    info = asyncio.run(server.gemini_server_info())
    assert info["build_commit"] == "phase45-test"
    assert info["build_provenance"] == {
        "commit": "phase45-test",
        "timestamp": "2026-10-17T12:00:00Z",
        "version": "0.2.0-test",
        "source": "GEMINI_BUILD_COMMIT",
        "stream_serializer": "modern-v2-locale-bound",
        "upstream_revision": "4402e5231760a1e85e28eb6cd0040ff302b764de",
    }
    assert info["upstream"]["module_origin"].replace("\\", "/").endswith("gemini_webapi/__init__.py")


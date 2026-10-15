import asyncio
import base64
from contextlib import asynccontextmanager
import json
from pathlib import Path

import gemini_webapi_mcp.server as server
from gemini_webapi_mcp.models import MODEL_ROUTE_HEADER, ModelSelection, parse_authenticated_models, resolve_model


class _Response:
    candidates = [object()]
    text = "The uploaded test number is 41827."


class _UploadClient:
    def __init__(self):
        self.upload_path = None
        self.upload_bytes = None

    async def generate_content(self, prompt, *, model, files, current_retry):
        assert prompt == "Extract the test number."
        assert model == "unspecified"
        assert current_retry == server.UPSTREAM_RETRY_BUDGET
        assert len(files) == 1
        self.upload_path = Path(files[0])
        self.upload_bytes = self.upload_path.read_bytes()
        return _Response()


class _Manager:
    def conversation_owner(self, _ctx):
        return "subject", "account"

    @asynccontextmanager
    async def request_slot(self, _ctx):
        yield

    async def invalidate(self, *_args):
        raise AssertionError("successful upload must not invalidate the client")


class _RequestContext:
    def __init__(self, manager):
        self.lifespan_context = {"account_clients": manager}


class _Context:
    def __init__(self, manager):
        self.request_context = _RequestContext(manager)


def test_file_bytes_upload_preserves_extension_uses_attachment_and_cleans_up(monkeypatch):
    client = _UploadClient()
    manager = _Manager()
    context = _Context(manager)
    selection = ModelSelection("gemini-flash", "unspecified", None)

    async def get_client(_ctx):
        return client

    async def select_model(_ctx, _client, _model, _thinking):
        return selection

    monkeypatch.setattr(server, "_get_client", get_client)
    monkeypatch.setattr(server, "_select_model", select_model)
    payload = base64.b64encode(b"phase 4.3 upload number 41827").decode()
    result = asyncio.run(server.gemini_upload_file_bytes(
        "evidence.txt", payload, context, prompt="Extract the test number."
    ))

    assert result == "The uploaded test number is 41827."
    assert client.upload_path.name == "evidence.txt"
    assert client.upload_bytes == b"phase 4.3 upload number 41827"
    assert not client.upload_path.parent.exists()


def test_file_path_upload_passes_existing_path_to_upstream(monkeypatch, tmp_path):
    source = tmp_path / "evidence.txt"
    source.write_text("41827")
    client = _UploadClient()
    manager = _Manager()
    context = _Context(manager)

    async def get_client(_ctx):
        return client

    async def select_model(_ctx, _client, _model, _thinking):
        return ModelSelection("gemini-flash", "unspecified", None)

    monkeypatch.setattr(server, "_get_client", get_client)
    monkeypatch.setattr(server, "_select_model", select_model)
    result = asyncio.run(server.gemini_upload_file(
        str(source), context, prompt="Extract the test number."
    ))

    assert result == "The uploaded test number is 41827."
    assert client.upload_path == source
    assert client.upload_bytes == b"41827"


def test_upload_mime_validation_rejects_executable_suffix():
    payload = base64.b64encode(b"not executable").decode()
    try:
        server._decode_upload_bytes("payload.exe", payload)
    except ValueError as error:
        assert "unsupported file type" in str(error)
    else:
        raise AssertionError("executable upload suffix must be rejected")


def _catalog_frame():
    body = [None] * 18
    body[15] = [
        ["lite-route", "Flash Lite", None, None, None, None, None, None, None, 6, None, "3.5 Flash-Lite"],
        ["flash-route", "Flash", None, None, None, None, None, None, None, 1, None, "3.6 Flash"],
        ["pro-route", "Pro", None, None, None, None, None, None, None, 3, None, "3.1 Pro"],
    ]
    body[16] = [8]
    body[17] = [19]
    return [["wrb.fr", "otAQ7b", json.dumps(body)]]


def _old_request(selection):
    inner = [None] * 73
    inner[0] = ["protocol test", 0, None, None, None, None, 0]
    return dict(selection.request_headers), {
        "at": "redacted",
        "f.req": json.dumps([None, json.dumps(inner)]),
    }


def test_modern_serializer_preserves_each_requested_model_and_mirrors_number():
    models = parse_authenticated_models(_catalog_frame())
    for model_name, route_id, model_number in (
        ("gemini-flash-lite", "lite-route", 6),
        ("gemini-flash", "flash-route", 1),
        ("gemini-pro", "pro-route", 3),
    ):
        selection = resolve_model(models, model_name)
        headers, data = _old_request(selection)
        headers, data = server._modern_stream_request(
            headers, data, selector_session_id="SELECTOR-SESSION", request_uuid="REQUEST-UUID"
        )
        model_header = json.loads(headers[MODEL_ROUTE_HEADER])
        inner = json.loads(json.loads(data["f.req"])[1])
        assert model_header[4] == route_id
        assert model_header[15:] == [1, "SELECTOR-SESSION"]
        assert inner[59] == "REQUEST-UUID"
        assert inner[79] == model_number
        assert inner[80] == 1
        assert headers["x-goog-ext-525005358-jspb"] == '["REQUEST-UUID",1]'


def test_modern_serializer_does_not_replace_requested_pro_with_flash_lite():
    models = parse_authenticated_models(_catalog_frame())
    selection = resolve_model(models, "gemini-pro")
    headers, data = _old_request(selection)
    headers, _ = server._modern_stream_request(
        headers, data, selector_session_id="SESSION", request_uuid="REQUEST"
    )
    assert json.loads(headers[MODEL_ROUTE_HEADER])[4] == "pro-route"


def test_modern_serializer_handles_capacity_field_13_without_losing_model_number():
    frames = _catalog_frame()
    body = json.loads(frames[0][2])
    body[16] = [21]
    frames[0][2] = json.dumps(body)
    selection = resolve_model(parse_authenticated_models(frames), "gemini-pro")
    headers, data = _old_request(selection)
    headers, data = server._modern_stream_request(
        headers, data, selector_session_id="SESSION", request_uuid="REQUEST"
    )
    model_header = json.loads(headers[MODEL_ROUTE_HEADER])
    inner = json.loads(json.loads(data["f.req"])[1])
    assert model_header[-3:] == [3, 1, "SESSION"]
    assert inner[79] == 3


def test_image_value_error_retains_safe_diagnostic_detail():
    result = server._handle_error(ValueError("generated image metadata is malformed"), operation="image")
    assert result == "Error: image_generation_failed — generated image metadata is malformed"


def test_image_parser_recovers_authenticated_client_cookie_jar():
    class Session:
        cookies = {"__Secure-1PSID": "test-only-cookie"}

    class Client:
        cookies = {}
        client = Session()

    selected = server._image_parser_cookies(Client(), {})
    assert selected == {"__Secure-1PSID": "test-only-cookie"}


def test_image_parser_preserves_nonempty_parser_cookie_jar():
    provided = {"__Secure-1PSID": "parser-cookie"}
    assert server._image_parser_cookies(object(), provided) is provided

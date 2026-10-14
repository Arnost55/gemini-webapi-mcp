import asyncio
import base64
from contextlib import asynccontextmanager
from pathlib import Path

import gemini_webapi_mcp.server as server
from gemini_webapi_mcp.models import ModelSelection


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

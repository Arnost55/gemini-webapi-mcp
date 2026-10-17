import json
import asyncio

import pytest
from pydantic import ValidationError

from gemini_webapi_mcp.protocol import (
    AccountModelRegistry, CapabilityState, ModelDescriptor, Operation,
    ProtocolError, ThinkingLevel, bind_stream_request,
)
from gemini_webapi_mcp.protocol.serializer import SerializationError
from gemini_webapi_mcp.protocol.streaming import StreamTracker
from gemini_webapi_mcp.protocol.adapter import LegacyClientAdapter
from gemini_webapi_mcp.protocol.contracts import GeminiRequest
from gemini_webapi_mcp.state_migration import copy_for_migration, inspect_state


def model(**changes):
    values = dict(public_id="gemini-flash", display_name="Flash", upstream_id="route-1",
                  operations=frozenset({Operation.CHAT}), state=CapabilityState.VERIFIED,
                  protocol_revision="test-r1")
    values.update(changes)
    return ModelDescriptor(**values)


def test_registry_is_account_scoped_and_never_substitutes_models():
    registry = AccountModelRegistry()
    registry.replace("one", [model()])
    registry.replace("two", [model(public_id="gemini-pro", upstream_id="route-2")])
    assert registry.resolve("one", "gemini-flash", Operation.CHAT).upstream_id == "route-1"
    with pytest.raises(ProtocolError) as error:
        registry.resolve("one", "gemini-pro", Operation.CHAT)
    assert error.value.code == "model_unavailable"


def test_registry_fails_closed_for_discovered_route_and_thinking():
    registry = AccountModelRegistry()
    registry.replace("one", [model(state=CapabilityState.DISCOVERED)])
    with pytest.raises(ProtocolError) as error:
        registry.resolve("one", "gemini-flash", Operation.CHAT)
    assert error.value.code == "model_route_unverified"
    registry.replace("one", [model()])
    with pytest.raises(ProtocolError) as error:
        registry.resolve("one", "gemini-flash", Operation.CHAT, ThinkingLevel.HIGH)
    assert error.value.code == "thinking_not_supported"


def test_typed_model_contract_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        model(magic_index=79)


def test_serializer_fails_closed_without_verified_selector():
    with pytest.raises(SerializationError):
        bind_stream_request({}, {"f.req": "[]"}, selector_session_id="session")


def test_stream_tracker_requires_order_progress_and_completion():
    stream = StreamTracker(stall_timeout=2, started_at=0, last_progress_at=0)
    stream.observe(0, progress=True, now=1)
    stream.assert_live(now=2)
    with pytest.raises(ProtocolError, match="progress"):
        stream.assert_live(now=4)
    stream.observe(1, progress=True, completed=True, now=4)
    stream.assert_complete()
    with pytest.raises(ProtocolError, match="out-of-order"):
        stream.observe(1, progress=False, now=5)


def test_typed_adapter_separates_reasoning_and_followups_from_public_text():
    class Response:
        text = 'Public answer.\n\n<FollowUp query="Next question"/>\n'
        thoughts = "private reasoning must not escape"
        metadata = ["cid", "rid", "rcid"]
        images = []

    class Native:
        async def generate_content(self, *args, **kwargs):
            return Response()

    async def discover(_native):
        return []

    request = GeminiRequest(
        operation=Operation.CHAT,
        model="gemini-flash",
        prompt="test",
        request_id="request-1",
    )
    response = asyncio.run(LegacyClientAdapter(Native(), discover).execute(request))
    assert response.text == "Public answer."
    assert response.reasoning_available is True
    assert response.follow_ups == ({"label": None, "query": "Next question"},)
    assert "private reasoning" not in response.model_dump_json()


def test_state_migration_is_copy_based_and_preflight_is_read_only(tmp_path):
    import sqlite3
    source = tmp_path / "conversations.sqlite3"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA user_version=2")
    registry = tmp_path / "registry.enc"
    registry.write_text("opaque")
    profiles = tmp_path / "accounts"
    profiles.mkdir()
    before = source.read_bytes()
    status = inspect_state(source, registry, profiles)
    assert status.conversations_schema == 2
    copy = copy_for_migration(source, tmp_path / "staging" / "copy.sqlite3")
    assert copy.read_bytes() == before == source.read_bytes()
    with pytest.raises(FileExistsError):
        copy_for_migration(source, copy)

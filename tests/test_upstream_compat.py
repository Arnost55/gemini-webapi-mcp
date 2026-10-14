import json

import pytest

from gemini_webapi.utils import parse_response_by_frame
from gemini_webapi_mcp.upstream import (
    AUDITED_COMMIT,
    AUDITED_VERSION,
    inspect_upstream,
    require_compatible_upstream,
)


def _frame(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    return f"{len(payload.encode('utf-16-le')) // 2}\n{payload}"


def test_audited_upstream_is_installed_and_has_required_surfaces():
    status = inspect_upstream()
    assert status.installed_version == AUDITED_VERSION
    assert status.audited_commit == AUDITED_COMMIT
    assert status.compatible
    assert status.stream_parser_utf16
    assert status.streaming_api
    assert status.authenticated_model_discovery is False
    assert status.independent_thinking_control is False
    require_compatible_upstream()


def test_complete_stream_fixture_with_multiple_frames():
    data = _frame(["first"]) + _frame(["emoji 😀"])
    parts, remainder = parse_response_by_frame(data)
    assert parts == ["first", "emoji 😀"]
    assert remainder == ""


@pytest.mark.parametrize("cut", [2, 4, 7])
def test_truncated_stream_retains_exact_remainder(cut):
    data = _frame(["delayed 😀 payload"])
    parts, remainder = parse_response_by_frame(data[:-cut])
    assert parts == []
    assert remainder == data[:-cut]


def test_delayed_stream_can_resume_without_duplicate_frames():
    data = _frame(["one"]) + _frame(["two"])
    split = len(data) - 5
    first, remainder = parse_response_by_frame(data[:split])
    second, remainder = parse_response_by_frame(remainder + data[split:])
    assert first + second == ["one", "two"]
    assert remainder == ""


def test_malformed_frame_is_not_misparsed_or_discarded():
    malformed = "12\n{not-json}\n"
    parts, remainder = parse_response_by_frame(malformed)
    assert parts == []
    # A complete but invalid frame is consumed so it cannot poison every
    # subsequent chunk; importantly it is never emitted as valid content.
    assert remainder == ""

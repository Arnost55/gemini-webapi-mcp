import asyncio

from gemini_webapi import APIError, RequestTimeoutError
from gemini_webapi_mcp.server import _upstream_failure_closes_client, mcp


def test_mcp_tool_list_exposes_conversation_and_account_management_tools():
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert {
        "gemini_list_chats",
        "gemini_get_chat",
        "gemini_get_chat_history",
        "gemini_delete_chat",
        "gemini_server_info",
        "gemini_get_account_info",
        "gemini_account_diagnostics",
        "gemini_deactivate_account",
        "gemini_delete_account",
    } <= names


def test_stalled_or_truncated_upstream_client_is_evicted_before_next_request():
    assert _upstream_failure_closes_client(APIError("Response stalled (zombie stream)."))
    assert _upstream_failure_closes_client(APIError("Stream interrupted or truncated."))
    assert _upstream_failure_closes_client(RequestTimeoutError("timed out"))
    assert not _upstream_failure_closes_client(APIError("Failed to generate contents. Status: 429"))

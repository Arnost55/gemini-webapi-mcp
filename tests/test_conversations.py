import concurrent.futures

import pytest

from gemini_webapi_mcp.conversations import ConversationStore


def test_conversation_survives_new_store_and_restores_metadata(tmp_path):
    path = tmp_path / "conversations.sqlite3"
    first = ConversationStore(path)
    session_id = first.create("subject-a", "account-a", "gemini-3.0-flash")
    first.add_messages("subject-a", "account-a", session_id, [("user", "remember this"), ("assistant", "I will")])
    first.update_metadata("subject-a", "account-a", session_id, ["cid", "rid", "rcid"])

    second = ConversationStore(path)
    row = second.get("subject-a", "account-a", session_id)
    assert row["cid"] == "cid"
    assert [m["content"] for m in second.history("subject-a", "account-a", session_id)] == ["remember this", "I will"]


def test_conversations_are_account_scoped(tmp_path):
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    session_id = store.create("subject-a", "account-a", "model")
    with pytest.raises(KeyError):
        store.get("subject-b", "account-b", session_id)
    assert store.list("subject-b", "account-b") == []


def test_concurrent_writes_are_serialized(tmp_path):
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    session_id = store.create("subject-a", "account-a", "model")

    def add(i):
        store.add_messages("subject-a", "account-a", session_id, [("user", f"message-{i}")])

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(add, range(20)))
    assert len(store.history("subject-a", "account-a", session_id)) == 20


def test_expiration_and_delete(tmp_path):
    store = ConversationStore(tmp_path / "conversations.sqlite3", ttl_seconds=0)
    session_id = store.create("subject-a", "account-a", "model")
    with pytest.raises(TimeoutError):
        store.get("subject-a", "account-a", session_id)

    live = ConversationStore(tmp_path / "live.sqlite3")
    sid = live.create("subject-a", "account-a", "model")
    live.delete("subject-a", "account-a", sid)
    with pytest.raises(KeyError):
        live.get("subject-a", "account-a", sid)

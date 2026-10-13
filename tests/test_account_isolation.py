from types import SimpleNamespace

from gemini_webapi_mcp import server
from gemini_webapi_mcp.registry import DynamicAccountRegistry, generate_key


def test_two_mcp_clients_keep_profiles_media_and_sessions_isolated(tmp_path, monkeypatch):
    registry_path = tmp_path / "accounts.json.enc"
    profile_root = tmp_path / "accounts"
    media_root = tmp_path / "media"
    key = generate_key()
    registry = DynamicAccountRegistry(registry_path, key)
    for subject, account in (("client-a", "account-a"), ("client-b", "account-b")):
        (profile_root / account).mkdir(parents=True)
        registry.register(subject=subject, account=account, scopes={"chat", "image"})
        registry.select(subject, account)

    monkeypatch.setenv("GEMINI_REGISTRY_PATH", str(registry_path))
    monkeypatch.setenv("GEMINI_REGISTRY_KEY", key)
    monkeypatch.setenv("GEMINI_PROFILE_ROOT", str(profile_root))
    monkeypatch.setenv("GEMINI_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("GEMINI_CONVERSATIONS_PATH", str(tmp_path / "conversations.sqlite3"))
    monkeypatch.setattr(server, "_oauth_subject", lambda ctx: ctx.subject)

    manager = server._AccountClientManager()
    client_a = SimpleNamespace(subject="client-a")
    client_b = SimpleNamespace(subject="client-b")

    subject_a, record_a = manager._account(client_a)
    subject_b, record_b = manager._account(client_b)
    assert (subject_a, record_a["account"]) == ("client-a", "account-a")
    assert (subject_b, record_b["account"]) == ("client-b", "account-b")
    assert manager._profile(record_a) == (profile_root / "account-a").resolve()
    assert manager._profile(record_b) == (profile_root / "account-b").resolve()
    assert manager.media_dir(client_a) == (media_root / "account-a").resolve()
    assert manager.media_dir(client_b) == (media_root / "account-b").resolve()
    assert manager.session_key(client_a, "same-session") != manager.session_key(client_b, "same-session")


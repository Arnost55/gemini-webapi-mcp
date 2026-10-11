import json

import pytest

from gemini_webapi_mcp.session import AccountMapping, GeminiSessionManager, SessionStatus, classify_gemini_page
from gemini_webapi_mcp.accounts import AccountRegistry
from gemini_webapi_mcp.handoff import HandoffManager


def test_classifies_gemini_page_as_ready():
    assert classify_gemini_page("https://gemini.google.com/app", "Gemini") == "ready"


def test_classifies_google_login_as_required():
    assert classify_gemini_page("https://accounts.google.com/ServiceLogin", "") == "login_required"


def test_classifies_google_consent_page_on_gemini_url_as_required():
    body = "Before you continue to Google We use cookies and data Sign in"
    assert classify_gemini_page("https://gemini.google.com/app", body) == "login_required"


def test_status_contains_presence_only():
    status = SessionStatus(has_psid=True)
    data = status.safe_dict()
    assert data["has_psid"] is True
    assert "cookie" not in repr(data).lower()


def test_manager_defaults_to_five_minutes(tmp_path):
    manager = GeminiSessionManager(tmp_path)
    assert manager.check_interval == 300
    assert manager.headless is True


def test_oauth_subject_resolves_only_to_its_isolated_profile(tmp_path):
    mapping = AccountMapping({"user_abc": "personal", "user_xyz": "work"})
    assert mapping.profile_for("user_abc", tmp_path) == tmp_path / "personal"
    assert mapping.profile_for("user_xyz", tmp_path) == tmp_path / "work"
    with pytest.raises(PermissionError):
        mapping.resolve("unknown")


def test_mapping_file_supports_subjects_object(tmp_path):
    config = tmp_path / "accounts.json"
    config.write_text(json.dumps({"subjects": {"user_abc": "personal"}}), encoding="utf-8")
    assert AccountMapping.from_file(config).resolve("user_abc") == "personal"


def test_registry_requires_explicit_selection_for_multiple_accounts(tmp_path):
    config = tmp_path / "accounts.json"
    config.write_text(json.dumps({"subjects": {"user": ["personal", "work"]}, "accounts": {"personal": {"scopes": ["gemini:use"]}, "work": {"scopes": ["gemini:use"]}}}), encoding="utf-8")
    registry = AccountRegistry(config)
    with pytest.raises(PermissionError):
        registry.select("user")
    assert registry.allow_request(registry.select("user", "work"), "gemini:use") is True


def test_registry_status_redacts_unexpected_error(tmp_path):
    config = tmp_path / "accounts.json"
    config.write_text(json.dumps({"accounts": {}}), encoding="utf-8")
    registry = AccountRegistry(config)
    registry.write_status("work", "error", "secret-cookie-value")
    assert "secret-cookie-value" not in registry.status_path.read_text(encoding="utf-8")


def test_browser_handoff_requires_verify_then_approval():
    manager = HandoffManager(ttl_seconds=60)
    token = manager.create("user", "work", now=100)
    with pytest.raises(PermissionError):
        manager.approve(token, now=101)
    manager.mark_verified(token, now=102)
    session = manager.approve(token, now=103)
    assert session.subject == "user"
    assert session.account == "work"
    assert manager.consume(token, now=104).state == "used"
    with pytest.raises(PermissionError):
        manager.consume(token, now=105)


def test_browser_handoff_expires_and_cannot_be_replayed():
    manager = HandoffManager(ttl_seconds=2)
    token = manager.create("user", "work", now=100)
    with pytest.raises(PermissionError):
        manager.inspect(token, now=102)

import json

import pytest

from gemini_webapi_mcp.session import AccountMapping, GeminiSessionManager, SessionStatus, classify_gemini_page, parse_env_bool
from gemini_webapi_mcp.accounts import AccountRegistry
from gemini_webapi_mcp.handoff import HandoffManager


def test_classifies_gemini_page_as_ready():
    assert classify_gemini_page("https://gemini.google.com/app", "Gemini") == "ready"


def test_classifies_google_login_as_required():
    assert classify_gemini_page("https://accounts.google.com/ServiceLogin", "") == "login_required"


def test_classifies_google_consent_page_on_gemini_url_as_required():
    body = "Before you continue to Google We use cookies and data Sign in"
    assert classify_gemini_page("https://gemini.google.com/app", body) == "login_required"


def test_classifies_google_automation_rejection_as_error():
    body = "Couldn't sign you in This browser or app may not be secure"
    assert classify_gemini_page("https://accounts.google.com/v3/signin/rejected", body) == "error"


def test_rejects_ambiguous_browser_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_BROWSER_CHANNEL", "chrome")
    monkeypatch.setenv("GEMINI_BROWSER_EXECUTABLE", "/usr/bin/google-chrome")
    with pytest.raises(ValueError):
        GeminiSessionManager(tmp_path)


def test_status_contains_presence_only():
    status = SessionStatus(has_psid=True)
    data = status.safe_dict()
    assert data["has_psid"] is True
    assert "cookie" not in repr(data).lower()


def test_manager_defaults_to_five_minutes(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_CHROMIUM_SANDBOX", raising=False)
    manager = GeminiSessionManager(tmp_path)
    assert manager.check_interval == 300
    assert manager.headless is True
    assert manager.browser_channel == "chrome"
    assert manager.chromium_sandbox is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_parse_env_bool_true(monkeypatch, value):
    monkeypatch.setenv("GEMINI_CHROMIUM_SANDBOX", value)
    assert parse_env_bool("GEMINI_CHROMIUM_SANDBOX") is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off"])
def test_parse_env_bool_false(monkeypatch, value):
    monkeypatch.setenv("GEMINI_CHROMIUM_SANDBOX", value)
    assert parse_env_bool("GEMINI_CHROMIUM_SANDBOX", default=True) is False


def test_parse_env_bool_rejects_invalid_value(monkeypatch):
    monkeypatch.setenv("GEMINI_CHROMIUM_SANDBOX", "sometimes")
    with pytest.raises(ValueError):
        GeminiSessionManager("/tmp/profile")


def test_sandbox_enabled_launch_options(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_CHROMIUM_SANDBOX", "true")
    options = GeminiSessionManager(tmp_path).browser_launch_options()
    assert options["chromium_sandbox"] is True
    assert "--no-sandbox" not in options["args"]


def test_sandbox_disabled_launch_options_are_backward_compatible(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_CHROMIUM_SANDBOX", "false")
    options = GeminiSessionManager(tmp_path).browser_launch_options()
    assert options["chromium_sandbox"] is False
    assert "--no-sandbox" not in options["args"]


def test_explicit_browser_executable_preserves_sandbox_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_BROWSER_EXECUTABLE", "/usr/bin/google-chrome")
    monkeypatch.setenv("GEMINI_CHROMIUM_SANDBOX", "true")
    options = GeminiSessionManager(tmp_path).browser_launch_options()
    assert options["executable_path"] == "/usr/bin/google-chrome"
    assert options["chromium_sandbox"] is True


def test_explicit_browser_executable_disables_default_channel(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_BROWSER_CHANNEL", raising=False)
    monkeypatch.setenv("GEMINI_BROWSER_EXECUTABLE", "/usr/bin/google-chrome")
    manager = GeminiSessionManager(tmp_path)
    assert manager.browser_channel is None


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

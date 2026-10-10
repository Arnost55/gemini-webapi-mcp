import json

import pytest

from gemini_webapi_mcp.session import AccountMapping, GeminiSessionManager, SessionStatus, classify_gemini_page


def test_classifies_gemini_page_as_ready():
    assert classify_gemini_page("https://gemini.google.com/app", "Gemini") == "ready"


def test_classifies_google_login_as_required():
    assert classify_gemini_page("https://accounts.google.com/ServiceLogin", "") == "login_required"


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

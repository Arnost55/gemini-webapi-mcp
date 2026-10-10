from gemini_webapi_mcp.session import GeminiSessionManager, SessionStatus, classify_gemini_page


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

import asyncio
import io
import json

import pytest

from gemini_webapi_mcp.session import (
    AccountMapping,
    DirectBrowserStartupError,
    GeminiSessionManager,
    SessionStatus,
    classify_gemini_page,
    parse_env_bool,
)
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


def test_startup_diagnostics_are_bounded_and_non_sensitive():
    status = SessionStatus(
        browser_executable="google-chrome", sandbox_enabled=False,
        display_available=True, browser_pid=123, return_code=1,
    ).safe_dict()
    assert status["browser_executable"] == "google-chrome"
    assert status["browser_pid"] == 123
    assert "url" not in status
    assert "profile" not in status
    assert "cookie" not in repr(status).lower()


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


class FakeDirectProcess:
    pid = 4321

    def __init__(self, return_code=None, stderr=b""):
        self.return_code = return_code
        self.stderr = io.BytesIO(stderr)
        self.terminated = False

    def poll(self):
        return self.return_code

    def terminate(self):
        self.terminated = True
        self.return_code = 0

    def wait(self, timeout=None):
        return self.return_code


def _direct_manager(monkeypatch, tmp_path, *, sandbox=False):
    monkeypatch.delenv("GEMINI_BROWSER_CHANNEL", raising=False)
    monkeypatch.setenv("GEMINI_BROWSER_EXECUTABLE", "/usr/bin/google-chrome")
    monkeypatch.setenv("GEMINI_CHROMIUM_SANDBOX", "true" if sandbox else "false")
    monkeypatch.setenv("DISPLAY", ":100")
    manager = GeminiSessionManager(tmp_path)
    monkeypatch.setattr(manager, "_display_available", lambda display: display == ":100")

    async def checked():
        return SessionStatus(state="login_required")

    monkeypatch.setattr(manager, "check_now", checked)
    return manager


def test_direct_browser_disables_sandbox_only_when_configured(monkeypatch, tmp_path):
    manager = _direct_manager(monkeypatch, tmp_path)
    (tmp_path / "Local State").write_text("{}", encoding="utf-8")
    commands = []

    def launch(command, **kwargs):
        commands.append((command, kwargs))
        return FakeDirectProcess()

    monkeypatch.setattr("gemini_webapi_mcp.session.subprocess.Popen", launch)
    status = asyncio.run(manager._start_direct_browser())
    assert status.state == "login_required"
    assert "--no-sandbox" in commands[0][0]
    assert commands[0][1]["stderr"] == -1


def test_direct_browser_preserves_enabled_sandbox(monkeypatch, tmp_path):
    manager = _direct_manager(monkeypatch, tmp_path, sandbox=True)
    (tmp_path / "Local State").write_text("{}", encoding="utf-8")
    commands = []
    monkeypatch.setattr(
        "gemini_webapi_mcp.session.subprocess.Popen",
        lambda command, **kwargs: commands.append(command) or FakeDirectProcess(),
    )
    asyncio.run(manager._start_direct_browser())
    assert "--no-sandbox" not in commands[0]


def test_direct_browser_requires_executable(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_BROWSER_EXECUTABLE", raising=False)
    monkeypatch.delenv("GEMINI_BROWSER_CHANNEL", raising=False)
    monkeypatch.setenv("DISPLAY", ":100")
    monkeypatch.setattr("gemini_webapi_mcp.session.shutil.which", lambda name: None)
    with pytest.raises(DirectBrowserStartupError) as error:
        asyncio.run(GeminiSessionManager(tmp_path)._start_direct_browser())
    assert error.value.code == "browser_executable_missing"


def test_direct_browser_requires_available_display(monkeypatch, tmp_path):
    manager = _direct_manager(monkeypatch, tmp_path)
    monkeypatch.setattr(manager, "_display_available", lambda display: False)
    with pytest.raises(DirectBrowserStartupError) as error:
        asyncio.run(manager._start_direct_browser())
    assert error.value.code == "display_unavailable"


def test_direct_browser_classifies_immediate_sandbox_exit(monkeypatch, tmp_path):
    manager = _direct_manager(monkeypatch, tmp_path, sandbox=True)
    monkeypatch.setattr(
        "gemini_webapi_mcp.session.subprocess.Popen",
        lambda *args, **kwargs: FakeDirectProcess(1, b"No usable sandbox! cookie=must-not-surface"),
    )
    with pytest.raises(DirectBrowserStartupError) as error:
        asyncio.run(manager._start_direct_browser())
    assert error.value.code == "sandbox_initialization_failed"
    assert "cookie" not in str(error.value)


def test_direct_browser_startup_times_out_and_can_be_cleaned_up(monkeypatch, tmp_path):
    manager = _direct_manager(monkeypatch, tmp_path)
    monkeypatch.setenv("GEMINI_BROWSER_STARTUP_TIMEOUT", "0.1")
    process = FakeDirectProcess()
    monkeypatch.setattr("gemini_webapi_mcp.session.subprocess.Popen", lambda *args, **kwargs: process)
    with pytest.raises(DirectBrowserStartupError) as error:
        asyncio.run(manager._start_direct_browser())
    assert error.value.code == "browser_startup_timeout"
    asyncio.run(manager.stop())
    assert process.terminated is True


def test_direct_browser_preserves_existing_profile_files(monkeypatch, tmp_path):
    marker = tmp_path / "Default" / "Cookies"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"existing-profile")
    (tmp_path / "Local State").write_text("{}", encoding="utf-8")
    manager = _direct_manager(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "gemini_webapi_mcp.session.subprocess.Popen", lambda *args, **kwargs: FakeDirectProcess(),
    )
    asyncio.run(manager._start_direct_browser())
    assert marker.read_bytes() == b"existing-profile"


def test_direct_browser_cookie_check_uses_nonblocking_basic_reader(monkeypatch, tmp_path):
    from gemini_webapi_mcp import server

    manager = _direct_manager(monkeypatch, tmp_path)
    manager._browser_process = FakeDirectProcess()
    calls = []

    def read_basic(path):
        calls.append(path)
        return [
            (".google.com", "__Secure-1PSID", "redacted"),
            (".google.com", "__Secure-1PSIDTS", "also-redacted"),
        ]

    monkeypatch.delenv("GEMINI_ACCOUNT_COOKIE_READER", raising=False)
    monkeypatch.setattr(server, "_read_basic_chromium_cookies", read_basic)
    status = manager._check_direct_browser()
    assert status.state == "ready"
    assert status.has_psid is True
    assert status.has_psidts is True
    assert calls == [tmp_path / "Default" / "Cookies"]


def test_direct_browser_requires_both_gemini_session_cookies(monkeypatch, tmp_path):
    from gemini_webapi_mcp import server

    manager = _direct_manager(monkeypatch, tmp_path)
    manager._browser_process = FakeDirectProcess()
    monkeypatch.delenv("GEMINI_ACCOUNT_COOKIE_READER", raising=False)
    monkeypatch.setattr(
        server, "_read_basic_chromium_cookies",
        lambda path: [(".google.com", "__Secure-1PSID", "redacted")],
    )

    status = manager._check_direct_browser()
    assert status.state == "login_required"
    assert status.has_psid is True
    assert status.has_psidts is False


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

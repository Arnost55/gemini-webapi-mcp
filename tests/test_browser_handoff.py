import json
from pathlib import Path

from gemini_webapi_mcp.browser_handoff import BrowserHandoffBroker


class FakeProcess:
    pid = 12345

    def poll(self):
        return 0

    def terminate(self):
        pass


def test_handoffs_get_separate_profiles_displays_and_vnc_targets(tmp_path: Path):
    commands = []

    def runner(command, **kwargs):
        commands.append((command, kwargs))
        return FakeProcess()

    broker = BrowserHandoffBroker(tmp_path, public_url="https://gemini.example/vnc.html", runner=runner)
    first = broker.create()
    second = broker.create()

    assert first.profile != second.profile
    assert first.display != second.display
    assert first.vnc_port != second.vnc_port
    assert first.token != second.token
    assert len(commands) == 8
    url = broker.browser_url(first)
    assert "sid=" + first.token in url
    assert "path=websockify%3Ftoken%3D" in url
    tokens = (tmp_path / "novnc-tokens").read_text(encoding="utf-8").splitlines()
    assert f"{first.token}: 127.0.0.1:{first.vnc_port}" in tokens
    assert f"{second.token}: 127.0.0.1:{second.vnc_port}" in tokens


def test_handoff_ready_state_never_reads_cookie_values(tmp_path: Path):
    broker = BrowserHandoffBroker(tmp_path, runner=lambda *args, **kwargs: FakeProcess())
    session = broker.create()
    Path(session.status_file).write_text(json.dumps({"state": "ready", "has_psid": True}), encoding="utf-8")
    assert broker.is_ready(session.token) is True
    Path(session.status_file).write_text(json.dumps({"state": "login_required"}), encoding="utf-8")
    assert broker.is_ready(session.token) is False


def test_handoff_consumption_removes_its_vnc_capability(tmp_path: Path):
    broker = BrowserHandoffBroker(tmp_path, runner=lambda *args, **kwargs: FakeProcess())
    session = broker.create()
    broker.consume(session.token)

    assert session.token not in broker._sessions
    assert session.token not in (tmp_path / "novnc-tokens").read_text(encoding="utf-8")
    assert not Path(session.profile).exists()

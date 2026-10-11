import json
import os
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
    assert any("xdpyinfo" in part for command, _ in commands for part in command)
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


def test_handoff_promotion_moves_only_the_completed_profile(tmp_path: Path):
    broker = BrowserHandoffBroker(tmp_path, runner=lambda *args, **kwargs: FakeProcess())
    session = broker.create()
    destination = tmp_path / "accounts" / "owned-account"
    broker.consume(session.token, promote_to=destination)

    assert destination.is_dir()
    assert not Path(session.profile).exists()
    assert session.token not in (tmp_path / "novnc-tokens").read_text(encoding="utf-8")


def test_shutdown_reclaims_active_desktop_and_capability(tmp_path: Path):
    broker = BrowserHandoffBroker(tmp_path, runner=lambda *args, **kwargs: FakeProcess())
    session = broker.create()

    broker.shutdown()

    assert broker._sessions == {}
    assert not Path(session.profile).exists()
    assert not Path(session.status_file).exists()
    assert not (tmp_path / "manifests" / f"{session.token}.json").exists()
    assert session.token not in (tmp_path / "novnc-tokens").read_text(encoding="utf-8")


def test_startup_recovers_stale_manifest_and_owned_profile(tmp_path: Path, monkeypatch):
    token = "stale-token"
    profile = tmp_path / "profiles" / token
    status = tmp_path / "status" / f"{token}.json"
    manifest_dir = tmp_path / "manifests"
    profile.mkdir(parents=True)
    status.parent.mkdir(parents=True)
    manifest_dir.mkdir(parents=True)
    status.write_text("{}", encoding="utf-8")
    (manifest_dir / f"{token}.json").write_text(json.dumps({
        "version": 1,
        "token": token,
        "profile": str(profile),
        "status_file": str(status),
        "owner_pid": 999999999,
        "pids": [],
    }), encoding="utf-8")
    broker = BrowserHandoffBroker(tmp_path, runner=lambda *args, **kwargs: FakeProcess())
    assert not profile.exists()
    assert not status.exists()
    assert not (manifest_dir / f"{token}.json").exists()
    assert broker._sessions == {}


def test_startup_does_not_recover_manifest_owned_by_live_process(tmp_path: Path):
    token = "live-token"
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / f"{token}.json").write_text(json.dumps({
        "version": 1,
        "token": token,
        "profile": str(tmp_path / "profiles" / token),
        "status_file": str(tmp_path / "status" / f"{token}.json"),
        "owner_pid": os.getpid(),
        "pids": [],
    }), encoding="utf-8")
    BrowserHandoffBroker(tmp_path, runner=lambda *args, **kwargs: FakeProcess())
    assert (manifest_dir / f"{token}.json").exists()

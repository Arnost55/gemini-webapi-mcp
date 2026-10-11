"""Isolated, short-lived browser desktops for Gemini Web login.

Each OAuth authorization receives a separate Chromium profile and virtual X
display.  The opaque handoff token is also the noVNC/websockify capability;
the OAuth authorization code is never used as a browser token.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl


@dataclass
class BrowserHandoff:
    token: str
    profile: str
    display: int
    vnc_port: int
    expires_at: float
    status_file: str
    processes: list[subprocess.Popen]
    owner_subject: str = ""
    account: str = ""


class BrowserHandoffBroker:
    """Own isolated browser desktops and a websockify token map.

    The broker intentionally persists only safe allocation metadata.  Browser
    cookies remain in the per-handoff Chromium profile and are never read,
    returned, or logged by this module.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        public_url: str = "",
        ttl_seconds: int = 300,
        display_start: int = 100,
        vnc_port_start: int = 5901,
        runner=subprocess.Popen,
    ) -> None:
        self.root = Path(root)
        self.public_url = public_url.rstrip("/")
        self.ttl_seconds = ttl_seconds
        self.display_start = display_start
        self.vnc_port_start = vnc_port_start
        self._runner = runner
        self._sessions: dict[str, BrowserHandoff] = {}
        self._lock = RLock()
        self._token_file = self.root / "novnc-tokens"
        self._manifest_dir = self.root / "manifests"
        self.recover_orphans()

    def revoke_stale_tokens(self) -> None:
        """Revoke capabilities from a previous broker process.

        This is intentionally called during application startup rather than
        construction: importing the module must remain side-effect free for
        CLI tools and worker processes.
        """
        with self._lock:
            if self._token_file.exists():
                self._write_tokens()

    def _manifest_path(self, token: str) -> Path:
        return self._manifest_dir / f"{token}.json"

    def _write_manifest(self, session: BrowserHandoff, pids: list[int] | None = None) -> None:
        self._manifest_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "token": session.token,
            "profile": session.profile,
            "status_file": session.status_file,
            "display": session.display,
            "vnc_port": session.vnc_port,
            "expires_at": session.expires_at,
            "owner_subject": session.owner_subject,
            "account": session.account,
            "owner_pid": os.getpid(),
            "pids": pids if pids is not None else [process.pid for process in session.processes if getattr(process, "pid", None)],
        }
        temporary = self._manifest_path(session.token).with_suffix(".tmp")
        temporary.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(temporary, self._manifest_path(session.token))

    @staticmethod
    def _pid_is_owned(pid: int, token: str, profile: str) -> bool:
        """Return true only for a process carrying this handoff's marker."""
        if os.name == "nt":
            return False
        try:
            raw = Path(f"/proc/{pid}/environ").read_bytes()
            env = raw.split(b"\0")
            return (
                f"GEMINI_HANDOFF_TOKEN={token}".encode() in env
                and f"GEMINI_HANDOFF_PROFILE={profile}".encode() in env
            )
        except (OSError, ValueError):
            return False

    def _recover_manifest(self, manifest_path: Path) -> None:
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            token = str(data["token"])
            profile = str(data["profile"])
            status_file = Path(str(data["status_file"]))
            pids = [int(pid) for pid in data.get("pids", [])]
        except (OSError, TypeError, ValueError, KeyError):
            return
        if not token or not profile or not token in manifest_path.name:
            return
        owner_pid = data.get("owner_pid")
        if owner_pid == os.getpid():
            return
        if isinstance(owner_pid, int):
            try:
                os.kill(owner_pid, 0)
            except OSError:
                pass
            else:
                # Another broker may still own this manifest. Leave it alone;
                # its expiry path will perform normal cleanup.
                return
        for pid in pids:
            if self._pid_is_owned(pid, token, profile):
                self._terminate_pid(pid)
        try:
            shutil.rmtree(profile, ignore_errors=True)
            status_file.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)
        except OSError:
            pass

    def recover_orphans(self) -> None:
        """Recover abandoned handoffs without killing unrelated processes."""
        if not self._manifest_dir.exists():
            return
        for manifest in self._manifest_dir.glob("*.json"):
            self._recover_manifest(manifest)

    @staticmethod
    def _terminate_pid(pid: int) -> None:
        try:
            killpg = getattr(os, "killpg", None)
            if killpg is None:
                os.kill(pid, signal.SIGTERM)
            else:
                killpg(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                return

    def _write_tokens(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        # TokenFile deliberately reloads this text format for every websocket:
        # ``token: host:port``. Do not replace it with JSON; that is the format
        # of a different websockify token plugin.
        targets = [
            f"{token}: 127.0.0.1:{session.vnc_port}"
            for token, session in self._sessions.items()
            if session.expires_at > time.time()
        ]
        temporary = self._token_file.with_suffix(".tmp")
        temporary.write_text("\n".join(targets) + ("\n" if targets else ""), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self._token_file)

    def _next_slot(self) -> tuple[int, int]:
        used_displays = {item.display for item in self._sessions.values()}
        used_ports = {item.vnc_port for item in self._sessions.values()}
        number = 0
        while self.display_start + number in used_displays or self.vnc_port_start + number in used_ports:
            number += 1
        return self.display_start + number, self.vnc_port_start + number

    def _write_fluxbox_config(self) -> Path:
        """Create a display-only window-manager config with no desktop UI."""
        path = self.root / "fluxboxrc"
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            "session.screen0.toolbar.visible: false\n"
            "session.screen0.toolbar.height: 0\n"
            "session.screen0.rootCommand: /bin/true\n"
            "session.screen0.workspaces: 1\n"
            "session.screen0.focusModel: ClickToFocus\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        return path

    def create(self, owner_subject: str = "", account: str = "") -> BrowserHandoff:
        """Start a browser desktop and return an unguessable, expiring token."""
        with self._lock:
            self.expire()
            token = secrets.token_urlsafe(32)
            display, vnc_port = self._next_slot()
            profile = self.root / "profiles" / token
            status_file = self.root / "status" / f"{token}.json"
            profile.mkdir(parents=True, exist_ok=False)
            status_file.parent.mkdir(parents=True, exist_ok=True)
            env = dict(
                os.environ,
                DISPLAY=f":{display}",
                GEMINI_HEADLESS="0",
                GEMINI_HANDOFF_TOKEN=token,
                GEMINI_HANDOFF_PROFILE=str(profile),
            )
            # x11vnc must not race Xvfb. Start it through a short wait loop so
            # a slow host cannot expose a handoff URL with a dead VNC target.
            vnc_command = [
                "/bin/sh", "-c",
                "until xdpyinfo -display \"$1\" >/dev/null 2>&1; do sleep 0.1; done; "
                "exec x11vnc -display \"$1\" -localhost -forever -nopw -rfbport \"$2\"",
                "handoff-vnc", f":{display}", str(vnc_port),
            ]
            wait_for_display = [
                "/bin/sh", "-c",
                "until xdpyinfo -display \"$1\" >/dev/null 2>&1; do sleep 0.1; done; "
                "shift; exec \"$@\"",
            ]
            # Use Fluxbox only as a lightweight window mapper. Its default
            # root command can invoke fbsetbg, so use a private config with no
            # root command and no toolbar.
            fluxbox_config = self._write_fluxbox_config()
            commands = [
                ["Xvfb", f":{display}", "-screen", "0", os.environ.get("GEMINI_BROWSER_SCREEN", "1440x900x24"), "-nolisten", "tcp"],
                vnc_command,
                [*wait_for_display, "handoff-fluxbox", f":{display}", "fluxbox", "-no-toolbar", "-rc", str(fluxbox_config)],
                [*wait_for_display, "handoff-browser", f":{display}", "uv", "run", "gemini-web-session", "--profile-dir", str(profile), "--headed", "--status-file", str(status_file)],
            ]
            processes: list[subprocess.Popen] = []
            session = BrowserHandoff(token, str(profile), display, vnc_port, time.time() + self.ttl_seconds, str(status_file), processes, owner_subject, account)
            self._write_manifest(session, [])
            try:
                for command in commands:
                    processes.append(self._runner(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
                    self._write_manifest(session)
            except Exception:
                for process in processes:
                    self._terminate(process)
                shutil.rmtree(profile, ignore_errors=True)
                self._manifest_path(token).unlink(missing_ok=True)
                raise
            self._sessions[token] = session
            self._write_tokens()
            return session

    def browser_url(self, session: BrowserHandoff) -> str:
        if not self.public_url:
            return ""
        parts = urlsplit(self.public_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["path"] = "websockify?" + urlencode({"token": session.token})
        query["sid"] = session.token
        return urlunsplit((parts.scheme, parts.netloc, parts.path or "/vnc.html", urlencode(query), parts.fragment))

    def is_ready(self, token: str) -> bool:
        with self._lock:
            session = self._sessions.get(token)
            if session is None or session.expires_at <= time.time():
                return False
            try:
                data = json.loads(Path(session.status_file).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return False
            return data.get("state") == "ready" and all(
                not hasattr(process, "poll") or process.poll() is None
                for process in session.processes
            )

    @staticmethod
    def _process_state(process: subprocess.Popen) -> str:
        if not hasattr(process, "poll"):
            return "unknown"
        return "running" if process.poll() is None else "exited"

    def status(self, token: str) -> dict[str, object]:
        """Return safe handoff state without exposing profile or cookie data."""
        with self._lock:
            session = self._sessions.get(token)
            if session is None or session.expires_at <= time.time():
                return {"state": "expired", "ready": False, "expires_at": 0}
            try:
                data = json.loads(Path(session.status_file).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            state = str(data.get("state", "starting"))
            components = {
                name: self._process_state(process)
                for name, process in zip(("xvfb", "vnc", "fluxbox", "chromium"), session.processes)
            }
            if state not in {"ready", "error", "stopped"} and "exited" in components.values():
                state = "error"
                data.setdefault("error", "browser_process_exited")
            result = {
                "state": state,
                "ready": state == "ready" and "exited" not in components.values(),
                "expires_at": session.expires_at,
                "components": components,
            }
            if data.get("error"):
                result["error"] = str(data["error"])
            return result

    def owner_for(self, token: str) -> str | None:
        with self._lock:
            session = self._sessions.get(token)
            if session is None or session.expires_at <= time.time():
                return None
            return session.owner_subject

    def consume(self, token: str, promote_to: str | Path | None = None) -> None:
        with self._lock:
            destination = None
            if promote_to is not None:
                destination = Path(promote_to).expanduser().resolve()
                if destination.exists():
                    raise FileExistsError("Gemini account profile already exists")
            session = self._sessions.pop(token, None)
            if session is None:
                raise ValueError("unknown or expired browser handoff")
            if promote_to is None:
                self._dispose(session)
            else:
                assert destination is not None
                destination.parent.mkdir(parents=True, exist_ok=True)
                for process in session.processes:
                    self._terminate(process)
                Path(session.status_file).unlink(missing_ok=True)
                shutil.move(session.profile, destination)
                self._manifest_path(token).unlink(missing_ok=True)
            self._write_tokens()

    def expire(self) -> None:
        for token, session in list(self._sessions.items()):
            if session.expires_at <= time.time():
                self._sessions.pop(token, None)
                self._dispose(session)
        if self.root.exists():
            self._write_tokens()

    def _dispose(self, session: BrowserHandoff) -> None:
        """Stop the desktop and remove its cookie-bearing Chromium profile."""
        for process in session.processes:
            self._terminate(process)
        try:
            Path(session.status_file).unlink(missing_ok=True)
        except OSError:
            pass
        shutil.rmtree(session.profile, ignore_errors=True)
        self._manifest_path(session.token).unlink(missing_ok=True)

    def shutdown(self) -> None:
        """Stop all desktops owned by this broker and revoke their tokens."""
        with self._lock:
            for session in list(self._sessions.values()):
                self._dispose(session)
            self._sessions.clear()
            self._write_tokens()

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            try:
                process.wait(timeout=0)
            except (AttributeError, OSError, subprocess.TimeoutExpired):
                pass
            return
        try:
            killpg = getattr(os, "killpg", None)
            if killpg is None:
                process.terminate()
            else:
                killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                process.terminate()
            except OSError:
                return
        wait = getattr(process, "wait", None)
        if wait is None:
            return
        try:
            wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                killpg = getattr(os, "killpg", None)
                if killpg is None:
                    kill = getattr(process, "kill", None)
                    if kill is None:
                        return
                    kill()
                else:
                    killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                try:
                    kill = getattr(process, "kill", None)
                    if kill is None:
                        return
                    kill()
                except OSError:
                    pass
            try:
                wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                # The child may be supervised by an external init process;
                # never block an OAuth request indefinitely on cleanup.
                pass

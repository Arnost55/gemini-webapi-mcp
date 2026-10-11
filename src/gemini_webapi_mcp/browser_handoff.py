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

    def create(self) -> BrowserHandoff:
        """Start a browser desktop and return an unguessable, expiring token."""
        with self._lock:
            self.expire()
            token = secrets.token_urlsafe(32)
            display, vnc_port = self._next_slot()
            profile = self.root / "profiles" / token
            status_file = self.root / "status" / f"{token}.json"
            profile.mkdir(parents=True, exist_ok=False)
            status_file.parent.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ, DISPLAY=f":{display}", GEMINI_HEADLESS="0")
            # x11vnc must not race Xvfb. Start it through a short wait loop so
            # a slow host cannot expose a handoff URL with a dead VNC target.
            vnc_command = [
                "/bin/sh", "-c",
                "until test -S /tmp/.X11-unix/X\"$1\"; do sleep 0.1; done; "
                "exec x11vnc -display \"$2\" -localhost -forever -nopw -rfbport \"$3\"",
                "handoff-vnc", str(display), f":{display}", str(vnc_port),
            ]
            commands = [
                ["Xvfb", f":{display}", "-screen", "0", os.environ.get("GEMINI_BROWSER_SCREEN", "1440x900x24"), "-nolisten", "tcp"],
                ["fluxbox"],
                vnc_command,
                ["uv", "run", "gemini-web-session", "--profile-dir", str(profile), "--headed", "--status-file", str(status_file)],
            ]
            processes: list[subprocess.Popen] = []
            try:
                for command in commands:
                    processes.append(self._runner(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
            except Exception:
                for process in processes:
                    self._terminate(process)
                shutil.rmtree(profile, ignore_errors=True)
                raise
            session = BrowserHandoff(token, str(profile), display, vnc_port, time.time() + self.ttl_seconds, str(status_file), processes)
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
            return data.get("state") == "ready"

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

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            try:
                process.wait(timeout=0)
            except (AttributeError, OSError, subprocess.TimeoutExpired):
                pass
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                process.terminate()
            except OSError:
                return
        try:
            process.wait(timeout=3)
        except (AttributeError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                process.wait(timeout=3)
            except (AttributeError, OSError, subprocess.TimeoutExpired):
                # The child may be supervised by an external init process;
                # never block an OAuth request indefinitely on cleanup.
                pass

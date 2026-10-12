"""Persistent Chromium session supervision for Gemini Web.

The manager deliberately reports cookie presence, never cookie values. Google
login is a one-time human bootstrap performed with ``--headed``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

SessionState = Literal["unknown", "ready", "login_required", "error", "stopped"]


def parse_env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable without silently accepting typos."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be one of true/false, 1/0, yes/no, or on/off")


@dataclass
class SessionStatus:
    state: SessionState = "unknown"
    checked_at: str | None = None
    last_valid_at: str | None = None
    has_psid: bool = False
    has_psidts: bool = False
    error: str | None = None

    def safe_dict(self) -> dict[str, object]:
        return asdict(self)


class AccountMapping:
    """Allow-list mapping OAuth subjects to isolated Gemini account profiles."""

    def __init__(self, mappings: dict[str, str]) -> None:
        self._mappings = dict(mappings)

    @classmethod
    def from_file(cls, path: str | Path) -> "AccountMapping":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        mappings = data.get("subjects", data)
        if not isinstance(mappings, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mappings.items()):
            raise ValueError("account mapping must be a JSON object of OAuth subjects to account names")
        return cls(mappings)

    def resolve(self, subject: str) -> str:
        """Resolve an OAuth subject or fail closed; never choose another account."""
        try:
            return self._mappings[subject]
        except KeyError as exc:
            raise PermissionError("OAuth subject is not mapped to a Gemini account") from exc

    def profile_for(self, subject: str, accounts_dir: str | Path) -> Path:
        account = self.resolve(subject)
        if Path(account).name != account or account in {"", ".", ".."}:
            raise ValueError("mapped account must be a simple profile name")
        return Path(accounts_dir) / account


def classify_gemini_page(url: str, body_text: str = "") -> SessionState:
    """Classify a page without inspecting or returning cookie values."""
    lowered_url = url.lower()
    lowered_body = body_text.lower()
    if any(
        marker in lowered_body
        for marker in (
            "this browser or app may not be secure",
            "couldn’t sign you in",
            "couldn't sign you in",
        )
    ):
        return "error"
    if any(marker in lowered_url for marker in ("/servicelogin", "/signin", "accounts.google.com")):
        return "login_required"
    if any(
        marker in lowered_body
        for marker in (
            "are you signed in",
            "sign in to continue",
            "before you continue to google",
            "we use cookies and data",
            "choose an account",
            "use another account",
            "sign in",
        )
    ):
        return "login_required"
    if "gemini.google.com" in lowered_url:
        return "ready"
    return "error"


class GeminiSessionManager:
    def __init__(self, profile_dir: str | Path, check_interval: float = 300, headless: bool | None = None,
                 login_url: str = "https://gemini.google.com/app") -> None:
        self.profile_dir = Path(profile_dir)
        self.check_interval = check_interval
        self.headless = os.environ.get("GEMINI_HEADLESS", "1") != "0" if headless is None else headless
        # Playwright defaults this option to false. Keep that default for
        # existing deployments and let hardened container deployments opt in.
        self.chromium_sandbox = parse_env_bool("GEMINI_CHROMIUM_SANDBOX", default=False)
        self.login_url = login_url
        self.browser_executable = os.environ.get("GEMINI_BROWSER_EXECUTABLE", "").strip() or None
        configured_channel = os.environ.get("GEMINI_BROWSER_CHANNEL")
        # Use the installed, supported Chrome channel by default. An explicit
        # executable takes precedence over the default channel, while an
        # explicitly configured channel still wins and is validated below.
        self.browser_channel = (
            configured_channel.strip()
            if configured_channel is not None
            else (None if self.browser_executable else "chrome")
        ) or None
        if self.browser_channel and self.browser_executable:
            raise ValueError("set only one of GEMINI_BROWSER_CHANNEL or GEMINI_BROWSER_EXECUTABLE")
        self.status_info = SessionStatus()
        self._playwright = None
        self._context = None
        self._page = None
        self._browser_process = None

    def browser_launch_options(self) -> dict[str, object]:
        """Build the single persistent-browser launch configuration.

        Playwright adds its control transport, including
        ``--remote-debugging-pipe``. Keep that transport under Playwright's
        control instead of supplying or removing it in application flags.
        """
        options: dict[str, object] = {
            "user_data_dir": str(self.profile_dir),
            "headless": self.headless,
            "chromium_sandbox": self.chromium_sandbox,
            "viewport": {"width": 1440, "height": 900},
            "args": [
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1440,900",
            ],
        }
        if self.browser_channel:
            options["channel"] = self.browser_channel
        if self.browser_executable:
            options["executable_path"] = self.browser_executable
        return options

    async def start(self) -> SessionStatus:
        if parse_env_bool("GEMINI_DIRECT_BROWSER", default=False):
            return await self._start_direct_browser()
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Install the browser extra and Chromium: uv sync --extra browser && uv run playwright install chromium") from exc
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        launch_options = self.browser_launch_options()
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                **launch_options,
            )
        except Exception as exc:
            await self.stop()
            raise RuntimeError(
                "The configured browser could not start with "
                f"GEMINI_CHROMIUM_SANDBOX={str(self.chromium_sandbox).lower()}. "
                "Install Google Chrome Stable or set GEMINI_BROWSER_EXECUTABLE to a supported browser; "
                "do not add --no-sandbox when sandboxing is enabled."
            ) from exc
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        return await self.check_now()

    async def _start_direct_browser(self) -> SessionStatus:
        """Launch ordinary Chrome; use the profile cookie DB for readiness."""
        executable = self.browser_executable or shutil.which("google-chrome") or shutil.which("google-chrome-stable")
        if not executable:
            raise RuntimeError("Google Chrome is not installed")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        command = [executable, f"--user-data-dir={self.profile_dir}", "--profile-directory=Default",
                   "--no-first-run", "--no-default-browser-check", "--disable-gpu",
                   "--window-size=1440,900", self.login_url]
        env = dict(os.environ)
        self._browser_process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return await self.check_now()

    async def check_now(self) -> SessionStatus:
        if self._browser_process is not None:
            return self._check_direct_browser()
        if self._page is None or self._context is None:
            raise RuntimeError("Session manager has not been started")
        try:
            await self._page.goto(self.login_url, wait_until="domcontentloaded", timeout=30_000)
            state = await self._classify()
            cookies = await self._context.cookies("https://gemini.google.com")
            names = {cookie["name"] for cookie in cookies}
            has_psid = any(name.endswith("PSID") for name in names)
            # A Gemini URL alone is not proof of authentication. In
            # particular, Google can render its consent screen at /app and a
            # transient DOM read after a reload can contain no login text.
            # Require the server-side session cookie before publishing ready.
            if state != "error" and has_psid:
                # Gemini may keep a generic sign-in label on an authenticated
                # page. A server-side PSID is stronger evidence than that text.
                state = "ready"
            elif state == "ready" and not has_psid:
                state = "login_required"
            now = datetime.now(timezone.utc).isoformat()
            self.status_info.state = state
            self.status_info.checked_at = now
            self.status_info.has_psid = has_psid
            self.status_info.has_psidts = "__Secure-1PSIDTS" in names
            self.status_info.error = None
            if state == "ready":
                self.status_info.last_valid_at = now
        except Exception as exc:
            self.status_info.state = "error"
            self.status_info.checked_at = datetime.now(timezone.utc).isoformat()
            self.status_info.error = type(exc).__name__
        return self.status_info

    def _check_direct_browser(self) -> SessionStatus:
        """Refresh cookie presence from the live Chrome profile, never values."""
        try:
            import browser_cookie3
            cookies = browser_cookie3.chrome(cookie_file=str(self.profile_dir / "Default" / "Cookies"), domain_name="gemini.google.com")
            names = {cookie.name for cookie in cookies if cookie.value}
            has_psid = "__Secure-1PSID" in names
            has_psidts = "__Secure-1PSIDTS" in names
            self.status_info.state = "ready" if has_psid else "login_required"
            self.status_info.has_psid = has_psid
            self.status_info.has_psidts = has_psidts
            self.status_info.error = None
        except Exception as exc:
            self.status_info.state = "error"
            self.status_info.error = type(exc).__name__
        self.status_info.checked_at = datetime.now(timezone.utc).isoformat()
        if self.status_info.state == "ready":
            self.status_info.last_valid_at = self.status_info.checked_at
        return self.status_info

    async def _classify(self) -> SessionState:
        body = await self._page.locator("body").inner_text(timeout=10_000)
        return classify_gemini_page(self._page.url, body)

    async def run(self) -> None:
        if self._page is None:
            await self.start()
        while True:
            await asyncio.sleep(self.check_interval)
            await self.check_now()

    async def stop(self) -> None:
        if self._browser_process is not None:
            if self._browser_process.poll() is None:
                self._browser_process.terminate()
                try:
                    self._browser_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._browser_process.kill()
            self._browser_process = None
        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self.status_info.state = "stopped"
        self._context = self._page = self._playwright = None


def write_safe_status(path: str | Path, status: SessionStatus) -> None:
    """Write state atomically without serialising cookies or browser storage."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(status.safe_dict(), separators=(",", ":")), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, output)


async def _main_async(args: argparse.Namespace) -> None:
    manager = GeminiSessionManager(args.profile_dir, args.interval, False if args.headed else None)
    try:
        status = await manager.start()
        if args.status_file:
            write_safe_status(args.status_file, status)
        print(status.safe_dict(), flush=True)
        if args.once:
            return
        while True:
            await asyncio.sleep(manager.check_interval)
            status = await manager.check_now()
            if args.status_file:
                write_safe_status(args.status_file, status)
    finally:
        await manager.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Keep a Gemini Web Chromium session warm")
    parser.add_argument("--profile-dir", default=os.environ.get("GEMINI_PROFILE_DIR", "./.gemini-chromium"))
    parser.add_argument("--interval", type=float, default=float(os.environ.get("GEMINI_SESSION_CHECK_INTERVAL", "300")))
    parser.add_argument("--headed", action="store_true", help="show Chromium for one-time human login")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    parser.add_argument("--status-file", help="write safe browser readiness state to this file")
    asyncio.run(_main_async(parser.parse_args()))


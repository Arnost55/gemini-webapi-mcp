"""Persistent Chromium session supervision for Gemini Web.

The manager deliberately reports cookie presence, never cookie values. Google
login is a one-time human bootstrap performed with ``--headed``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

SessionState = Literal["unknown", "ready", "login_required", "error", "stopped"]


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


def classify_gemini_page(url: str, body_text: str = "") -> SessionState:
    """Classify a page without inspecting or returning cookie values."""
    lowered_url = url.lower()
    lowered_body = body_text.lower()
    if any(marker in lowered_url for marker in ("/servicelogin", "/signin", "accounts.google.com")):
        return "login_required"
    if "are you signed in" in lowered_body or "sign in to continue" in lowered_body:
        return "login_required"
    if "gemini.google.com" in lowered_url:
        return "ready"
    return "error"


class GeminiSessionManager:
    def __init__(self, profile_dir: str | Path, check_interval: float = 300, headless: bool = True,
                 login_url: str = "https://gemini.google.com/app") -> None:
        self.profile_dir = Path(profile_dir)
        self.check_interval = check_interval
        self.headless = headless
        self.login_url = login_url
        self.status_info = SessionStatus()
        self._playwright = None
        self._context = None
        self._page = None

    async def start(self) -> SessionStatus:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Install the browser extra and Chromium: uv sync --extra browser && uv run playwright install chromium") from exc
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir), headless=self.headless,
            args=["--disable-dev-shm-usage"],
        )
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        return await self.check_now()

    async def check_now(self) -> SessionStatus:
        if self._page is None or self._context is None:
            raise RuntimeError("Session manager has not been started")
        try:
            await self._page.goto(self.login_url, wait_until="domcontentloaded", timeout=30_000)
            state = await self._classify()
            if state == "login_required":
                await self._page.reload(wait_until="domcontentloaded", timeout=30_000)
                state = await self._classify()
            cookies = await self._context.cookies("https://gemini.google.com")
            names = {cookie["name"] for cookie in cookies}
            now = datetime.now(timezone.utc).isoformat()
            self.status_info.state = state
            self.status_info.checked_at = now
            self.status_info.has_psid = "__Secure-1PSID" in names
            self.status_info.has_psidts = "__Secure-1PSIDTS" in names
            self.status_info.error = None
            if state == "ready":
                self.status_info.last_valid_at = now
        except Exception as exc:
            self.status_info.state = "error"
            self.status_info.checked_at = datetime.now(timezone.utc).isoformat()
            self.status_info.error = type(exc).__name__
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
        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self.status_info.state = "stopped"
        self._context = self._page = self._playwright = None


async def _main_async(args: argparse.Namespace) -> None:
    manager = GeminiSessionManager(args.profile_dir, args.interval, not args.headed)
    try:
        status = await manager.start()
        print(status.safe_dict(), flush=True)
        if args.once:
            return
        await manager.run()
    finally:
        await manager.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Keep a Gemini Web Chromium session warm")
    parser.add_argument("--profile-dir", default=os.environ.get("GEMINI_PROFILE_DIR", "./.gemini-chromium"))
    parser.add_argument("--interval", type=float, default=float(os.environ.get("GEMINI_SESSION_CHECK_INTERVAL", "300")))
    parser.add_argument("--headed", action="store_true", help="show Chromium for one-time human login")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    asyncio.run(_main_async(parser.parse_args()))


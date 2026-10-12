"""
MCP Server for Google Gemini via browser cookies.

Uses gemini_webapi library to access Gemini Web App for free,
without requiring paid API keys. Authentication is done through
browser cookies (__Secure-1PSID and __Secure-1PSIDTS).
"""

import asyncio
from collections import deque
import json
import logging
import os
import random
import re
import string
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, quote, urlsplit, urlunsplit

from mcp.server.fastmcp import FastMCP, Context
from .models import capabilities_json
from .oauth import LocalOAuth
from .browser_handoff import BrowserHandoffBroker
from .viewer import DEFAULT_RFB_URL, content_security_policy, viewer_page
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

# ---------------------------------------------------------------------------
# Logging (stderr only — stdout reserved for MCP stdio transport)
# ---------------------------------------------------------------------------
logger = logging.getLogger("gemini_mcp")
logger.addHandler(logging.StreamHandler(sys.stderr))
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
IMAGES_DIR = Path.home() / "Pictures" / "gemini"
DEFAULT_MODEL = "gemini-3.0-flash"

# Hard ceiling for a single image-generation call (seconds). The StreamGenerate
# response via gemini_webapi is erratically slow (measured 22s..345s for the
# same prompt) while the same account generates in ~10s in the browser — the
# library's stall-watchdog + @running(retry=5) backoff can otherwise stretch a
# call to minutes. We cap the whole call so it either returns or fails fast with
# a clear message instead of hanging the MCP client. This is an upper bound;
# fast generations still return as soon as Gemini responds.
#
# 600s is sized for the worst case: image-edit on a portrait reference with a
# dense layout (text/logos/details). Such edits routinely stall 3–4× × 45s
# inside Nano Banana 2 before yielding the result (~210s wall-time observed).
# Override with GEMINI_GEN_TIMEOUT.
GEN_TIMEOUT = float(os.environ.get("GEMINI_GEN_TIMEOUT", "600"))
# Per-network-op timeout for image download in image.save() (seconds). Without
# this the CDN fetch of the full-size (=s0, ~4.5MB) image can hang forever.
DOWNLOAD_TIMEOUT = float(os.environ.get("GEMINI_DOWNLOAD_TIMEOUT", "60"))

# Optional stage timing: set GEMINI_DEBUG_TIMING=1 to log per-stage wall-time.
_DEBUG_TIMING = os.environ.get("GEMINI_DEBUG_TIMING") == "1"
# c8o8Fe 2x-upscale RPC gives full-resolution downloads (~1792×2390 for portrait
# vs ~896×1200 preview). Costs ~20s/image extra; disable with GEMINI_SKIP_2X=1.
_SKIP_2X = os.environ.get("GEMINI_SKIP_2X", "0") != "0"
# Skip the model-id remap + version bump in patched_request (which forces the
# slow thinking-advanced model). Set to "1" to send the requested model as-is.
_NO_REMAP = os.environ.get("GEMINI_NO_REMAP") == "1"


def _stage(label: str, t0: float) -> float:
    """Log elapsed seconds since t0 for a named stage when timing is enabled."""
    import time
    now = time.monotonic()
    if _DEBUG_TIMING:
        logger.info("[timing] %s: %.2fs", label, now - t0)
    return now

# ---------------------------------------------------------------------------
# Watermark removal — self-calibrating reverse alpha-blend.
#
# Gemini stamps a small neutral-white "✦" sparkle near the bottom-right corner.
# Its POSITION (margin from the corner), SIZE and OPACITY are NOT fixed — they
# drift with the generation pipeline AND with the model's auto-chosen native
# resolution. (txt2img landscape frames now carry the mark at margin≈96 /
# alpha≈0.27 — the slot the edit pipeline uses — while older portrait frames sat
# at margin≈32 / alpha≈0.50.) Hardcoding those constants stamped a dark ✦ ghost
# the moment Google moved the mark, so we no longer assume them.
#
# Instead we MEASURE everything from the frame, using the one invariant — the ✦
# SHAPE (assets/wm_alpha_edit.npy, normalized to 0..1):
#   1. localize — scan shape-correlation over position + scale in the corner;
#   2. estimate — least-squares fit the per-image opacity A of the white overlay
#      (L = bg + A·shape·(255-bg); background from a plane fit);
#   3. subtract — reverse the alpha-blend with alpha_map = shape·A, premult =
#      alpha_map·255  →  original = (watermarked - premult) / (1 - alpha_map);
#   4. self-check — if the spot now holds an inverse ghost, revert.
# Background-independent (clean on charcoal AND white) and resolution-independent
# (any native aspect ratio). Honours GEMINI_WM_KEEP=1 as a diagnostics kill-switch.
# ---------------------------------------------------------------------------
_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_WHITE = 255.0                        # the ✦ is a neutral-white overlay
_ANCHOR_MARGINS = (96, 32)            # the two corner slots Gemini stamps the ✦ at
_CORR_GHOST = -0.30                   # post-removal corr below this → revert (would ghost)
_CORR_WORSE = 0.10                    # post-removal |corr| may not grow beyond this
_WM_DETECT_CORR = 0.30                # calibrated-map correlation required to remove
_wm_shape = None                      # cache: normalized ✦ shape (0..1)


def _load_wm_shape():
    """Normalized ✦ shape (0..1) — the single calibrated invariant."""
    global _wm_shape
    import numpy as np
    if _wm_shape is None:
        alpha = np.load(_ASSETS_DIR / "wm_alpha_edit.npy").astype(np.float32)
        _wm_shape = alpha / float(alpha.max())
    return _wm_shape


def _load_wm_map(size: int, orientation: str = "landscape"):
    """Compatibility loader for the calibrated synthetic watermark maps.

    Older integrations and the regression fixtures use the original alpha and
    premultiplied maps.  Keep that diagnostic API backed by the shipped assets
    while the production remover uses the normalized shape above.
    """
    import numpy as np
    from PIL import Image

    suffix = "txt" if orientation == "portrait" else "edit"
    alpha = np.load(_ASSETS_DIR / f"wm_alpha_{suffix}.npy").astype(np.float32)
    premult = np.load(_ASSETS_DIR / f"wm_premult_{suffix}.npy").astype(np.float32)
    if alpha.shape[0] != size:
        alpha = np.asarray(Image.fromarray(alpha).resize((size, size), Image.BILINEAR), dtype=np.float32)
        premult = np.asarray(Image.fromarray(premult[..., 0]).resize((size, size), Image.BILINEAR), dtype=np.float32)[..., None]
    return alpha, premult


def _shape_at(logo):
    """The ✦ shape bilinearly resized to `logo` px."""
    import numpy as np
    from PIL import Image
    shape = _load_wm_shape()
    if logo == shape.shape[0]:
        return shape
    return np.asarray(Image.fromarray((shape * 255).astype("uint8")).resize(
        (logo, logo), Image.BILINEAR), np.float32) / 255.0


def _plane_bg(g, shape):
    """Least-squares background plane fit over pixels outside the ✦ (shape≈0)."""
    import numpy as np
    m = shape < 0.06
    ys, xs = np.mgrid[0:g.shape[0], 0:g.shape[1]]
    A = np.c_[xs[m], ys[m], np.ones(int(m.sum()))]
    coef, *_ = np.linalg.lstsq(A, g[m], rcond=None)
    return coef[0] * xs + coef[1] * ys + coef[2]


def _star_corr(box, shape):
    """Correlation of background-subtracted luminance with the ✦ shape.

    ~+1 = bright sparkle present, ~0 = clean background, strongly negative = a
    dark ghost (e.g. over-subtraction). Background-robust via a plane fit.
    """
    import numpy as np
    g = box.mean(2)
    d = g - _plane_bg(g, shape)
    core = shape > 0.15
    dv = d[core] - d[core].mean()
    sv = shape[core] - shape[core].mean()
    denom = float(np.sqrt((dv ** 2).sum() * (sv ** 2).sum()))
    return float((dv * sv).sum() / denom) if denom > 1e-9 else 0.0


def _reverse_at(arr, x0, y0, logo, alpha, premult):
    """Reverse the alpha-blend in arr[y0:y0+logo, x0:x0+logo] in place."""
    import numpy as np
    box = arr[y0:y0 + logo, x0:x0 + logo]
    inv = np.clip(1.0 - alpha, 1e-3, 1.0)[..., None]
    arr[y0:y0 + logo, x0:x0 + logo] = np.clip((box - premult) / inv, 0, 255)


def _estimate_alpha(arr, x0, y0, logo):
    """Least-squares per-image opacity A of the white ✦ overlay, from the frame.

    Model: L = bg + A·shape·(255-bg)  ⇒  A = <k, L-bg> / <k, k>, k = shape·(255-bg).
    Fits over all star pixels (shape>0) so partial-coverage edges constrain A too.
    """
    import numpy as np
    sh = _shape_at(logo)
    L = arr[y0:y0 + logo, x0:x0 + logo].mean(2)
    bg = _plane_bg(L, sh)
    k = sh * (_WHITE - bg)
    m = sh > 0.02
    denom = float((k[m] ** 2).sum())
    if denom < 1e-6:
        return None
    A = float((k[m] * (L[m] - bg[m])).sum() / denom)
    return float(np.clip(A, 0.0, 0.97))


def _remove_watermark(image_path: str, upscaled: bool = False) -> Optional[bool]:
    """Remove the Gemini ✦ from image_path in place by reverse alpha-blend.

    The mark is stamped at one of two fixed corner anchors (margin 96 or 32 from
    the bottom-right) — absolute offsets that don't depend on resolution. We
    process BOTH anchors deterministically rather than detecting the mark by
    correlation: a weak ✦ on a bright/textured background (e.g. snow) has a tiny
    correlation that no threshold can separate from false matches, but its
    per-image opacity still measures correctly. On the empty anchor the fitted
    opacity is ≈0, so the subtraction is a near no-op. A self-check reverts any
    anchor whose removal would ghost (dark inverse) or otherwise grow |corr| —
    so subtracting at an empty/textured anchor can never make things worse.

    upscaled doubles the anchors/logo for 2x frames. Returns True if anything was
    removed, else None. Honours GEMINI_WM_KEEP=1.
    """
    if os.environ.get("GEMINI_WM_KEEP") == "1":
        return None
    try:
        import numpy as np
        from PIL import Image
    except Exception as exc:
        logger.warning("numpy/PIL unavailable — watermark NOT removed: %s", exc)
        return None
    try:
        img = Image.open(image_path).convert("RGB")
        w, h = img.size
        arr = np.asarray(img, np.float32).copy()
    except Exception as exc:
        logger.warning("watermark removal failed to open %s: %s", image_path, exc)
        return None

    try:
        logo = 96 if upscaled else 48
        margins = [m * 2 for m in _ANCHOR_MARGINS] if upscaled else list(_ANCHOR_MARGINS)
        if not upscaled:
            # The web client has used both the original 48 px mark and a 96 px
            # rendition, with margins that have changed independently.  Score
            # the finite set of shipped calibrated maps first.  This avoids the
            # old wandering argmax and lets a clean textured corner fail closed.
            candidates = []
            for candidate_size in (96, 48):
                for margin in (32, 64, 96, 192):
                    x0, y0 = w - margin - candidate_size, h - margin - candidate_size
                    if x0 < 0 or y0 < 0 or x0 + candidate_size > w or y0 + candidate_size > h:
                        continue
                    box = arr[y0:y0 + candidate_size, x0:x0 + candidate_size].mean(2)
                    for orientation in ("portrait", "landscape"):
                        alpha, premult = _load_wm_map(candidate_size, orientation)
                        t, q = alpha - alpha.mean(), box - box.mean()
                        corr = float((t * q).sum() / (np.sqrt((t * t).sum() * (q * q).sum()) + 1e-6))
                        candidates.append((corr, x0, y0, candidate_size, margin, alpha, premult))
            best = max(candidates, key=lambda item: item[0], default=None)
            if best is None or best[0] < _WM_DETECT_CORR:
                return None
            corr, x0, y0, candidate_size, margin, alpha, premult = best
            box = arr[y0:y0 + candidate_size, x0:x0 + candidate_size]
            before = arr.copy()
            inv = np.clip(1.0 - alpha, 1e-3, 1.0)[..., None]
            arr[y0:y0 + candidate_size, x0:x0 + candidate_size] = np.clip((box - premult) / inv, 0, 255)
            after_box = arr[y0:y0 + candidate_size, x0:x0 + candidate_size].mean(2)
            t, q = alpha - alpha.mean(), after_box - after_box.mean()
            after_corr = float((t * q).sum() / (np.sqrt((t * t).sum() * (q * q).sum()) + 1e-6))
            # A mathematically exact reverse blend can produce a tiny negative
            # NCC from uint8 quantisation and textured backgrounds.  Keep a
            # margin below the documented ghost threshold so that exact
            # calibrated reversals are not rejected at the boundary.
            if after_corr > corr or after_corr < -0.35:
                arr[:] = before
                return None
            Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(image_path)
            logger.info("✦ removed at m%d (logo %d corr %.2f→%.2f): %s", margin, candidate_size, corr, after_corr, image_path)
            return True
        sh = _shape_at(logo)
        removed = False
        for margin in margins:
            x0, y0 = w - margin - logo, h - margin - logo
            if x0 < 0 or y0 < 0:
                continue
            box = arr[y0:y0 + logo, x0:x0 + logo]
            if box.shape[0] != logo or box.shape[1] != logo:
                continue
            A = _estimate_alpha(arr, x0, y0, logo)
            if A is None or A <= 0.0:
                continue                          # no overlay here (fit opacity ≈0)
            before_corr = _star_corr(box, sh)
            before = arr.copy()
            alpha = sh * A
            premult = (alpha * _WHITE)[..., None]
            _reverse_at(arr, x0, y0, logo, alpha, premult)
            after = _star_corr(arr[y0:y0 + logo, x0:x0 + logo], sh)
            if after < _CORR_GHOST or abs(after) > abs(before_corr) + _CORR_WORSE:
                arr[:] = before                   # would ghost/worsen — revert this anchor
                logger.info("✦ anchor m%d reverted (corr %.2f→%.2f): %s",
                            margin, before_corr, after, image_path)
                continue
            removed = True
            logger.info("✦ removed at m%d (logo %d A %.2f, corr %.2f→%.2f): %s",
                        margin, logo, A, before_corr, after, image_path)
        if removed:
            Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(image_path)
            return True
        return None
    except Exception as exc:
        logger.warning("watermark removal failed on %s: %s", image_path, exc)
        return None


# ---------------------------------------------------------------------------
# Cookie resolution: env vars → browser-cookie3 → error
# ---------------------------------------------------------------------------

def _resolve_cookies() -> tuple[str, str]:
    """Resolve Gemini auth cookies with clear priority chain.

    1. Environment variables GEMINI_PSID / GEMINI_PSIDTS (explicit override).
    2. Chrome browser cookies via browser-cookie3 (automatic).
    3. RuntimeError with actionable instructions.

    Returns (psid, psidts). psidts may be empty.
    Cookie values are never logged.
    """
    # --- Priority 1: explicit env vars ---
    psid = os.environ.get("GEMINI_PSID", "")
    psidts = os.environ.get("GEMINI_PSIDTS", "")
    if psid:
        logger.info("Using Gemini cookies from environment variables")
        return psid, psidts

    # --- Priority 2: Chrome browser cookies ---
    try:
        import browser_cookie3

        cookie_file = os.environ.get("CHROME_COOKIE_FILE") or None
        cj = browser_cookie3.chrome(domain_name=".google.com", cookie_file=cookie_file)
        for cookie in cj:
            # domain_name is a substring filter: `.google.com.bd` etc. also match,
            # and their session cookies are not valid for gemini.google.com.
            if cookie.domain != ".google.com":
                continue
            if cookie.name == "__Secure-1PSID" and cookie.value:
                psid = cookie.value
            elif cookie.name == "__Secure-1PSIDTS" and cookie.value:
                psidts = cookie.value
        if psid:
            logger.info("Using Gemini cookies from Chrome browser")
            return psid, psidts
        logger.warning("browser-cookie3: no __Secure-1PSID cookie found in Chrome")
    except ImportError:
        logger.warning("browser-cookie3 not installed — cannot read cookies from Chrome")
    except Exception as exc:
        logger.warning("browser-cookie3: failed to read Chrome cookies — %s", type(exc).__name__)

    # --- Nothing worked ---
    raise RuntimeError(
        "Gemini cookies not found. Options:\n"
        "  1. Log into gemini.google.com in Chrome and install browser-cookie3, or\n"
        "  2. Set GEMINI_PSID (and optionally GEMINI_PSIDTS) environment variables."
    )


def _make_gen_id() -> str:
    """Generate a client-side gen_id for c8o8Fe RPC (16-char repeating pattern)."""
    base = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return (base * 3)[:16]


# ---------------------------------------------------------------------------
# Account-scoped client lifecycle
# ---------------------------------------------------------------------------

class _AccountClientManager:
    """Create and cache Gemini clients only after account authorization.

    A client is keyed by the authenticated OAuth subject and its persisted
    account selection.  There is deliberately no process-wide cookie fallback:
    an HTTP request without an owned, selected account cannot reach Gemini.
    """

    def __init__(self) -> None:
        self.clients: dict[tuple[str, str], object] = {}
        self.sessions: dict[tuple[str, str, str], object] = {}
        self._rate_windows: dict[tuple[str, str], deque[float]] = {}
        self._global_rate_window: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._cookie_pairs: dict[tuple[str, str], tuple[str, str | None]] = {}
        self._profiles: dict[tuple[str, str], Path] = {}
        self._refresh_task: asyncio.Task | None = None

    def _allow_request(self, subject: str, account: str, requests_per_minute: int) -> None:
        now = time.monotonic()
        cutoff = now - 60.0
        global_limit = int(os.environ.get("GEMINI_GLOBAL_REQUESTS_PER_MINUTE", "0"))
        while self._global_rate_window and self._global_rate_window[0] <= cutoff:
            self._global_rate_window.popleft()
        if global_limit > 0 and len(self._global_rate_window) >= global_limit:
            raise PermissionError("Gemini server request rate limit exceeded")
        key = (subject, account)
        window = self._rate_windows.setdefault(key, deque())
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) >= requests_per_minute:
            raise PermissionError("Gemini account request rate limit exceeded")
        window.append(now)
        self._global_rate_window.append(now)

    @staticmethod
    def _profile(record: dict) -> Path:
        root = Path(os.environ.get("GEMINI_PROFILE_ROOT", "/var/lib/gemini-mcp/accounts")).expanduser().resolve()
        candidate = Path(str(record.get("profile", ""))).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PermissionError("Gemini account profile is outside GEMINI_PROFILE_ROOT") from exc
        if not candidate.is_dir():
            raise RuntimeError("selected Gemini account profile is not available")
        return candidate

    @staticmethod
    def _account(ctx: Context) -> tuple[str, dict]:
        from .registry import DynamicAccountRegistry
        path = os.environ.get("GEMINI_REGISTRY_PATH")
        key = _configured_registry_key()
        if not path or not key:
            raise RuntimeError("encrypted account registry is not configured")
        subject = _oauth_subject(ctx)
        record = DynamicAccountRegistry(path, key).selected(subject)
        return subject, record

    @staticmethod
    def _cookies(profile: Path) -> tuple[str, str | None]:
        try:
            import browser_cookie3
            cookies = browser_cookie3.chrome(
                cookie_file=str(profile / "Default" / "Cookies"),
                domain_name="gemini.google.com",
            )
        except Exception as exc:
            raise RuntimeError("cannot read cookies for the selected Gemini account") from exc
        psid = psidts = None
        for cookie in cookies:
            if cookie.name == "__Secure-1PSID" and cookie.value:
                psid = cookie.value
            elif cookie.name == "__Secure-1PSIDTS" and cookie.value:
                psidts = cookie.value
        if not psid:
            raise RuntimeError("selected Gemini account is not logged in")
        return psid, psidts

    async def _build_client(self, key: tuple[str, str], profile: Path):
        from gemini_webapi import GeminiClient
        psid, psidts = self._cookies(profile)
        client = GeminiClient(secure_1psid=psid, secure_1psidts=psidts or None)
        await client.init(timeout=300, watchdog_timeout=45, auto_close=False, auto_refresh=True)
        _patch_client(client)
        self._cookie_pairs[key] = (psid, psidts)
        self._profiles[key] = profile
        return client

    async def refresh_cookies(self) -> None:
        async with self._lock:
            for key, old_client in list(self.clients.items()):
                profile = self._profiles.get(key)
                if profile is None:
                    continue
                try:
                    pair = self._cookies(profile)
                    if pair == self._cookie_pairs.get(key):
                        continue
                    new_client = await self._build_client(key, profile)
                    await old_client.close()
                    self.clients[key] = new_client
                except Exception:
                    logger.warning("Gemini cookie refresh failed for account %s", key[1], exc_info=True)

    async def _refresh_loop(self) -> None:
        interval = max(60, int(os.environ.get("GEMINI_COOKIE_REFRESH_SECONDS", "300")))
        while True:
            await asyncio.sleep(interval)
            await self.refresh_cookies()

    def start_refresh_loop(self) -> None:
        if self._refresh_task is None:
            self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def get(self, ctx: Context):
        from gemini_webapi import GeminiClient
        subject, record = self._account(ctx)
        account = str(record["account"])
        key = (subject, account)
        async with self._lock:
            self._allow_request(subject, account, int(record.get("requests_per_minute", 30)))
            client = self.clients.get(key)
            if client is not None:
                return client
            client = await self._build_client(key, self._profile(record))
            self.clients[key] = client
            return client

    def session_key(self, ctx: Context, session_id: str) -> tuple[str, str, str]:
        subject, record = self._account(ctx)
        return subject, str(record["account"]), session_id

    def media_dir(self, ctx: Context) -> Path:
        _, record = self._account(ctx)
        root = Path(os.environ.get("GEMINI_MEDIA_ROOT", str(IMAGES_DIR))).expanduser().resolve()
        account_dir = (root / str(record["account"])).resolve()
        try:
            account_dir.relative_to(root)
        except ValueError as exc:
            raise PermissionError("Gemini account media directory is outside GEMINI_MEDIA_ROOT") from exc
        account_dir.mkdir(parents=True, exist_ok=True)
        return account_dir

    async def close(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None
        for client in list(self.clients.values()):
            try:
                await client.close()
            except Exception:
                logger.warning("failed to close account-scoped Gemini client", exc_info=True)
        self.clients.clear()
        self.sessions.clear()

@asynccontextmanager
async def app_lifespan(server):
    manager = _AccountClientManager()
    manager.start_refresh_loop()
    _handoff_broker.revoke_stale_tokens()
    try:
        yield {"account_clients": manager}
    finally:
        await manager.close()
        _handoff_broker.shutdown()


mcp = FastMCP("gemini-webapi-mcp", lifespan=app_lifespan)

# The public reverse-proxy origin is expected to be configured explicitly.
# Keep localhost entries for local development and add the configured host so
# the SDK's DNS-rebinding protection does not reject the public MCP URL.
_public_url = os.environ.get("GEMINI_PUBLIC_URL", "").rstrip("/")
if _public_url:
    from urllib.parse import urlparse
    _public_origin = urlparse(_public_url)
    _public_host = _public_origin.hostname
    if _public_host:
        _public_port = _public_origin.port or (443 if _public_origin.scheme == "https" else 80)
        mcp.settings.transport_security.allowed_hosts.append(_public_host)
        mcp.settings.transport_security.allowed_hosts.append(f"{_public_host}:{_public_port}")
        mcp.settings.transport_security.allowed_origins.append(_public_url)

# Local OAuth is intentionally independent from Google authentication. It
# authenticates the MCP client; a separately isolated browser desktop links a
# Gemini account. The noVNC token is opaque and is never an OAuth code.
_handoff_broker = BrowserHandoffBroker(
    os.environ.get("GEMINI_HANDOFF_ROOT", "/var/lib/gemini-mcp/handoffs"),
    public_url=os.environ.get("GEMINI_NOVNC_PUBLIC_URL", ""),
    ttl_seconds=int(os.environ.get("GEMINI_HANDOFF_TTL", "300")),
)


def _viewer_ws_url() -> str:
    configured = os.environ.get("GEMINI_NOVNC_WS_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    public = os.environ.get("GEMINI_NOVNC_PUBLIC_URL", "").strip()
    if not public:
        return ""
    parsed = urlsplit(public)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/websockify"


def _new_browser_handoff(subject: str = "", account: str = "") -> tuple[str, str]:
    handoff = _handoff_broker.create(owner_subject=subject, account=account)
    public = os.environ.get("GEMINI_PUBLIC_URL", "").rstrip("/")
    if public:
        return handoff.token, f"{public}/auth/browser/viewer?sid={quote(handoff.token, safe='')}"
    return handoff.token, _handoff_broker.browser_url(handoff)


def _configured_registry_key() -> str | None:
    """Load a registry key from an environment variable or Docker secret file."""
    direct = os.environ.get("GEMINI_REGISTRY_KEY")
    if direct:
        return direct.strip()
    path = os.environ.get("GEMINI_REGISTRY_KEY_FILE")
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="ascii").strip()
    except OSError:
        logger.warning("Gemini account registry key file is unavailable")
        return None


def _consume_browser_handoff(token: str, subject: str, account: str) -> None:
    """Promote one completed browser profile into its owner-bound account."""
    from .registry import DynamicAccountRegistry

    registry_path = os.environ.get("GEMINI_REGISTRY_PATH")
    registry_key = _configured_registry_key()
    if not registry_path or not registry_key:
        raise RuntimeError("encrypted account registry is not configured")
    profile_root = Path(os.environ.get("GEMINI_PROFILE_ROOT", "/var/lib/gemini-mcp/accounts")).expanduser().resolve()
    destination = profile_root / account
    try:
        destination.relative_to(profile_root)
    except ValueError as exc:
        raise PermissionError("invalid Gemini account profile name") from exc
    registry = DynamicAccountRegistry(registry_path, registry_key)
    registry.register(
        subject=subject,
        account=account,
        profile=account,
        scopes={"chat", "image", "upload", "url"},
        requests_per_minute=int(os.environ.get("GEMINI_ACCOUNT_RPM", "30")),
        state="pending",
    )
    try:
        _handoff_broker.consume(token, promote_to=destination)
        registry.set_state(account, "ready")
    except Exception:
        try:
            registry.set_state(account, "revoked")
        except Exception:
            logger.warning("failed to revoke incomplete Gemini account registration", exc_info=True)
        raise


_oauth = LocalOAuth(
    os.environ.get("GEMINI_PUBLIC_URL", "http://localhost:8000"),
    state_path=os.environ.get("GEMINI_OAUTH_STATE_PATH", "/var/lib/gemini-mcp/oauth-state.json"),
    handoff_url_factory=_new_browser_handoff if os.environ.get("GEMINI_PUBLIC_URL") else None,
    handoff_ready=_handoff_broker.is_ready,
    handoff_consume=_consume_browser_handoff,
)


def _oauth_subject(ctx: Context) -> str:
    """Resolve the authenticated bearer token without a local-user fallback."""
    try:
        request = ctx.request_context.request
        header = request.headers.get("authorization", "")
    except AttributeError:
        header = ""
    scheme, _, token = header.partition(" ")
    subject = _oauth.subject_for(token) if scheme.lower() == "bearer" and token else None
    if not subject:
        raise PermissionError("an authenticated OAuth bearer token is required")
    return subject


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def oauth_resource_metadata(request: Request):
    issuer = _oauth.issuer
    return JSONResponse({"resource": issuer + "/mcp", "authorization_servers": [issuer], "scopes_supported": ["openid", "profile"]})


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request):
    """Return a dependency-free liveness response for reverse proxies."""
    return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})


@mcp.custom_route("/.well-known/oauth-protected-resource/mcp", methods=["GET"])
@mcp.custom_route("/mcp/.well-known/oauth-protected-resource", methods=["GET"])
async def oauth_resource_metadata_alias(request: Request):
    """Compatibility aliases used by MCP clients during discovery."""
    return await oauth_resource_metadata(request)


@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def oauth_server_metadata(request: Request):
    issuer = _oauth.issuer
    return JSONResponse({
        "issuer": issuer,
        "authorization_endpoint": issuer + "/oauth/authorize",
        "token_endpoint": issuer + "/oauth/token",
        "registration_endpoint": issuer + "/oauth/register",
        "scopes_supported": ["openid", "profile"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    })


@mcp.custom_route("/.well-known/openid-configuration", methods=["GET"])
async def openid_configuration(request: Request):
    """OIDC-style discovery alias used by clients that probe OpenID first."""
    return await oauth_server_metadata(request)


@mcp.custom_route("/oauth/register", methods=["POST"])
async def oauth_register(request: Request):
    try:
        return JSONResponse(_oauth.register(await request.json()), status_code=201)
    except (ValueError, TypeError) as exc:
        return JSONResponse({"error": "invalid_client_metadata", "error_description": str(exc)}, status_code=400)


@mcp.custom_route("/oauth/authorize", methods=["GET"])
async def oauth_authorize(request: Request):
    q = request.query_params
    try:
        page = _oauth.authorize_page(q["client_id"], q["redirect_uri"], q["code_challenge"], q.get("state", ""))
        return HTMLResponse(page)
    except (KeyError, ValueError) as exc:
        return PlainTextResponse(str(exc), status_code=400)


def _viewer_ancestors() -> list[str]:
    raw = os.environ.get("GEMINI_VIEWER_FRAME_ANCESTORS", "'self'")
    values = [item.strip() for item in raw.split() if item.strip()]
    # Only accept explicit origins or the CSP self keyword.  Paths, schemes
    # without hosts, and arbitrary tokens must not become frame policies.
    valid = []
    for value in values:
        if value == "'self'":
            valid.append(value)
            continue
        parsed = urlsplit(value)
        if parsed.scheme in {"https", "http"} and parsed.netloc and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment:
            valid.append(value.rstrip("/"))
    return valid or ["'self'"]


def _viewer_origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    allowed = set(_viewer_ancestors())
    if "'self'" in allowed:
        allowed.add(_oauth.issuer)
    return origin.rstrip("/") in {item.rstrip("/") for item in allowed}


@mcp.custom_route("/auth/browser/viewer", methods=["GET"])
async def browser_viewer(request: Request):
    if not _viewer_origin_allowed(request):
        return PlainTextResponse("viewer origin is not allowed", status_code=403)
    sid = request.query_params.get("sid", "")
    if not sid or len(sid) > 128:
        return PlainTextResponse("invalid browser handoff", status_code=400)
    state = _handoff_broker.status(sid)
    if state["state"] == "expired":
        return PlainTextResponse("browser handoff expired", status_code=410)
    ws_url = _viewer_ws_url()
    if not ws_url:
        return PlainTextResponse("embedded viewer is not configured", status_code=503)
    rfb_url = os.environ.get("GEMINI_VIEWER_RFB_URL", DEFAULT_RFB_URL)
    body = viewer_page(sid=sid, ws_url=ws_url, expires_at=float(state["expires_at"]), rfb_url=rfb_url)
    headers = {
        "Content-Security-Policy": content_security_policy(rfb_url, ws_url, _viewer_ancestors()),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    }
    return HTMLResponse(body, headers=headers)


@mcp.custom_route("/auth/browser/status", methods=["GET"])
async def browser_status(request: Request):
    sid = request.query_params.get("sid", "")
    if not sid or len(sid) > 128:
        return JSONResponse({"error": "invalid browser handoff"}, status_code=400)
    state = _handoff_broker.status(sid)
    return JSONResponse(state, status_code=200 if state["state"] != "expired" else 410, headers={"Cache-Control": "no-store"})


@mcp.custom_route("/auth/browser/verify", methods=["POST"])
async def browser_verify(request: Request):
    """Force an immediate safe readiness check for the manual viewer button."""
    if not _viewer_origin_allowed(request):
        return JSONResponse({"error": "viewer origin is not allowed"}, status_code=403)
    sid = request.query_params.get("sid", "")
    if not sid or len(sid) > 128:
        return JSONResponse({"error": "invalid browser handoff"}, status_code=400)
    state = _handoff_broker.verify(sid)
    if state["state"] == "expired":
        return JSONResponse(state, status_code=410, headers={"Cache-Control": "no-store"})
    return JSONResponse(state, headers={"Cache-Control": "no-store"})

@mcp.custom_route("/auth/browser/cancel", methods=["POST"])
async def browser_cancel(request: Request):
    if not _viewer_origin_allowed(request):
        return JSONResponse({"error": "viewer origin is not allowed"}, status_code=403)
    sid = request.query_params.get("sid", "")
    if not sid or len(sid) > 128:
        return JSONResponse({"error": "invalid browser handoff"}, status_code=400)
    try:
        _handoff_broker.consume(sid)
    except ValueError:
        return JSONResponse({"error": "expired or unknown browser handoff"}, status_code=410)
    return JSONResponse({"cancelled": True}, headers={"Cache-Control": "no-store"})


@mcp.custom_route("/oauth/complete", methods=["GET"])
async def oauth_complete(request: Request):
    """Finish the browser handoff, then redirect with the one-time code."""
    try:
        state = request.query_params["state"]
        target = _oauth.completion_redirect(request.query_params["code"], state)
        parts = urlsplit(target)
        query = parse_qsl(parts.query, keep_blank_values=True)
        query.append(("state", state))
        target = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        return RedirectResponse(target, status_code=302)
    except (KeyError, ValueError) as exc:
        return PlainTextResponse(str(exc), status_code=400)


@mcp.custom_route("/oauth/token", methods=["POST"])
async def oauth_token(request: Request):
    form = await request.form()
    try:
        grant_type = form.get("grant_type")
        if grant_type == "authorization_code":
            result = _oauth.exchange(str(form["code"]), str(form["client_id"]), str(form["redirect_uri"]), str(form["code_verifier"]))
        elif grant_type == "refresh_token":
            result = _oauth.refresh(str(form["refresh_token"]), str(form.get("client_id", "")) or None)
        else:
            raise ValueError("unsupported grant_type")
        return JSONResponse(result)
    except (KeyError, ValueError) as exc:
        return JSONResponse({"error": "invalid_grant", "error_description": str(exc)}, status_code=400)


@mcp.tool(name="gemini_list_accounts", annotations={"title": "List Connected Gemini Accounts", "readOnlyHint": True})
async def gemini_list_accounts(ctx: Context) -> dict:
    """List safe metadata for Gemini accounts connected to this MCP identity."""
    from .registry import DynamicAccountRegistry
    path = os.environ.get("GEMINI_REGISTRY_PATH")
    key = _configured_registry_key()
    if not path or not key:
        return {"accounts": [], "warning": "account_registry_not_configured"}
    subject = _oauth_subject(ctx)
    return {"accounts": DynamicAccountRegistry(path, key).list_safe(subject)}


@mcp.tool(name="gemini_select_account", annotations={"title": "Select Gemini Account"})
async def gemini_select_account(account_id: str, ctx: Context) -> dict:
    """Set the requested account as the explicit account for a future session."""
    from .registry import DynamicAccountRegistry
    path = os.environ.get("GEMINI_REGISTRY_PATH")
    key = _configured_registry_key()
    if not path or not key:
        raise RuntimeError("account registry is not configured")
    subject = _oauth_subject(ctx)
    account = DynamicAccountRegistry(path, key).select(subject, account_id)
    return {"selected": account["account"], "profile": account.get("profile"), "state": account.get("state")}


@mcp.tool(
    name="gemini_list_models",
    annotations={
        "title": "List Gemini Web Models",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def gemini_list_models(ctx: Context) -> dict:
    """List models observed in the current web session and their capabilities.

    A fallback list is returned when the upstream web client has no model
    enumeration surface. Fallback entries are explicitly marked as such.
    """
    # Resolve the account outside the fallback boundary.  A missing OAuth
    # identity, selection, profile, or session must fail closed rather than
    # being mistaken for a harmless discovery failure.
    client = await _get_client(ctx)
    try:
        return {"models": capabilities_json(client)}
    except Exception as exc:
        logger.warning("model discovery unavailable: %s", type(exc).__name__)
        return {"models": capabilities_json(None), "warning": "discovery_unavailable"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_client(ctx: Context):
    return await ctx.request_context.lifespan_context["account_clients"].get(ctx)


def _get_sessions(ctx: Context) -> dict:
    return ctx.request_context.lifespan_context["account_clients"].sessions


def _get_media_dir(ctx: Context) -> Path:
    return ctx.request_context.lifespan_context["account_clients"].media_dir(ctx)


_image_mode = False
_image_lock = asyncio.Lock()

# Populated by _patched_parse hook during StreamGenerate response parsing.
_image_tokens: dict[str, str] = {}   # preview_url -> download_token
_last_metadata: list = []             # [cid, rid, rcid, ...] from last response


def _patch_client(gemini_client):
    """Patch GeminiClient for image generation and 2x download support.

    1. Override model ID in header (Google rotates IDs periodically).
    2. Add browser-compatible body params and extra headers during image generation.
    3. Intercept response parsing to capture image download tokens for c8o8Fe RPC.
    """
    # Model id used by the browser for fast image generation (Flash, captured
    # 2026-05-21 from a real StreamGenerate request on a logged-in session).
    # The previous map forced "e051ce1aa80aa576" = flash-THINKING-advanced, the
    # slow Nano-Banana-thinking route that streamed `data_analysis_tool` for
    # 20s..345s. The browser uses "56fdd199312815e2" (flash-advanced) which
    # returns in ~10s. Override via GEMINI_IMAGE_MODEL_ID if Google rotates it.
    _IMAGE_MODEL_ID = os.environ.get("GEMINI_IMAGE_MODEL_ID", "56fdd199312815e2")
    # Capability flags array — the browser sends [4,5,6,8] (we used to send [4]).
    _IMAGE_MODEL_CAPS = "[4,5,6,8]"

    # Browser-compatible body params (indices in inner_req_list), captured from a
    # real browser image-generation request (2026-05-21). These route the request
    # to the fast image path; the prior values (6:[0], 17:[[0]], 68:1) matched the
    # slow path.
    _BROWSER_PARAMS = {
        1: [os.environ.get("GEMINI_LANGUAGE", "en")],
        6: [1],
        7: 1,
        10: 1,
        11: 0,
        17: [[1]],
        18: 0,
        27: 1,
        30: [4],
        41: [1],
        53: 0,
        61: [],
        68: 2,
    }

    http = gemini_client.client  # curl_cffi.AsyncSession
    _orig_request = http.request

    async def patched_request(method, url, **kwargs):
        global _image_mode
        if method == "POST" and "StreamGenerate" in str(url) and _image_mode:
            headers = kwargs.get("headers") or {}

            # Per-request UUID — the browser uses an UPPERCASE uuid, embedded in
            # both jspb headers AND inner[59], all three must match.
            req_uuid = str(uuid.uuid4()).upper()

            # Replace the model header with the exact browser shape for fast image
            # generation, rather than string-patching the library's slow header.
            # Browser: [1,null,null,null,"<id>",null,null,0,[4,5,6,8],null,null,2,null,null,1,1,"<UUID>"]
            if not _NO_REMAP:
                headers["x-goog-ext-525001261-jspb"] = (
                    '[1,null,null,null,"' + _IMAGE_MODEL_ID + '",null,null,0,'
                    + _IMAGE_MODEL_CAPS + ',null,null,2,null,null,1,1,"' + req_uuid + '"]'
                )

            headers["x-goog-ext-73010989-jspb"] = "[0]"
            headers["x-goog-ext-73010990-jspb"] = "[0,0,0]"
            headers["x-goog-ext-525005358-jspb"] = json.dumps([req_uuid, 1])

            kwargs["headers"] = headers

            # Inject browser-compatible body params into f.req
            data = kwargs.get("data")
            if isinstance(data, dict) and "f.req" in data:
                try:
                    outer = json.loads(data["f.req"])
                    inner = json.loads(outer[1])
                    # Force the captured browser values (overwrite, not only-if-None:
                    # the library pre-sets some of these to slow-path values).
                    for idx, val in _BROWSER_PARAMS.items():
                        if idx < len(inner):
                            inner[idx] = val
                    # Sync UUID with header
                    inner[59] = req_uuid

                    # Fix file_data format:
                    # Library:  [[[url], "name"]]
                    # Browser:  [[[url, 1, null, "mime"], "name", null*6, [0]]]
                    file_data = inner[0][3] if isinstance(inner[0], list) and len(inner[0]) > 3 else None
                    if file_data and isinstance(file_data, list):
                        _MIME_MAP = {
                            ".png": "image/png", ".jpg": "image/jpeg",
                            ".jpeg": "image/jpeg", ".webp": "image/webp",
                            ".gif": "image/gif", ".bmp": "image/bmp",
                        }
                        for fd in file_data:
                            if isinstance(fd, list) and len(fd) == 2:
                                url_arr, filename = fd[0], fd[1]
                                if isinstance(url_arr, list) and len(url_arr) == 1:
                                    ext = Path(filename).suffix.lower() if isinstance(filename, str) else ""
                                    mime = _MIME_MAP.get(ext, "image/png")
                                    fd[0] = [url_arr[0], 1, None, mime]
                                    fd.extend([None, None, None, None, None, None, [0]])

                    outer[1] = json.dumps(inner)
                    data["f.req"] = json.dumps(outer)
                    kwargs["data"] = data
                    if _DEBUG_TIMING:
                        logger.info("[req] model_hdr=%s", headers.get("x-goog-ext-525001261-jspb"))
                        logger.info("[req] inner idx 6=%s 7=%s 17=%s 30=%s 68=%s len=%d",
                                    inner[6] if len(inner) > 6 else "?",
                                    inner[7] if len(inner) > 7 else "?",
                                    inner[17] if len(inner) > 17 else "?",
                                    inner[30] if len(inner) > 30 else "?",
                                    inner[68] if len(inner) > 68 else "?", len(inner))
                except Exception as _e:
                    if _DEBUG_TIMING:
                        logger.warning("[req] patch failed: %s", _e)

        return await _orig_request(method, url, **kwargs)

    http.request = patched_request

    # --- Patch _parse_generated_images for new dict-based response format ---
    # Google changed response structure: [12][7][0] (list) -> [12][0]["8"] (dict).
    # See HanaokaYuzu/Gemini-API issues #229, #260, #264.
    import gemini_webapi.client as _gwc
    from gemini_webapi.utils import (
        get_nested_value,
        parse_response_by_frame as _orig_parse,
    )
    from gemini_webapi.types import GeneratedImage
    import orjson as _json

    _orig_parse_images = _gwc._parse_generated_images

    def _patched_parse_images(candidate_data, proxy=None, cookies=None, account_index=0, session_kwargs=None):
        # Try original parser first (old list-based format)
        result = _orig_parse_images(candidate_data, proxy, cookies, account_index, session_kwargs)
        if result:
            return result

        # Fallback: new dict-based format at [12][0]["8"]
        val12 = get_nested_value(candidate_data, [12])
        if not isinstance(val12, list) or not val12:
            return result
        entry = val12[0]
        if not isinstance(entry, dict) or "8" not in entry:
            return result
        generated_images = []
        for gen_img_data in entry["8"]:
            # Structure: [[[null, null, null, [null, 1, "filename.png", "url", ...], ...], ...]]
            url_arr = get_nested_value(gen_img_data, [0, 0, 3])
            if isinstance(url_arr, list) and len(url_arr) >= 4:
                url = url_arr[3]
                title = url_arr[2] or "[Generated Image]"
                token = url_arr[4] if len(url_arr) > 4 else None
                if url and isinstance(url, str) and url.startswith("http"):
                    if token:
                        _image_tokens[url] = token
                    # Force a download timeout so image.save()'s CDN fetch of the
                    # full-size (=s0) image can never hang the call forever.
                    sk = dict(session_kwargs or {})
                    sk.setdefault("timeout", DOWNLOAD_TIMEOUT)
                    generated_images.append(
                        GeneratedImage(
                            url=url,
                            title=f"[Generated Image]",
                            alt="",
                            proxy=proxy,
                            cookies=cookies,
                            account_index=account_index,
                            session_kwargs=sk,
                        )
                    )
        if generated_images:
            logger.info("Parsed %d images from new dict-based response format", len(generated_images))
        return generated_images

    _gwc._parse_generated_images = _patched_parse_images

    # --- Wrap parse_response_by_frame to capture image download tokens ---
    def _patched_parse(buffer):
        parts, remaining = _orig_parse(buffer)
        for part in parts:
            inner_json_str = get_nested_value(part, [2])
            if not inner_json_str:
                continue
            try:
                part_json = _json.loads(inner_json_str)
                # Capture conversation metadata (cid/rid)
                m_data = get_nested_value(part_json, [1])
                if isinstance(m_data, list) and len(m_data) >= 2 and m_data[0]:
                    if len(_last_metadata) >= 3:
                        _last_metadata[0] = m_data[0]
                        _last_metadata[1] = m_data[1]
                    else:
                        _last_metadata.clear()
                        _last_metadata.extend(m_data)
                # Capture image download tokens + rcid from candidates
                candidates = get_nested_value(part_json, [4], [])
                for cand in candidates:
                    rcid = get_nested_value(cand, [0])
                    if rcid and isinstance(rcid, str) and rcid.startswith("rc_"):
                        if len(_last_metadata) >= 2:
                            if len(_last_metadata) == 2:
                                _last_metadata.append(rcid)
                            else:
                                _last_metadata[2] = rcid
                    # Old format: [12][7][0]
                    for gid in get_nested_value(cand, [12, 7, 0], []):
                        url = get_nested_value(gid, [0, 3, 3])
                        token = get_nested_value(gid, [0, 3, 5])
                        if url and token:
                            _image_tokens[url] = token
                    # New dict format: [12][0]["8"]
                    val12 = get_nested_value(cand, [12])
                    if isinstance(val12, list) and val12 and isinstance(val12[0], dict):
                        for gid in val12[0].get("8", []):
                            url_arr = get_nested_value(gid, [0, 0, 3])
                            if isinstance(url_arr, list) and len(url_arr) >= 5:
                                url, token = url_arr[3], url_arr[4]
                                if url and token:
                                    _image_tokens[url] = token
            except Exception:
                pass
        return parts, remaining

    _gwc.parse_response_by_frame = _patched_parse
    logger.info("Patched GeminiClient with browser-compatible parameters")


def _handle_error(e: Exception) -> str:
    from gemini_webapi import AuthError, APIError, RequestTimeoutError

    if isinstance(e, AuthError):
        return (
            "Error: Authentication failed. Cookies may have expired. "
            "Re-login to gemini.google.com in Chrome, then call gemini_reset."
        )
    if isinstance(e, RequestTimeoutError):
        return "Error: Request timed out. Try again or use a lighter model."
    if isinstance(e, APIError):
        return f"Error: Gemini API error — {e}"
    return f"Error: {type(e).__name__} — {e}"


async def _fetch_download_url(client, token: str, prompt: str, metadata: list, image_index: int = 0) -> str | None:
    """Call c8o8Fe RPC to get a high-resolution (2x) download URL for a generated image.

    Google stores a 2x upscaled version accessible only through this RPC endpoint.
    Returns the download URL or None on failure.
    """
    import orjson as _json
    from gemini_webapi.constants import Endpoint
    from gemini_webapi.utils import parse_response_by_frame, get_nested_value

    cid = metadata[0] if metadata else None
    rid = metadata[1] if len(metadata) > 1 else None
    rcid = metadata[2] if len(metadata) > 2 else None
    if not (rid and rcid and cid):
        logger.warning("c8o8Fe skipped: missing metadata (cid/rid/rcid)")
        return None

    gen_id = _make_gen_id()
    inner_payload = _json.dumps([
        [
            [None, None, None, [None, None, None, None, None, token]],
            [f"http://googleusercontent.com/image_generation_content/{image_index}", image_index],
            None,
            [19, prompt],
            None, None, None, None, None,
            gen_id,
        ],
        [rid, rcid, cid, None, gen_id],
        1, 0, 1,
    ]).decode("utf-8")

    outer_payload = _json.dumps(
        [[["c8o8Fe", inner_payload, None, "generic"]]]
    ).decode("utf-8")

    params: dict = {
        "rpcids": "c8o8Fe",
        "_reqid": client._reqid,
        "rt": "c",
        "source-path": Endpoint.get_source_path(client.account_index),
    }
    client._reqid += 100000
    if client.build_label:
        params["bl"] = client.build_label
    if client.session_id:
        params["f.sid"] = client.session_id

    try:
        resp = await client.client.post(
            Endpoint.get_batch_exec_url(client.account_index),
            params=params,
            data={"at": client.access_token, "f.req": outer_payload},
            headers={
                "x-goog-ext-525001261-jspb": "[1,null,null,null,null,null,null,0,[4,4]]",
                "x-goog-ext-73010989-jspb": "[0]",
            },
            timeout=60,
        )
        if resp.status_code != 200:
            logger.warning("c8o8Fe returned status %d", resp.status_code)
            return None

        text = resp.text
        if text.startswith(")]}'"):
            text = text[4:].lstrip()
        parts, _ = parse_response_by_frame(text)
        # parse_response_by_frame returns each sublist as a separate part:
        # part = ['wrb.fr', 'c8o8Fe', '["url"]', None, None, None, 'generic']
        for part in parts:
            if isinstance(part, list) and len(part) > 2 and part[1] == "c8o8Fe":
                inner_str = part[2]
                if inner_str and isinstance(inner_str, str):
                    inner = _json.loads(inner_str)
                    if isinstance(inner, list) and inner:
                        return inner[0]
        logger.warning("c8o8Fe: no download URL in response (%d parts parsed)", len(parts))
    except Exception as exc:
        logger.warning("c8o8Fe failed: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="gemini_start_chat",
    annotations={
        "title": "Start Gemini Chat Session",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def gemini_start_chat(
    ctx: Context,
    model: Optional[str] = None,
) -> str:
    """Start a new multi-turn chat session with Gemini.

    The session maintains conversation history so follow-up messages
    have full context. Pass the returned session_id to gemini_chat.

    Args:
        model: Model name for this session. Defaults to gemini-3.0-flash.

    Returns:
        JSON with session_id to use in subsequent gemini_chat calls.
    """
    try:
        client = await _get_client(ctx)
        chat = client.start_chat(model=model or DEFAULT_MODEL)
        session_id = str(uuid.uuid4())[:8]
        manager = ctx.request_context.lifespan_context["account_clients"]
        _get_sessions(ctx)[manager.session_key(ctx, session_id)] = chat
        return json.dumps({
            "session_id": session_id,
            "model": model or DEFAULT_MODEL,
            "message": f"Chat session started. Use session_id '{session_id}' in gemini_chat.",
        })
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="gemini_chat",
    annotations={
        "title": "Gemini Chat",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def gemini_chat(
    prompt: str,
    ctx: Context,
    model: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """Send a text prompt to Google Gemini and get a response.

    Args:
        prompt: The text prompt to send to Gemini.
        model: Model name (e.g. 'gemini-3.0-flash', 'gemini-3.0-pro',
               'gemini-3.0-flash-thinking'). Defaults to gemini-3.0-flash.
        session_id: Optional session ID from gemini_start_chat for
                    multi-turn conversation with context.

    Returns:
        Gemini's text response. When using flash-thinking model,
        also includes the model's reasoning process.
    """
    try:
        client = await _get_client(ctx)

        if session_id:
            manager = ctx.request_context.lifespan_context["account_clients"]
            sessions = _get_sessions(ctx)
            chat = sessions.get(manager.session_key(ctx, session_id))
            if not chat:
                return f"Error: Session '{session_id}' not found. Start a new one with gemini_start_chat."
            response = await chat.send_message(prompt)
        else:
            response = await client.generate_content(
                prompt, model=model or DEFAULT_MODEL
            )

        text = response.text or "(empty response)"
        thoughts = response.thoughts
        if thoughts:
            return f"**Thinking:**\n{thoughts}\n\n**Response:**\n{text}"
        return text
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="gemini_generate_image",
    annotations={
        "title": "Gemini Image Generation",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def gemini_generate_image(
    prompt: str,
    ctx: Context,
    model: Optional[str] = None,
    files: Optional[list[str]] = None,
    conversation_id: Optional[list[str]] = None,
) -> str:
    """Generate or edit images with Gemini.

    Without files: generates a new image from the text prompt.
    With files: edits/transforms the provided image(s) based on the prompt.

    Pass conversation_id from a previous call to continue refining images
    in the same conversation thread (e.g. "make it more dramatic", "add rain").
    You can also use a cid from the Gemini web URL (gemini.google.com/app/{cid}).

    Images are saved to ~/Pictures/gemini/ and full file paths are returned.

    Args:
        prompt: Description of the image to generate, or editing instruction
                (e.g. 'change the background to blue', 'make it a cartoon').
        model: Model name. Defaults to gemini-3.0-flash-thinking
               (Nano Banana 2, supports non-square aspect ratios).
        files: Optional list of file paths to images to edit/transform.
        conversation_id: Optional list of [cid, rid, rcid] from a previous
                         gemini_generate_image response to continue the conversation.
                         Passing just [cid] (from browser URL) also works.

    Returns:
        JSON with generated image paths, conversation_id for continuation, or an error message.
    """
    global _image_mode
    import time
    t0 = time.monotonic()
    try:
        client = await _get_client(ctx)

        # Validate input files
        resolved_files = []
        if files:
            for f in files:
                p = Path(f).expanduser().resolve()
                if not p.exists():
                    return f"Error: File not found — {p}"
                resolved_files.append(str(p))

        chat = None
        async with _image_lock:
            t = _stage("acquired image lock", t0)
            _image_mode = True
            try:
                if conversation_id:
                    chat = client.start_chat(
                        metadata=conversation_id,
                        model=model or "gemini-3.0-flash-thinking",
                    )
                    gen_coro = chat.send_message(prompt, files=resolved_files or None)
                else:
                    kwargs = {"model": model or "gemini-3.0-flash-thinking"}
                    if resolved_files:
                        kwargs["files"] = resolved_files
                    gen_coro = client.generate_content(prompt, **kwargs)
                # Hard cap: never let the library's stall-retry loop run for
                # minutes when Gemini throttles the account. Fail fast instead.
                response = await asyncio.wait_for(gen_coro, timeout=GEN_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("generate_content exceeded %.0fs cap — aborting", GEN_TIMEOUT)
                return (
                    f"Error: Gemini did not return an image within {GEN_TIMEOUT:.0f}s "
                    "via the library (the browser is usually faster). Try again."
                )
            finally:
                _image_mode = False
            _stage("generate_content returned", t)

        if not response.images:
            return response.text or "No images were generated. Try rephrasing your prompt."

        # --- Try to get 2x download URLs via c8o8Fe RPC ---
        # Always prefer _last_metadata from monkey-patched response parsing
        # (captures raw stream data needed for c8o8Fe). Fall back to chat metadata.
        if _last_metadata and _last_metadata[0]:
            metadata = list(_last_metadata)
        elif chat:
            metadata = [chat.cid or "", chat.rid or "", chat.rcid or ""]
        else:
            metadata = []
        download_urls: dict[int, str] = {}
        if not _SKIP_2X:
            for i, image in enumerate(response.images):
                token = _image_tokens.pop(image.url, None)
                if token and metadata:
                    logger.info("Requesting 2x download URL for image %d...", i)
                    dl_url = await _fetch_download_url(client, token, prompt, metadata, i)
                    if dl_url:
                        download_urls[i] = dl_url
                        logger.info("Got 2x download URL for image %d", i)
        _image_tokens.clear()  # clean up any leftover tokens
        t = _stage("c8o8Fe (2x url) done", t)

        media_dir = _get_media_dir(ctx)
        saved = []

        for i, image in enumerate(response.images):
            # Ensure a download timeout even if this image came from the
            # original (non-patched) parser, where session_kwargs is empty.
            try:
                if "timeout" not in getattr(image, "session_kwargs", {}):
                    image.session_kwargs = {**getattr(image, "session_kwargs", {}),
                                            "timeout": DOWNLOAD_TIMEOUT}
            except Exception:
                pass
            # Use 2x upscale URL from c8o8Fe if available
            has_upscale = i in download_urls
            if has_upscale:
                # Use c8o8Fe 2x URL with =s0 for full resolution (not =s2048 which downscales)
                image.url = re.sub(r"=[^/]*$", "", download_urls[i]) + "=s0"
            elif re.search(r"=s\d+(-[a-z0-9]+)*$", image.url):
                # googleusercontent URL with an explicit =sNNNN size suffix: bump to
                # =s0 for full resolution. gg-dl token URLs have no such suffix and
                # reject =s0 with HTTP 400, so we leave those untouched (download as-is,
                # which already returns the full-size image).
                image.url = re.sub(r"=s\d+(-[a-z0-9]+)*$", "=s0", image.url)

            try:
                # full_size=False only when c8o8Fe already gave us a URL with =s0;
                # otherwise library's =s2048 is needed for full-size preview download.
                # Without this gating, an un-upscaled URL with no suffix drops to a
                # ~382px thumbnail instead of the ~896px preview.
                filepath = await image.save(
                    path=str(media_dir),
                    filename=f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{i}.png",
                    verbose=False,
                    full_size=not has_upscale,
                )
            except Exception as save_err:
                logger.warning("Image save failed for %d: %s", i, save_err)
                continue
            t = _stage(f"download+save image {i}", t)

            if not filepath:
                continue

            try:
                _remove_watermark(filepath, upscaled=has_upscale)
            except Exception as wm_err:
                logger.warning("Watermark removal failed: %s", wm_err)
            t = _stage(f"watermark removal image {i}", t)
            title = getattr(image, "title", None) or f"image_{i}"
            saved.append({"title": title, "path": filepath, "dir": str(media_dir)})

        # For response: prefer chat metadata (clean cid/rid/rcid), fall back to raw
        if chat:
            conv_id = [chat.cid or "", chat.rid or "", chat.rcid or ""]
        else:
            conv_id = metadata[:3] if metadata and metadata[0] else None
        result = {
            "text": response.text or "",
            "images_saved_to": str(media_dir),
            "images": saved,
            "conversation_id": conv_id,
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="gemini_upload_file",
    annotations={
        "title": "Gemini File Upload & Analysis",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def gemini_upload_file(
    file_path: str,
    ctx: Context,
    prompt: str = "Describe this file.",
    model: Optional[str] = None,
) -> str:
    """Upload a file (image, PDF, document, video) to Gemini and ask a question about it.

    Args:
        file_path: Absolute path to the file to upload.
        prompt: Question or instruction about the file
                (e.g. 'What is shown in this image?').
        model: Model name. Defaults to gemini-3.0-flash.

    Returns:
        Gemini's text response about the uploaded file.
    """
    try:
        p = Path(file_path).expanduser().resolve()
        if not p.exists():
            return f"Error: File not found — {p}"

        client = await _get_client(ctx)
        response = await client.generate_content(
            prompt, model=model or DEFAULT_MODEL, files=[str(p)]
        )
        return response.text or "(empty response)"

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="gemini_analyze_url",
    annotations={
        "title": "Gemini URL Analysis",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def gemini_analyze_url(
    url: str,
    ctx: Context,
    prompt: str = "Summarize this content.",
    model: Optional[str] = None,
) -> str:
    """Analyze a URL — YouTube videos, webpages, articles, etc.

    Gemini can watch YouTube videos and read webpages, then answer
    questions about their content.

    Args:
        url: The URL to analyze (YouTube, article, webpage, etc.).
        prompt: Question or instruction about the content
                (e.g. 'Summarize this video', 'What are the key points?').
        model: Model name. Defaults to gemini-3.0-flash.

    Returns:
        Gemini's analysis of the URL content.
    """
    try:
        client = await _get_client(ctx)
        full_prompt = f"{prompt}\n\n{url}"
        response = await client.generate_content(
            full_prompt, model=model or DEFAULT_MODEL
        )
        return response.text or "(empty response)"
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="gemini_reset",
    annotations={
        "title": "Reset Gemini Client",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def gemini_reset(ctx: Context) -> str:
    """Re-initialise the Gemini client (refresh cookies, clear state).

    Use this when you get authentication errors or want a fresh session.

    Returns:
        Confirmation message or error.
    """
    try:
        from gemini_webapi import GeminiClient

        old = await _get_client(ctx)
        await old.close()

        subject, record = _AccountClientManager._account(ctx)
        psid, psidts = _AccountClientManager._cookies(_AccountClientManager._profile(record))
        new_client = GeminiClient(secure_1psid=psid, secure_1psidts=psidts or None)
        await new_client.init(timeout=300, watchdog_timeout=45, auto_close=False, auto_refresh=True)
        _patch_client(new_client)

        manager = ctx.request_context.lifespan_context["account_clients"]
        key = (subject, str(record["account"]))
        manager.clients[key] = new_client
        return "Gemini client re-initialised with fresh cookies."

    except Exception as e:
        return _handle_error(e)

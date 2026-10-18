"""
MCP Server for Google Gemini via browser cookies.

Uses gemini_webapi library to access Gemini Web App for free,
without requiring paid API keys. Authentication is done through
browser cookies (__Secure-1PSID and __Secure-1PSIDTS).
"""

import asyncio
import base64
from collections import deque
from contextvars import ContextVar
import hashlib
import json
import logging
import mimetypes
import os
import random
import re
import string
import shutil
import sys
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from importlib.metadata import PackageNotFoundError, version as package_version
from urllib.parse import parse_qsl, urlencode, quote, urlsplit, urlunsplit

from mcp.server.fastmcp import FastMCP, Context
from mcp.types import CallToolResult, TextContent
from .build_info import build_provenance
from .image_viewer import (
    IMAGE_VIEWER_HTML,
    IMAGE_VIEWER_URI,
    IMAGE_VIEWER_V1_URI,
    LEGACY_IMAGE_VIEWER_HTML,
    LEGACY_IMAGE_VIEWER_URI,
    image_viewer_resource_meta,
)
from .media import MediaType, create_media_artifact
from .studio_capabilities import gemini_video_capabilities, notebooklm_capabilities
from .video_viewer import VIDEO_VIEWER_HTML, VIDEO_VIEWER_URI, video_viewer_resource_meta
from .observability import image_delivery_event, new_correlation_id
from .models import (
    MODEL_ROUTE_HEADER,
    ModelCatalogCache,
    ModelSelectionError,
    capabilities_json,
    current_request_fields,
    current_request_headers,
    current_request_model_id,
    discover_authenticated_models,
    discovery_metadata,
    model_request_context,
    resolve_model,
)
from .oauth import LocalOAuth
from .browser_handoff import BrowserHandoffBroker
from .viewer import DEFAULT_RFB_URL, content_security_policy, viewer_page
from .protocol.serializer import bind_stream_request
from .protocol.adapter import LegacyClientAdapter
from .protocol.serializer import SERIALIZER_REVISION
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
CHAT_TIMEOUT = float(os.environ.get("GEMINI_CHAT_TIMEOUT", "300"))
# Per-network-op timeout for image download in image.save() (seconds). Without
# this the CDN fetch of the full-size (=s0, ~4.5MB) image can hang forever.
DOWNLOAD_TIMEOUT = float(os.environ.get("GEMINI_DOWNLOAD_TIMEOUT", "60"))
# Generation and upload requests are not safely replayable: an upstream request
# can complete after a transport failure and a retry may create a second turn or
# second upload.  Operators may explicitly opt in after assessing that risk.
UPSTREAM_RETRY_BUDGET = max(0, min(int(os.environ.get("GEMINI_UPSTREAM_RETRIES", "0")), 5))


def _env_flag(name: str, default: bool = False) -> bool:
    """Parse a boolean environment flag without treating arbitrary text as true."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# The upstream library rotates __Secure-1PSIDTS over the network. Multiple
# account-scoped clients doing that independently can trigger Google's 429
# limiter, while the server already rereads the persistent Chromium profile.
# Keep upstream rotation available, but make it explicitly opt-in.
UPSTREAM_AUTO_REFRESH = _env_flag("GEMINI_UPSTREAM_AUTO_REFRESH", False)
UPSTREAM_REFRESH_INTERVAL = max(
    600.0, float(os.environ.get("GEMINI_UPSTREAM_REFRESH_INTERVAL", "1800"))
)

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

def _read_basic_chromium_cookies(cookie_file: Path) -> list[tuple[str, str, str]]:
    """Read Google cookies from Chromium's Linux basic-password-store DB.

    Returns only (domain, name, value); values are never logged.  Chromium's
    v10 format uses PBKDF2-HMAC-SHA1("peanuts", "saltysalt", 1) and AES-CBC.
    The database is copied first so a live Chromium process can keep its WAL
    and lock files without exposing the application to partial reads.
    """
    import hashlib
    import shutil
    import sqlite3
    import tempfile

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(prefix="gemini-cookies-", suffix=".db", delete=False) as tmp:
            tmp_name = tmp.name
        shutil.copyfile(cookie_file, tmp_name)
        db = sqlite3.connect(f"file:{tmp_name}?mode=ro", uri=True)
        try:
            rows = db.execute(
                "SELECT host_key, name, encrypted_value, value FROM cookies"
            ).fetchall()
        finally:
            db.close()
    except (OSError, sqlite3.Error) as exc:
        raise RuntimeError("cookie_storage_unreadable: Chromium cookie database could not be opened") from exc
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    key = hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, dklen=16)
    iv = b" " * 16
    result = []
    for domain, name, encrypted, plain in rows:
        if not domain or not domain.lstrip(".").lower().endswith("google.com"):
            continue
        value = plain or ""
        if encrypted:
            blob = bytes(encrypted)
            if not blob.startswith(b"v10"):
                continue
            try:
                payload = blob[3:]
                decrypted = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
                decoded = decrypted.update(payload) + decrypted.finalize()
                padding = decoded[-1]
                if not 1 <= padding <= 16 or decoded[-padding:] != bytes([padding]) * padding:
                    continue
                decoded = decoded[:-padding]
                # Chromium cookie schema v24+ prefixes encrypted values with
                # a 32-byte SHA256(host_key) value. Accept both old and new DBs.
                if len(decoded) >= 32 and decoded[:32] == hashlib.sha256(domain.encode()).digest():
                    decoded = decoded[32:]
                value = decoded.decode("utf-8")
            except Exception:
                continue
        if value and name in {"__Secure-1PSID", "__Secure-1PSIDTS"}:
            result.append((domain, name, value))
    if not result:
        raise RuntimeError("cookie_decryption_failed: Chromium cookie database could not be decrypted")
    return result


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
        self._maintenance_task: asyncio.Task | None = None
        self._request_slots: dict[tuple[str, str], asyncio.Semaphore] = {}
        self.model_catalog = ModelCatalogCache()
        from .conversations import ConversationStore
        self.conversations = ConversationStore()

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
    def _cookie_values(profile: Path) -> tuple[str | None, str | None]:
        cookie_file = profile / "Default" / "Cookies"
        local_state = profile / "Local State"
        if not profile.is_dir():
            raise RuntimeError("cookie_storage_missing: account profile directory is missing")
        if not cookie_file.is_file():
            raise RuntimeError("cookie_storage_missing: Chromium Cookies database is missing")
        if not local_state.is_file():
            raise RuntimeError("cookie_key_missing: Chromium Local State is missing")
        if not os.access(cookie_file, os.R_OK) or not os.access(local_state, os.R_OK):
            raise RuntimeError("cookie_storage_permission: Chromium cookie files are not readable")
        # Account profiles are launched with Chromium's basic password store.
        # Prefer the direct v10 reader so browser-cookie3 does not probe
        # SecretService/KWallet and emit a misleading KeyError on headless hosts.
        reader = os.environ.get("GEMINI_ACCOUNT_COOKIE_READER", "basic").lower()
        if reader == "basic":
            cookies = _read_basic_chromium_cookies(cookie_file)
        else:
            try:
                import browser_cookie3
                cookies = browser_cookie3.chrome(
                    cookie_file=str(cookie_file),
                    key_file=str(local_state),
                )
            except ImportError as exc:
                raise RuntimeError("cookie_reader_missing: browser-cookie3 is not installed") from exc
            except Exception as exc:
                logger.info("browser-cookie3 could not read Chromium cookies: %s", type(exc).__name__)
                raise RuntimeError("cookie_storage_unreadable: configured browser cookie reader failed") from exc
        psid = psidts = None
        for cookie in cookies:
            if isinstance(cookie, tuple):
                raw_domain, name, value = cookie
            else:
                raw_domain, name, value = cookie.domain, cookie.name, cookie.value
            domain = (raw_domain or "").lstrip(".").lower()
            if not domain.endswith("google.com"):
                continue
            if name == "__Secure-1PSID" and value:
                psid = value
            elif name == "__Secure-1PSIDTS" and value:
                psidts = value
        return psid, psidts

    @staticmethod
    def _cookies(profile: Path) -> tuple[str, str]:
        psid, psidts = _AccountClientManager._cookie_values(profile)
        if not psid or not psidts:
            missing = []
            if not psid:
                missing.append("__Secure-1PSID")
            if not psidts:
                missing.append("__Secure-1PSIDTS")
            raise RuntimeError("required Gemini session cookie(s) missing: " + ", ".join(missing))
        return psid, psidts

    async def _build_client(self, key: tuple[str, str], profile: Path):
        from gemini_webapi import GeminiClient
        psid, psidts = self._cookies(profile)
        client = GeminiClient(secure_1psid=psid, secure_1psidts=psidts or None)
        await client.init(
            timeout=float(os.environ.get("GEMINI_CLIENT_TIMEOUT", "300")),
            watchdog_timeout=float(os.environ.get("GEMINI_WATCHDOG_TIMEOUT", "90")),
            auto_close=False,
            auto_refresh=UPSTREAM_AUTO_REFRESH,
            refresh_interval=UPSTREAM_REFRESH_INTERVAL,
        )
        _patch_client(client)
        try:
            discovered = await discover_authenticated_models(client)
            self.model_catalog.set(key, discovered)
            if discovered:
                logger.info("Discovered %d authenticated Gemini model routes for account %s", len(discovered), key[1])
        except Exception:
            # Discovery is additive. A protocol drift here must not make an
            # otherwise healthy authenticated chat client unusable.
            logger.warning("Authenticated Gemini model discovery failed for account %s", key[1], exc_info=True)
        self._cookie_pairs[key] = (psid, psidts)
        self._profiles[key] = profile
        # The third-party client is now quarantined behind the application
        # protocol contract. Attribute proxying keeps the 20 MCP tools stable
        # while they migrate incrementally to typed execute() requests.
        return LegacyClientAdapter(client, discover_authenticated_models)

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
                    self.model_catalog.invalidate(key)
                except Exception:
                    logger.warning("Gemini cookie refresh failed for account %s", key[1], exc_info=True)

    async def _refresh_loop(self) -> None:
        interval = max(60, int(os.environ.get("GEMINI_COOKIE_REFRESH_SECONDS", "300")))
        while True:
            await asyncio.sleep(interval)
            await self.refresh_cookies()

    async def _maintain_browser_profile(self, profile: Path) -> None:
        executable = os.environ.get("GEMINI_BROWSER_EXECUTABLE") or shutil.which("google-chrome") or shutil.which("google-chrome-stable")
        if not executable:
            return
        command = [
            executable, f"--user-data-dir={profile}", "--profile-directory=Default",
            "--password-store=basic", "--headless=new", "--no-first-run",
            "--no-default-browser-check", "--disable-gpu", "https://gemini.google.com/app",
        ]
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=20)
        except asyncio.TimeoutError:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()

    async def _maintenance_loop(self) -> None:
        interval = max(86400, int(os.environ.get("GEMINI_BROWSER_MAINTENANCE_SECONDS", "259200")))
        while True:
            await asyncio.sleep(interval)
            for profile in set(self._profiles.values()):
                try:
                    await self._maintain_browser_profile(profile)
                except Exception:
                    logger.warning("Chromium profile maintenance failed", exc_info=True)

    def start_refresh_loop(self) -> None:
        if self._refresh_task is None:
            self._refresh_task = asyncio.create_task(self._refresh_loop())
        if self._maintenance_task is None:
            self._maintenance_task = asyncio.create_task(self._maintenance_loop())

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

    def conversation_owner(self, ctx: Context) -> tuple[str, str]:
        subject, record = self._account(ctx)
        return subject, str(record["account"])

    @asynccontextmanager
    async def request_slot(self, ctx: Context):
        """Bound concurrent upstream requests per selected account."""
        subject, record = self._account(ctx)
        key = (subject, str(record["account"]))
        # GeminiClient owns mutable request/session state.  Queue concurrent
        # MCP calls per account by default; deployments may raise the limit
        # only after validating their upstream client version supports it.
        limit = max(1, min(int(os.environ.get("GEMINI_MAX_CONCURRENT_PER_ACCOUNT", "1")), 8))
        async with self._lock:
            slot = self._request_slots.get(key)
            if slot is None:
                slot = self._request_slots[key] = asyncio.Semaphore(limit)
        async with slot:
            yield

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
        if self._maintenance_task is not None:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass
            self._maintenance_task = None
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
        self._request_slots.clear()
        self.model_catalog.invalidate()

    async def invalidate(self, ctx: Context, client) -> None:
        """Close and evict a timed-out client so its HTTP connection is not reused."""
        subject, record = self._account(ctx)
        key = (subject, str(record["account"]))
        async with self._lock:
            current = self.clients.get(key)
            if current is not client:
                return
            self.clients.pop(key, None)
            self.model_catalog.invalidate(key)
            try:
                await asyncio.wait_for(client.close(), timeout=5)
            except Exception:
                logger.warning("timed-out Gemini client did not close cleanly for account %s", key[1])

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


@mcp.resource(
    IMAGE_VIEWER_URI,
    name="gemini-image-result-viewer",
    title="Gemini image result",
    description="Private MCP App viewer for native image tool results.",
    mime_type="text/html;profile=mcp-app",
    meta=image_viewer_resource_meta(),
)
def gemini_image_result_viewer() -> str:
    """Return a static viewer; image bytes arrive only in the tool result."""
    return IMAGE_VIEWER_HTML


@mcp.resource(
    LEGACY_IMAGE_VIEWER_URI,
    name="gemini-image-result-viewer-v2",
    title="Gemini image result (compatibility)",
    description="Backward-compatible URI for cached Gemini image tool descriptors.",
    mime_type="text/html;profile=mcp-app",
    meta=image_viewer_resource_meta(),
)
def gemini_image_result_viewer_v2() -> str:
    """Serve the corrected renderer at the previous resource URI."""
    return LEGACY_IMAGE_VIEWER_HTML


@mcp.resource(
    IMAGE_VIEWER_V1_URI,
    name="gemini-image-result-viewer-v1",
    title="Gemini image result (oldest compatibility)",
    description="Backward-compatible URI for the original cached image viewer descriptor.",
    mime_type="text/html;profile=mcp-app",
    meta=image_viewer_resource_meta(),
)
def gemini_image_result_viewer_oldest() -> str:
    """Serve the corrected renderer at the original resource URI."""
    return LEGACY_IMAGE_VIEWER_HTML


@mcp.resource(
    VIDEO_VIEWER_URI,
    name="gemini-video-result-viewer",
    title="Gemini video result",
    description="Private MCP App player for validated embedded video results.",
    mime_type="text/html;profile=mcp-app",
    meta=video_viewer_resource_meta(),
)
def gemini_video_result_viewer() -> str:
    """Return a static player; private video bytes arrive in the tool result."""
    return VIDEO_VIEWER_HTML


def _app_version() -> str:
    try:
        return package_version("gemini-webapi-mcp")
    except PackageNotFoundError:
        return os.environ.get("GEMINI_APP_VERSION", "0.0.0-dev")

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
    account = _account_name_for_login(subject, account)
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


def _account_name_for_login(subject: str, proposed: str) -> str:
    """Reuse an owned selection; otherwise derive a stable account name.

    Earlier OAuth flows appended a random authorization-code suffix to every
    login, so reauthenticating the same MCP identity created another registry
    entry. Existing selected accounts remain authoritative; new identities get
    a deterministic opaque name rather than a per-login name.
    """
    path = os.environ.get("GEMINI_REGISTRY_PATH")
    key = _configured_registry_key()
    if not path or not key:
        return proposed
    try:
        from .registry import DynamicAccountRegistry

        registry = DynamicAccountRegistry(path, key)
        selected = registry.selection(subject)
        if selected:
            return selected
        ready = [item["account"] for item in registry.list_safe(subject) if item.get("state") == "ready"]
        if len(ready) == 1:
            registry.select(subject, ready[0])
            return ready[0]
    except Exception:
        # Registration will report the underlying failure later; do not
        # silently substitute another account here.
        logger.info("could not resolve an existing Gemini account selection")
    stable = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:20]
    return f"gemini-{stable}"


def _consume_browser_handoff(token: str, subject: str, account: str) -> None:
    """Promote one completed browser profile into its owner-bound account."""
    from .registry import DynamicAccountRegistry

    registry_path = os.environ.get("GEMINI_REGISTRY_PATH")
    registry_key = _configured_registry_key()
    if not registry_path or not registry_key:
        raise RuntimeError("encrypted account registry is not configured")
    profile_root = Path(os.environ.get("GEMINI_PROFILE_ROOT", "/var/lib/gemini-mcp/accounts")).expanduser().resolve()
    # Resolve an existing/stable account *before* selecting the profile
    # destination.  Doing this afterwards can promote a completed login into
    # an unregistered random handoff directory while the registry continues
    # to point at the selected account's old profile.
    account = _account_name_for_login(subject, account)
    destination = profile_root / account
    try:
        destination.relative_to(profile_root)
    except ValueError as exc:
        raise PermissionError("invalid Gemini account profile name") from exc
    registry = DynamicAccountRegistry(registry_path, registry_key)
    previous = next(
        (item for item in registry.list_safe(subject) if item["account"] == account),
        None,
    )
    registry.register(
        subject=subject,
        account=account,
        profile=account,
        scopes={"chat", "image", "upload", "url"},
        requests_per_minute=int(os.environ.get("GEMINI_ACCOUNT_RPM", "30")),
        state="pending",
    )
    try:
        _handoff_broker.consume(token, promote_to=destination, replace_existing=True)
        registry.set_state(account, "ready")
    except Exception:
        try:
            registry.set_state(account, previous["state"] if previous else "revoked")
        except Exception:
            logger.warning("failed to restore Gemini account registration state", exc_info=True)
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
    body = viewer_page(sid=sid, ws_url=ws_url, expires_at=float(state["expires_at"]), complete_url=request.query_params.get("complete", ""), rfb_url=rfb_url)
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
    state = _handoff_broker.confirm_manual(sid)
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


@mcp.tool(
    name="gemini_account_diagnostics",
    annotations={"title": "Inspect Gemini Account Health", "readOnlyHint": True},
)
async def gemini_account_diagnostics(ctx: Context, account_id: Optional[str] = None) -> dict:
    """Return sanitized account/profile references without exposing credentials."""
    from .registry import DynamicAccountRegistry

    path = os.environ.get("GEMINI_REGISTRY_PATH")
    key = _configured_registry_key()
    if not path or not key:
        return {"error": "account_registry_not_configured"}
    subject = _oauth_subject(ctx)
    registry = DynamicAccountRegistry(path, key)
    records = registry.list_safe(subject)
    if account_id:
        records = [record for record in records if record["account"] == account_id]
        if not records:
            return {"error": "account_not_found"}
    selected = registry.selection(subject)
    manager = ctx.request_context.lifespan_context["account_clients"]
    root = Path(os.environ.get("GEMINI_PROFILE_ROOT", "/var/lib/gemini-mcp/accounts")).expanduser().resolve()
    result = []
    for record in records:
        account = str(record["account"])
        profile = Path(str(record.get("profile") or account)).expanduser()
        if not profile.is_absolute():
            profile = root / profile
        try:
            profile = profile.resolve()
            profile.relative_to(root)
            profile_exists = profile.is_dir()
        except ValueError:
            profile_exists = False
        profile_last_modified = None
        if profile_exists:
            try:
                profile_last_modified = profile.stat().st_mtime
            except OSError:
                pass
        credentials_available = False
        has_psid = False
        has_psidts = False
        identity_fingerprint = None
        credential_error = None
        if profile_exists:
            try:
                psid, psidts = manager._cookie_values(profile)
                has_psid = bool(psid)
                has_psidts = bool(psidts)
                credentials_available = has_psid and has_psidts
                if psid:
                    identity_fingerprint = hashlib.sha256(psid.encode("utf-8")).hexdigest()[:16]
            except Exception as exc:
                credential_error = type(exc).__name__
        result.append({
            "account_suffix": account[-12:],
            "account": account,
            "record_source": "encrypted_registry",
            "created_at": record.get("created_at"),
            "created_at_source": "registry" if record.get("created_at") else "unavailable",
            "updated_at": record.get("updated_at"),
            "profile_last_modified": profile_last_modified,
            "last_local_conversation_activity": manager.conversations.last_activity_for_account(subject, account),
            "profile_exists": profile_exists,
            "credentials_available": credentials_available,
            "has_psid": has_psid,
            "has_psidts": has_psidts,
            "authentication_verified": "not_tested",
            "google_identity_fingerprint": identity_fingerprint,
            "credential_error_category": credential_error,
            "referenced_conversations": manager.conversations.count_for_account(subject, account),
            "active_session_references": sum(
                1 for key in manager.sessions if key[:2] == (subject, account)
            ),
            "selected": account == selected,
            "state": record.get("state"),
            "suspected_duplicate": False,
        })
    fingerprints = [item["google_identity_fingerprint"] for item in result if item["google_identity_fingerprint"]]
    for item in result:
        item["suspected_duplicate"] = bool(
            item["google_identity_fingerprint"] and fingerprints.count(item["google_identity_fingerprint"]) > 1
        )
    return {"accounts": result, "selected_account": selected}


@mcp.tool(name="gemini_get_account_info", annotations={"title": "Get Gemini Account Information", "readOnlyHint": True})
async def gemini_get_account_info(ctx: Context, account_id: Optional[str] = None) -> dict:
    """Compatibility alias for the sanitized account diagnostics tool."""
    return await gemini_account_diagnostics(ctx, account_id)


@mcp.tool(
    name="gemini_deactivate_account",
    annotations={"title": "Deactivate Gemini Account", "readOnlyHint": False},
)
async def gemini_deactivate_account(
    account_id: str,
    confirm_account_id: str,
    ctx: Context,
) -> dict:
    """Disable one exact account without deleting its profile or conversations."""
    if not account_id or confirm_account_id != account_id:
        return {
            "error": "confirmation_required",
            "message": "Pass the exact account_id again as confirm_account_id. No profile or conversation is deleted.",
        }
    from .registry import DynamicAccountRegistry

    path = os.environ.get("GEMINI_REGISTRY_PATH")
    key = _configured_registry_key()
    if not path or not key:
        return {"error": "account_registry_not_configured"}
    subject = _oauth_subject(ctx)
    registry = DynamicAccountRegistry(path, key)
    owned = [item for item in registry.list_safe(subject) if item["account"] == account_id]
    if not owned:
        return {"error": "account_not_found"}
    manager = ctx.request_context.lifespan_context["account_clients"]
    conversations = manager.conversations.count_for_account(subject, account_id)
    active_sessions = sum(1 for key in manager.sessions if key[:2] == (subject, account_id))
    registry.set_state(account_id, "disabled")
    return {
        "deactivated": account_id,
        "profile_deleted": False,
        "conversations_deleted": False,
        "referenced_conversations": conversations,
        "active_session_references": active_sessions,
    }


@mcp.tool(name="gemini_delete_account", annotations={"title": "Delete Gemini Account Registry Record", "readOnlyHint": False})
async def gemini_delete_account(account_id: str, confirm_delete: str, ctx: Context) -> dict:
    """Delete only a disabled registry record after an exact confirmation.

    Chromium profiles and local conversations remain intact for manual recovery.
    """
    expected = f"DELETE:{account_id}"
    if not account_id or confirm_delete != expected:
        return {
            "error": "confirmation_required",
            "message": "Pass confirm_delete exactly as DELETE:<account_id>. Profiles and conversations are retained.",
        }
    from .registry import DynamicAccountRegistry

    path = os.environ.get("GEMINI_REGISTRY_PATH")
    key = _configured_registry_key()
    if not path or not key:
        return {"error": "account_registry_not_configured"}
    subject = _oauth_subject(ctx)
    registry = DynamicAccountRegistry(path, key)
    records = [item for item in registry.list_safe(subject) if item["account"] == account_id]
    if not records:
        return {"error": "account_not_found"}
    if records[0].get("state") != "disabled":
        return {"error": "account_must_be_deactivated_first"}
    manager = ctx.request_context.lifespan_context["account_clients"]
    conversations = manager.conversations.count_for_account(subject, account_id)
    registry.delete(subject, account_id)
    return {
        "deleted_registry_record": account_id,
        "profile_deleted": False,
        "conversations_deleted": False,
        "retained_conversation_references": conversations,
    }


@mcp.tool(
    name="gemini_server_info",
    annotations={"title": "Gemini MCP Server Information", "readOnlyHint": True},
)
async def gemini_server_info() -> dict:
    """Return non-sensitive build, protocol, capability, and schema metadata."""
    tools = sorted(name for name in mcp._tool_manager._tools if name.startswith("gemini_"))
    model_metadata = discovery_metadata()
    db_path = Path(os.environ.get(
        "GEMINI_CONVERSATIONS_PATH", "/var/lib/gemini-mcp/conversations.sqlite3"
    )).expanduser()
    schema_version = 0
    try:
        import sqlite3
        if db_path.is_file():
            with sqlite3.connect(db_path, timeout=2) as db:
                schema_version = int(db.execute("PRAGMA user_version").fetchone()[0])
    except (OSError, sqlite3.Error):
        schema_version = -1
    from .upstream import inspect_upstream

    provenance = build_provenance()
    build_commit = provenance["commit"]
    build_timestamp = provenance["timestamp"]
    build_version = provenance["version"] if provenance["version"] != "unknown" else _app_version()
    return {
        "application_version": _app_version(),
        "build_commit": build_commit,
        "build_provenance": {
            "commit": build_commit,
            "timestamp": build_timestamp,
            "version": build_version,
            "source": provenance["source"],
            "stream_serializer": _STREAM_SERIALIZER_VERSION,
            "upstream_revision": "4402e5231760a1e85e28eb6cd0040ff302b764de",
        },
        "protocol": {
            "adapter": "legacy-client-quarantine",
            "adapter_version": "xob0t-4402e523-compat1",
            "serializer_revision": SERIALIZER_REVISION,
            "capability_policy": "account_scoped_verified_routes_only",
        },
        "mcp_protocol_version": "2025-03-26",
        "enabled_capabilities": [
            "chat", "persistent_conversations", "image_generation", "image_editing",
            "file_upload", "url_analysis", "account_scoped_model_catalog",
            "versioned_protocol_adapter",
            "native_mcp_image_content", "account_private_media_artifacts",
        ],
        "registered_tools": tools,
        "registered_tool_count": len(tools),
        "model_discovery": {
            "mode": model_metadata["mode"],
            "thinking_level_protocol_verified": model_metadata["thinking_level_protocol_verified"],
        },
        "conversation_schema_version": schema_version,
        "upstream": inspect_upstream().public(),
    }


@mcp.tool(
    name="gemini_video_capabilities",
    annotations={"title": "Gemini Video Capabilities", "readOnlyHint": True},
)
async def gemini_video_capability_report() -> dict:
    """Report browser observations separately from MCP-callable support."""
    return gemini_video_capabilities()


@mcp.tool(
    name="notebooklm_capabilities",
    annotations={"title": "NotebookLM Capabilities", "readOnlyHint": True},
)
async def notebooklm_capability_report() -> dict:
    """Report verified consumer and enterprise NotebookLM boundaries."""
    return notebooklm_capabilities()


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
    ctx.request_context.lifespan_context["account_clients"].model_catalog.invalidate()
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
    manager = ctx.request_context.lifespan_context["account_clients"]
    key = manager.conversation_owner(ctx)
    try:
        models, discovered_at = manager.model_catalog.get(key, client)
        authenticated = any(item.source == "authenticated_rpc" for item in models)
        return {
            "models": [item.public(discovered_at=discovered_at) for item in models],
            "discovery": {
                "account_scoped": True,
                "cached": True,
                "thinking_controls": "only listed when a verified route is configured",
                **discovery_metadata(client),
                **({
                    "mode": "authenticated_get_user_status",
                    "authenticated_account_capability_endpoint": True,
                    "reason": "models and routes were returned for this authenticated account",
                } if authenticated else {}),
            },
        }
    except Exception as exc:
        logger.warning("model discovery unavailable: %s", type(exc).__name__)
        return {"models": capabilities_json(None), "warning": "discovery_unavailable"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_client(ctx: Context):
    return await ctx.request_context.lifespan_context["account_clients"].get(ctx)


async def _select_model(ctx: Context, client, model: str | None, thinking_level: str | None = None):
    """Resolve a public model through this account's short-lived catalog."""
    manager = ctx.request_context.lifespan_context["account_clients"]
    key = manager.conversation_owner(ctx)
    capabilities, _ = manager.model_catalog.get(key, client)
    requested = model
    if requested is None and any(item.source == "authenticated_rpc" for item in capabilities):
        requested = capabilities[0].id
    return resolve_model(capabilities, requested or DEFAULT_MODEL, thinking_level)


def _get_sessions(ctx: Context) -> dict:
    return ctx.request_context.lifespan_context["account_clients"].sessions


def _conversation_session(ctx: Context, row: dict, client, selection):
    """Reconstruct an upstream ChatSession from persisted Gemini metadata."""
    metadata = [row.get("cid"), row.get("rid"), row.get("rcid")]
    if not any(metadata):
        return client.start_chat(model=selection.upstream_model)
    return client.start_chat(metadata=metadata, model=selection.upstream_model)


def _get_media_dir(ctx: Context) -> Path:
    return ctx.request_context.lifespan_context["account_clients"].media_dir(ctx)


def _native_image_content(path: str):
    """Validate image bytes and return an MCP image with MIME from the bytes."""
    resolved = Path(path).expanduser().resolve(strict=True)
    artifact = create_media_artifact(
        resolved,
        account_id="local-content-validation",
        media_type=MediaType.IMAGE,
        media_root=resolved.parent,
    )
    return artifact.as_image_content("local-content-validation")


_UPLOAD_MIME_TYPES = {
    "text/plain", "application/pdf", "image/png", "image/jpeg", "image/webp",
    "text/markdown", "text/csv", "application/json", "video/mp4", "video/webm",
}


def _validate_upload_path(file_path: str) -> Path:
    """Validate a caller-supplied path without broadening filesystem access."""
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    max_bytes = int(os.environ.get("GEMINI_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
    if path.stat().st_size > max_bytes:
        raise ValueError("file_upload_failed — file exceeds the configured upload limit")
    mime, _ = mimetypes.guess_type(path.name)
    if mime and mime not in _UPLOAD_MIME_TYPES:
        raise ValueError("file_upload_failed — unsupported file type")
    return path


def _decode_upload_bytes(filename: str, data_base64: str) -> tuple[str, bytes]:
    """Decode bounded MCP-uploaded bytes and validate their declared type."""
    name = Path(filename).name
    if not name or name in {".", ".."}:
        raise ValueError("file_upload_failed — a filename is required")
    max_bytes = int(os.environ.get("GEMINI_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
    if not isinstance(data_base64, str) or len(data_base64) > ((max_bytes + 2) // 3) * 4 + 16:
        raise ValueError("file_upload_failed — file exceeds the configured upload limit")
    try:
        data = base64.b64decode(data_base64, validate=True)
    except (ValueError, TypeError, base64.binascii.Error) as exc:
        raise ValueError("file_upload_failed — data is not valid base64") from exc
    if not data:
        raise ValueError("file_upload_failed — empty files are not supported")
    if len(data) > max_bytes:
        raise ValueError("file_upload_failed — file exceeds the configured upload limit")
    mime, _ = mimetypes.guess_type(name)
    if mime and mime not in _UPLOAD_MIME_TYPES:
        raise ValueError("file_upload_failed — unsupported file type")
    return name, data


_image_lock = asyncio.Lock()

# These values must be request-local.  The upstream parser is process-global;
# a module-global image flag caused unrelated Flash/upload requests that
# overlapped image generation to receive the image request rewrite.
_image_mode: ContextVar[bool] = ContextVar("gemini_image_mode", default=False)
_image_tokens: ContextVar[dict[str, str] | None] = ContextVar("gemini_image_tokens", default=None)
_image_metadata: ContextVar[list | None] = ContextVar("gemini_image_metadata", default=None)
_request_diagnostics: ContextVar[dict | None] = ContextVar(
    "gemini_request_diagnostics", default=None
)
_STREAM_SERIALIZER_VERSION = "modern-v2-locale-bound"


def _modern_stream_request(
    headers: dict,
    data: dict,
    *,
    selector_session_id: str,
    language: str = "en",
    request_uuid: str | None = None,
) -> tuple[dict, dict]:
    """Apply the authenticated StreamGenerate request contract.

    Maintained Gemini-API mirrors the discovered model number into f.req[79],
    appends thinking mode and a client-session UUID to the model selector, and
    binds a separate per-request UUID in both the body and headers.  The pinned
    fork predates all of those fields.
    """
    return bind_stream_request(
        headers, data, selector_session_id=selector_session_id,
        language=language, request_uuid=request_uuid,
    )


def _image_parser_cookies(client, provided):
    """Return the authenticated cookie jar used by GeneratedImage validation."""
    try:
        if provided is not None and len(provided) > 0:
            return provided
    except TypeError:
        pass
    for candidate in (
        getattr(client, "cookies", None),
        getattr(getattr(client, "client", None), "cookies", None),
    ):
        try:
            if candidate is not None and len(candidate) > 0:
                return candidate
        except TypeError:
            continue
    return provided


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
    selector_session_id = str(uuid.uuid4()).upper()

    async def patched_request(method, url, **kwargs):
        # Configured model routes may carry evidence-backed request headers.
        # They are ContextVar-scoped so concurrent account requests cannot
        # leak a selected model or thinking configuration into each other.
        configured_headers = current_request_headers()
        if configured_headers:
            headers = dict(kwargs.get("headers") or {})
            headers.update(configured_headers)
            kwargs["headers"] = headers
        configured_fields = current_request_fields()
        if method == "POST" and "StreamGenerate" in str(url) and configured_fields:
            data = kwargs.get("data")
            if isinstance(data, dict) and "f.req" in data:
                outer = json.loads(data["f.req"])
                inner = json.loads(outer[1])
                required = max(configured_fields) + 1
                if len(inner) < required:
                    inner.extend([None] * (required - len(inner)))
                for index, value in configured_fields.items():
                    inner[index] = value
                outer[1] = json.dumps(inner)
                data = dict(data)
                data["f.req"] = json.dumps(outer)
                kwargs["data"] = data
        if method == "POST" and "StreamGenerate" in str(url) and configured_headers:
            # Maintained Gemini-API binds the locale in both f.req[1] and the
            # query string. The pinned fork only serialized the body value.
            # Modern Flash and Pro reject that incomplete request even though
            # Flash-Lite currently accepts it.
            params = dict(kwargs.get("params") or {})
            params["hl"] = os.environ.get("GEMINI_LANGUAGE", "en")
            kwargs["params"] = params
            headers, data = _modern_stream_request(
                dict(kwargs.get("headers") or {}),
                kwargs.get("data") if isinstance(kwargs.get("data"), dict) else {},
                selector_session_id=selector_session_id,
                language=os.environ.get("GEMINI_LANGUAGE", "en"),
            )
            kwargs["headers"] = headers
            kwargs["data"] = data
            try:
                model_header = json.loads(headers[MODEL_ROUTE_HEADER])
                outer = json.loads(data["f.req"])
                inner = json.loads(outer[1])
                _request_diagnostics.set({
                    "model": current_request_model_id() or "unknown",
                    "serializer": _STREAM_SERIALIZER_VERSION,
                    "model_number": inner[79] if len(inner) > 79 else None,
                    "field_count": len(inner),
                    "locale_bound": bool(params.get("hl")),
                    "selector_session_bound": len(model_header) >= 17 and isinstance(model_header[-1], str),
                    "request_uuid_bound": bool(headers.get("x-goog-ext-525005358-jspb")) and len(inner) > 59 and bool(inner[59]),
                })
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                _request_diagnostics.set({
                    "model": current_request_model_id() or "unknown",
                    "serializer": _STREAM_SERIALIZER_VERSION,
                    "serialization_valid": False,
                })
        if method == "POST" and "StreamGenerate" in str(url) and _image_mode.get():
            headers = kwargs.get("headers") or {}

            # Per-request UUID — the browser uses an UPPERCASE uuid, embedded in
            # both jspb headers AND inner[59], all three must match.
            req_uuid = str(uuid.uuid4()).upper()

            # Use the captured image selector as a base. Runtime thinking and
            # session fields are bound below by the same modern serializer used
            # for authenticated text models.
            if not _NO_REMAP:
                headers["x-goog-ext-525001261-jspb"] = (
                    '[1,null,null,null,"' + _IMAGE_MODEL_ID + '",null,null,0,'
                    + _IMAGE_MODEL_CAPS + ',null,null,2,null,null,1]'
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

            headers, data = _modern_stream_request(
                dict(kwargs.get("headers") or {}),
                kwargs.get("data") if isinstance(kwargs.get("data"), dict) else {},
                selector_session_id=selector_session_id,
                language=os.environ.get("GEMINI_LANGUAGE", "en"),
                request_uuid=req_uuid,
            )
            kwargs["headers"] = headers
            kwargs["data"] = data

        response = await _orig_request(method, url, **kwargs)
        diagnostics = _request_diagnostics.get()
        if method == "POST" and "StreamGenerate" in str(url) and diagnostics is not None:
            diagnostics = dict(diagnostics)
            diagnostics["http_status"] = getattr(response, "status_code", "unknown")
            _request_diagnostics.set(diagnostics)
        return response

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
        effective_cookies = _image_parser_cookies(gemini_client, cookies)
        for gen_img_data in entry["8"]:
            # Structure: [[[null, null, null, [null, 1, "filename.png", "url", ...], ...], ...]]
            url_arr = get_nested_value(gen_img_data, [0, 0, 3])
            if isinstance(url_arr, list) and len(url_arr) >= 4:
                url = url_arr[3]
                title = url_arr[2] or "[Generated Image]"
                token = url_arr[4] if len(url_arr) > 4 else None
                if url and isinstance(url, str) and url.startswith("http"):
                    tokens = _image_tokens.get()
                    if token and tokens is not None:
                        tokens[url] = token
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
                            cookies=effective_cookies,
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
        # Do not inspect or mutate image state for ordinary text/upload calls.
        # This parser is shared by every GeminiClient in this process.
        if not _image_mode.get():
            return parts, remaining
        tokens = _image_tokens.get()
        metadata = _image_metadata.get()
        if tokens is None or metadata is None:
            return parts, remaining
        for part in parts:
            inner_json_str = get_nested_value(part, [2])
            if not inner_json_str:
                continue
            try:
                part_json = _json.loads(inner_json_str)
                # Capture conversation metadata (cid/rid)
                m_data = get_nested_value(part_json, [1])
                if isinstance(m_data, list) and len(m_data) >= 2 and m_data[0]:
                    if len(metadata) >= 3:
                        metadata[0] = m_data[0]
                        metadata[1] = m_data[1]
                    else:
                        metadata.clear()
                        metadata.extend(m_data)
                # Capture image download tokens + rcid from candidates
                candidates = get_nested_value(part_json, [4], [])
                for cand in candidates:
                    rcid = get_nested_value(cand, [0])
                    if rcid and isinstance(rcid, str) and rcid.startswith("rc_"):
                        if len(metadata) >= 2:
                            if len(metadata) == 2:
                                metadata.append(rcid)
                            else:
                                metadata[2] = rcid
                    # Old format: [12][7][0]
                    for gid in get_nested_value(cand, [12, 7, 0], []):
                        url = get_nested_value(gid, [0, 3, 3])
                        token = get_nested_value(gid, [0, 3, 5])
                        if url and token:
                            tokens[url] = token
                    # New dict format: [12][0]["8"]
                    val12 = get_nested_value(cand, [12])
                    if isinstance(val12, list) and val12 and isinstance(val12[0], dict):
                        for gid in val12[0].get("8", []):
                            url_arr = get_nested_value(gid, [0, 0, 3])
                            if isinstance(url_arr, list) and len(url_arr) >= 5:
                                url, token = url_arr[3], url_arr[4]
                                if url and token:
                                    tokens[url] = token
            except Exception:
                pass
        return parts, remaining

    _gwc.parse_response_by_frame = _patched_parse
    logger.info("Patched GeminiClient with browser-compatible parameters")


class _ResponseParseError(RuntimeError):
    """The upstream response completed but did not contain a usable result."""


class _UpstreamRejectedError(RuntimeError):
    """Gemini returned a structured candidate that is an error, not an answer."""


_GENERIC_UPSTREAM_FAILURES = (
    "sorry, something went wrong",
    "i seem to be encountering an error",
    "i encountered an error doing what you asked",
    "i'm having a hard time fulfilling your request",
    "i’m having a hard time fulfilling your request",
    "can i help you with something else instead",
)


def _upstream_failure_closes_client(error: Exception) -> bool:
    """Whether a failed upstream call must not leave its client cached.

    The pinned client closes itself before raising these stream failures.
    Keeping that instance cached makes the next request hit a closed HTTP
    session, so evict it and let the next request rebuild from the existing
    account profile.
    """
    from gemini_webapi import APIError, RequestTimeoutError

    if isinstance(error, RequestTimeoutError):
        return True
    if not isinstance(error, APIError):
        return False
    detail = str(error).lower()
    return any(marker in detail for marker in (
        "response stalled", "zombie stream", "stream interrupted", "truncated",
    ))


def _response_text(response, *, operation: str) -> str:
    """Return a real model answer, never a known upstream failure placeholder.

    The web endpoint can emit an error as an otherwise-valid text candidate.
    First validate the response envelope; the narrow text check is a final
    safety net for that undocumented behaviour, not the primary classifier.
    """
    candidates = getattr(response, "candidates", None)
    if not isinstance(candidates, list) or not candidates:
        raise _ResponseParseError(f"Gemini returned no candidates for {operation}")
    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text.strip():
        raise _ResponseParseError(f"Gemini returned no text for {operation}")
    from .protocol.normalization import normalize_response_text

    public = normalize_response_text(text).text
    normalized = " ".join(public.split()).lower()
    if any(marker in normalized for marker in _GENERIC_UPSTREAM_FAILURES):
        diagnostics = _request_diagnostics.get() or {}
        safe_keys = (
            "model", "serializer", "model_number", "field_count",
            "locale_bound", "selector_session_bound", "request_uuid_bound",
            "http_status", "serialization_valid",
        )
        detail = ", ".join(
            f"{key}={diagnostics[key]}" for key in safe_keys if key in diagnostics
        )
        suffix = f" ({detail}, parser_stage=candidate_text)" if detail else ""
        raise _UpstreamRejectedError(f"Gemini rejected the {operation} request{suffix}")
    return public


def _handle_error(e: Exception, *, operation: str | None = None) -> str:
    from gemini_webapi import AuthError, APIError, RequestTimeoutError
    from gemini_webapi.exceptions import (
        ImageGenerationBlocked,
        ModelInvalid,
        RateLimitExceeded,
        UsageLimitExceeded,
    )

    if isinstance(e, ModelSelectionError):
        return f"Error: {e.code} — {e}"
    if isinstance(e, _ResponseParseError):
        return f"Error: response_parse_error — {e}"
    if isinstance(e, _UpstreamRejectedError):
        return f"Error: upstream_rejected — {e}"
    if isinstance(e, ImageGenerationBlocked):
        return "Error: image_generation_unavailable — Gemini reports that image generation is unavailable for this account or region."
    if isinstance(e, ModelInvalid):
        return f"Error: model_unavailable — {e}"
    if isinstance(e, (RateLimitExceeded, UsageLimitExceeded)):
        return f"Error: rate_limited — {e}"
    if isinstance(e, AuthError):
        return (
            "Error: authentication_error — Cookies may have expired. "
            "Re-login to gemini.google.com in Chrome, then call gemini_reset."
        )
    if isinstance(e, RequestTimeoutError):
        return "Error: request_timeout — Gemini did not respond before the configured timeout."
    if isinstance(e, APIError):
        detail = str(e)
        lowered = detail.lower()
        status = re.search(r"(?:status|http)\s*[:=]?\s*(\d{3})", lowered)
        if status and status.group(1) == "429":
            return f"Error: rate_limited — {detail}"
        if status and status.group(1) in {"401", "403"}:
            return f"Error: authentication_error — {detail}"
        if status and status.group(1) == "404":
            return f"Error: model_unavailable — {detail}"
        if "response stalled" in lowered or "zombie stream" in lowered:
            return "Error: upstream_stall — Gemini's stream stopped producing progress; no model substitution was attempted."
        if "truncated" in lowered or "stream interrupted" in lowered:
            return f"Error: response_parse_error — Gemini closed or truncated the response before a complete frame arrived: {detail}"
        if "connection" in lowered and ("closed" in lowered or "reset" in lowered):
            return f"Error: upstream_rejected — Gemini closed the response connection: {detail}"
        if ("not available" in lowered or "not enabled" in lowered or "upgrade" in lowered) and "pro" in lowered:
            return f"Error: model_unavailable — Gemini Pro is not available to this account: {detail}"
        if "model" in lowered and ("unknown" in lowered or "invalid" in lowered):
            return f"Error: model_unavailable — {detail}"
        if "rate" in lowered or "too many requests" in lowered:
            return f"Error: rate_limited — {detail}"
        if "parse" in lowered or "response body" in lowered:
            return f"Error: response_parse_error — {detail}"
        if operation == "upload":
            return f"Error: upload_failed — {detail}"
        return f"Error: unknown_upstream_error — {detail}"
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return "Error: request_timeout — Gemini did not respond before the configured timeout."
    if operation == "image" and isinstance(e, ValueError):
        detail = re.sub(r"[\r\n]+", " ", str(e)).strip()
        return f"Error: image_generation_failed — {detail or 'Gemini returned invalid image metadata'}"
    if operation == "upload":
        if isinstance(e, AttributeError):
            # AttributeError used to be returned without the missing attribute,
            # making the library/API mismatch impossible to identify remotely.
            detail = re.sub(r"[\r\n]+", " ", str(e)).strip()
            return f"Error: upload_client_incompatible — {detail or 'the upload client API is incompatible'}"
        return f"Error: upload_failed — {type(e).__name__}"
    return f"Error: unknown_upstream_error — {type(e).__name__}"


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
    thinking_level: Optional[str] = None,
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
        manager = ctx.request_context.lifespan_context["account_clients"]
        subject, account = manager.conversation_owner(ctx)
        selection = await _select_model(ctx, client, model, thinking_level)
        session_id = manager.conversations.create(subject, account, selection.id, selection.thinking_level)
        return json.dumps({
            "session_id": session_id,
            "model": selection.id,
            "thinking_level": selection.thinking_level,
            "persistent": True,
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
    thinking_level: Optional[str] = None,
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

        manager = ctx.request_context.lifespan_context["account_clients"]
        subject, account = manager.conversation_owner(ctx)
        if session_id:
            try:
                row = manager.conversations.get(subject, account, session_id)
            except KeyError:
                return "Error: session_not_found — start a new conversation with gemini_start_chat."
            except TimeoutError:
                return "Error: session_expired — start a new conversation with gemini_start_chat."
            if model is not None and model != row["model"]:
                return "Error: session_model_mismatch — use gemini_switch_model before changing a persistent conversation."
            if thinking_level is not None and thinking_level != row.get("thinking_level"):
                return "Error: session_thinking_level_mismatch — start a new conversation or switch the saved model selection."
            selection = await _select_model(ctx, client, row["model"], row.get("thinking_level"))
            chat = _conversation_session(ctx, row, client, selection)
            try:
                async with manager.request_slot(ctx):
                    with model_request_context(selection):
                        response = await asyncio.wait_for(
                            chat.send_message(prompt, current_retry=UPSTREAM_RETRY_BUDGET),
                            timeout=CHAT_TIMEOUT,
                        )
            except asyncio.TimeoutError:
                await manager.invalidate(ctx, client)
                raise
            text = _response_text(response, operation="chat")
            manager.conversations.add_messages(subject, account, session_id, [("user", prompt), ("assistant", text)])
            manager.conversations.update_metadata(subject, account, session_id, [chat.cid, chat.rid, chat.rcid])
        else:
            selection = await _select_model(ctx, client, model, thinking_level)
            try:
                async with manager.request_slot(ctx):
                    with model_request_context(selection):
                        response = await asyncio.wait_for(
                            client.generate_content(
                                prompt,
                                model=selection.upstream_model,
                                current_retry=UPSTREAM_RETRY_BUDGET,
                            ),
                            timeout=CHAT_TIMEOUT,
                        )
            except asyncio.TimeoutError:
                await manager.invalidate(ctx, client)
                raise

        text = _response_text(response, operation="chat")
        return text
    except Exception as e:
        if "manager" in locals() and "client" in locals() and _upstream_failure_closes_client(e):
            await manager.invalidate(ctx, client)
        return _handle_error(e)


@mcp.tool(name="gemini_list_chats", annotations={"title": "List Gemini Conversations", "readOnlyHint": True})
async def gemini_list_chats(ctx: Context, limit: int = 50, offset: int = 0) -> dict:
    manager = ctx.request_context.lifespan_context["account_clients"]
    subject, account = manager.conversation_owner(ctx)
    conversations = manager.conversations.list(subject, account, limit, offset)
    return {"conversations": conversations, "offset": max(0, offset), "limit": max(1, min(limit, 100)), "next_offset": offset + len(conversations) if len(conversations) == max(1, min(limit, 100)) else None}


@mcp.tool(name="gemini_get_chat", annotations={"title": "Get Gemini Conversation", "readOnlyHint": True})
async def gemini_get_chat(session_id: str, ctx: Context) -> dict:
    manager = ctx.request_context.lifespan_context["account_clients"]
    subject, account = manager.conversation_owner(ctx)
    try:
        return manager.conversations.get(subject, account, session_id)
    except KeyError:
        return {"error": "session_not_found"}
    except TimeoutError:
        return {"error": "session_expired"}


@mcp.tool(name="gemini_get_chat_history", annotations={"title": "Get Local Gemini Conversation History", "readOnlyHint": True})
async def gemini_get_chat_history(session_id: str, ctx: Context, limit: int = 100) -> dict:
    manager = ctx.request_context.lifespan_context["account_clients"]
    subject, account = manager.conversation_owner(ctx)
    try:
        return {"session_id": session_id, "source": "local_mcp_history", "messages": manager.conversations.history(subject, account, session_id, limit)}
    except KeyError:
        return {"error": "session_not_found"}
    except TimeoutError:
        return {"error": "session_expired"}


@mcp.tool(name="gemini_delete_chat", annotations={"title": "Delete Local Gemini Conversation", "readOnlyHint": False})
async def gemini_delete_chat(session_id: str, ctx: Context, confirm: bool = False) -> dict:
    if not confirm:
        return {"error": "confirmation_required", "message": "Set confirm=true to delete only this local conversation."}
    manager = ctx.request_context.lifespan_context["account_clients"]
    subject, account = manager.conversation_owner(ctx)
    try:
        manager.conversations.delete(subject, account, session_id)
        return {"deleted": session_id}
    except KeyError:
        return {"error": "session_not_found"}
    except TimeoutError:
        return {"error": "session_expired"}


@mcp.tool(name="gemini_switch_model", annotations={"title": "Switch Conversation Model", "readOnlyHint": False})
async def gemini_switch_model(session_id: str, model: str, ctx: Context,
                              thinking_level: Optional[str] = None) -> dict:
    """Change the model used when reconstructing a persisted conversation."""
    if not model or len(model) > 128:
        return {"error": "unsupported_model", "message": "A non-empty model identifier is required."}
    manager = ctx.request_context.lifespan_context["account_clients"]
    subject, account = manager.conversation_owner(ctx)
    try:
        row = manager.conversations.get(subject, account, session_id)
        client = await _get_client(ctx)
        selection = await _select_model(ctx, client, model, thinking_level)
        manager.conversations.update_model(subject, account, session_id, selection.id, selection.thinking_level)
        return {"session_id": session_id, "model": selection.id,
                "thinking_level": selection.thinking_level,
                "upstream_conversation_preserved": bool(row.get("cid"))}
    except KeyError:
        return {"error": "session_not_found"}
    except TimeoutError:
        return {"error": "session_expired"}


@mcp.tool(
    name="gemini_generate_image",
    # The tool returns a text metadata block plus native MCP Image blocks.
    # Structured output would wrap the return value in {result: ...} and
    # validate it against the -> str annotation before content conversion.
    structured_output=False,
    annotations={
        "title": "Gemini Image Generation",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
    meta={
        # MCP Apps is an optional presentation extension. The tool remains
        # model-visible and returns complete standard MCP content regardless
        # of whether a host advertises or renders the UI resource.
        "ui": {
            "resourceUri": IMAGE_VIEWER_URI,
            "visibility": ["model", "app"],
        },
        "openai/outputTemplate": IMAGE_VIEWER_URI,
        "openai/ui": {"availableDisplayModes": ["inline", "fullscreen"]},
    },
)
async def gemini_generate_image(
    prompt: str,
    ctx: Context,
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
    files: Optional[list[str]] = None,
    conversation_id: Optional[list[str]] = None,
):
    """Generate or edit images with Gemini.

    Without files: generates a new image from the text prompt.
    With files: edits/transforms the provided image(s) based on the prompt.

    Pass conversation_id from a previous call to continue refining images
    in the same conversation thread (e.g. "make it more dramatic", "add rain").
    You can also use a cid from the Gemini web URL (gemini.google.com/app/{cid}).

    Images are returned as native MCP image content and an interactive MCP App
    image preview/gallery for clients that support widgets. Private storage
    paths are deliberately omitted from user-visible and structured results.
    Clients without widget support should display the returned image content directly
    in the conversation using the native image block; do not repeat Gemini's internal
    <Image ...> protocol marker as text or offer a text/SVG substitute when an
    image block is present.

    Args:
        prompt: Description of the image to generate, or editing instruction
                (e.g. 'change the background to blue', 'make it a cartoon').
        model: Optional catalog model to validate for image generation. The
               authenticated Flash family is supported; Flash-Lite and Pro
               are rejected until an image route is independently verified.
        files: Optional list of file paths to images to edit/transform.
        conversation_id: Optional list of [cid, rid, rcid] from a previous
                         gemini_generate_image response to continue the conversation.
                         Passing just [cid] (from browser URL) also works.

    Returns:
        Native MCP image content plus structured private-artifact metadata and
        the conversation identifier needed for authorized follow-up editing.
    """
    t0 = time.monotonic()
    correlation_id = new_correlation_id()
    diagnostic_model = model or DEFAULT_MODEL
    image_delivery_event(
        "tool_execution_started",
        correlation_id=correlation_id,
        status="started",
        model=diagnostic_model,
    )
    try:
        if thinking_level is not None:
            # Image generation uses a separately captured browser route. The
            # installed client exposes no verified independent thinking knob
            # for that route, so accepting it would be a misleading no-op.
            raise ModelSelectionError(
                "thinking_not_supported",
                "image generation has no verified independent thinking-level request mapping",
            )
        client = await _get_client(ctx)
        if model is not None:
            selection = await _select_model(ctx, client, model)
            if "flash" not in selection.id or "lite" in selection.id:
                raise ModelSelectionError(
                    "image_model_not_supported",
                    "the requested catalog model has no verified image-generation route",
                )

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
            mode_token = _image_mode.set(True)
            tokens_token = _image_tokens.set({})
            metadata_token = _image_metadata.set([])
            try:
                if conversation_id:
                    chat = client.start_chat(
                        metadata=conversation_id,
                        model="gemini-3.0-flash",
                    )
                    gen_coro = chat.send_message(
                        prompt, files=resolved_files or None, current_retry=UPSTREAM_RETRY_BUDGET
                    )
                else:
                    # The installed fork needs a known enum value to enter its
                    # generator. _patch_client replaces this stale seed header
                    # with the separately verified image-mode route.
                    kwargs = {"model": "gemini-3.0-flash"}
                    if resolved_files:
                        kwargs["files"] = resolved_files
                    kwargs["current_retry"] = UPSTREAM_RETRY_BUDGET
                    gen_coro = client.generate_content(prompt, **kwargs)
                # Hard cap: never let the library's stall-retry loop run for
                # minutes when Gemini throttles the account. Fail fast instead.
                response = await asyncio.wait_for(gen_coro, timeout=GEN_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("generate_content exceeded %.0fs cap — aborting", GEN_TIMEOUT)
                await ctx.request_context.lifespan_context["account_clients"].invalidate(ctx, client)
                image_delivery_event(
                    "tool_execution_failed",
                    correlation_id=correlation_id,
                    status="timeout",
                    model=diagnostic_model,
                    exception_category="TimeoutError",
                    duration_ms=round((time.monotonic() - t0) * 1000),
                )
                return (
                    f"Error: Gemini did not return an image within {GEN_TIMEOUT:.0f}s "
                    "via the library (the browser is usually faster). Try again."
                )
            finally:
                _image_mode.reset(mode_token)
            _stage("generate_content returned", t)
        image_delivery_event(
            "gemini_generation_completed",
            correlation_id=correlation_id,
            status="complete",
            model=diagnostic_model,
            artifact_count=len(response.images),
            duration_ms=round((time.monotonic() - t0) * 1000),
        )

        if not response.images:
            # A text-only image response is not a successful image generation.
            # Preserve a real explanatory model response, but reject known
            # upstream failure placeholders.
            return _response_text(response, operation="image generation")

        # --- Try to get 2x download URLs via c8o8Fe RPC ---
        # Prefer request-local stream metadata (needed for c8o8Fe), then fall
        # back to the chat object when the upstream parser did not expose it.
        captured_metadata = _image_metadata.get() or []
        image_tokens = _image_tokens.get()
        if image_tokens is None:
            image_tokens = {}
        if captured_metadata and captured_metadata[0]:
            metadata = list(captured_metadata)
        elif chat:
            metadata = [chat.cid or "", chat.rid or "", chat.rcid or ""]
        else:
            metadata = []
        download_urls: dict[int, str] = {}
        if not _SKIP_2X:
            for i, image in enumerate(response.images):
                token = image_tokens.pop(image.url, None)
                if token and metadata:
                    logger.info("Requesting 2x download URL for image %d...", i)
                    dl_url = await _fetch_download_url(client, token, prompt, metadata, i)
                    if dl_url:
                        download_urls[i] = dl_url
                        logger.info("Got 2x download URL for image %d", i)
        image_tokens.clear()  # clean up any leftover tokens
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
            saved.append(
                {
                    "title": title,
                    "path": filepath,
                    "dir": str(media_dir),
                    "source_index": i,
                }
            )

        image_delivery_event(
            "image_download_completed",
            correlation_id=correlation_id,
            status="complete" if saved else "failed",
            artifact_count=len(saved),
            duration_ms=round((time.monotonic() - t0) * 1000),
        )

        # For response: prefer chat metadata (clean cid/rid/rcid), fall back to raw
        if chat:
            conv_id = [chat.cid or "", chat.rid or "", chat.rcid or ""]
        else:
            conv_id = metadata[:3] if metadata and metadata[0] else None
        manager = ctx.request_context.lifespan_context["account_clients"]
        subject, account = manager.conversation_owner(ctx)
        artifact_owner = f"{subject}\x1f{account}"
        artifacts = []
        image_blocks = []
        extracted_image_indices = []
        for item in saved:
            try:
                artifact = create_media_artifact(
                    item["path"],
                    account_id=artifact_owner,
                    conversation_reference=conv_id,
                    media_type=MediaType.IMAGE,
                    media_root=media_dir,
                )
                artifacts.append(artifact)
                image_blocks.append(artifact.as_image_content(artifact_owner))
                extracted_image_indices.append(item["source_index"])
            except (OSError, ValueError):
                logger.warning("Generated image artifact validation failed", exc_info=True)

        if not image_blocks:
            raise RuntimeError("image generation returned no valid image artifacts")

        mime_types = [block.mimeType for block in image_blocks]
        encoded_bytes = sum(len(block.data) for block in image_blocks)
        image_delivery_event(
            "artifact_validation_completed",
            correlation_id=correlation_id,
            status="complete",
            artifact_count=len(artifacts),
            image_blocks=len(image_blocks),
            mime_types=mime_types,
            encoded_bytes=encoded_bytes,
        )

        from .protocol.normalization import normalize_response_text
        normalized = normalize_response_text(
            response.text or "", extracted_image_indices=extracted_image_indices
        )
        public_text = normalized.text.strip()
        visible_text = public_text or (
            "Generated image successfully."
            if len(image_blocks) == 1
            else f"Generated {len(image_blocks)} images successfully."
        )
        content = [TextContent(type="text", text=visible_text)]
        content.extend(image_blocks)
        result = CallToolResult(
            content=content,
            structuredContent={
                "text": visible_text,
                "prompt": prompt,
                "artifacts": [artifact.public_metadata() for artifact in artifacts],
                "conversation_id": conv_id,
                "correlation_id": correlation_id,
                "delivery": {
                    "status": "complete",
                    "image_blocks": len(image_blocks),
                    "text_blocks": 1,
                    "mime_types": mime_types,
                    "encoded_bytes": encoded_bytes,
                },
                "follow_ups": [
                    {"label": item.label, "query": item.query}
                    for item in normalized.follow_ups
                ],
            },
            isError=False,
            _meta={"correlation_id": correlation_id},
        )
        image_delivery_event(
            "mcp_result_serialization_ready",
            correlation_id=correlation_id,
            status="complete",
            artifact_count=len(artifacts),
            image_blocks=len(image_blocks),
            text_blocks=1,
            mime_types=mime_types,
            encoded_bytes=encoded_bytes,
            duration_ms=round((time.monotonic() - t0) * 1000),
        )
        return result

    except Exception as e:
        image_delivery_event(
            "tool_execution_failed",
            correlation_id=correlation_id,
            status="failed",
            model=diagnostic_model,
            exception_category=type(e).__name__,
            duration_ms=round((time.monotonic() - t0) * 1000),
        )
        return _handle_error(e, operation="image")
    finally:
        # Reset request-local parser state even when an image download fails.
        if "tokens_token" in locals():
            _image_tokens.reset(tokens_token)
        if "metadata_token" in locals():
            _image_metadata.reset(metadata_token)


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
    thinking_level: Optional[str] = None,
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
        try:
            p = _validate_upload_path(file_path)
        except FileNotFoundError as exc:
            return f"Error: File not found — {exc.args[0]}"
        except ValueError as exc:
            return f"Error: {exc}"

        client = await _get_client(ctx)
        manager = ctx.request_context.lifespan_context["account_clients"]
        selection = await _select_model(ctx, client, model, thinking_level)
        async with manager.request_slot(ctx):
            with model_request_context(selection):
                response = await asyncio.wait_for(
                    client.generate_content(
                        prompt, model=selection.upstream_model, files=[str(p)],
                        current_retry=UPSTREAM_RETRY_BUDGET,
                    ),
                    timeout=CHAT_TIMEOUT,
                )
        return _response_text(response, operation="file upload")

    except Exception as e:
        if "manager" in locals() and "client" in locals() and _upstream_failure_closes_client(e):
            await manager.invalidate(ctx, client)
        return _handle_error(e, operation="upload")


@mcp.tool(
    name="gemini_upload_file_bytes",
    annotations={
        "title": "Gemini Upload File Bytes",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def gemini_upload_file_bytes(
    filename: str,
    data_base64: str,
    ctx: Context,
    prompt: str = "Describe this file.",
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
) -> str:
    """Upload file bytes supplied by the MCP caller.

    This is the safe alternative when the caller's filesystem is not shared
    with the MCP container.  The decoded file is held in a private temporary
    directory for the duration of the upstream request and is then removed.
    The filename is reduced to a basename before it is used.
    """
    temp_dir: Path | None = None
    try:
        safe_name, data = _decode_upload_bytes(filename, data_base64)
        client = await _get_client(ctx)
        selection = await _select_model(ctx, client, model, thinking_level)
        account_clients = ctx.request_context.lifespan_context["account_clients"]
        account = re.sub(r"[^A-Za-z0-9_.-]", "_", account_clients.conversation_owner(ctx)[1])
        temp_dir = Path(tempfile.mkdtemp(prefix=f"gemini-upload-{account}-"))
        temp_path = temp_dir / safe_name
        temp_path.write_bytes(data)
        async with account_clients.request_slot(ctx):
            with model_request_context(selection):
                response = await asyncio.wait_for(
                    client.generate_content(
                        prompt, model=selection.upstream_model, files=[str(temp_path)],
                        current_retry=UPSTREAM_RETRY_BUDGET,
                    ),
                    timeout=CHAT_TIMEOUT,
                )
        return _response_text(response, operation="file upload")
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as e:
        if "account_clients" in locals() and "client" in locals() and _upstream_failure_closes_client(e):
            await account_clients.invalidate(ctx, client)
        return _handle_error(e, operation="upload")
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


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
    thinking_level: Optional[str] = None,
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
        manager = ctx.request_context.lifespan_context["account_clients"]
        selection = await _select_model(ctx, client, model, thinking_level)
        async with manager.request_slot(ctx):
            with model_request_context(selection):
                response = await asyncio.wait_for(
                    client.generate_content(
                        full_prompt, model=selection.upstream_model,
                        current_retry=UPSTREAM_RETRY_BUDGET,
                    ),
                    timeout=CHAT_TIMEOUT,
                )
        return _response_text(response, operation="URL analysis")
    except Exception as e:
        if "manager" in locals() and "client" in locals() and _upstream_failure_closes_client(e):
            await manager.invalidate(ctx, client)
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
        await new_client.init(
            timeout=float(os.environ.get("GEMINI_CLIENT_TIMEOUT", "300")),
            watchdog_timeout=float(os.environ.get("GEMINI_WATCHDOG_TIMEOUT", "90")),
            auto_close=False,
            auto_refresh=UPSTREAM_AUTO_REFRESH,
            refresh_interval=UPSTREAM_REFRESH_INTERVAL,
        )
        _patch_client(new_client)

        manager = ctx.request_context.lifespan_context["account_clients"]
        key = (subject, str(record["account"]))
        manager.clients[key] = LegacyClientAdapter(new_client, discover_authenticated_models)
        manager.model_catalog.invalidate(key)
        return "Gemini client re-initialised with fresh cookies."

    except Exception as e:
        return _handle_error(e)

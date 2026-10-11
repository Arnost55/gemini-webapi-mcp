<h1 align="center">gemini-webapi-mcp</h1>

<p align="center">
  MCP server for Google Gemini — image generation and editing, chat, and file analysis through browser sessions.<br>
  No API key required. Uses the Gemini Web experience.
</p>

<p align="center">
  <a href="https://github.com/Arnost55/gemini-webapi-mcp/blob/main/LICENSE"><img src="https://img.shields.io/github/license/AndyShaman/gemini-webapi-mcp?style=flat-square&color=green" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/MCP-compatible-8A2BE2?style=flat-square" alt="MCP">
  <a href="https://github.com/Arnost55/gemini-webapi-mcp/stargazers"><img src="https://img.shields.io/github/stars/AndyShaman/gemini-webapi-mcp?style=flat-square&color=yellow" alt="Stars"></a>
</p>

<p align="center">
  <a href="https://t.me/AI_Handler"><img src="https://img.shields.io/badge/Telegram-канал автора-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white" alt="Telegram"></a>
  &nbsp;
  <a href="https://www.youtube.com/channel/UCLkP6wuW_P2hnagdaZMBtCw"><img src="https://img.shields.io/badge/YouTube-канал автора-FF0000?style=for-the-badge&logo=youtube&logoColor=white" alt="YouTube"></a>
</p>

---

## Features

- **Image generation** from text descriptions (Nano Banana 2 with aspect ratio support)
- **2x resolution** — automatically downloads upscaled version (2048x2048 → 2816x1536 and above)
- **Image editing** — send an image + prompt to get a modified version
- **File analysis** — video, images, PDF, documents
- **Text chat** with Gemini (Flash, Pro, Flash-Thinking)
- **Auto watermark removal** — Gemini's sparkle mark is stripped by a built-in deterministic Reverse Alpha Blending pass (no external binaries, ML models, or downloads)
- **Auto-authentication** via Chrome browser cookies
- **Dynamic model discovery** through `gemini_list_models`, with capability metadata and explicit fallbacks
- **STDIO and Streamable HTTP** transports for local clients and authenticated MCP gateways
- **Docker and Docker Compose** deployment with persistent account volumes
- **Public URL ready** through an external HTTPS reverse proxy or MCP tunnel
- **Persistent Chromium session supervisor** with five-minute checks, reload recovery, and login-required status
- **One isolated Chromium browser per authorization** with its own profile, X display, VNC target, and expiring opaque capability
- **Universal embedded login viewer** with a responsive RFB surface, desktop mouse/keyboard controls, mobile touch scrolling, cursor suppression, fullscreen, status, countdown, and cancellation
- **Per-account scopes, maintenance mode, request limits, and safe status files**
- **Secure browser handoff state** with five-minute expiry and capability removal after authorization completes
- **Encrypted dynamic account registry** using AES-256-GCM; Base64 is only an encoding envelope
- **Multi-worker registry safety** with advisory locking, reload-before-mutate, fsync, and atomic replacement
- **Account lifecycle controls** for ready, disabled, and revoked profiles
- **Local OAuth/DCR foundation** for ChatGPT MCP clients with PKCE
- **Debian browser display** using Xvfb and internal websockify/RFB transport for GUI-free, per-connection login; the standard noVNC desktop UI is not exposed
- **Crash recovery** for service-owned Chromium/Xvfb/fluxbox/VNC handoffs after restart

## Quick Start

This fork is based on the verified upstream `AndyShaman/gemini-webapi-mcp`
repository at commit `69d2f5220ad25c9dbe8146d9300283d5dfafb174` and is licensed
under AGPL-3.0-or-later.

The server supports STDIO for local clients and Streamable HTTP for an
authenticated gateway. Model discovery is available through
`gemini_list_models`; unavailable discovery data is reported with explicit
fallback metadata.

### ChatGPT dynamic registration and connected accounts

Streamable HTTP also publishes local OAuth 2.1 metadata and supports dynamic
client registration at `/oauth/register`. Public clients must use PKCE
(`S256`); redirect URIs are validated and authorization codes are single-use.
This authenticates ChatGPT to the MCP server. It is not Google OAuth: Gemini
Web login happens separately in a short-lived, isolated Chromium desktop.
The server does not expose or return browser cookies.

The MCP exposes `gemini_list_accounts` and `gemini_select_account` for the
connected-account workflow. ChatGPT owns the visual account screen; the MCP
provides the metadata, authorization flow, identities, and account state that
screen consumes. Set `GEMINI_PUBLIC_URL` to the HTTPS origin before using
remote OAuth.

See [`docs/install.md`](docs/install.md) for complete local, Debian/Proxmox,
browser, account, and gateway installation instructions. See
[`docs/gateway.md`](docs/gateway.md) for OAuth boundaries, persistent sessions,
multi-account isolation, scopes, and rate limits.

The browser handoff is served by the built-in OAuth authorization page when
`GEMINI_PUBLIC_URL` and `GEMINI_NOVNC_WS_URL` are set. The link opens the
custom `/auth/browser/viewer` surface, which connects to isolated Chromium
through the internal RFB transport. It does not claim Google OAuth support for
Gemini Web.

The handoff creates an isolated login desktop. After login and OAuth state
validation, that profile is promoted into a new owner-bound persistent account
profile. The browser capability is removed, and future requests load cookies
only from the selected account profile.

### Multi-account Gemini sessions

Each Google account uses a separate persistent Chromium profile. OAuth
authentication identifies the caller, while Google login remains inside the
selected Gemini profile. A caller mapped to multiple accounts must choose one
explicitly.

Example account policy:

```json
{
  "subjects": {
    "oauth-subject-personal": ["personal"],
    "oauth-subject-work": ["work"]
  },
  "accounts": {
    "personal": {
      "scopes": ["gemini:use"],
      "requests_per_minute": 30
    },
    "work": {
      "scopes": ["gemini:use"],
      "requests_per_minute": 30
    }
  }
}
```

The registry rejects unmapped subjects, disabled accounts, missing scopes, and
ambiguous account selection. It writes only safe account status metadata and
never stores cookie values in logs or MCP results.

### Persistent browser session

Install the optional browser support and Chromium runtime:

```bash
uv sync --extra browser
uv run playwright install chromium
```

Bootstrap a profile with a visible browser, complete Google login, and exit:

```bash
uv run gemini-web-session \
  --profile-dir /var/lib/gemini-mcp/accounts/personal \
  --headed --once
```

Then run the supervisor headlessly:

```bash
uv run gemini-web-session \
  --profile-dir /var/lib/gemini-mcp/accounts/personal
```

The supervisor checks every five minutes by default. If Gemini redirects to
login, it records `login_required`; reloading a page cannot refresh Google
credentials. A Google browser rejection is recorded as `error` instead.

Google may reject Playwright's bundled Chromium with “This browser or app may
not be secure” because Google blocks sign-ins controlled by software
automation. The server does not bypass that protection. On a host where a
supported installed browser is available, select it explicitly with one of
these settings (never both):

```bash
GEMINI_BROWSER_CHANNEL=chrome       # or msedge
# GEMINI_BROWSER_EXECUTABLE=/usr/bin/google-chrome
```

The included Docker image installs Google Chrome Stable and uses the `chrome`
Playwright channel by default. Standalone installs use the same default unless
you set `GEMINI_BROWSER_CHANNEL` or `GEMINI_BROWSER_EXECUTABLE`. Rebuild and
recreate an existing container after changing the image; an environment
variable cannot install Chrome into an old image.

The login browser is intentionally launched with its sandbox enabled. The
project does not add `--no-sandbox`: Google may reject that unsupported flag,
and adding it would weaken the isolated login environment. If Chrome cannot
start without that flag, fix the container's sandbox/runtime permissions or
install a supported Chrome build rather than disabling the sandbox.

This changes only which browser Playwright launches; it does not spoof browser
identity or guarantee that Google will permit a remote/Xvfb session. If Google
still rejects the sign-in, use an approved normal browser login flow or an
official Google API OAuth integration. Do not add webdriver-removal flags or
user-agent spoofing.

### 1. Log into Gemini

Open Chrome, go to [gemini.google.com](https://gemini.google.com) and sign in.

### 2. Install the MCP server

**From GitHub (no clone needed):**

```bash
uv run --with "gemini-webapi-mcp @ git+https://github.com/Arnost55/gemini-webapi-mcp.git" gemini-webapi-mcp
```

**Local install:**

```bash
git clone https://github.com/Arnost55/gemini-webapi-mcp.git
cd gemini-webapi-mcp
uv sync
uv run gemini-webapi-mcp
```

### 3. Add MCP config

<details>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add-json gemini '{"command":"uv","args":["run","--with","gemini-webapi-mcp @ git+https://github.com/Arnost55/gemini-webapi-mcp.git","gemini-webapi-mcp"]}'
```

Or add manually to `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "gemini": {
      "command": "uv",
      "args": ["run", "--with", "gemini-webapi-mcp @ git+https://github.com/Arnost55/gemini-webapi-mcp.git", "gemini-webapi-mcp"]
    }
  }
}
```

</details>

<details>
<summary><b>Claude Desktop</b></summary>

Add to Claude Desktop config:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "gemini": {
      "command": "uv",
      "args": ["run", "--with", "gemini-webapi-mcp @ git+https://github.com/Arnost55/gemini-webapi-mcp.git", "gemini-webapi-mcp"]
    }
  }
}
```

</details>

<details>
<summary><b>Other MCP clients</b></summary>

Use the standard MCP stdio config:

```json
{
  "mcpServers": {
    "gemini": {
      "command": "uv",
      "args": ["run", "--with", "gemini-webapi-mcp @ git+https://github.com/Arnost55/gemini-webapi-mcp.git", "gemini-webapi-mcp"]
    }
  }
}
```

Config file path depends on your MCP client.

</details>
**Local install (after cloning)** — replace args with:

```json
"args": ["--directory", "/path/to/gemini-webapi-mcp", "run", "gemini-webapi-mcp"]
```

### 4. Install the skill for Claude Code (optional)

The [`skill/`](skill/) folder contains a Claude Code skill — prompting tips, tool documentation and an image generation guide. The skill auto-activates when working with Gemini.

```bash
cp -r skill ~/.claude/skills/gemini-mcp
```

### 5. Verify

Run the server manually — if it initializes without errors, everything works:

```bash
uv run --with "gemini-webapi-mcp @ git+https://github.com/Arnost55/gemini-webapi-mcp.git" gemini-webapi-mcp
```

Then open Claude Code or Claude Desktop and try: *"Generate a watercolor cat image with Gemini"*.

## Authentication

The legacy local mode reads cookies from Chrome automatically via
`browser-cookie3`. For reliable multi-account deployments, use the persistent
Chromium supervisor and one profile directory per Google account as described
above. Do not share a profile between accounts.

OAuth for ChatGPT or another MCP client is a separate gateway concern. It
identifies the caller and authorizes an account mapping; it does not log the
caller into Google. Put a reviewed OAuth 2.1 gateway in front of the
Streamable HTTP endpoint and validate the subject and scopes before selecting a
Gemini profile. This repository does not claim to implement Google OAuth or a
complete authorization server.

> **Gemini answers "Are you signed in?" or error 1096 while Chrome is logged in?** The server takes cookies from the `.google.com` domain only (since 2026-09-21); on an older version, cookies of Google country domains in Chrome (`.google.com.bd`, `.google.co.uk` …) break auth — update the server.

If cookie auto-detection fails, set them manually:

1. Open Chrome DevTools on gemini.google.com → Application → Cookies
2. Copy `__Secure-1PSID` and `__Secure-1PSIDTS` values
3. Add to your MCP config:

```json
{
  "mcpServers": {
    "gemini": {
      "command": "uv",
      "args": ["run", "--with", "gemini-webapi-mcp @ git+https://github.com/Arnost55/gemini-webapi-mcp.git", "gemini-webapi-mcp"],
      "env": {
        "GEMINI_PSID": "your__Secure-1PSID_value",
        "GEMINI_PSIDTS": "your__Secure-1PSIDTS_value"
      }
    }
  }
}
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GEMINI_PSID` | Cookie value `__Secure-1PSID` | auto from Chrome |
| `GEMINI_PSIDTS` | Cookie value `__Secure-1PSIDTS` | auto from Chrome |
| `GEMINI_LANGUAGE` | Gemini response language (`ru`, `en`, `ja`, ...) | `en` |
| `GEMINI_ACCOUNT_INDEX` | Legacy Chrome account index for local cookie mode | `0` |

## High Resolution (2x)

The server automatically requests an upscaled version of each generated image — the same mechanism used by the "Download" button in Gemini's web interface. Google performs server-side upscaling, delivering images at 2x resolution:

| Model | Native | 2x (downloaded) |
|-------|--------|-----------------|
| Flash-Thinking (16:9) | 1408x768 | 2816x1536 |
| Flash-Thinking (9:16) | 768x1376 | 1536x2752 |
| Flash-Thinking (1:1) | 1024x1024 | 2048x2048 |

If the 2x version is unavailable (timeout, network error), the server automatically falls back to native resolution.

## Watermark Removal

Gemini adds a sparkle watermark (4-point star) to the bottom-right corner of generated images. The server removes it with a built-in reverse alpha-blend — `original = (L − A·shape·255) / (1 − A·shape)` — with no external binaries, ML models, or downloads.

The mark is always stamped at one of two fixed corner anchors — **96 px** or **32 px** from the bottom-right (absolute offsets that don't depend on resolution; the logo is 48 px, ×2 when 2x-upscaled). The server does **not** detect the mark by a correlation threshold — that's unreliable on bright backgrounds, where a faint translucent star barely contrasts. Instead it deterministically processes **both anchors**:

1. **per-image opacity** — the mark's strength is fit from the frame itself (`L = bg + A·shape·(255−bg)`, least-squares); on an empty anchor `A ≈ 0`, so the subtraction is a no-op;
2. **reverse alpha-blend** at the anchor;
3. **|corr| self-check** — a subtraction is accepted only if it *reduces* the correlation with the star shape; otherwise it is reverted. It can never damage an empty, textured, or coloured-content anchor.

The approach hard-codes no resolution-dependent values, so it works at any native resolution or aspect ratio — on light, dark, and coloured backgrounds alike.

The only calibrated invariant is the star shape in `src/gemini_webapi_mcp/assets/wm_alpha_edit.npy`. Set `GEMINI_WM_KEEP=1` to disable removal (keep the raw watermark).

## Tools

| Tool | Description |
|------|-------------|
| `gemini_generate_image` | Generate new or edit existing images |
| `gemini_upload_file` | Analyze files — video, images, PDF, documents |
| `gemini_analyze_url` | Analyze URLs — YouTube videos, webpages, articles |
| `gemini_chat` | Text chat (single or multi-turn) |
| `gemini_start_chat` | Start a multi-turn session |
| `gemini_reset` | Re-initialize client on auth errors |

## Models

| Model | Default for | Notes |
|-------|-------------|-------|
| `gemini-3.0-flash` | chat, file analysis | Fast |
| `gemini-3.0-flash-thinking` | image generation | Nano Banana 2, supports aspect ratios |
| `gemini-3.0-pro` | — | Alternative model |

## Usage Examples

Once configured, Claude calls the right tools automatically. Just ask in chat:

| Task | What to tell Claude |
|------|---------------------|
| Generate an image | *"Generate a watercolor cat with Gemini"* |
| Edit an image | *"Edit /path/to/cat.png with Gemini — make the cat gray"* |
| Iterative refinement | *"Now make the background darker"* (same conversation) |
| Analyze a video | *"Analyze this video with Gemini: https://youtube.com/watch?v=..."* |
| Analyze a file | *"Upload /path/to/doc.pdf to Gemini and summarize it"* |

Tools that Claude will call:

```
gemini_generate_image(prompt="a cat in watercolor style")
gemini_generate_image(prompt="make it gray", files=["/path/to/cat.png"])
gemini_generate_image(prompt="make the background darker", conversation_id=["c_abc", "r_123", "rc_456"])
gemini_analyze_url(url="https://youtube.com/watch?v=...", prompt="Summarize this video")
gemini_upload_file(file_path="/path/to/doc.pdf", prompt="Summarize key points")
```

## Acknowledgements

This project is built on top of [gemini-webapi](https://github.com/HanaokaYuzu/Gemini-API) by [@HanaokaYuzu](https://github.com/HanaokaYuzu) (fork by [@xob0t](https://github.com/xob0t/Gemini-API) with curl_cffi support) — a reverse-engineered async Python wrapper for the Google Gemini web app. Licensed under AGPL-3.0.

The watermark-removal algorithm (Reverse Alpha Blending) was originally inspired by [`gwt-mini`](https://github.com/allenk/GeminiWatermarkTool) by [@allenk](https://github.com/allenk) (Allen Kuo, MIT License) and [gemini-watermark-remover](https://github.com/GargantuaX/gemini-watermark-remover) by [@GargantuaX](https://github.com/GargantuaX) (MIT License). The current version uses its own built-in implementation and calibrated alpha maps — no external binaries required.

## License

[AGPL-3.0](LICENSE) — free to use, modify, and distribute, provided the source code remains open.

**[@AndyShaman](https://github.com/AndyShaman)** · [gemini-webapi-mcp](https://github.com/AndyShaman/gemini-webapi-mcp)



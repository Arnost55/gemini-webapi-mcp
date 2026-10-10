# Gateway and ChatGPT Web deployment

The server supports both transports:

```bash
# Codex, Claude, and local MCP clients
uv run gemini-webapi-mcp --transport stdio

# A reverse proxy or MCP tunnel can expose this endpoint to ChatGPT
uv run gemini-webapi-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

Expose the HTTP listener only through an authenticated HTTPS reverse proxy or a
trusted MCP tunnel. Do not bind it to `0.0.0.0` directly on the public network.
The MCP SDK handles the Streamable HTTP protocol; the gateway must preserve
request bodies, `Mcp-Method`, `Mcp-Name`, and authorization headers.

## Authentication boundary

This project authenticates to Gemini Web with a browser session (`__Secure-1PSID`
and optionally `__Secure-1PSIDTS`). Those values are bearer credentials and must
be supplied through a secret manager or process environment; never put them in
logs, screenshots, git, or MCP tool results.

ChatGPT-to-MCP OAuth is a separate boundary. ChatGPT-compatible deployments
need an HTTPS authorization server and protected-resource metadata, including:

- `/.well-known/oauth-protected-resource`
- `/.well-known/oauth-authorization-server`
- authorization-code flow with PKCE S256
- an explicit `resource` value carried through authorization and token exchange
- access-token validation on every MCP request

The current repository does not pretend to be an authorization server. Put an
OAuth 2.1 provider or a reviewed MCP gateway in front of the HTTP listener and
configure its scopes to allow only the tools you intend to expose. This makes
the ChatGPT integration testable without confusing ChatGPT identity with Google
account authentication.

## Persistent Gemini Web session

The optional session supervisor keeps a dedicated Chromium profile for Gemini
Web. A human completes Google login once in headed mode; later checks run
headless and expose only safe status metadata. Cookie values are never printed,
returned by MCP, or written to logs.

```bash
uv sync --extra browser
uv run playwright install chromium

# One-time login bootstrap on a machine with a desktop session
uv run gemini-web-session --profile-dir /var/lib/gemini-mcp/chromium --headed --once

# Service mode: check every five minutes by default
uv run gemini-web-session --profile-dir /var/lib/gemini-mcp/chromium
```

Each check loads Gemini, classifies login redirects, reloads once when the
session appears invalid, and then records `login_required` if the retry still
needs login. A status of `error` means the browser check failed; it does not
include exception text that could contain secrets. The Chromium profile must
be readable only by the service account.

## Video generation

The upstream client currently provides video *analysis* through file upload or
URL analysis. It does not expose a verified Gemini Web video-generation RPC.
This fork therefore does not fabricate a `generate_video` tool or claim Veo
access. The intended next milestone is an adapter with a job id, status polling,
bounded expiry, and authenticated download; it should be enabled only after a
real browser request has been captured and tested against the current account.

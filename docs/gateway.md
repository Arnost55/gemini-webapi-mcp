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

Set the gateway's external origin explicitly:

```env
GEMINI_PUBLIC_URL=https://gemini-mcp.example.com
```

It must be the HTTPS origin users see. The gateway uses it for OAuth callback,
metadata, and browser-handoff URLs; `127.0.0.1` remains only the internal proxy
target. The value is rejected unless it is an HTTPS origin.

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

For multiple accounts, map the OAuth subject from the validated access token to
an account name. Dynamic registration stores the account mapping in an AES-256-GCM encrypted registry. Supply its key separately with `GEMINI_REGISTRY_KEY_FILE`. Base64 can wrap the key and ciphertext for JSON, but Base64 is not encryption. The registry stores metadata and profile names only; cookies remain in Chromium.

Each name must have its own profile directory; an unmapped
subject must be rejected rather than assigned a default account.

```json
{
  "subjects": {
    "oauth-subject-personal": "personal",
    "oauth-subject-work": "work"
  }
}
```

Use separate directories such as `/var/lib/gemini-mcp/accounts/personal` and
`/var/lib/gemini-mcp/accounts/work`, and run the headed bootstrap once for each
directory. OAuth identifies the caller; it does not perform Google login.

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

The gateway can also use the account registry for scopes, maintenance mode,
per-account request limits, and safe status files. A subject mapped to several
accounts must select one explicitly. Keep registry and status files outside
the repository and readable only by the service account.

## Browser handoff login

When the gateway needs to connect a Google account without requiring a local
browser GUI, it can expose a short-lived handoff page:

```text
1. Create login session for account: work
2. Open Google login in the isolated Chromium profile
3. Wait for Gemini session verification
4. Ask the user to approve: Allow this account
5. Consume the one-time handoff
```

The repository provides the state machine in
`gemini_webapi_mcp.handoff.HandoffManager`:

```text
pending → verified → approved → used
pending → expired
any active state → revoked
```

The gateway must generate its own HTTPS route, bind the handoff to the
validated OAuth subject and selected account, and pass the selected profile to
the session manager. Tokens must be stored hashed, expire quickly, be
single-use, and never contain cookies or Google credentials. The user must
approve after the Gemini page is verified as ready.

This is a browser handoff, not Google OAuth. A normal OAuth redirect can
authenticate the MCP caller to the gateway, but it does not transfer Gemini
Web cookies into a server-side browser profile.

## Video generation

The upstream client currently provides video *analysis* through file upload or
URL analysis. It does not expose a verified Gemini Web video-generation RPC.
This fork therefore does not fabricate a `generate_video` tool or claim Veo
access. The intended next milestone is an adapter with a job id, status polling,
bounded expiry, and authenticated download; it should be enabled only after a
real browser request has been captured and tested against the current account.

# Security threat model

This project has two separate authentication boundaries. MCP clients
authenticate to this server with OAuth 2.1, dynamic registration, authorization
code, PKCE S256, state, and bearer tokens. The user authenticates to Gemini Web
inside an isolated Chromium profile. The second boundary is not Google OAuth.
Gemini cookies remain in the profile volume and are never placed in OAuth URLs,
registry JSON, logs, or MCP responses.

## Account isolation

```text
ChatGPT/Codex -> DCR + OAuth/PKCE -> bearer subject -> selected owned account
    -> profile under GEMINI_PROFILE_ROOT -> profile cookies
    -> client keyed by (subject, account) -> Gemini Web
```

There is no global Gemini client or global cookie fallback for HTTP execution.
Sessions and generated image output are account-scoped. Profiles must resolve
below the configured profile root, and disabled, revoked, missing, or ambiguous
accounts fail closed.

## Browser handoff threats

- OAuth state is stored with the authorization code and checked on completion.
- Authorization codes are one-time and short-lived.
- PKCE challenges and verifiers are validated as S256 base64url values.
- Redirect URIs are registered and restricted to HTTPS or exact localhost HTTP.
- The noVNC `sid` is an opaque capability separate from the OAuth code.
- Each handoff has its own Chromium profile, display, VNC target, and token map.
- Completion promotes only the profile associated with that authorization.
- Expiry and completion terminate processes with bounded SIGTERM/SIGKILL waits,
  remove status data and token mappings, and delete unpromoted profiles.

The noVNC listener is loopback-bound. Publish only the protected noVNC HTTP
surface through TLS and a reverse proxy; never publish the VNC port itself.
Treat the generated noVNC URL as a bearer capability and apply no-referrer
headers at the proxy.

## Persistence and release gate

OAuth state and account metadata use AES-256-GCM and atomic replacement. Keep
keys, profiles, and state volumes persistent across container recreation and
readable only by the service account. Base64 is encoding, not encryption.

The encrypted account registry uses a separate sibling `.lock` file for
cross-process advisory locking. Every mutation reloads the latest encrypted
state while holding that lock, then writes a flushed temporary file and swaps
it into place. This prevents lost updates and partial files across workers;
the lock file itself contains no account data. The registry still requires a
reliable local filesystem with advisory-lock support and is not a substitute
for a distributed database.

Browser handoffs persist a token-scoped manifest containing only process,
profile, display, port, and expiry metadata. On startup, stale manifests are
recovered; POSIX cleanup signals a recorded process only when its environment
contains the matching handoff token and profile marker. This avoids killing a
reused PID belonging to another service. Windows recovery is conservative and
does not signal persisted PIDs without equivalent ownership verification.
Shutdown revokes active noVNC mappings and removes temporary profiles. A
container restart cannot recover a live browser login; users must start a new
handoff when the old desktop is gone.

The implementation is ready for code review after the automated checks, but
live two-account Gemini execution, public reverse-proxy behavior, and Google
session stability remain deployment checks rather than claims established by
the unit suite.

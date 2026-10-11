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

JSON persistence is safe against partial-file replacement, but is not a
multi-worker coordination protocol. Run one application worker until
inter-process locking or a shared storage layer is added. Key rotation requires
a controlled migration. The implementation is not called production-ready
until orphan cleanup, resource-limit enforcement, live proxy tests, and the
full integration suite are complete.

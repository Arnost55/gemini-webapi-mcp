# Configuration

Configuration is read from environment variables. Defaults and validation are
defined in the source configuration module; check that source when deploying a
new revision. Do not paste real values into issues or logs.

| Variable | Purpose |
|---|---|
| `GEMINI_PUBLIC_URL` | Public HTTPS origin used by the gateway OAuth metadata and browser handoff. |
| `GEMINI_REGISTRY_PATH` | Encrypted account registry path. |
| `GEMINI_REGISTRY_KEY_FILE` | Path to the separately mounted registry encryption key. |
| `GEMINI_OAUTH_STATE_KEY_FILE` | Key file for encrypted durable MCP OAuth state. |
| `GEMINI_OAUTH_STATE_PATH` | Persistent OAuth state file path. |
| `GEMINI_PROFILE_ROOT` | Root directory for isolated account browser profiles. |
| `GEMINI_CONVERSATIONS_PATH` | Persistent conversation database location. |
| `GEMINI_HANDOFF_ROOT` | Temporary browser-login handoff state location. |
| `GEMINI_BROWSER_CHANNEL` | Installed browser channel, e.g. `chrome`; mutually exclusive with executable path. |
| `GEMINI_BROWSER_EXECUTABLE` | Explicit browser executable path; mutually exclusive with channel. |
| `GEMINI_CHROMIUM_SANDBOX` | Whether direct Chrome handoff keeps Chrome sandbox enabled. |
| `GEMINI_HEADLESS` | Browser display mode setting. |
| `GEMINI_NOVNC_PUBLIC_URL` | Public HTTPS viewer URL for the temporary login handoff. |
| `GEMINI_NOVNC_WS_URL` | Public WSS WebSocket route for handoff display. |
| `GEMINI_VIEWER_FRAME_ANCESTORS` | Explicit CSP frame ancestors for the browser handoff viewer. |

The repository Compose file supplies additional paths, health checks, and
resource limits. Run `docker compose config` after editing it. Store secrets in
mounted secret files or a secret manager; do not put keys or cookies in the
Compose file, source control, command history, or diagnostics. Advanced
timeouts and limits are defined in `config.py` and should be verified against
the exact version before relying on them.

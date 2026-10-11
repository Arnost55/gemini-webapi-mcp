# Installation and deployment

## Local Codex or another STDIO client

Install Python 3.10 or newer and [uv](https://docs.astral.sh/uv/), then:

```bash
git clone https://github.com/Arnost55/gemini-webapi-mcp.git
cd gemini-webapi-mcp
uv sync
uv run gemini-webapi-mcp --transport stdio
```

Configure the client to launch `uv run gemini-webapi-mcp --transport stdio`
from this checkout. The legacy cookie mode reads an existing local Chrome
session. For multiple accounts, use the persistent Chromium profiles below.

## Debian or a Proxmox guest

Create a dedicated service account and keep the checkout and profiles outside
user home directories:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv ca-certificates
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo useradd --system --home /var/lib/gemini-mcp --create-home --shell /usr/sbin/nologin gemini-mcp
sudo -u gemini-mcp git clone https://github.com/Arnost55/gemini-webapi-mcp.git /var/lib/gemini-mcp/app
cd /var/lib/gemini-mcp/app
sudo -u gemini-mcp uv sync --extra browser
sudo -u gemini-mcp uv run playwright install chromium
sudo install -d -o gemini-mcp -g gemini-mcp -m 700 /var/lib/gemini-mcp/accounts
```

The first Google login requires a headed browser. Complete it from a desktop
session or an approved remote desktop connection, one profile at a time:

```bash
sudo -u gemini-mcp \
  /var/lib/gemini-mcp/app/.venv/bin/gemini-web-session \
  --profile-dir /var/lib/gemini-mcp/accounts/personal --headed --once
```

After login, run the profile headlessly. Do not copy profile directories,
cookies, or browser storage into tickets, repositories, or chat messages.

## Streamable HTTP gateway

Run the MCP server on loopback and place a reviewed HTTPS reverse proxy or MCP
tunnel in front of it:

```bash
uv run gemini-webapi-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

The built-in authorization server publishes protected-resource metadata,
supports dynamic public-client registration, PKCE S256, authorization-code
exchange, refresh-token rotation, and durable state when its volume is kept.
It does not implement Google OAuth. Google login remains inside an isolated
Chromium profile.

Configure the public origin in the gateway environment:

```bash
GEMINI_PUBLIC_URL=https://gemini.proxmoxserver.dev
```

This is the URL used for OAuth callbacks and handoff links. The MCP container
still listens internally on `127.0.0.1:8000`; the public HTTPS proxy forwards
that origin to it.

### Browser handoff login

For a GUI-free local client, the gateway exposes a short-lived custom browser
viewer. It contains only the isolated Chromium viewport:

```text
Connect Gemini account: work
[Open Google login]

Gemini session verified
[Allow this account]
```

Each authorization creates an independent Chromium profile, X display, loopback
VNC server, and opaque viewer capability URL. The URL expires after five minutes
by default. Completion succeeds only after the profile reports `ready`, and it
then removes the VNC capability and stops that temporary desktop. Browser
cookies never appear in URLs, logs, or MCP responses. This is a browser
handoff, not a claim that Google OAuth for Gemini Web is available.

The viewer link is a short-lived capability. Configure its HTTPS/WSS proxy with
`Referrer-Policy: no-referrer`, do not record query strings in access logs, and
do not expose the raw VNC port. The handoff profile is promoted into
the selected account directory after successful completion; expired or failed
handoffs are deleted.

Set `GEMINI_OAUTH_STATE_KEY_FILE` to a protected 32-byte key encoded as
URL-safe Base64. The server uses it to encrypt durable DCR registrations and
OAuth bearer-token records with AES-256-GCM. Reuse the same key on a container
recreation; changing it makes the existing OAuth state unreadable and requires
new client registrations.

## Dynamic encrypted account registry

Dynamic registration is the recommended deployment mode. Generate one key on the Debian guest and keep it out of Git:

```bash
uv run gemini-account-registry keygen --output ./registry.key
chmod 600 ./registry.key
```

The registry uses AES-256-GCM. Base64 is only used to encode the key and
encrypted JSON fields; it is not encryption. The encrypted file contains
account metadata and profile names, never cookies or credentials. Back up the
encrypted registry together with the browser profile volume, and use a
different key per environment.

The registry also creates `registry.json.enc.lock` beside the encrypted file.
Keep both on the same persistent volume. Multiple workers can register and
select accounts safely because each update locks and reloads the current file
before an fsync plus atomic replacement. Do not place the registry on storage
that does not provide reliable advisory file locks.
## Account policy

Keep the policy outside Git, for example at
`/etc/gemini-webapi-mcp/accounts.json`:

```json
{
  "subjects": {
    "oauth-subject-personal": ["personal"],
    "oauth-subject-work": ["work"]
  },
  "accounts": {
    "personal": {"scopes": ["gemini:use"], "requests_per_minute": 30},
    "work": {"scopes": ["gemini:use"], "requests_per_minute": 30}
  }
}
```

Each account must use a different profile directory. A subject mapped to more
than one account must send an explicit account selection. Keep the policy,
status files, and Chromium profiles readable only by the service account.

## Verification

```bash
uv run pytest -q
uv run gemini-web-session --profile-dir /var/lib/gemini-mcp/accounts/personal --once
```

The session command reports only safe state such as `ready` or
`login_required`; it never prints cookie values. A live Gemini check requires
a bootstrapped Google session and is separate from the unit test suite.

## Docker

Build and start the included image:

```bash
uv run gemini-account-registry keygen --output ./registry.key
docker compose build
docker compose up -d
docker compose logs -f gemini-mcp
```

The image installs Google Chrome Stable and launches it by default through the
`chrome` Playwright channel. If you set `GEMINI_BROWSER_CHANNEL=chrome` on an
older image, rebuild and recreate the container; an environment variable does
not download Chrome into an existing image.

The container listens on port 8000, while Compose binds it to loopback.
Chromium profiles and status data persist in the `gemini-state` volume. Keep
`registry.key` outside Git. Compose mounts it as a secret and stores the encrypted registry in the persistent `gemini-state` volume.

### Run the published GHCR image

```bash
docker login ghcr.io -u Arnost55
docker volume create gemini-state
docker run -d \
  --name gemini-mcp \
  --restart unless-stopped \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  -p 127.0.0.1:8000:8000 \
  -v gemini-state:/var/lib/gemini-mcp \
  -v "$PWD/registry.key:/run/secrets/gemini_registry_key:ro" \
  -e GEMINI_REGISTRY_PATH=/var/lib/gemini-mcp/registry.json.enc \
  -e GEMINI_REGISTRY_KEY_FILE=/run/secrets/gemini_registry_key \
  -e GEMINI_OAUTH_STATE_KEY_FILE=/run/secrets/gemini_registry_key \
  -e GEMINI_PUBLIC_URL=https://gemini.proxmoxserver.dev \
  -e GEMINI_OAUTH_STATE_PATH=/var/lib/gemini-mcp/oauth-state.json \
  -e GEMINI_HANDOFF_ROOT=/var/lib/gemini-mcp/handoffs \
  -e GEMINI_NOVNC_WS_URL=wss://gemini.proxmoxserver.dev/websockify \
  -e GEMINI_VIEWER_FRAME_ANCESTORS="'self' https://chatgpt.com" \
  ghcr.io/arnost55/gemini-webapi-mcp:latest
```

Keep the same `gemini-state` volume across image updates and container
recreation. It stores dynamic OAuth client registrations, OAuth tokens, the
encrypted account registry, and Chromium profiles. Reusing that volume keeps
the existing ChatGPT MCP connection registered; changing or deleting it
requires a new dynamic registration.

Each OAuth authorization now gets a private, unguessable `sid` and viewer
token. It is mapped only to the dedicated temporary VNC target for that login,
not a shared desktop. Completion, expiry, or container restart removes the live
desktop capability. Keep the embedded viewer and WSS transport behind HTTPS;
the token is a short-lived capability and must never be copied to tickets,
logs, or chat messages. Proxy
`/websockify` with WebSocket upgrades to the container's loopback transport;
do not expose port 6080 publicly.

The container writes token-scoped handoff manifests under the persistent state
volume. On Debian/POSIX restart, stale manifests are cleaned only when the
recorded processes carry the exact service markers; unrelated desktop
processes are not terminated. Windows cleanup is conservative and leaves
ambiguous persisted processes untouched. A restart invalidates the old live
desktop, so begin a new login handoff if the browser page is no longer ready.

### Compose with the published image

Save this as `docker-compose.yml` beside `registry.key`:

```yaml
services:
  gemini-mcp:
    image: ghcr.io/arnost55/gemini-webapi-mcp:latest
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - gemini-state:/var/lib/gemini-mcp
      - ./registry.key:/run/secrets/gemini_registry_key:ro
    environment:
      GEMINI_REGISTRY_PATH: /var/lib/gemini-mcp/registry.json.enc
      GEMINI_REGISTRY_KEY_FILE: /run/secrets/gemini_registry_key
      GEMINI_OAUTH_STATE_KEY_FILE: /run/secrets/gemini_registry_key
      GEMINI_PROFILE_ROOT: /var/lib/gemini-mcp/accounts
      GEMINI_OAUTH_STATE_PATH: /var/lib/gemini-mcp/oauth-state.json
      GEMINI_HANDOFF_ROOT: /var/lib/gemini-mcp/handoffs
      GEMINI_PUBLIC_URL: https://gemini.proxmoxserver.dev
      GEMINI_NOVNC_WS_URL: wss://gemini.proxmoxserver.dev/websockify
      GEMINI_VIEWER_FRAME_ANCESTORS: "'self' https://chatgpt.com"
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL

volumes:
  gemini-state:
```

Start it with:

```bash
docker compose up -d
docker compose logs -f gemini-mcp
```

If the package is private, authenticate with a GitHub token that has
`read:packages` before pulling the image. Keep the port bound to `127.0.0.1`
when an HTTPS gateway or tunnel is handling public access.

## Public HTTPS URL

The MCP server supports Streamable HTTP, but it does not manufacture a public
URL. Put an HTTPS reverse proxy or MCP tunnel in front of the loopback listener:

```text
https://gemini-mcp.example.com  ->  http://127.0.0.1:8000
```

Cloudflare Tunnel, Tailscale Funnel, an SSH tunnel with a secured proxy, or a
normal VPS reverse proxy can provide reachability. The public layer must
enforce HTTPS, OAuth 2.1/PKCE, origin and CSRF checks, rate limits, and account
mapping. Never expose port 8000 directly to the Internet.

## Current integration boundaries

Implemented components include model discovery, MCP transport startup,
Chromium session supervision, account policy selection, rate limiting, safe
status persistence, built-in OAuth metadata/DCR/PKCE, encrypted durable OAuth
state, and browser-handoff token lifecycle management.

The built-in OAuth server validates bearer tokens for account management and
Gemini execution. It binds a successful browser handoff to the OAuth subject,
promotes the profile into the encrypted registry's account directory, and
requires explicit selection when a subject owns multiple accounts. Put an
additional reviewed gateway in front when offering the service to untrusted
users.

Do not describe the deployment as Google OAuth. The user still signs into
Gemini Web inside the selected isolated browser profile.

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

The public gateway must authenticate callers, validate OAuth access tokens and
PKCE, enforce scopes, resolve the OAuth subject to an account, and reject
unmapped or ambiguous selections. The MCP server does not implement Google
OAuth. Google login remains inside the selected Chromium profile.

### Browser handoff login

For a GUI-free local client, the gateway can expose a short-lived handoff URL:

```text
Connect Gemini account: work
[Open Google login]

Gemini session verified
[Allow this account]
```

The gateway creates a one-time handoff bound to the validated OAuth subject and
selected account. The URL opens the isolated remote Chromium session, where the
user completes Google login. After the session manager reports `ready`, the
user explicitly approves the connection. The gateway then consumes the token
and expires it. Handoffs must expire after a short period, reject replay, and
never contain cookies or credentials. This is a browser handoff, not a claim
that Google OAuth for Gemini Web is available.

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
cp accounts.example.json accounts.json
docker compose build
docker compose up -d
docker compose logs -f gemini-mcp
```

The container listens on port 8000, while Compose binds it to loopback.
Chromium profiles and status data persist in the `gemini-state` volume. Keep
`accounts.json` outside Git and replace the example OAuth subjects.

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

Implemented library components include model discovery, MCP transport startup,
Chromium session supervision, account policy selection, rate limiting, safe
status persistence, and browser handoff token lifecycle management.

The deployment still needs a gateway application or reviewed MCP gateway to
connect these components to HTTP routes. That gateway must provide OAuth 2.1
metadata, PKCE S256, access-token validation, subject and scope extraction,
login handoff routes, approval UI, HTTPS, CSRF protection, secure cookies,
rate-limit responses, and audit logs.

Do not describe the deployment as Google OAuth. The user still signs into
Gemini Web inside the selected isolated browser profile.

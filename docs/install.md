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

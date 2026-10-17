# Authentication and accounts

There are separate authentication boundaries:

1. MCP clients authenticate to the server using its OAuth 2.1 authorization
   code flow with PKCE and durable encrypted state.
2. A human signs into Google inside an isolated Chromium profile through the
   browser handoff. This is not Google OAuth and does not expose cookies to the
   MCP client.
3. The encrypted account registry maps authorized MCP subjects to account
   names and scopes; callers with several accounts must select one explicitly.

Create the registry key using `gemini-account-registry keygen` and keep the
key outside the persistent data volume where practical. Back up the key
separately but together with encrypted registry and profile data. Losing or
changing the key can make persisted encrypted state unreadable. Never reset
profiles or copy browser cookies as routine troubleshooting.

The handoff URL is a short-lived capability. Use HTTPS/WSS, suppress query
strings in proxy logs, set `Referrer-Policy: no-referrer`, and do not share its
URL. See [install.md](install.md) and
[authentication architecture](authentication-architecture.md).
